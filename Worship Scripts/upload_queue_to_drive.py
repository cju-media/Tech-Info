import os
import json
import io
import shutil
import re
from googleapiclient.discovery import build
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials
from googleapiclient.http import MediaIoBaseUpload

QUEUE_DIR = 'uploads_queue'
THUMBNAILS_DEST_PARENT_FOLDER_ID = '1KI_KifGRzRnafb5Z0IuXmdrgIEyB5_3f'
SERMON_DEST_PARENT_FOLDER_ID = '1Ji2Bbe7vWTcaRCpdQOjzwQgxsIoOWdy4'

def get_or_create_date_folder(service, parent_folder_id, date_str):
    query = f"name='{date_str}' and '{parent_folder_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
    results = service.files().list(q=query, spaces='drive', fields='files(id, name)').execute()
    items = results.get('files', [])
    if items:
        return items[0]['id']
    else:
        file_metadata = {
            'name': date_str,
            'parents': [parent_folder_id],
            'mimeType': 'application/vnd.google-apps.folder'
        }
        folder = service.files().create(body=file_metadata, fields='id', supportsAllDrives=True).execute()
        return folder.get('id')

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

def upload_to_drive(service, file_path, original_filename, folder_id):
    print(f"Uploading {original_filename} to Google Drive folder {folder_id}...")

    try:
        # Determine mimetype automatically if possible, otherwise default
        import mimetypes
        mime_type, _ = mimetypes.guess_type(file_path)
        if not mime_type:
            mime_type = 'application/octet-stream'

        media = MediaIoBaseUpload(io.BytesIO(open(file_path, "rb").read()), mimetype=mime_type, resumable=True)

        file_metadata = {
            'name': original_filename,
            'parents': [folder_id]
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

        # The frontend prepends a timestamp and folder ID like "1678901234_FOLDERID_filename.ext"
        parts = filename.split('_', 2)

        if len(parts) == 3 and parts[0].isdigit():
            folder_id = parts[1]
            original_filename = parts[2]

            if folder_id in [THUMBNAILS_DEST_PARENT_FOLDER_ID, SERMON_DEST_PARENT_FOLDER_ID] and original_filename.lower().endswith(('.jpg', '.jpeg')):
                date_pattern = re.compile(r'(\d{1,4}[-/]\d{1,2}[-/]\d{1,4})')
                match = date_pattern.search(original_filename)
                if match:
                    date_str = match.group(1)
                    print(f"Found date {date_str} in {original_filename}, redirecting to subfolder of {folder_id}")
                    try:
                        new_folder_id = get_or_create_date_folder(drive_service, folder_id, date_str)
                        if new_folder_id:
                            folder_id = new_folder_id
                    except Exception as e:
                        print(f"Error redirecting folder for {original_filename}: {e}")

            if upload_to_drive(drive_service, file_path, original_filename, folder_id):
                # If successful, remove it so the GitHub Action can commit the deletion
                os.remove(file_path)
                print(f"Removed {filename} from queue.")
            else:
                print(f"Failed to process {filename}, leaving in queue.")
        else:
            print(f"File {filename} does not match expected format TIMESTAMP_FOLDERID_FILENAME. Skipping.")

if __name__ == "__main__":
    main()
