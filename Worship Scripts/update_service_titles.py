import os
import sys
import json
import requests
import datetime
import zoneinfo
import pypdf
import io
from google import genai
from google.genai import types
from googleapiclient.discovery import build
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials
from googleapiclient.http import MediaIoBaseUpload

DRIVE_FOLDER_ID = '1BICfy0OQa3fNvo69iEOpEx_66KCQGeUv'

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
            # Must have write access
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

def upload_to_drive(service, file_path, filename):
    print(f"Uploading {filename} to Google Drive...")

    # Check if file exists in folder
    query = f"'{DRIVE_FOLDER_ID}' in parents and name = '{filename}' and trashed = false"
    try:
        results = service.files().list(
            q=query,
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
            fields="files(id, name)"
        ).execute()
        files = results.get('files', [])

        media = MediaIoBaseUpload(io.BytesIO(open(file_path, "rb").read()), mimetype='text/plain', resumable=True)

        if files:
            # Update existing file
            file_id = files[0]['id']
            service.files().update(
                fileId=file_id,
                media_body=media,
                supportsAllDrives=True
            ).execute()
            print(f"Updated existing file {filename} in Drive.")
        else:
            # Create new file
            file_metadata = {
                'name': filename,
                'parents': [DRIVE_FOLDER_ID]
            }
            service.files().create(
                body=file_metadata,
                media_body=media,
                supportsAllDrives=True
            ).execute()
            print(f"Created new file {filename} in Drive.")
    except Exception as e:
        error_msg = str(e)
        if "storageQuotaExceeded" in error_msg or "Service Accounts do not have storage quota" in error_msg:
            print(f"Error modifying {filename}: Quota exceeded.")
            print(f"  -> Raw Error: {error_msg}")
            print("  -> If you are using GDRIVE_OAUTH_JSON, the file in Drive may be owned by the Service Account which has no quota.")
            print("  -> FIX: Manually delete the problematic file from Google Drive so the script can recreate it under your account.")
        else:
            print(f"Error uploading {filename} to Google Drive: {e}")


