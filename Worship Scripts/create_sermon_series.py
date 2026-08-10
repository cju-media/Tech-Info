import os
import json
import io
import re
import datetime
import zoneinfo
from googleapiclient.discovery import build
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials
from googleapiclient.http import MediaIoBaseUpload
import dateutil.parser

PARENT_FOLDER_ID = '1Ji2Bbe7vWTcaRCpdQOjzwQgxsIoOWdy4'
YOUTUBE_PLAYLIST_ID = 'PLGtiSp5WvUc_I0M_vvfSdGY9dJ43ZofXs'

def get_youtube_service():
    creds_json = os.environ.get('YOUTUBE_CREDENTIALS_JSON')
    if not creds_json:
        print("Warning: YOUTUBE_CREDENTIALS_JSON environment variable not found.")
        return None

    try:
        creds_info = json.loads(creds_json)
        creds = Credentials.from_authorized_user_info(creds_info)
        service = build('youtube', 'v3', credentials=creds)
        print("Using YOUTUBE_CREDENTIALS_JSON for YouTube authentication.")
        return service
    except Exception as e:
        print(f"Error authenticating with YouTube: {e}")
        return None

def get_upcoming_stream_url(service, target_date):
    if not service:
        return None

    try:
        playlist_response = service.playlistItems().list(
            part='snippet',
            playlistId=YOUTUBE_PLAYLIST_ID,
            maxResults=50
        ).execute()

        video_ids = [item['snippet']['resourceId']['videoId'] for item in playlist_response.get('items', [])]
        if not video_ids:
            return None

        video_response = service.videos().list(
            part='snippet,liveStreamingDetails',
            id=','.join(video_ids)
        ).execute()

        la_tz = zoneinfo.ZoneInfo('America/Los_Angeles')

        for video in video_response.get('items', []):
            if 'liveStreamingDetails' in video:
                scheduled_start_time = video['liveStreamingDetails'].get('scheduledStartTime')
                if scheduled_start_time:
                    start_time_utc = dateutil.parser.parse(scheduled_start_time)
                    start_time_la = start_time_utc.astimezone(la_tz)
                    if start_time_la.date() == target_date:
                        return f"https://youtube.com/watch?v={video['id']}"

    except Exception as e:
        print(f"Error fetching upcoming streams from YouTube: {e}")

    return None

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

    formatted_text = f"{title} - {minister} || FCCLA Sermon"

    # 3. Calculate date and output filename
    sunday = get_upcoming_sunday()
    date_str = sunday.strftime("%m-%d-%Y")

    output_dir = "Sermon-Series"
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    output_filename = f"Sermon Series Title {date_str}.txt"
    output_path = os.path.join(output_dir, output_filename)

    # 4. Process description
    desc_template_path = os.path.join("Sermon-Series", "Sermon-Series-Description.txt")
    desc_output_filename = f"SS Description-{date_str}.txt"
    desc_output_path = os.path.join(output_dir, desc_output_filename)

    desc_text = None
    try:
        with open(desc_template_path, "r") as f:
            desc_template = f.read()

        yt_service = get_youtube_service()
        youtube_link = get_upcoming_stream_url(yt_service, sunday.date())
        if not youtube_link:
            # This script can regenerate the same week's description more than
            # once (e.g. a later sermon-title correction re-triggers it)
            # before create_youtube_stream.py has scheduled the livestream
            # yet. Don't stomp an already-correct link from a prior run with
            # the placeholder just because the stream isn't found *this* time
            # - backfill_sermon_series_link.py is what actually fills this in
            # once the stream exists if we don't find it here.
            existing_link = None
            if os.path.exists(desc_output_path):
                try:
                    with open(desc_output_path, "r") as f:
                        existing_desc = f.read()
                    link_match = re.search(r"^Watch the Service: (.+)$", existing_desc, re.MULTILINE)
                    if link_match and link_match.group(1).strip() != "YOUTUBE SERVICE LINK":
                        existing_link = link_match.group(1).strip()
                except Exception as e:
                    print(f"Warning: could not check existing description for a link: {e}")

            if existing_link:
                youtube_link = existing_link
                print(f"Reusing previously-found stream link from existing description: {youtube_link}")
            else:
                youtube_link = "YOUTUBE SERVICE LINK"  # Fallback if not found
                print(f"Warning: Could not find upcoming stream for {date_str} in playlist.")

        # Calculate OW Link
        # e.g. 8-2-26 -> month-day-year (no leading zeros, 2 digit year)
        m = sunday.month
        d = sunday.day
        y = sunday.strftime("%y")
        ow_link = f"https://www.fccla.org/ows/{m}-{d}-{y}"

        desc_text = desc_template.replace("YOUTUBE SERVICE LINK", youtube_link).replace("OW LINK", ow_link)

    except FileNotFoundError:
        print(f"Warning: Could not find description template at {desc_template_path}")

    # 5. Write to local files
    with open(output_path, "w") as f:
        f.write(formatted_text)
    print(f"Successfully wrote local file: {output_path}")

    if desc_text is not None:
        with open(desc_output_path, "w") as f:
            f.write(desc_text)
        print(f"Successfully wrote local description file: {desc_output_path}")

    # 6. Upload to Drive
    drive_service = get_drive_service()
    if not drive_service:
        print("Skipping Google Drive upload due to missing credentials.")
        return

    target_folder_id = get_or_create_folder(drive_service, date_str, PARENT_FOLDER_ID)
    upload_to_drive(drive_service, output_path, output_filename, target_folder_id)

    if desc_text is not None:
         upload_to_drive(drive_service, desc_output_path, desc_output_filename, target_folder_id)

if __name__ == "__main__":
    main()
