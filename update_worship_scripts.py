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


def parse_date_range(text):
    # Try to find (Start Date - End Date)
    match = re.search(r'\((.*?)-(.*?)\)', text)
    if match:
        try:
            start_dt = dateutil.parser.parse(match.group(1).strip())
            end_dt = dateutil.parser.parse(match.group(2).strip())

            # Fallback for missing year logic
            if start_dt.year == 1900 or start_dt.year < 2000:
                start_dt = start_dt.replace(year=datetime.now().year)
            if end_dt.year == 1900 or end_dt.year < 2000:
                end_dt = end_dt.replace(year=datetime.now().year)

            return start_dt, end_dt
        except:
            pass
    return None, None

def get_files_in_folder(service, folder_id):
    files = []
    page_token = None
    while True:
        try:
            results = service.files().list(
                q=f"'{folder_id}' in parents and trashed = false",
                fields="nextPageToken, files(id, name, mimeType)",
                pageToken=page_token
            ).execute()
            files.extend(results.get('files', []))
            page_token = results.get('nextPageToken')
            if not page_token:
                break
        except Exception as e:
            print(f"Error accessing folder {folder_id}: {e}")
            break
    return files

def main():
    if not os.environ.get('GDRIVE_API_KEY'):
        with open('worship_scripts.json', 'w') as out:
            json.dump({}, out)
        print("Created empty worship_scripts.json for testing")
        return

    service = get_drive_service()

    print("Fetching series folders from Google Drive root...")
    folders = get_files_in_folder(service, ROOT_FOLDER_ID)

    now = datetime.now()
    active_folders = []

    for folder in folders:
        if folder['mimeType'] == 'application/vnd.google-apps.folder':
            start_dt, end_dt = parse_date_range(folder['name'])
            if start_dt and end_dt:
                if start_dt <= now <= end_dt:
                    print(f"Found active folder: {folder['name']}")
                    active_folders.append(folder)

    worship_scripts = {}
    import requests
    output_dir = 'Service Scripts'
    os.makedirs(output_dir, exist_ok=True)

    for active_folder in active_folders:
        print(f"Searching docs in: {active_folder['name']}")
        docs = get_files_in_folder(service, active_folder['id'])

        for doc in docs:
            if doc['mimeType'] == 'application/vnd.google-apps.document':
                date_str = extract_date(doc['name'])
                if date_str:
                    doc_dt = dateutil.parser.parse(date_str)
                    if doc_dt >= now:
                        export_link = f"https://docs.google.com/document/d/{doc['id']}/export?format=pdf"
                        try:
                            res = requests.get(export_link)
                            if res.status_code == 200:
                                pdf_path = f"{output_dir}/{date_str}.pdf"
                                with open(pdf_path, 'wb') as pdf_file:
                                    pdf_file.write(res.content)
                                worship_scripts[date_str] = pdf_path
                                print(f"Downloaded upcoming script for {date_str}: {pdf_path}")
                            else:
                                print(f"Failed to download {doc['name']} (status {res.status_code})")
                        except Exception as e:
                            print(f"Error downloading {doc['name']}: {e}")

    with open('worship_scripts.json', 'w') as out:
        json.dump(worship_scripts, out, indent=2)
    print(f"Saved {len(worship_scripts)} scripts to worship_scripts.json")

if __name__ == '__main__':
    main()
