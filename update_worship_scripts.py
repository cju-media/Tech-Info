import os
import json
import re
from datetime import datetime
from googleapiclient.discovery import build
import dateutil.parser
import pypdf
import time
from google import genai
from google.genai import types

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


def get_speaker_info(text, date_str):
    api_key = os.environ.get('GEMINI_API_KEY')
    if not api_key:
        print("No GEMINI_API_KEY found, skipping speaker info extraction.")
        return None

    # Gemini free tier allows 15 RPM. Add a 5 second delay to ensure we stay under the limit
    # especially during the initial cache backfill of multiple scripts.
    print("[Gemini] Sleeping for 5 seconds to respect rate limits...")
    time.sleep(5)

    try:
        print(f"[Gemini] Passing full PDF text for {date_str} to Gemini for speaker extraction...")

        default_client = genai.Client(api_key=api_key) # Defaults to v1beta

        # A comprehensive list of potential model names to overcome 404s and Limit 0s across tiers
        models_to_try = [
            'gemini-2.5-flash',
            'gemini-2.0-flash',
            'gemini-2.0-flash-exp',
            'gemini-1.5-flash',
            'gemini-1.5-flash-latest',
            'gemini-1.5-flash-001',
            'gemini-1.5-flash-002',
            'gemini-1.5-flash-8b',
            'gemini-1.5-flash-8b-latest',
            'gemini-1.5-pro',
            'gemini-1.5-pro-latest',
            'gemini-1.0-pro',
            'gemini-1.0-pro-latest',
            'gemini-pro'
        ]

        configs = [(default_client, m) for m in models_to_try]

        prompt = f"""
        Extract the names of the people doing the "Worship Leading" (or Worship Leader) and the "Sermon" (or Preaching) from the following text.
        Note that the key of who is speaking is usually located on the first page, but the full context of the service script is provided below.
        Format the output exactly like this, on a single line:
        Worship Leader: [Name] | Sermon: [Name]

        If you cannot find one of them, write "Unknown" for that person.

        Text:
        {text}
        """

        for client, model_name in configs:
            try:
                # Simple backoff loop for 429 Resource Exhausted (where limit > 0)
                for attempt in range(3):
                    try:
                        print(f"[Gemini] Attempting with model {model_name}...")
                        response = client.models.generate_content(
                            model=model_name,
                            contents=prompt
                        )
                        if response and response.text:
                            result = response.text.strip()
                            print(f"[Gemini] Response received for {date_str} via {model_name}: {result}")
                            return result
                        else:
                            print(f"[Gemini] Empty response received for {date_str} via {model_name}.")
                            return None
                    except Exception as inner_e:
                        error_msg = str(inner_e)
                        # The string 'limit: 0' can appear in standard 429 quota exhaustion errors on the free tier.
                        # We should only treat it as a hard model lockout if it's explicitly a 404 or a permission error,
                        # but given standard free tier behavior, we'll treat 429s as standard rate limits and retry.
                        if '404' in error_msg or 'NOT_FOUND' in error_msg:
                            print(f"[Gemini] 404 Not Found for {model_name}, moving to next model config...")
                            break # Breaks attempt loop, goes to next client config
                        elif '429' in error_msg or 'RESOURCE_EXHAUSTED' in error_msg:
                            if attempt == 2:
                                print(f"[Gemini] Exhausted retries for {model_name}, trying next model...")
                                break # Move to next model config
                            delay = 15 * (attempt + 1)
                            print(f"[Gemini] Rate limit hit. Retrying {model_name} in {delay} seconds...")
                            time.sleep(delay)
                        else:
                            print(f"[Gemini] Non-recoverable error for {model_name}: {error_msg}. Moving to next config...")
                            break
            except Exception as outer_e:
                print(f"[Gemini] Outer error testing {model_name}: {outer_e}")
                continue

        print(f"[Gemini] All fallback models exhausted for {date_str}.")

    except Exception as e:
        print(f"[Gemini] Error querying Gemini for {date_str}: {e}")
    return None

def extract_pdf_info(pdf_path, date_str):
    is_communion = False
    speaker_info = None
    try:
        reader = pypdf.PdfReader(pdf_path)
        if len(reader.pages) > 0:
            first_page_text = reader.pages[0].extract_text()
            if first_page_text and "Communion" in first_page_text:
                is_communion = True

            full_text = ""
            for page in reader.pages:
                page_text = page.extract_text()
                if page_text:
                    full_text += page_text + "\n"

            if full_text:
                speaker_info = get_speaker_info(full_text, date_str)
    except Exception as e:
        print(f"Error reading PDF {pdf_path}: {e}")
    return is_communion, speaker_info

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
                            has_valid_speaker_info = worship_scripts[date_str].get('speakerInfo') is not None
                            if old_modified_time == modified_time and has_valid_speaker_info:
                                # File hasn't changed, skip download but keep in new state
                                worship_scripts_new[date_str] = worship_scripts[date_str]
                                print(f"Skipping {date_str} (No changes since last run and speaker info exists)")
                                should_download = False
                            elif old_modified_time == modified_time and not has_valid_speaker_info:
                                print(f"Re-downloading {date_str} to extract missing speaker info...")

                        if should_download:
                            try:
                                pdf_path = f"{output_dir}/{date_str}.pdf"
                                request = service.files().export_media(fileId=doc['id'], mimeType='application/pdf')
                                pdf_content = request.execute()
                                with open(pdf_path, 'wb') as pdf_file:
                                    pdf_file.write(pdf_content)

                                is_communion, speaker_info = extract_pdf_info(pdf_path, date_str)

                                # Save URL encoded path for the web and modifiedTime
                                worship_scripts_new[date_str] = {
                                    'path': pdf_path,
                                    'modifiedTime': modified_time,
                                    'isCommunion': is_communion,
                                    'speakerInfo': speaker_info
                                }
                                print(f"Downloaded upcoming script for {date_str}: {pdf_path}")
                                print(f"[Record] Recorded into repo JSON for {date_str}: isCommunion={is_communion}, speakerInfo='{speaker_info}'")
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
