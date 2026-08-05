import os
import json
import datetime
import zoneinfo
import io
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from googleapiclient.http import MediaIoBaseDownload
from google import genai
from google.genai import types

DRIVE_FOLDER_ID = '17-0kiqBKa0k5ofW6gOPrVbHl7nqanuQz'
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
IGNORE_FILE = os.path.join(BASE_DIR, 'ignore-files.json')
DATES_FILE = os.path.join(BASE_DIR, 'event-dates.json')
PROMPT_FILE = os.path.join(BASE_DIR, 'events-processing-prompt.txt')

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
    return None

def main():
    # Load settings
    with open(IGNORE_FILE, 'r') as f:
        ignore_files = json.load(f)
    with open(DATES_FILE, 'r') as f:
        event_dates = json.load(f)
    with open(PROMPT_FILE, 'r') as f:
        prompt_text = f.read().strip()

    service = get_drive_service()
    if not service:
        print("Failed to get Google Drive service.")
        return

    # List files in the folder
    query = f"'{DRIVE_FOLDER_ID}' in parents and trashed = false"
    results = service.files().list(q=query, fields="files(id, name, mimeType)").execute()
    drive_files = results.get('files', [])

    # Setup Gemini
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("GEMINI_API_KEY is missing.")
        return
    client = genai.Client(api_key=api_key)

    tz = zoneinfo.ZoneInfo("America/Los_Angeles")
    today = datetime.datetime.now(tz).date()

    current_drive_file_names = []

    for file_info in drive_files:
        file_id = file_info['id']
        file_name = file_info['name']
        current_drive_file_names.append(file_name)

        if file_name in ignore_files:
            continue

        if file_name not in event_dates:
            print(f"Processing new file: {file_name}")
            # Download file
            request = service.files().get_media(fileId=file_id)
            fh = io.BytesIO()
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while done is False:
                status, done = downloader.next_chunk()
            fh.seek(0)
            file_bytes = fh.read()
            mime_type = file_info.get('mimeType', 'image/jpeg')

            # Send to Gemini
            try:
                gemini_response = client.models.generate_content(
                    model='gemini-2.5-flash',
                    contents=[
                        prompt_text,
                        types.Part.from_bytes(data=file_bytes, mime_type=mime_type)
                    ]
                )
                if gemini_response and gemini_response.text:
                    date_str = gemini_response.text.strip()
                    print(f"Gemini extracted date: {date_str}")
                    # Validate date format (MM/DD/YY)
                    try:
                        datetime.datetime.strptime(date_str, "%m/%d/%y")
                        event_dates[file_name] = date_str
                    except ValueError:
                        print(f"Invalid date format returned by Gemini: {date_str}")
            except Exception as e:
                print(f"Error querying Gemini for {file_name}: {e}")

    # Check for past events and delete
    files_to_delete_keys = []
    for file_name, date_str in event_dates.items():
        try:
            event_date = datetime.datetime.strptime(date_str, "%m/%d/%y").date()
            if event_date < today:
                print(f"Event for {file_name} is in the past ({date_str}). Deleting...")
                files_to_delete_keys.append(file_name)
                # Find file id to delete
                for f in drive_files:
                    if f['name'] == file_name:
                        try:
                            service.files().delete(fileId=f['id']).execute()
                            print(f"Deleted {file_name} from Drive.")
                        except Exception as e:
                            print(f"Error deleting {file_name} from Drive: {e}")
        except ValueError:
            print(f"Invalid date in event-dates.json for {file_name}: {date_str}")
            files_to_delete_keys.append(file_name) # Remove invalid entries

    # Remove deleted files from dictionary
    for k in files_to_delete_keys:
        if k in event_dates:
            del event_dates[k]

    # Clean up dictionary for files manually deleted from Drive
    keys_to_remove = []
    for file_name in event_dates:
        if file_name not in current_drive_file_names:
            print(f"File {file_name} not found in Drive. Removing from JSON.")
            keys_to_remove.append(file_name)
    for k in keys_to_remove:
        del event_dates[k]

    # Save dates
    with open(DATES_FILE, 'w') as f:
        json.dump(event_dates, f, indent=2)
    print("Updated event-dates.json.")

if __name__ == "__main__":
    main()
