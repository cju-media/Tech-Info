"""
One-off / occasional cleanup: removes duplicate flyers from the "Events_Ads"
Google Drive folder (the flat folder the upload dashboard's Events Ad zone
drops flyers into -- see Utilities/uploads/index.html).

Overlapping "Process Uploads Queue" runs used to upload the same queued file
to Drive two or three times (a batch upload pushed one commit per file, each
firing the workflow, and every run processed the whole queue it saw at
checkout). The concurrency gate on process_uploads.yml stops new duplicates;
this clears out the ones already there.

"Duplicate" here means byte-identical: same MD5 checksum. Files are grouped
by checksum, the earliest-created copy of each group is kept, and the rest
are moved to Drive's Trash (never permanently deleted -- Drive keeps trashed
items ~30 days, so a mistake is recoverable). Files with a unique checksum
are left completely alone; a genuinely different flyer that happens to reuse
a filename is not a duplicate and is not touched.

Google Drive only lets a personal (non-Shared-Drive) file's OWNER trash it --
Editor access does not include canTrash (see cleanup_events_folder.py's long
note). Every duplicate the queue race produced was uploaded by the pipeline's
own Drive account, so it owns them and can trash them. Anything this script
can't trash (a hand-uploaded copy) is reported and left for manual cleanup.

Auth: same pattern as cleanup_events_folder.py -- prefers the service account
(GDRIVE_SERVICE_ACCOUNT_JSON), which has been granted edit access on this
folder, over GDRIVE_OAUTH_JSON.

DRY_RUN defaults to "1" (report only). Set DRY_RUN=0 to actually trash.
"""

import os
import json
import datetime

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials

EVENTS_FOLDER_ID = '17-0kiqBKa0k5ofW6gOPrVbHl7nqanuQz'


def get_drive_service():
    # Prefer the service account here (same reasoning as cleanup_events_folder.py):
    # it's the account that owns the pipeline-uploaded duplicates, so it's the
    # one allowed to trash them.
    service_account_json = os.environ.get('GDRIVE_SERVICE_ACCOUNT_JSON')
    if service_account_json:
        try:
            creds_dict = json.loads(service_account_json)
            creds = service_account.Credentials.from_service_account_info(
                creds_dict, scopes=['https://www.googleapis.com/auth/drive']
            )
            print(f"Using GDRIVE_SERVICE_ACCOUNT_JSON for authentication ({creds_dict.get('client_email')}).")
            return build('drive', 'v3', credentials=creds)
        except Exception as e:
            print(f"Error parsing GDRIVE_SERVICE_ACCOUNT_JSON: {e}")

    oauth_json = os.environ.get('GDRIVE_OAUTH_JSON')
    if oauth_json:
        try:
            creds_dict = json.loads(oauth_json)
            creds = Credentials.from_authorized_user_info(creds_dict, scopes=['https://www.googleapis.com/auth/drive'])
            print("Using GDRIVE_OAUTH_JSON for authentication.")
            return build('drive', 'v3', credentials=creds)
        except Exception as e:
            print(f"Error parsing GDRIVE_OAUTH_JSON: {e}")
            return None

    print("Warning: Neither GDRIVE_SERVICE_ACCOUNT_JSON nor GDRIVE_OAUTH_JSON is set.")
    return None


def list_folder_files(service, folder_id):
    """Every non-trashed, non-folder file directly in folder_id."""
    query = (
        f"'{folder_id}' in parents and trashed=false "
        f"and mimeType != 'application/vnd.google-apps.folder'"
    )
    files = []
    page_token = None
    while True:
        results = service.files().list(
            q=query, spaces='drive',
            fields=('nextPageToken, files(id, name, md5Checksum, size, '
                    'createdTime, owners(emailAddress), capabilities(canTrash))'),
            pageToken=page_token, supportsAllDrives=True, includeItemsFromAllDrives=True,
            pageSize=1000,
        ).execute()
        files.extend(results.get('files', []))
        page_token = results.get('nextPageToken')
        if not page_token:
            break
    return files


def parse_created(ts):
    """Drive createdTime -> datetime, for picking the earliest copy. Falls
    back to datetime.max so a file with an unparseable timestamp is never
    chosen as the one to keep over a file with a real one."""
    if not ts:
        return datetime.datetime.max
    try:
        return datetime.datetime.fromisoformat(ts.replace('Z', '+00:00'))
    except ValueError:
        return datetime.datetime.max


def main():
    is_dry_run = os.environ.get('DRY_RUN', '1') == '1'

    service = get_drive_service()
    if not service:
        print("No Drive service available; aborting.")
        return

    print(f"Scanning Events_Ads folder ({EVENTS_FOLDER_ID}) for byte-identical duplicates...")
    files = list_folder_files(service, EVENTS_FOLDER_ID)
    print(f"Found {len(files)} file(s) in the folder.")

    by_checksum = {}
    no_checksum = []
    for f in files:
        md5 = f.get('md5Checksum')
        if not md5:
            no_checksum.append(f)
            continue
        by_checksum.setdefault(md5, []).append(f)

    if no_checksum:
        print(f"\n{len(no_checksum)} file(s) have no MD5 checksum (Google-native or export-only); "
              f"skipping those, they can't be compared: {[f['name'] for f in no_checksum]}")

    dup_groups = {md5: group for md5, group in by_checksum.items() if len(group) > 1}

    if not dup_groups:
        print("\nNo duplicates found. Nothing to do.")
        return

    total_extra = sum(len(g) - 1 for g in dup_groups.values())
    print(f"\n{len(dup_groups)} set(s) of duplicates, {total_extra} redundant copy(ies) to trash.\n")

    trashed_count = 0
    manual_needed = []

    for md5, group in dup_groups.items():
        group.sort(key=lambda f: parse_created(f.get('createdTime')))
        keep = group[0]
        drop = group[1:]
        print(f"Checksum {md5[:10]}...  ({int(keep.get('size', 0)):,} bytes)")
        print(f"  KEEP  {keep['name']}  (created {keep.get('createdTime')}, id {keep['id']})")
        for f in drop:
            owner = (f.get('owners') or [{}])[0].get('emailAddress', 'unknown')
            can_trash = (f.get('capabilities') or {}).get('canTrash', False)
            if not can_trash:
                print(f"  SKIP  {f['name']}  (id {f['id']}) -- not trashable via API (owner: {owner})")
                manual_needed.append(f)
                continue
            if is_dry_run:
                print(f"  DRY RUN: would trash {f['name']}  (created {f.get('createdTime')}, id {f['id']})")
                continue
            try:
                service.files().update(
                    fileId=f['id'], body={'trashed': True}, supportsAllDrives=True
                ).execute()
                print(f"  TRASHED  {f['name']}  (id {f['id']})")
                trashed_count += 1
            except HttpError as e:
                print(f"  FAILED to trash {f['name']} (id {f['id']}): {e}")
                manual_needed.append(f)
        print()

    if manual_needed:
        print("Copies that need manual deletion (not owned by this account / not trashable):")
        for f in manual_needed:
            print(f"  - {f['name']}  https://drive.google.com/file/d/{f['id']}/view")

    if is_dry_run:
        print(f"Done (dry run). Would have trashed {total_extra - len(manual_needed)} copy(ies). "
              f"Set DRY_RUN=0 to apply.")
    else:
        print(f"Done. Trashed {trashed_count} duplicate copy(ies).")


if __name__ == "__main__":
    main()
