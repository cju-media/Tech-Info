import os
import json
import io
from googleapiclient.discovery import build
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials
from googleapiclient.http import MediaIoBaseUpload

DRIVE_FOLDER_ID = '1BICfy0OQa3fNvo69iEOpEx_66KCQGeUv'

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

def upload_to_drive(service, file_path, filename):
    print(f"Uploading {filename} to Google Drive...")

    query = f"'{DRIVE_FOLDER_ID}' in parents and name = '{filename}' and trashed = false"
    try:
        results = service.files().list(
            q=query,
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
            fields="files(id, name)"
        ).execute()
        files = results.get('files', [])

        media = MediaIoBaseUpload(io.BytesIO(open(file_path, "rb").read()), mimetype='text/plain', resumable=True)

        if files:
            file_id = files[0]['id']
            service.files().update(
                fileId=file_id,
                media_body=media,
                supportsAllDrives=True
            ).execute()
            print(f"Updated existing file {filename} in Drive.")
            return True
        else:
            file_metadata = {
                'name': filename,
                'parents': [DRIVE_FOLDER_ID]
            }
            service.files().create(
                body=file_metadata,
                media_body=media,
                supportsAllDrives=True
            ).execute()
            print(f"Created new file {filename} in Drive.")
            return True
    except Exception as e:
        error_msg = str(e)
        if "storageQuotaExceeded" in error_msg or "Service Accounts do not have storage quota" in error_msg:
            print(f"Error modifying {filename}: Quota exceeded.")
            print(f"  -> Raw Error: {error_msg}")
            print("  -> If you are using GDRIVE_OAUTH_JSON, the file in Drive may be owned by the Service Account which has no quota.")
            print("  -> FIX: Manually delete the problematic file from Google Drive so the script can recreate it under your account.")
        else:
            print(f"Error uploading {filename} to Google Drive: {e}")
        return False

def main():
    titles_dir = "service-titles"

    if not os.path.exists(titles_dir):
        print(f"Directory {titles_dir} does not exist. Nothing to upload.")
        return

    drive_service = get_drive_service()
    if not drive_service:
        print("Skipping Google Drive upload due to missing credentials.")
        return

    print("Syncing files to Google Drive...")
    files_uploaded = 0
    for filename in os.listdir(titles_dir):
        if filename.endswith(".txt"):
            file_path = os.path.join(titles_dir, filename)
            if upload_to_drive(drive_service, file_path, filename):
                files_uploaded += 1

    print("Sync complete.")

    # If any files were uploaded, write a flag file for the github action to see
    if files_uploaded > 0:
        with open(".sync_success", "w") as f:
            f.write("true")

if __name__ == "__main__":
    main()
