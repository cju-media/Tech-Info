import os
import json
from datetime import datetime, timedelta
import pytz
from googleapiclient.discovery import build
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials

SOURCE_FOLDER_ID = '1wntwzav8sqcBOROpsr_Lm3MzAPyfGUUh'
DEST_FOLDER_ID = '1Ji2Bbe7vWTcaRCpdQOjzwQgxsIoOWdy4'

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

def get_or_create_folder(service, parent_id, folder_name):
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
        file_metadata = {
            'name': folder_name,
            'parents': [parent_id],
            'mimeType': 'application/vnd.google-apps.folder'
        }
        folder = service.files().create(
            body=file_metadata,
            supportsAllDrives=True,
            fields='id'
        ).execute()
        print(f"Created new folder '{folder_name}' with ID: {folder.get('id')}")
        return folder.get('id')

def main():
    service = get_drive_service()
    if not service:
        print("Could not get Google Drive service. Exiting.")
        return

    # Calculate yesterday (Sunday)
    tz = pytz.timezone('America/Los_Angeles')
    now = datetime.now(tz)

    # Check if today is Monday
    # if now.weekday() != 0:
    #    print("Today is not Monday. The script should only run on Mondays.")
    #    # We allow it to run anyway in case of manual execution

    yesterday = now - timedelta(days=1)
    # Ensure yesterday is Sunday? In standard logic, if we run on Monday, yesterday is Sunday.
    # What if it's run manually on Tuesday? Let's just find the most recent Sunday.
    days_since_sunday = (now.weekday() + 1) % 7
    if days_since_sunday == 0:
        days_since_sunday = 7 # if today is Sunday, look for last Sunday? No, if today is Sunday, Sunday is today (0). But script says "yesterday".
        # We will strictly use yesterday for this script if it runs on Mondays.

    # Just explicitly go back to the most recent Sunday
    sunday_date = now - timedelta(days=now.weekday() + 1)

    sunday_str_formatted = sunday_date.strftime('%m-%d-%Y')
    print(f"Target Sunday date: {sunday_str_formatted}")

    # Query most recent video file from source folder
    # We query for video/mp4 files, order by createdTime desc
    query = f"'{SOURCE_FOLDER_ID}' in parents and mimeType contains 'video/' and trashed = false"
    results = service.files().list(
        q=query,
        orderBy="createdTime desc",
        pageSize=10,
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
        fields="files(id, name, createdTime, size)"
    ).execute()

    files = results.get('files', [])
    if not files:
        print("No video files found in the source folder.")
        return

    recent_video = files[0]
    print(f"Most recent video: {recent_video['name']} (created: {recent_video.get('createdTime')}, size: {recent_video.get('size')} bytes)")

    # 1. Check size: around 100MB
    size_bytes = int(recent_video.get('size', 0))
    # 50MB to 150MB
    if not (50 * 1024 * 1024 <= size_bytes <= 150 * 1024 * 1024):
        print(f"Video size {size_bytes} bytes is not around 100MB. Skipping.")
        return

    # 2. Created on Sunday (in US/Pacific)
    created_time_str = recent_video.get('createdTime')
    if not created_time_str:
        print("Could not retrieve created time. Skipping.")
        return

    # Parse RFC 3339 format
    # createdTime is like '2024-07-21T18:30:00.000Z'
    created_time = datetime.strptime(created_time_str, '%Y-%m-%dT%H:%M:%S.%fZ')
    created_time_utc = pytz.utc.localize(created_time)
    created_time_pt = created_time_utc.astimezone(tz)

    if created_time_pt.date() != sunday_date.date():
        print(f"Video was created on {created_time_pt.date()}, not target Sunday {sunday_date.date()}. Skipping.")
        return

    # 3. Created in the 11am hour
    if created_time_pt.hour != 11:
        print(f"Video was created in hour {created_time_pt.hour}, not 11am. Skipping.")
        return

    # 4. "11" in the title
    if '11' not in recent_video['name']:
        print(f"Video title does not contain '11'. Skipping.")
        return

    print("Video matches all parameters!")

    # Get or create subfolder with Sunday's date in destination folder
    dest_subfolder_id = get_or_create_folder(service, DEST_FOLDER_ID, sunday_str_formatted)

    # Check if it already exists in the subfolder to avoid duplicate copies
    query_existing = f"'{dest_subfolder_id}' in parents and name = '{recent_video['name']}' and trashed = false"
    existing_results = service.files().list(
        q=query_existing,
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
        fields="files(id)"
    ).execute()
    if existing_results.get('files'):
        print(f"Video '{recent_video['name']}' already exists in destination subfolder '{sunday_str_formatted}'. Skipping copy.")
        return

    # Copy file to destination subfolder
    print(f"Copying video to destination subfolder...")
    copied_file = {
        'name': recent_video['name'],
        'parents': [dest_subfolder_id]
    }
    service.files().copy(
        fileId=recent_video['id'],
        body=copied_file,
        supportsAllDrives=True
    ).execute()
    print(f"Successfully copied video '{recent_video['name']}' to '{sunday_str_formatted}'!")

    # Process Sermon-Series-Description.txt
    sermon_desc_path = "Worship Scripts/Sermon-Series/Sermon-Series-Description.txt"
    if os.path.exists(sermon_desc_path):
        print(f"Found {sermon_desc_path}. Processing...")
        try:
            with open(sermon_desc_path, 'r', encoding='utf-8') as f:
                content = f.read()

            # Format M-DD-YY (no leading zero for month, 2-digit year)
            m_dd_yy = f"{sunday_date.month}-{sunday_date.strftime('%d-%y')}"
            ow_link = f"https://www.fccla.org/ows/{m_dd_yy}"

            # Replace placeholder
            modified_content = content.replace("OW LINK", ow_link)

            # Upload modified text to the Drive subfolder
            from googleapiclient.http import MediaIoBaseUpload
            import io

            new_filename = f"Sermon-Series-Description-{sunday_str_formatted}.txt"
            print(f"Uploading {new_filename} to Drive subfolder...")

            media = MediaIoBaseUpload(io.BytesIO(modified_content.encode('utf-8')), mimetype='text/plain', resumable=True)
            file_metadata = {
                'name': new_filename,
                'parents': [dest_subfolder_id]
            }

            service.files().create(
                body=file_metadata,
                media_body=media,
                supportsAllDrives=True
            ).execute()
            print(f"Successfully uploaded {new_filename}!")

        except Exception as e:
            print(f"Error processing {sermon_desc_path}: {e}")
    else:
        print(f"{sermon_desc_path} not found locally. Skipping description upload.")


if __name__ == "__main__":
    main()
