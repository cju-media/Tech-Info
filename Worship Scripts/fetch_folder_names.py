import os
import json
from googleapiclient.discovery import build
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials

FOLDER_IDS = [
    '1MVeC2j0v4zTA1sVjhLz06bqEz3qbaYxs',
    '1ctYBJnFLNkdNhgoU4XLcgJc3QTz7MqwI'
]

OUTPUT_JSON_PATH = 'uploads/folder_names.json'

def get_drive_service():
    oauth_json = os.environ.get('GDRIVE_OAUTH_JSON')
    if oauth_json:
        try:
            creds_dict = json.loads(oauth_json)
            # Use standard drive scope to match the generated token
            creds = Credentials.from_authorized_user_info(creds_dict, scopes=['https://www.googleapis.com/auth/drive'])
            print("Using GDRIVE_OAUTH_JSON for authentication.")
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
            print("Using GDRIVE_SERVICE_ACCOUNT_JSON for authentication.")
            return build('drive', 'v3', credentials=creds)
        except Exception as e:
            print(f"Error parsing GDRIVE_SERVICE_ACCOUNT_JSON: {e}")
            return None

    print("Warning: Neither GDRIVE_OAUTH_JSON nor GDRIVE_SERVICE_ACCOUNT_JSON is set.")
    return None

def main():
    drive_service = get_drive_service()
    if not drive_service:
        print("Skipping Google Drive fetch due to missing credentials.")
        return

    folder_mapping = {}

    for folder_id in FOLDER_IDS:
        try:
            folder = drive_service.files().get(
                fileId=folder_id,
                fields="id, name",
                supportsAllDrives=True
            ).execute()

            name = folder.get('name')
            print(f"Fetched name for {folder_id}: {name}")
            folder_mapping[folder_id] = name
        except Exception as e:
            print(f"Error fetching folder {folder_id}: {e}")
            # Do not overwrite with failure to avoid breaking the frontend
            return

    # Ensure output directory exists
    os.makedirs(os.path.dirname(OUTPUT_JSON_PATH), exist_ok=True)

    with open(OUTPUT_JSON_PATH, 'w', encoding='utf-8') as f:
        json.dump(folder_mapping, f, indent=4)

    print(f"Successfully saved folder mapping to {OUTPUT_JSON_PATH}")

if __name__ == "__main__":
    main()