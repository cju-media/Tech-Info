import os
import json
import io
import shutil
from googleapiclient.discovery import build
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials
from googleapiclient.http import MediaIoBaseUpload

DRIVE_FOLDER_ID = '1MVeC2j0v4zTA1sVjhLz06bqEz3qbaYxs'
QUEUE_DIR = 'uploads_queue'

def get_drive_service():
    oauth_json = os.environ.get('GDRIVE_OAUTH_JSON')
    if oauth_json:
        try:
            creds_dict = json.loads(oauth_json)
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

def upload_to_drive(service, file_path, original_filename):
    print(f"Uploading {original_filename} to Google Drive...")

    try:
        # Determine mimetype automatically if possible, otherwise default
        import mimetypes
        mime_type, _ = mimetypes.guess_type(file_path)
        if not mime_type:
            mime_type = 'application/octet-stream'

        media = MediaIoBaseUpload(io.BytesIO(open(file_path, "rb").read()), mimetype=mime_type, resumable=True)

        file_metadata = {
            'name': original_filename,
            'parents': [DRIVE_FOLDER_ID]
        }

        service.files().create(
            body=file_metadata,
            media_body=media,
            supportsAllDrives=True
        ).execute()

        print(f"Successfully uploaded {original_filename} to Drive.")
        return True
    except Exception as e:
        print(f"Error uploading {original_filename} to Google Drive: {e}")
        return False

def main():
    if not os.path.exists(QUEUE_DIR):
        print(f"Queue directory '{QUEUE_DIR}' does not exist. Nothing to do.")
        return

    files_in_queue = [f for f in os.listdir(QUEUE_DIR) if os.path.isfile(os.path.join(QUEUE_DIR, f)) and not f.startswith('.')]

    if not files_in_queue:
        print("No files in the queue. Exiting.")
        return

    drive_service = get_drive_service()
    if not drive_service:
        print("Skipping Google Drive upload due to missing credentials.")
        return

    print(f"Found {len(files_in_queue)} file(s) to process.")

    for filename in files_in_queue:
        file_path = os.path.join(QUEUE_DIR, filename)

        # The frontend prepends a timestamp like "1678901234_filename.ext"
        # We want to strip the timestamp for the actual drive upload name
        parts = filename.split('_', 1)
        original_filename = parts[1] if len(parts) > 1 and parts[0].isdigit() else filename

        if upload_to_drive(drive_service, file_path, original_filename):
            # If successful, remove it so the GitHub Action can commit the deletion
            os.remove(file_path)
            print(f"Removed {filename} from queue.")
        else:
            print(f"Failed to process {filename}, leaving in queue.")

if __name__ == "__main__":
    main()
