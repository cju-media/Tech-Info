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
import json
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials

SOURCE_FOLDER_ID = '15tZbMeMuDDVbPKsntLK3_F5kHsqdC7l5'  # has Title/Description .txt
TARGET_FOLDER_ID = '1W2gpCIZ9zXOFonDwVrl3CrzSHuxEIHgp'  # has the video + thumbnail


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


def main():
    service = get_drive_service()
    if not service:
        return

    print("BEFORE:")
    source_files = list_folder(service, SOURCE_FOLDER_ID, "source (title/description) folder")
    list_folder(service, TARGET_FOLDER_ID, "target (video/thumbnail) folder")

    to_move = [f for f in source_files if f['mimeType'] == 'text/plain']

    if not to_move:
        print("\nNo text/plain files found in the source folder to move. Nothing to do.")
        return

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
    list_folder(service, TARGET_FOLDER_ID, "target (video/thumbnail) folder")


if __name__ == "__main__":
    main()
