"""
Backfill the real YouTube stream link into a Sermon Series description once
the worship livestream is created.

Why this exists: create_sermon_series.py generates the Sermon Series
Description ahead of the livestream being scheduled (it's driven by the
sermon title syncing, a separate pipeline from the Worship Service
thumbnail upload that actually creates the livestream). When it runs before
the stream exists, it embeds a "YOUTUBE SERVICE LINK" placeholder instead of
a real URL.

create_youtube_stream.py already fires a `youtube_stream_created`
repository_dispatch event the moment it creates a stream, with the stream's
URL and date. This script is meant to run off that same event and patch the
placeholder (in both the git copy and every matching Drive folder, since a
given date can end up with more than one same-named folder) with the real
link. If the description already has the correct link, this is a no-op.
"""

import os
import io
import re
import sys
import json
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload, MediaIoBaseDownload
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials

SERMON_SERIES_PARENT_ID = '1Ji2Bbe7vWTcaRCpdQOjzwQgxsIoOWdy4'
LINK_LINE_PREFIX = "Watch the Service: "
PLACEHOLDER = "YOUTUBE SERVICE LINK"


def get_drive_service():
    oauth_json = os.environ.get('GDRIVE_OAUTH_JSON')
    if oauth_json:
        try:
            creds_dict = json.loads(oauth_json)
            creds = Credentials.from_authorized_user_info(creds_dict, scopes=['https://www.googleapis.com/auth/drive'])
            return build('drive', 'v3', credentials=creds)
        except Exception as e:
            print(f"Error parsing GDRIVE_OAUTH_JSON: {e}")

    service_account_json = os.environ.get('GDRIVE_SERVICE_ACCOUNT_JSON')
    if service_account_json:
        try:
            creds_dict = json.loads(service_account_json)
            creds = service_account.Credentials.from_service_account_info(
                creds_dict, scopes=['https://www.googleapis.com/auth/drive']
            )
            return build('drive', 'v3', credentials=creds)
        except Exception as e:
            print(f"Error parsing GDRIVE_SERVICE_ACCOUNT_JSON: {e}")
            return None

    print("Warning: Neither GDRIVE_OAUTH_JSON nor GDRIVE_SERVICE_ACCOUNT_JSON is set.")
    return None


def patch_link(content, stream_url):
    """Replace the 'Watch the Service: <link>' line's link with stream_url.
    Returns (new_content, changed)."""
    pattern = re.compile(rf"^({re.escape(LINK_LINE_PREFIX)}).*$", re.MULTILINE)
    if not pattern.search(content):
        return content, False

    def repl(m):
        return f"{m.group(1)}{stream_url}"

    new_content = pattern.sub(repl, content)
    return new_content, (new_content != content)


def backfill_local(date_str, stream_url):
    path = os.path.join("Sermon-Series", f"SS Description-{date_str}.txt")
    if not os.path.exists(path):
        print(f"No local description file at {path}, skipping local backfill.")
        return False

    with open(path, 'r') as f:
        content = f.read()

    new_content, changed = patch_link(content, stream_url)
    if not changed:
        print(f"Local file {path} already has the correct link (or no link line found). No change.")
        return False

    with open(path, 'w') as f:
        f.write(new_content)
    print(f"Updated local file {path} with real stream link.")
    return True


def backfill_drive(service, date_str, stream_url):
    # Defensively check every folder with this date name, not just the first
    # match, since duplicate same-named folders have happened before.
    query = (f"'{SERMON_SERIES_PARENT_ID}' in parents and name = '{date_str}' "
             f"and mimeType = 'application/vnd.google-apps.folder' and trashed = false")
    folders = service.files().list(
        q=query, supportsAllDrives=True, includeItemsFromAllDrives=True,
        fields="files(id, name)"
    ).execute().get('files', [])

    if not folders:
        print(f"No Drive folder named '{date_str}' found under Sermon Series parent.")
        return

    for folder in folders:
        query_text = f"'{folder['id']}' in parents and mimeType = 'text/plain' and trashed = false"
        text_files = service.files().list(
            q=query_text, supportsAllDrives=True, includeItemsFromAllDrives=True,
            fields="files(id, name)"
        ).execute().get('files', [])

        for t_file in text_files:
            name_lower = t_file['name'].lower()
            if 'description' not in name_lower or 'sermon-series-description' in name_lower:
                continue

            request = service.files().get_media(fileId=t_file['id'])
            fh = io.BytesIO()
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()
            content = fh.getvalue().decode('utf-8')

            new_content, changed = patch_link(content, stream_url)
            if not changed:
                print(f"Drive file '{t_file['name']}' in folder {folder['id']} already correct (or no link line). No change.")
                continue

            media = MediaIoBaseUpload(io.BytesIO(new_content.encode('utf-8')), mimetype='text/plain', resumable=True)
            service.files().update(fileId=t_file['id'], media_body=media, supportsAllDrives=True).execute()
            print(f"Updated Drive file '{t_file['name']}' in folder {folder['id']} with real stream link.")


def main():
    stream_url = os.environ.get('STREAM_URL')
    date_str = os.environ.get('STREAM_DATE')

    if not stream_url or not date_str:
        print("STREAM_URL and STREAM_DATE must be set. Nothing to do.")
        sys.exit(0)

    print(f"Backfilling Sermon Series description link for {date_str} -> {stream_url}")

    backfill_local(date_str, stream_url)

    service = get_drive_service()
    if service:
        backfill_drive(service, date_str, stream_url)
    else:
        print("Could not get Drive service, skipping Drive backfill.")


if __name__ == "__main__":
    main()