def main():
    # 1. Check if today is Sunday
    tz = zoneinfo.ZoneInfo("America/Los_Angeles")
    now_pt = datetime.datetime.now(tz)

    force_update = os.environ.get("FORCE_UPDATE", "").lower() == "true"

    if now_pt.weekday() == 6 and not force_update:
        print("Today is Sunday. Do not update text files. Exiting.")
        return

    # 2. Calculate the coming Sunday's date to check if PDF is titled for it
    days_ahead = 6 - now_pt.weekday()
    sunday = now_pt + datetime.timedelta(days=days_ahead)

    # PDF naming convention is like "3.1.26 OW.pdf"
    # So we construct possible filename prefixes
    month = sunday.month
    day = sunday.day
    year = sunday.year % 100

    target_prefix = f"{month}.{day}.{year} OW"

    print(f"Looking for PDF starting with: {target_prefix}")

    # 5. Fetch GitHub API for OWs
    api_url = "https://api.github.com/repos/TheCathedralFCCLA/OW/contents/OWs"

    headers = {}
    github_token = os.environ.get("GITHUB_TOKEN")
    if github_token:
        headers["Authorization"] = f"Bearer {github_token}"

    try:
        api_response = requests.get(api_url, headers=headers)
        api_response.raise_for_status()
        ows_contents = api_response.json()
    except Exception as e:
        print(f"Error fetching OWs contents: {e}")
        return

    # 6. Find the target file
    target_file_info = None
    for item in ows_contents:
        if item["name"].startswith(target_prefix) and item["name"].endswith(".pdf"):
            target_file_info = item
            break

    if not target_file_info:
        print(f"No PDF found for coming Sunday ({target_prefix}). Exiting.")
        return

    target_filename = target_file_info["name"]
    print(f"Found target filename: {target_filename}")

    current_sha = target_file_info["sha"]
    download_url = target_file_info["download_url"]

    # 8. Compare with local state
    state_file = "service_titles_state.json"
    state_data = {}
    if os.path.exists(state_file):
        try:
            with open(state_file, "r") as f:
                state_data = json.load(f)
        except json.JSONDecodeError:
            pass

    titles_dir = "service-titles"
    last_processed_sha = state_data.get("last_processed_sha")
    needs_update = False

    if force_update:
        print("FORCE_UPDATE is set to true. Bypassing SHA checks and forcing an update.")
        needs_update = True
    elif last_processed_sha == current_sha:
        print("PDF SHA matches last processed SHA. Checking if files are missing in Google Drive...")
        drive_service = get_drive_service()
        if drive_service:
            try:
                query = f"'{DRIVE_FOLDER_ID}' in parents and trashed = false"
                results = drive_service.files().list(q=query, supportsAllDrives=True, includeItemsFromAllDrives=True, fields="files(name)").execute()
                drive_files = [f['name'] for f in results.get('files', [])]

                if os.path.exists(titles_dir):
                    local_files = [f for f in os.listdir(titles_dir) if f.endswith('.txt')]
                    for lf in local_files:
                        if lf not in drive_files:
                            needs_update = True
                            print(f"File {lf} missing in Drive. Forcing update and write to disk.")
                            break
                else:
                    needs_update = True
            except Exception as e:
                print(f"Error checking drive: {e}")

        if not needs_update:
            print("PDF SHA matches last processed SHA and all files exist in Drive. No updates needed. Exiting.")
            return

    # 9. Download the PDF
    print(f"Downloading PDF from {download_url}...")
    try:
        pdf_response = requests.get(download_url, headers=headers)
        pdf_response.raise_for_status()
        pdf_content = pdf_response.content
    except Exception as e:
        print(f"Error downloading PDF: {e}")
        return

    # 10. Extract text from PDF
    print("Extracting text from PDF...")
    pdf_text = ""
    try:
        reader = pypdf.PdfReader(io.BytesIO(pdf_content))
        for page in reader.pages:
            text = page.extract_text()
            if text:
                pdf_text += text + "\n"
    except Exception as e:
        print(f"Error reading PDF: {e}")
        return

    # 11. Read worship-prompt.txt
    prompt_file = "worship-prompt.txt"
    try:
        with open(prompt_file, "r") as f:
            prompt_template = f.read()
    except Exception as e:
        print(f"Error reading prompt file {prompt_file}: {e}")
        return

    # 12. Send to Gemini
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("GEMINI_API_KEY environment variable is missing.")
        return

    prompt = f"{prompt_template}\n\nPDF Text:\n{pdf_text}"
    print("Sending prompt to Gemini...")
    client = genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(timeout=30000)
    )

    model_name = 'gemini-3.5-flash'

    try:
        gemini_response = client.models.generate_content(
            model=model_name,
            contents=prompt
        )
        if not gemini_response or not gemini_response.text:
            print("No response from Gemini.")
            return

        result_text = gemini_response.text.strip()
    except Exception as e:
        print(f"Error querying Gemini: {e}")
        return

    # 13, 14, 15. Parse output and write to text files
    print("Parsing Gemini output and updating text files...")

    titles_dir = "service-titles"
    if not os.path.exists(titles_dir):
        os.makedirs(titles_dir)

    # Clear all .txt files in the directory first
    for filename in os.listdir(titles_dir):
        if filename.endswith(".txt"):
            filepath = os.path.join(titles_dir, filename)
            with open(filepath, "w") as f:
                f.write("")

    # Parse Gemini output
    lines = result_text.split('\n')
    for line in lines:
        line = line.strip()
        if not line:
            continue

        parts = line.split(maxsplit=1)
        if len(parts) >= 1:
            file_key = parts[0]
            content = parts[1] if len(parts) > 1 else ""

            filename = f"{file_key}.txt"
            filepath = os.path.join(titles_dir, filename)

            if os.path.exists(filepath):
                with open(filepath, "w") as f:
                    f.write(content.strip())
            else:
                print(f"Warning: File {filename} does not exist in {titles_dir}. Skipping.")

    # Also generate communion.txt based on the entire PDF text
    is_communion = "communion" in pdf_text.lower()
    communion_filepath = os.path.join(titles_dir, "communion.txt")
    with open(communion_filepath, "w") as f:
        f.write("True" if is_communion else "False")


    # 16. Upload to Google Drive
    print("Uploading updated files to Google Drive...")
    drive_service = get_drive_service()
    if drive_service:
        for filename in os.listdir(titles_dir):
            if filename.endswith(".txt"):
                file_path = os.path.join(titles_dir, filename)
                # Only upload if the file is not empty to avoid uploading cleared files,
                # actually wait, the user said "Update files with the same name and add files if they don't exist currently."
                # If a section was omitted by Gemini, it was cleared in step 13.
                # Let's upload all files regardless, or maybe only non-empty?
                # Re-reading prompt: "Whenever the service title text files are updated upload them to this Google Drive folder."
                # We should upload all files that exist in the directory.
                upload_to_drive(drive_service, file_path, filename)
    else:
        print("Skipping Google Drive upload due to missing credentials.")

    # Update state file
    state_data["last_processed_sha"] = current_sha
    try:
        with open(state_file, "w") as f:
            json.dump(state_data, f)
        print("Updated state file successfully.")
    except Exception as e:
        print(f"Error writing state file: {e}")
        return

    print("Update complete.")
    with open(".sync_success", "w") as f:
        f.write("true")

if __name__ == "__main__":
    main()
