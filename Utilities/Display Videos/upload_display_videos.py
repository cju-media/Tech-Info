"""
Moves videos from the upload dashboard into the Display Videos Drive folder.

The dashboard can only talk to GitHub -- the Drive credentials are Actions
secrets -- so a video has to pass through the repo on its way. Not through
main, though: everything committed there stays in the history every clone
downloads, and one lobby video is most of the size of the whole repo. So
the dashboard commits each batch to its own short-lived branch,
video-upload/<timestamp>, and this moves it to Drive and deletes the branch.
Nothing reaches main, and GitHub garbage-collects the orphaned blobs.

GitHub's API refuses large blobs -- a 57 MB video came back "too large to
process" -- so the dashboard splits each video into 10 MB parts, well clear
of wherever the limit is:

    Utilities/display_video_queue/video-1/manifest.json
        {"name": "building.mov", "size": 60397977, "sha256": "...", "parts": 6}
    Utilities/display_video_queue/video-1/part-0001 ... part-0006

This puts the parts back together, checks the result against the hash the
dashboard took of the original, uploads it, gives it a shuffle code (mode
"new", so it lands at a random point in the loop), and deletes the branch.
A branch is only deleted once every video on it is in Drive; anything that
fails leaves the branch for the next run. A video already in the folder
with the same bytes isn't uploaded twice, so a retry is always safe.

Env:
    GITHUB_TOKEN, GITHUB_REPOSITORY   provided by Actions
    GDRIVE_OAUTH_JSON / GDRIVE_SERVICE_ACCOUNT_JSON   required
    BRANCH           process just this branch (default: every video-upload/*)
    DRY_RUN          "1" downloads and checks, but uploads and deletes nothing
"""

import hashlib
import io
import json
import os
import random
import sys
import urllib.parse

import requests

import shuffle_display_videos as shuffle

BRANCH_PREFIX = 'video-upload/'
QUEUE_PATH = 'Utilities/display_video_queue'
API = 'https://api.github.com'


class GitHub:
    def __init__(self, token, repo):
        self.repo = repo
        self.session = requests.Session()
        self.session.headers.update({'Authorization': f'Bearer {token}',
                                     'Accept': 'application/vnd.github+json'})

    def _url(self, path):
        return f'{API}/repos/{self.repo}/{path}'

    def get_json(self, path, **params):
        resp = self.session.get(self._url(path), params=params, timeout=60)
        resp.raise_for_status()
        return resp.json()

    def blob(self, sha):
        resp = self.session.get(self._url(f'git/blobs/{sha}'), timeout=300,
                                headers={'Accept': 'application/vnd.github.raw'})
        resp.raise_for_status()
        return resp.content

    def upload_branches(self):
        refs = self.get_json(f'git/matching-refs/heads/{BRANCH_PREFIX}')
        return [r['ref'][len('refs/heads/'):] for r in refs]

    def listing(self, path, branch):
        quoted = urllib.parse.quote(path)
        return self.get_json(f'contents/{quoted}', ref=branch)

    def delete_branch(self, branch):
        resp = self.session.delete(
            self._url(f'git/refs/heads/{urllib.parse.quote(branch)}'), timeout=60)
        if resp.status_code not in (204, 422):     # 422: already gone
            resp.raise_for_status()


def read_videos(gh, branch):
    """[(name, bytes)] for every video queued on `branch`, reassembled and
    checked. Raises if any part is missing or the bytes don't match."""
    videos = []
    for entry in gh.listing(QUEUE_PATH, branch):
        if entry.get('type') != 'dir':
            continue
        files = {f['name']: f['sha'] for f in gh.listing(entry['path'], branch)}
        manifest = json.loads(gh.blob(files['manifest.json']))
        videos.append((manifest['name'], assemble(manifest, files, gh.blob)))
    return videos


def assemble(manifest, files, fetch):
    """The original video from its parts, or ValueError if it isn't whole."""
    parts = []
    for n in range(1, int(manifest['parts']) + 1):
        name = f'part-{n:04d}'
        if name not in files:
            raise ValueError(f"{manifest['name']}: {name} is missing")
        parts.append(fetch(files[name]))
    data = b''.join(parts)
    if len(data) != int(manifest['size']):
        raise ValueError(f"{manifest['name']}: {len(data)} bytes, expected {manifest['size']}")
    if hashlib.sha256(data).hexdigest() != manifest['sha256'].lower():
        raise ValueError(f"{manifest['name']}: bytes don't match the dashboard's hash")
    return data


