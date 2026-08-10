"""
One-off utility to fix a specific instance of the "duplicate dated Sermon
Series folder" bug.

Background: create_sermon_series.py, upload_queue_to_drive.py, and
migrate_videos.py each independently run their own "find or create a folder
named <MM-DD-YYYY> under the Sermon Series parent" logic. Because Google
Drive allows multiple folders with the same name under the same parent, two
separate "08-09-2026" folders were created:
  - SOURCE_FOLDER_ID: created by create_sermon_series.py, holds the
    Title/Description .txt files.
  - TARGET_FOLDER_ID: created by upload_queue_to_drive.py (sermon series
    thumbnail) and later reused by migrate_videos.py, which copied this
    Sunday's video there.

This script moves the text/plain files (Title + Description) out of
SOURCE_FOLDER_ID and into TARGET_FOLDER_ID so migrate_videos.py can find
them alongside the video on its next run, then reports the final contents
of both folders. It does not delete or trash anything.

This script is meant to be run once, manually, via workflow_dispatch. It is
not part of the regular automation pipeline.
"""

import os
import io
import json
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
from google.oauth2.credentials import Credentials

SERMON_SERIES_PARENT_ID = '1Ji2Bbe7vWTcaRCpdQOjzwQgxsIoOWdy4'
SOURCE_FOLDER_ID = '15tZbMeMuDDVbPKsntLK3_F5kHsqdC7l5'  # had the Description .txt
TARGET_FOLDER_ID = '1W2gpCIZ9zXOFonDwVrl3CrzSHuxEIHgp'  # has the video + thumbnail

# The Title file (Sermon Series Title 08-09-2026.txt) was uploaded to Drive on
# Aug 3 per the sermon_series.yml run log, but is now missing from Drive
# entirely (not in the source folder, not in trash, not findable anywhere by
# name). Its content is still preserved in git history untouched since Aug 3,
# so we restore it directly from the repo rather than guessing at what
# happened to the Drive copy.
LOCAL_TITLE_FILE = os.path.join(
    os.path.dirname(__file__), '..', 'Worship Scripts', 'Sermon-Series', 'Sermon Series Title 08-09-2026.txt'
)
TITLE_FILENAME = 'Sermon Series Title 08-09-2026.txt'


def get_drive_service():
    oauth_json = os.environ.get('GDRIVE_OAUTH_JSON')
    if not oauth_json:
        print("Error: GDRIVE_OAUTH_JSON environment variable not found.")
        return None
    creds_dict = json.loads(oauth_json)
    creds = Credentials.from_authorized_user_info(creds_dict, scopes=['https://www.googleapis.com/auth/drive'])
    return build('drive', 'v3', credentials=creds)


def list_folder(service, folder_id, label):
    results = service.files().list(
        q=f"'{folder_id}' in parents and trashed = false",
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
        fields="files(id, name, mimeType)"
    ).execute()
    files = results.get('files', [])
    print(f"\n--- Contents of {label} ({folder_id}) ---")
    for f in files:
        print(f"  {f['name']}  [{f['mimeType']}]  id={f['id']}")
    if not files:
        print("  (empty)")
    return files


def diagnose_missing_title(service):
    print("\n=== DIAGNOSTIC: locating 'Sermon Series Title 08-09-2026.txt' ===")

    # 1. Any other folders under the Sermon Series parent also named 08-09-2026?
    q = (f"'{SERMON_SERIES_PARENT_ID}' in parents and name = '08-09-2026' "
         f"and mimeType = 'application/vnd.google-apps.folder' and trashed = false")
    results = service.files().list(
        q=q, supportsAllDrives=True, includeItemsFromAllDrives=True,
        fields="files(id, name)"
    ).execute()
    folders = results.get('files', [])
    print(f"\nAll folders named '08-09-2026' under Sermon Series parent ({SERMON_SERIES_PARENT_ID}):")
    for fdr in folders:
        print(f"  {fdr['name']}  id={fdr['id']}")
        list_folder(service, fdr['id'], f"  subfolder {fdr['id']}")

    # 2. Global search by name, including trashed, regardless of parent.
    q2 = "name contains 'Sermon Series Title 08-09-2026'"
    results2 = service.files().list(
        q=q2, supportsAllDrives=True, includeItemsFromAllDrives=True,
        corpora='allDrives',
        fields="files(id, name, parents, trashed, mimeType, modifiedTime)"
    ).execute()
    matches = results2.get('files', [])
    print(f"\nGlobal search for name containing 'Sermon Series Title 08-09-2026' (any parent, any trashed state):")
    if not matches:
        print("  No matches found anywhere in Drive.")
    for m in matches:
        print(f"  {m['name']}  id={m['id']}  parents={m.get('parents')}  trashed={m.get('trashed')}  modified={m.get('modifiedTime')}")


def main():
    service = get_drive_service()
    if not service:
        return

    print("BEFORE:")
    source_files = list_folder(service, SOURCE_FOLDER_ID, "source (title/description) folder")
    list_folder(service, TARGET_FOLDER_ID, "target (video/thumbnail) folder")

    to_move = [f for f in source_files if f['mimeType'] == 'text/plain']

    if not to_move:
        print("\nNo text/plain files found in the source folder to move.")
    else:
        print(f"\nMoving {len(to_move)} file(s) from source to target folder...")
        for f in to_move:
            print(f"  Moving '{f['name']}' ({f['id']})...")
            service.files().update(
                fileId=f['id'],
                addParents=TARGET_FOLDER_ID,
                removeParents=SOURCE_FOLDER_ID,
                supportsAllDrives=True,
                fields='id, parents'
            ).execute()
        print("Move complete.")

    print("\nAFTER:")
    list_folder(service, SOURCE_FOLDER_ID, "source (title/description) folder")
    target_after = list_folder(service, TARGET_FOLDER_ID, "target (video/thumbnail) folder")

    diagnose_missing_title(service)

    have_title = any(f['name'] == TITLE_FILENAME for f in target_after)
    if have_title:
        print(f"\n'{TITLE_FILENAME}' already present in target folder. Nothing to restore.")
    elif os.path.exists(LOCAL_TITLE_FILE):
        with open(LOCAL_TITLE_FILE, 'r') as fh:
            content = fh.read()
        print(f"\nRestoring '{TITLE_FILENAME}' into target folder from git (content: {content!r})...")
        media = MediaIoBaseUpload(io.BytesIO(content.encode('utf-8')), mimetype='text/plain', resumable=True)
        file_metadata = {'name': TITLE_FILENAME, 'parents': [TARGET_FOLDER_ID]}
        created = service.files().create(
            body=file_metadata, media_body=media, supportsAllDrives=True, fields='id'
        ).execute()
        print(f"Created '{TITLE_FILENAME}' in target folder, id={created.get('id')}")

        print("\nFINAL target folder contents:")
        list_folder(service, TARGET_FOLDER_ID, "target (video/thumbnail) folder")
    else:
        print(f"\nCould not find local file at {LOCAL_TITLE_FILE} to restore from.")


if __name__ == "__main__":
    main()
