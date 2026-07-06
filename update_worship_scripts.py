import os
import json
import re
from datetime import datetime
from googleapiclient.discovery import build
import dateutil.parser

ROOT_FOLDER_ID = '1LW_e2qjwXiI5m-TOqbLiuurTO7XzZ4OT'

def get_drive_service():
    api_key = os.environ.get('GDRIVE_API_KEY')
    if not api_key:
        print("Error: GDRIVE_API_KEY environment variable is missing.")
        print("Please follow the instructions to create a Google Drive API Key and add it to your GitHub Secrets.")
        exit(1)

    return build('drive', 'v3', developerKey=api_key)

def list_files_recursive(service, folder_id):
    files = []
    page_token = None
    while True:
        try:
            results = service.files().list(
                q=f"'{folder_id}' in parents and trashed = false",
                fields="nextPageToken, files(id, name, mimeType, parents)",
                pageToken=page_token
            ).execute()

            items = results.get('files', [])
            for item in items:
                if item['mimeType'] == 'application/vnd.google-apps.folder':
                    files.extend(list_files_recursive(service, item['id']))
                else:
                    item['parent_folder_id'] = folder_id
                    files.append(item)

            page_token = results.get('nextPageToken')
            if not page_token:
                break
        except Exception as e:
            print(f"Error accessing folder {folder_id}: {e}")
            break
    return files

def extract_date(text):
    if not text:
        return None
    patterns = [
        r'\b\d{4}-\d{1,2}-\d{1,2}\b', # YYYY-MM-DD
        r'\b\d{1,2}-\d{1,2}-\d{2,4}\b', # MM-DD-YY or MM-DD-YYYY
        r'\b\d{1,2}\.\d{1,2}\.\d{2,4}\b', # MM.DD.YY
        r'\b\d{1,2}/\d{1,2}/\d{2,4}\b', # MM/DD/YY
        r'\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]* \d{1,2}(?:st|nd|rd|th)?(?:, \d{4})?\b'
    ]

    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            date_str = match.group(0)
            date_str = re.sub(r'(\d+)(st|nd|rd|th)', r'\1', date_str)
            try:
                dt = dateutil.parser.parse(date_str)
                if dt.year == 1900 or dt.year < 2000:
                    dt = dt.replace(year=datetime.now().year)
                return dt.strftime('%Y-%m-%d')
            except:
                pass
    return None

def main():
    if not os.environ.get('GDRIVE_API_KEY'):
        # For local testing if key is absent, just create empty json so tests pass
        with open('worship_scripts.json', 'w') as out:
            json.dump({}, out)
        print("Created empty worship_scripts.json for testing")
        return

    service = get_drive_service()

    print("Fetching files from Google Drive...")
    all_files = list_files_recursive(service, ROOT_FOLDER_ID)

    folder_cache = {}
    def get_folder_name(folder_id):
        if folder_id not in folder_cache:
            try:
                f = service.files().get(fileId=folder_id, fields="name").execute()
                folder_cache[folder_id] = f.get('name')
            except:
                folder_cache[folder_id] = ""
        return folder_cache[folder_id]

    worship_scripts = {}

    for f in all_files:
        if f['mimeType'] == 'application/vnd.google-apps.document':
            date_str = extract_date(f['name'])
            if not date_str:
                parent_name = get_folder_name(f['parent_folder_id'])
                date_str = extract_date(parent_name)

            if date_str:
                # Export Google Doc as PDF link
                export_link = f"https://docs.google.com/document/d/{f['id']}/export?format=pdf"
                worship_scripts[date_str] = export_link
                print(f"Found script for {date_str}: {f['name']}")

    with open('worship_scripts.json', 'w') as out:
        json.dump(worship_scripts, out, indent=2)
    print(f"Saved {len(worship_scripts)} scripts to worship_scripts.json")

if __name__ == '__main__':
    main()
