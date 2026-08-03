import os
import json
from datetime import datetime, timedelta
import pytz
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaFileUpload
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials
import io

SOURCE_FOLDER_ID = '1wntwzav8sqcBOROpsr_Lm3MzAPyfGUUh'
DEST_FOLDER_ID = '1Ji2Bbe7vWTcaRCpdQOjzwQgxsIoOWdy4'
YOUTUBE_PLAYLIST_ID = 'PLGtiSp5WvUc9v95hvMCERRUvWBXJJsGrP'

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


def get_youtube_service():
    creds_json = os.environ.get('YOUTUBE_CREDENTIALS_JSON')
    if not creds_json:
        print("Error: YOUTUBE_CREDENTIALS_JSON environment variable not found.")
        return None

    try:
        creds_info = json.loads(creds_json)
        creds = Credentials.from_authorized_user_info(creds_info)
        service = build('youtube', 'v3', credentials=creds)
        return service
    except Exception as e:
        print(f"Error authenticating with YouTube: {e}")
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
    # We query for video/mp4 files, order by modifiedTime desc
    query = f"'{SOURCE_FOLDER_ID}' in parents and mimeType contains 'video/' and trashed = false"
    results = service.files().list(
        q=query,
        orderBy="modifiedTime desc",
        pageSize=10,
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
        fields="files(id, name, modifiedTime, size)"
    ).execute()

    files = results.get('files', [])
    if not files:
        print("No video files found in the source folder.")
        return

    recent_video = files[0]
    print(f"Most recent video: {recent_video['name']} (modified: {recent_video.get('modifiedTime')}, size: {recent_video.get('size')} bytes)")

    # 1. Check size: around 100MB
    size_bytes = int(recent_video.get('size', 0))
    # 50MB to 150MB
    if not (50 * 1024 * 1024 <= size_bytes <= 150 * 1024 * 1024):
        print(f"Video size {size_bytes} bytes is not around 100MB. Skipping.")
        return

    # 2 & 3 & 4. Validate Date and Time based on Filename
    # Filenames are expected to be like "2026-07-19 11-28-40.mp4"
    import re
    match = re.search(r"(\d{4}-\d{2}-\d{2}) (\d{2})-\d{2}-\d{2}", recent_video['name'])
    if not match:
        print(f"Video filename '{recent_video['name']}' does not match expected date format. Skipping.")
        return

    parsed_date_str = match.group(1)
    parsed_hour_str = match.group(2)

    parsed_date = datetime.strptime(parsed_date_str, '%Y-%m-%d').date()
    parsed_hour = int(parsed_hour_str)

    if parsed_date != sunday_date.date():
        print(f"Video was recorded on {parsed_date}, not target Sunday {sunday_date.date()}. Skipping.")
        return

    if parsed_hour != 11:
        print(f"Video was recorded in hour {parsed_hour}, not 11am. Skipping.")
        return

    if '11' not in recent_video['name']:
        # This is somewhat redundant with the hour check, but keeping to strictly follow original rule
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

    copied_video_id = None
    if existing_results.get('files'):
        print(f"Video '{recent_video['name']}' already exists in destination subfolder '{sunday_str_formatted}'. Skipping copy.")
        copied_video_id = existing_results['files'][0]['id']
    else:
        # Copy file to destination subfolder
        print(f"Copying video to destination subfolder...")
        copied_file = {
            'name': recent_video['name'],
            'parents': [dest_subfolder_id]
        }
        copied_response = service.files().copy(
            fileId=recent_video['id'],
            body=copied_file,
            supportsAllDrives=True
        ).execute()
        copied_video_id = copied_response.get('id')
        print(f"Successfully copied video '{recent_video['name']}' to '{sunday_str_formatted}'!")

    # Search for Title and Description in the destination subfolder for YouTube upload
    youtube_service = get_youtube_service()
    if youtube_service and copied_video_id:
        # We need to find the title and description files in the same dest_subfolder_id
        # Usually they are generated by create_sermon_series.py and uploaded here
        query_text_files = f"'{dest_subfolder_id}' in parents and mimeType = 'text/plain' and trashed = false"
        text_results = service.files().list(
            q=query_text_files,
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
            fields="files(id, name)"
        ).execute()

        video_title = None
        video_description = None

        for t_file in text_results.get('files', []):
            name_lower = t_file['name'].lower()
            if 'title' in name_lower:
                request = service.files().get_media(fileId=t_file['id'])
                fh = io.BytesIO()
                downloader = MediaIoBaseDownload(fh, request)
                done = False
                while done is False:
                    status, done = downloader.next_chunk()
                video_title = fh.getvalue().decode('utf-8').strip()
            elif 'description' in name_lower and 'sermon-series-description' not in name_lower: # prefer specific desc file if there are multiple? Actually, sermon series script outputs "SS Description-..."
                request = service.files().get_media(fileId=t_file['id'])
                fh = io.BytesIO()
                downloader = MediaIoBaseDownload(fh, request)
                done = False
                while done is False:
                    status, done = downloader.next_chunk()
                video_description = fh.getvalue().decode('utf-8').strip()

        if video_title and video_description:
            print(f"Found YouTube Title: {video_title}")

            # Check if video already exists in playlist (with pagination)
            already_uploaded = False
            next_page_token = None

            while True:
                playlist_response = youtube_service.playlistItems().list(
                    part='snippet',
                    playlistId=YOUTUBE_PLAYLIST_ID,
                    maxResults=50,
                    pageToken=next_page_token
                ).execute()

                for item in playlist_response.get('items', []):
                    if item['snippet']['title'] == video_title:
                        print(f"Video with title '{video_title}' already exists in playlist. Skipping upload.")
                        already_uploaded = True
                        break

                if already_uploaded:
                    break

                next_page_token = playlist_response.get('nextPageToken')
                if not next_page_token:
                    break

            if not already_uploaded:
                print("Downloading video locally for YouTube upload...")
                local_video_path = f"/tmp/{recent_video['name']}"
                request = service.files().get_media(fileId=copied_video_id)
                fh = io.FileIO(local_video_path, 'wb')
                downloader = MediaIoBaseDownload(fh, request)
                done = False
                while done is False:
                    status, done = downloader.next_chunk()
                    if status:
                        print(f"Download {int(status.progress() * 100)}%.")

                print("Video downloaded successfully. Starting YouTube upload...")
                privacy_status = os.environ.get('YOUTUBE_PRIVACY_STATUS', 'private')

                try:
                    body = {
                        'snippet': {
                            'title': video_title,
                            'description': video_description,
                            'categoryId': '22'
                        },
                        'status': {
                            'privacyStatus': privacy_status,
                            'selfDeclaredMadeForKids': False
                        }
                    }

                    media = MediaFileUpload(local_video_path, chunksize=-1, resumable=True, mimetype='video/mp4')
                    insert_request = youtube_service.videos().insert(
                        part=','.join(body.keys()),
                        body=body,
                        media_body=media
                    )

                    response = None
                    while response is None:
                        status, response = insert_request.next_chunk()
                        if status:
                            print(f"Upload {int(status.progress() * 100)}%.")

                    uploaded_video_id = response.get('id')
                    print(f"Video uploaded successfully! Video ID: {uploaded_video_id}")

                    # Add to playlist
                    print(f"Adding video {uploaded_video_id} to playlist {YOUTUBE_PLAYLIST_ID}...")
                    youtube_service.playlistItems().insert(
                        part='snippet',
                        body={
                            'snippet': {
                                'playlistId': YOUTUBE_PLAYLIST_ID,
                                'resourceId': {
                                    'kind': 'youtube#video',
                                    'videoId': uploaded_video_id
                                }
                            }
                        }
                    ).execute()
                    print("Video added to playlist successfully.")

                except Exception as e:
                    print(f"Error during YouTube upload/playlist insertion: {e}")

                # Cleanup local video file
                if os.path.exists(local_video_path):
                    os.remove(local_video_path)
                    print(f"Deleted local file {local_video_path}.")
        else:
            print("Could not find both Title and Description text files in the destination subfolder. Skipping YouTube upload.")

    # Process Sermon-Series-Description.txt
    sermon_desc_path = "Worship Scripts/Sermon-Series/Sermon-Series-Description.txt"
    if os.path.exists(sermon_desc_path):
        print(f"Found {sermon_desc_path}. Processing...")
        try:

            # Get the most recent video from the YouTube playlist
            youtube_service = get_youtube_service()
            youtube_link = ""
            if youtube_service:
                try:
                    playlist_response = youtube_service.playlistItems().list(
                        part='snippet',
                        playlistId='PLGtiSp5WvUc_I0M_vvfSdGY9dJ43ZofXs',
                        maxResults=1
                    ).execute()

                    items = playlist_response.get('items', [])
                    if items:
                        video_id = items[0]['snippet']['resourceId']['videoId']
                        youtube_link = f"https://youtube.com/watch?v={video_id}"
                        print(f"Found recent YouTube video: {youtube_link}")
                    else:
                        print("No videos found in YouTube playlist.")
                except Exception as e:
                    print(f"Error querying YouTube playlist: {e}")
            else:
                print("Skipping YouTube link replacement because YouTube service could not be initialized.")

            with open(sermon_desc_path, 'r', encoding='utf-8') as f:
                content = f.read()

            # Format M-DD-YY (no leading zero for month, 2-digit year)
            m_dd_yy = f"{sunday_date.month}-{sunday_date.strftime('%d-%y')}"
            ow_link = f"https://www.fccla.org/ows/{m_dd_yy}"

            # Replace placeholders
            modified_content = content.replace("OW LINK", ow_link)
            if youtube_link:
                import re
                modified_content = re.sub(r'(?i)youtube service link', youtube_link, modified_content)


            # Upload modified text to the Drive subfolder
            from googleapiclient.http import MediaIoBaseUpload

            new_filename = f"Sermon-Series-Description-{sunday_str_formatted}.txt"

            # Check if file already exists
            query_desc = f"'{dest_subfolder_id}' in parents and name = '{new_filename}' and trashed = false"
            desc_results = service.files().list(
                q=query_desc,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
                fields="files(id)"
            ).execute()

            existing_files = desc_results.get('files', [])
            media = MediaIoBaseUpload(io.BytesIO(modified_content.encode('utf-8')), mimetype='text/plain', resumable=True)

            if existing_files:
                file_id = existing_files[0]['id']
                print(f"Updating existing {new_filename} (ID: {file_id}) in Drive subfolder...")
                service.files().update(
                    fileId=file_id,
                    media_body=media,
                    supportsAllDrives=True
                ).execute()
                print(f"Successfully updated {new_filename}!")
            else:
                print(f"Uploading new {new_filename} to Drive subfolder...")
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
