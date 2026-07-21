import os
import json
import io
import datetime
import zoneinfo
from googleapiclient.discovery import build
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials
from googleapiclient.http import MediaIoBaseUpload

PARENT_FOLDER_ID = '1Ji2Bbe7vWTcaRCpdQOjzwQgxsIoOWdy4'

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

def get_upcoming_sunday():
    tz = zoneinfo.ZoneInfo("America/Los_Angeles")
    now_pt = datetime.datetime.now(tz)
    days_ahead = 6 - now_pt.weekday()
    if days_ahead <= 0:
        days_ahead += 7
    return now_pt + datetime.timedelta(days=days_ahead)

def get_or_create_folder(service, folder_name, parent_id):
    query = f"'{parent_id}' in parents and name = '{folder_name}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    results = service.files().list(
        q=query,
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
        fields="files(id, name)"
    ).execute()
    files = results.get('files', [])

    if files:
        print(f"Found existing folder '{folder_name}' with ID: {files[0]['id']}")
        return files[0]['id']
    else:
        print(f"Creating folder '{folder_name}'...")
        file_metadata = {
            'name': folder_name,
            'mimeType': 'application/vnd.google-apps.folder',
            'parents': [parent_id]
        }
        folder = service.files().create(
            body=file_metadata,
            supportsAllDrives=True,
            fields='id'
        ).execute()
        print(f"Created folder '{folder_name}' with ID: {folder.get('id')}")
        return folder.get('id')

def upload_to_drive(service, file_path, filename, parent_folder_id):
    print(f"Uploading {filename} to Google Drive...")
    query = f"'{parent_folder_id}' in parents and name = '{filename}' and trashed = false"

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
    else:
        file_metadata = {
            'name': filename,
            'parents': [parent_folder_id]
        }
        service.files().create(
            body=file_metadata,
            media_body=media,
            supportsAllDrives=True
        ).execute()
        print(f"Created new file {filename} in Drive.")

def main():
    # 1. Read input files
    title_path = "service-titles/sermon-title.txt"
    minister_path = "service-titles/sermon-minister.txt"

    try:
        with open(title_path, "r") as f:
            title = f.read().strip()
    except FileNotFoundError:
        print(f"Could not find {title_path}")
        return

    try:
        with open(minister_path, "r") as f:
            minister = f.read().strip()
    except FileNotFoundError:
        print(f"Could not find {minister_path}")
        return

    # 2. Process title
    if title.lower().startswith("sermon - "):
        title = title[9:].strip()

    formatted_text = f"{title} || {minister}"

    # 3. Calculate date and output filename
    sunday = get_upcoming_sunday()
    date_str = sunday.strftime("%m-%d-%Y")

    output_dir = "Sermon-Series"
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    output_filename = f"Sermon Series Title {date_str}.txt"
    output_path = os.path.join(output_dir, output_filename)

    # 4. Write to local file
    with open(output_path, "w") as f:
        f.write(formatted_text)
    print(f"Successfully wrote local file: {output_path}")

    # 5. Upload to Drive
    drive_service = get_drive_service()
    if not drive_service:
        print("Skipping Google Drive upload due to missing credentials.")
        return

    target_folder_id = get_or_create_folder(drive_service, date_str, PARENT_FOLDER_ID)
    upload_to_drive(drive_service, output_path, output_filename, target_folder_id)

if __name__ == "__main__":
    main()