# --------------------------------------------------------------------------
# Drive
# --------------------------------------------------------------------------

MIME_TYPES = {'.mov': 'video/quicktime', '.mp4': 'video/mp4', '.m4v': 'video/x-m4v'}


def already_in_folder(drive, folder_id, data):
    """The Drive file with exactly these bytes, if the folder has one."""
    md5 = hashlib.md5(data).hexdigest()
    token = None
    while True:
        resp = drive.files().list(
            q=f"'{folder_id}' in parents and trashed=false",
            fields='nextPageToken, files(id, name, md5Checksum)', pageToken=token,
            supportsAllDrives=True, includeItemsFromAllDrives=True).execute()
        for f in resp.get('files', []):
            if f.get('md5Checksum') == md5:
                return f
        token = resp.get('nextPageToken')
        if not token:
            return None


def upload(drive, folder_id, name, data):
    from googleapiclient.http import MediaIoBaseUpload
    mime = MIME_TYPES.get(os.path.splitext(name)[1].lower(), 'application/octet-stream')
    media = MediaIoBaseUpload(io.BytesIO(data), mimetype=mime, resumable=True,
                              chunksize=8 * 1024 * 1024)
    return drive.files().create(body={'name': name, 'parents': [folder_id]},
                                media_body=media, fields='id, name',
                                supportsAllDrives=True).execute()


def shuffle_new(drive, folder_id, dry_run):
    """Give the new arrivals codes, so they join the loop at random points."""
    files = shuffle.list_files(drive, folder_id)
    for f, new_name in shuffle.plan(files, 'new', random):
        print(f"  {f['name']}  ->  {new_name}")
        if not dry_run:
            shuffle.rename(drive, f, new_name)


# --------------------------------------------------------------------------

def process_branch(gh, drive, folder_id, branch, dry_run):
    print(f'\n{branch}')
    videos = read_videos(gh, branch)
    print(f'  {len(videos)} video(s), all parts present and matching.')
    for name, data in videos:
        existing = already_in_folder(drive, folder_id, data)
        if existing:
            print(f"  {name}: already in Drive as {existing['name']!r}; not uploading again.")
            continue
        print(f'  {name}: uploading {len(data) / 1048576:.1f} MB...')
        if not dry_run:
            upload(drive, folder_id, name, data)
    shuffle_new(drive, folder_id, dry_run)
    if dry_run:
        print(f'  DRY RUN: would delete {branch}.')
    else:
        gh.delete_branch(branch)
        print(f'  Deleted {branch}.')


def main():
    dry_run = os.environ.get('DRY_RUN') == '1'
    folder_id = os.environ.get('DISPLAY_VIDEOS_FOLDER_ID') or shuffle.FOLDER_ID
    gh = GitHub(os.environ['GITHUB_TOKEN'], os.environ['GITHUB_REPOSITORY'])

    branch = (os.environ.get('BRANCH') or '').strip()
    if branch.startswith('refs/heads/'):
        branch = branch[len('refs/heads/'):]
    branches = [branch] if branch else gh.upload_branches()
    branches = [b for b in branches if b.startswith(BRANCH_PREFIX)]
    if not branches:
        print('No video uploads waiting.')
        return

    from upload_queue_to_drive import get_drive_service
    drive = get_drive_service()
    if not drive:
        sys.exit('No Drive credentials: set GDRIVE_OAUTH_JSON or GDRIVE_SERVICE_ACCOUNT_JSON.')

    failed = []
    for b in branches:
        try:
            process_branch(gh, drive, folder_id, b, dry_run)
        except Exception as exc:                  # noqa: BLE001
            print(f'  Failed ({exc.__class__.__name__}: {exc}); leaving {b} for the next run.')
            failed.append(b)
    if failed:
        sys.exit(f'{len(failed)} upload branch(es) not finished: ' + ', '.join(failed))


if __name__ == '__main__':
    main()
