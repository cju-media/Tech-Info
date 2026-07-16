import os
import json
import re
from datetime import datetime
from googleapiclient.discovery import build
import dateutil.parser
import pypdf
import time
import random
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

    # 1. Calculate a dynamic delay to stay under the 250,000 TPM limit
    # (Approx. 4 characters = 1 token). We calculate how much of our TPM bucket
    # this massive text consumes and wait that proportion of 60 seconds.
    char_count = len(text)
    estimated_tokens = int(char_count / 4)
    print(f"[Gemini] Full PDF size: {char_count} chars (~{estimated_tokens} tokens).")

    # Calculate delay (Minimum 15 seconds, maximum 50 seconds based on file size)
    safe_delay = max(15, min(50, int((estimated_tokens / 250000) * 60) + 5))
    print(f"[Gemini] Resting for {safe_delay}s to safeguard the TPM rate limit...")
    time.sleep(safe_delay)

    # 2. Configure the client to fail fast using an explicit timeout (e.g., 30,000 milliseconds)
    from google.genai import types
    client = genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(timeout=30000) # Prevents 15-minute hanging
    )
    model_name = 'gemini-3.5-flash'

    prompt = f"""
    Extract the names of the people doing the "Worship Leading" (or Worship Leader) and the "Sermon" (or Preaching) from the following text.
    Note that the key of who is speaking is usually located on the first page, but the full context of the service script is provided below.

    Instead of just listing the names, you need to assign them to microphones based on these rules:
    - If Laura is speaking, she is always LAV1.
    - If Michael is speaking, he is always LAV2.
    - Any additional speakers should be numbered sequentially in any order (LAV3, LAV4, etc.).

    Format the output exactly like this, on multiple lines:
    <strong>Speakers - [X] Lavs total</strong>
    LAV1: [Name]
    LAV2: [Name]
    LAV3: [Name]

    Text:
    {text}
    """

    # User requested: "Only use Gemini 3.5 flash once per request"
    try:
        print(f"[Gemini] Requesting analysis for {date_str}...")
        response = client.models.generate_content(
            model=model_name,
            contents=prompt
        )

        if response and response.text:
            result = response.text.strip()
            print(f"[Gemini] Successfully parsed: {result}")
            return result

    except Exception as e:
        error_msg = str(e)
        print(f"[Gemini] API error: {error_msg}")

    print(f"[Gemini] Failed to process {date_str}.")
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

    # Add this before hitting Gemini:
    print("[System] Adding startup jitter to bypass shared IP rate-limiting blocks...")
    time.sleep(random.randint(10, 30))

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

                            # Only download/process if the file is new/modified OR if it's missing speakerInfo
                            if old_modified_time == modified_time and has_valid_speaker_info:
                                # File hasn't changed and we already have speaker info, skip download but keep in new state
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

                                # Retain youtubeDescriptionModifiedTime if it exists so we don't accidentally wipe it
                                youtube_mod_time = None
                                if date_str in worship_scripts:
                                    youtube_mod_time = worship_scripts[date_str].get('youtubeDescriptionModifiedTime')

                                # Save URL encoded path for the web and modifiedTime
                                worship_scripts_new[date_str] = {
                                    'path': pdf_path,
                                    'modifiedTime': modified_time,
                                    'isCommunion': is_communion,
                                    'speakerInfo': speaker_info
                                }

                                if youtube_mod_time:
                                    worship_scripts_new[date_str]['youtubeDescriptionModifiedTime'] = youtube_mod_time

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
