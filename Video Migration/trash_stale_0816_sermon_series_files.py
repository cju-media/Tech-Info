"""One-off: move the incorrectly-generated 08-16-2026 Sermon Series files
(mislabeled with 08-09-2026's already-used 'Kin-dom Economics' title, see
Worship Scripts/worship workflows/create_sermon_series.py fix) to Drive
trash. Not a permanent delete - recoverable from Trash like Drive's own
Delete button.
"""
import os
import json
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials

PARENT_FOLDER_ID = '1Ji2Bbe7vWTcaRCpdQOjzwQgxsIoOWdy4'  # Sermon-Series parent, from create_sermon_series.py
DATE_FOLDER_NAME = '08-16-2026'
TARGET_FILENAMES = [
    'Sermon Series Title 08-16-2026.txt',
    'SS Description-08-16-2026.txt',
]


def get_drive_service():
    oauth_json = os.environ.get('GDRIVE_OAUTH_JSON')
    creds_dict = json.loads(oauth_json)
    creds = Credentials.from_authorized_user_info(creds_dict, scopes=['https://www.googleapis.com/auth/drive'])
    return build('drive', 'v3', credentials=creds)


def main():
    service = get_drive_service()

    # Find the 08-16-2026 date folder under the Sermon-Series parent
    query = f"'{PARENT_FOLDER_ID}' in parents and name = '{DATE_FOLDER_NAME}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    results = service.files().list(
        q=query, supportsAllDrives=True, includeItemsFromAllDrives=True, fields="files(id, name)"
    ).execute()
    folders = results.get('files', [])
    if not folders:
        print(f"No '{DATE_FOLDER_NAME}' folder found under parent {PARENT_FOLDER_ID}. Nothing to do.")
        return
    folder_id = folders[0]['id']
    print(f"Found folder '{DATE_FOLDER_NAME}' -> {folder_id}")

    for filename in TARGET_FILENAMES:
        query = f"'{folder_id}' in parents and name = '{filename}' and trashed = false"
        results = service.files().list(
            q=query, supportsAllDrives=True, includeItemsFromAllDrives=True, fields="files(id, name)"
        ).execute()
        files = results.get('files', [])
        if not files:
            print(f"'{filename}' not found in folder (already trashed/removed?). Skipping.")
            continue
        for f in files:
            service.files().update(fileId=f['id'], body={'trashed': True}, supportsAllDrives=True).execute()
            print(f"Trashed '{filename}' (id={f['id']}).")


if __name__ == "__main__":
    main()
