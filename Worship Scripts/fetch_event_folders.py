import os
import json
from googleapiclient.discovery import build
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials

# The private top-level folder ID provided by the user
TOP_LEVEL_FOLDER_ID = '1OCjUQURkwzrLpavLUZhpWhyp3F6L4V33'
OUTPUT_JSON_PATH = 'events/events_data.json'

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

    print(f"Querying subfolders in {TOP_LEVEL_FOLDER_ID}...")

    events = []
    page_token = None

    try:
        while True:
            # Query for folders only that are directly inside the top level folder
            response = drive_service.files().list(
                q=f"'{TOP_LEVEL_FOLDER_ID}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false",
                spaces='drive',
                fields='nextPageToken, files(id, name, createdTime)',
                pageToken=page_token,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True
            ).execute()

            for file in response.get('files', []):
                events.append({
                    'id': file.get('id'),
                    'name': file.get('name'),
                    'createdTime': file.get('createdTime')
                })

            page_token = response.get('nextPageToken', None)
            if page_token is None:
                break

        print(f"Fetched {len(events)} event folders.")
    except Exception as e:
        print(f"Error fetching folders: {e}")
        return

    # Ensure output directory exists
    os.makedirs(os.path.dirname(OUTPUT_JSON_PATH), exist_ok=True)

    with open(OUTPUT_JSON_PATH, 'w', encoding='utf-8') as f:
        json.dump(events, f, indent=4)

    print(f"Successfully saved events to {OUTPUT_JSON_PATH}")

if __name__ == "__main__":
    main()