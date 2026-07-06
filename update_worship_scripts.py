import os
import json
import re
from datetime import datetime
from googleapiclient.discovery import build
import dateutil.parser

ROOT_FOLDER_ID = '1LW_e2qjwXiI5m-TOqbLiuurTO7XzZ4OT'


from google.oauth2 import service_account

def get_drive_service():
    service_account_json = os.environ.get('GDRIVE_SERVICE_ACCOUNT_JSON')
    api_key = os.environ.get('GDRIVE_API_KEY')

    if service_account_json:
        try:
            creds_dict = json.loads(service_account_json)
            creds = service_account.Credentials.from_service_account_info(
                creds_dict, scopes=['https://www.googleapis.com/auth/drive.readonly']
            )
            return build('drive', 'v3', credentials=creds)
        except Exception as e:
            print(f"Error parsing GDRIVE_SERVICE_ACCOUNT_JSON: {e}")
            exit(1)

    if api_key:
        return build('drive', 'v3', developerKey=api_key)

    print("Error: Neither GDRIVE_SERVICE_ACCOUNT_JSON nor GDRIVE_API_KEY environment variable is set.")
    print("Please follow the instructions to create a Google Cloud Service Account and add its JSON key to your GitHub Secrets.")
    exit(1)
def list_files_recursive(service, folder_id):
    files = []
    page_token = None
    while True:
        try:
            results = service.files().list(
                q=f"'{folder_id}' in parents and trashed = false",
                fields="nextPageToken, files(id, name, mimeType, parents)",
                pageToken=page_token,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True
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

    # Try just looking for two dates in the string if the format is slightly different
    dates = []
    # Match basic date patterns
    for date_match in re.finditer(r'\d{1,2}/\d{1,2}/\d{2,4}', text):
        try:
            dt = dateutil.parser.parse(date_match.group(0))
            if dt.year == 1900 or dt.year < 2000:
                dt = dt.replace(year=datetime.now().year)
            dates.append(dt)
        except:
            pass

    if len(dates) >= 2:
        return min(dates), max(dates)

    return None, None
def get_files_in_folder(service, folder_id):
    files = []
    page_token = None
    while True:
        try:
            results = service.files().list(
                q=f"'{folder_id}' in parents and trashed = false",
                fields="nextPageToken, files(id, name, mimeType, shortcutDetails, modifiedTime)",
                pageToken=page_token,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True
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
    if not os.environ.get('GDRIVE_API_KEY') and not os.environ.get('GDRIVE_SERVICE_ACCOUNT_JSON'):
        with open('worship_scripts.json', 'w') as out:
            json.dump({}, out)
        print("Created empty worship_scripts.json for testing")
        return

    service = get_drive_service()

    print("Fetching series folders from Google Drive root...")

    # We should search for any folder within the shared drive
    # But since it's a specific folder we want to search in, let's stick to ROOT_FOLDER_ID.
    # Wait, the user said: "Make sure it is looking in this folder: https://drive.google.com/drive/u/0/folders/1LW_e2qjwXiI5m-TOqbLiuurTO7XzZ4OT"
    # That is exactly what ROOT_FOLDER_ID is set to.

    # Maybe the folder has folders inside it that we aren't finding because they are shortcuts?
    # Let's ensure we fetch shortcuts too. Or maybe we need `supportsAllDrives=True`.

    folders = []
    page_token = None
    while True:
        try:
            results = service.files().list(
                q=f"'{ROOT_FOLDER_ID}' in parents and trashed = false",
                fields="nextPageToken, files(id, name, mimeType, shortcutDetails, modifiedTime)",
                pageToken=page_token,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True
            ).execute()
            folders.extend(results.get('files', []))
            page_token = results.get('nextPageToken')
            if not page_token:
                break
        except Exception as e:
            print(f"Error accessing folder {ROOT_FOLDER_ID}: {e}")
            break


    print(f"Discovered {len(folders)} files/folders.")
    print("--- RAW DISCOVERED FILES ---")
    for folder in folders:
        print(f"Name: {folder.get('name')}, MimeType: {folder.get('mimeType')}")
    print("----------------------------")
    now = datetime.now()
    active_folders = []

    for folder in folders:
        if 'folder' in folder['mimeType'] or 'shortcut' in folder['mimeType']:
            start_dt, end_dt = parse_date_range(folder['name'])
            if start_dt and end_dt:
                if now.date() <= end_dt.date(): # Only filter out old series folders, allow future ones
                    print(f"Found active folder: {folder['name']}")
                    active_folders.append(folder)


    worship_scripts = {}
    if os.path.exists('worship_scripts.json'):
        try:
            with open('worship_scripts.json', 'r') as f:
                worship_scripts = json.load(f)
                # Handle old format which was just string paths
                for k, v in worship_scripts.items():
                    if isinstance(v, str):
                        worship_scripts[k] = {'path': v, 'modifiedTime': None}
        except:
            pass
    worship_scripts_new = {}


    output_dir = 'service-scripts'
    os.makedirs(output_dir, exist_ok=True)

    for active_folder in active_folders:
        print(f"Searching docs in: {active_folder['name']}")

        target_id = active_folder['id']
        if active_folder['mimeType'] == 'application/vnd.google-apps.shortcut':
            target_id = active_folder.get('shortcutDetails', {}).get('targetId', target_id)

        docs = []
        page_token = None
        while True:
            try:
                results = service.files().list(
                    q=f"'{target_id}' in parents and trashed = false",
                    fields="nextPageToken, files(id, name, mimeType, shortcutDetails, modifiedTime)",
                    pageToken=page_token,
                    supportsAllDrives=True,
                    includeItemsFromAllDrives=True
                ).execute()
                docs.extend(results.get('files', []))
                page_token = results.get('nextPageToken')
                if not page_token:
                    break
            except Exception as e:
                print(f"Error accessing folder {target_id}: {e}")
                break

        for doc in docs:
            if doc['mimeType'] == 'application/vnd.google-apps.document':
                date_str = extract_date(doc['name'])
                if date_str:
                    doc_dt = dateutil.parser.parse(date_str)
                    if doc_dt.date() >= now.date():

                        modified_time = doc.get('modifiedTime')

                        # Check against previous state
                        should_download = True
                        if date_str in worship_scripts:
                            old_modified_time = worship_scripts[date_str].get('modifiedTime')
                            if old_modified_time and old_modified_time == modified_time:
                                # File hasn't changed, skip download but keep in new state
                                worship_scripts_new[date_str] = worship_scripts[date_str]
                                print(f"Skipping {date_str} (No changes since last run)")
                                should_download = False

                        if should_download:
                            try:
                                pdf_path = f"{output_dir}/{date_str}.pdf"
                                request = service.files().export_media(fileId=doc['id'], mimeType='application/pdf')
                                pdf_content = request.execute()
                                with open(pdf_path, 'wb') as pdf_file:
                                    pdf_file.write(pdf_content)

                                # Save URL encoded path for the web and modifiedTime
                                worship_scripts_new[date_str] = {
                                    'path': pdf_path,
                                    'modifiedTime': modified_time
                                }
                                print(f"Downloaded upcoming script for {date_str}: {pdf_path}")
                            except Exception as e:
                                print(f"Error downloading {doc['name']}: {e}")
                                # Keep old data if it fails
                                if date_str in worship_scripts:
                                    worship_scripts_new[date_str] = worship_scripts[date_str]


    with open('worship_scripts.json', 'w') as out:
        json.dump(worship_scripts_new, out, indent=2)
    print(f"Saved {len(worship_scripts_new)} scripts to worship_scripts.json")

    # Cleanup old scripts
    for filename in os.listdir(output_dir):
        if filename.endswith('.pdf'):
            date_str = filename[:-4]
            if date_str not in worship_scripts_new:
                file_path = os.path.join(output_dir, filename)
                try:
                    os.remove(file_path)
                    print(f"Deleted old script: {file_path}")
                except Exception as e:
                    print(f"Error deleting old script {file_path}: {e}")

if __name__ == '__main__':
    main()
