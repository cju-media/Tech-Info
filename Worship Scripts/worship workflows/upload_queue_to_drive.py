import os
import json
import io
import shutil
import re
import requests
import datetime
import zoneinfo
from googleapiclient.discovery import build
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials
from googleapiclient.http import MediaIoBaseUpload

QUEUE_DIR = 'Utilities/uploads_queue'
FAILED_QUEUE_DIR = os.path.join(QUEUE_DIR, '_failed')
THUMBNAILS_DEST_PARENT_FOLDER_ID = '1KI_KifGRzRnafb5Z0IuXmdrgIEyB5_3f'
SERMON_DEST_PARENT_FOLDER_ID = '1Ji2Bbe7vWTcaRCpdQOjzwQgxsIoOWdy4'

# Real Google Drive file/folder IDs are long alphanumeric (+ -/_) strings
# (every known ID in this repo is 33 chars). Used to catch filenames that
# parse "successfully" but produce garbage that was never a Drive ID -
# without this, such a file just fails the upload every run forever and
# sits stuck in the queue (see: 1785433685454_Service_Title_2026-8-2-26.jpg,
# where the legacy underscore-split fallback below extracted folder_id =
# "Service" from a filename that didn't actually contain any known ID).
DRIVE_ID_PATTERN = re.compile(r'^[A-Za-z0-9_-]{20,}$')


def quarantine_file(file_path, filename, reason):
    """Move an unprocessable queue file out of the active queue so it stops
    being retried (and failing the same way) on every future run, while
    keeping it around (committed, inspectable) instead of silently
    disappearing."""
    print(f"Quarantining '{filename}': {reason}")
    os.makedirs(FAILED_QUEUE_DIR, exist_ok=True)
    try:
        shutil.move(file_path, os.path.join(FAILED_QUEUE_DIR, filename))
    except Exception as e:
        print(f"Error quarantining '{filename}': {e}")


def dispatch_event(event_type, client_payload):
    """Fire a repository_dispatch event so imessage_notifications.yml (or
    anything else) can react. Mirrors Youtube Processing/create_youtube_stream.py."""
    pat = os.environ.get('PAT')
    if not pat:
        print("Warning: PAT environment variable not set, cannot dispatch GitHub event.")
        return

    repo = "cju-media/tech-info"
    url = f"https://api.github.com/repos/{repo}/dispatches"
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "Authorization": f"token {pat}"
    }
    payload = {"event_type": event_type, "client_payload": client_payload}

    try:
        response = requests.post(url, headers=headers, json=payload)
        if response.status_code == 204:
            print(f"Successfully dispatched '{event_type}' github event.")
        else:
            print(f"Failed to dispatch '{event_type}' github event: {response.status_code} {response.text}")
    except Exception as e:
        print(f"Error dispatching '{event_type}' github event: {e}")

def get_upcoming_sunday():
    tz = zoneinfo.ZoneInfo("America/Los_Angeles")
    now_pt = datetime.datetime.now(tz)
    days_ahead = 6 - now_pt.weekday()
    if days_ahead <= 0:
        days_ahead += 7
    sunday = now_pt + datetime.timedelta(days=days_ahead)
    return sunday.strftime("%m-%d-%Y")

def get_or_create_date_folder(service, parent_folder_id, date_str):
    query = f"name='{date_str}' and '{parent_folder_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
    results = service.files().list(q=query, spaces='drive', fields='files(id, name)').execute()
    items = results.get('files', [])
    if items:
        return items[0]['id']
    else:
        file_metadata = {
            'name': date_str,
            'parents': [parent_folder_id],
            'mimeType': 'application/vnd.google-apps.folder'
        }
        folder = service.files().create(body=file_metadata, fields='id', supportsAllDrives=True).execute()
        return folder.get('id')

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

def upload_to_drive(service, file_path, original_filename, folder_id):
    print(f"Uploading {original_filename} to Google Drive folder {folder_id}...")

    try:
        # Determine mimetype automatically if possible, otherwise default
        import mimetypes
        mime_type, _ = mimetypes.guess_type(file_path)
        if not mime_type:
            mime_type = 'application/octet-stream'

        media = MediaIoBaseUpload(io.BytesIO(open(file_path, "rb").read()), mimetype=mime_type, resumable=True)

        file_metadata = {
            'name': original_filename,
            'parents': [folder_id]
        }

        service.files().create(
            body=file_metadata,
            media_body=media,
            supportsAllDrives=True
        ).execute()

        print(f"Successfully uploaded {original_filename} to Drive.")
        return True
    except Exception as e:
        print(f"Error uploading {original_filename} to Google Drive: {e}")
        return False

def main():
    if not os.path.exists(QUEUE_DIR):
        print(f"Queue directory '{QUEUE_DIR}' does not exist. Nothing to do.")
        return

    files_in_queue = [f for f in os.listdir(QUEUE_DIR) if os.path.isfile(os.path.join(QUEUE_DIR, f)) and not f.startswith('.')]

    if not files_in_queue:
        print("No files in the queue. Exiting.")
        return

    drive_service = get_drive_service()
    if not drive_service:
        print("Skipping Google Drive upload due to missing credentials.")
        return

    print(f"Found {len(files_in_queue)} file(s) to process.")

    for filename in files_in_queue:
        file_path = os.path.join(QUEUE_DIR, filename)

        # Handle both the new `---` delimiter and the legacy `_` delimiter which breaks on IDs with underscores
        if '---' in filename:
            parts = filename.split('---', 2)
        else:
            # Fallback for old queued files: search for known folder IDs first to prevent splitting mid-ID
            known_ids = [THUMBNAILS_DEST_PARENT_FOLDER_ID, SERMON_DEST_PARENT_FOLDER_ID, '1MVeC2j0v4zTA1sVjhLz06bqEz3qbaYxs', '1ctYBJnFLNkdNhgoU4XLcgJc3QTz7MqwI']
            parts = None
            for known_id in known_ids:
                if f"_{known_id}_" in filename:
                    # Example: 1785467269621_1KI_KifGRzRnafb5Z0IuXmdrgIEyB5_3f_filename.jpg
                    ts_part, rest = filename.split(f"_{known_id}_", 1)
                    if ts_part.isdigit():
                        parts = [ts_part, known_id, rest]
                        break

            # If no known ID was found, fallback to the simple underscore split (might still break if ID has an underscore)
            if not parts:
                parts = filename.split('_', 2)

        if parts and len(parts) == 3 and parts[0].isdigit():
            folder_id = parts[1]
            original_filename = parts[2]

            if not DRIVE_ID_PATTERN.match(folder_id):
                quarantine_file(file_path, filename,
                                 f"parsed folder_id '{folder_id}' doesn't look like a real Drive ID "
                                 f"(this would just fail the upload and get stuck retrying every run)")
                continue

            if folder_id in [THUMBNAILS_DEST_PARENT_FOLDER_ID, SERMON_DEST_PARENT_FOLDER_ID] and original_filename.lower().endswith(('.jpg', '.jpeg')):
                date_str = None

                if folder_id == SERMON_DEST_PARENT_FOLDER_ID:
                    # Sermon series thumbnails always target the upcoming Sunday folder
                    date_str = get_upcoming_sunday()
                    print(f"Sermon Series Thumbnail detected. Using upcoming Sunday date: {date_str}")
                else:
                    # Worship service thumbnails extract the date from the filename
                    date_pattern = re.compile(r'(\d{1,4}[-/]\d{1,2}[-/]\d{1,4})')
                    match = date_pattern.search(original_filename)
                    if match:
                        date_str = match.group(1)
                        print(f"Found date {date_str} in {original_filename}")

                if date_str:
                    print(f"Redirecting {original_filename} to subfolder '{date_str}' of {folder_id}")
                    try:
                        new_folder_id = get_or_create_date_folder(drive_service, folder_id, date_str)
                        if new_folder_id:
                            folder_id = new_folder_id
                    except Exception as e:
                        print(f"Error redirecting folder for {original_filename}: {e}")

            if upload_to_drive(drive_service, file_path, original_filename, folder_id):
                if folder_id in [THUMBNAILS_DEST_PARENT_FOLDER_ID] or (date_str and "Worship Service Thumbnail" in str(folder_id)):
                    # The folder_id variable might have been reassigned to a new subfolder ID, so we check original logic
                    pass

                # If successful, check if it's a worship service thumbnail to create stream
                if parts[1] == THUMBNAILS_DEST_PARENT_FOLDER_ID and date_str:
                    title_path = os.path.join("Worship Scripts", "service-titles", "title.txt")
                    desc_path = os.path.join("Youtube Processing", "Description.txt")

                    if not (os.path.exists(title_path) and os.path.exists(desc_path)):
                        print(f"Worship Service Thumbnail detected but title/description missing. Executing text generation scripts for {date_str}...")
                        import subprocess

                        env_copy = os.environ.copy()
                        env_copy['FORCE_UPDATE'] = 'true'

                        update_titles_script = os.path.join("Worship Scripts", "worship workflows", "update_service_titles.py")
                        if os.path.exists(update_titles_script):
                            try:
                                print(f"Running {update_titles_script}...")
                                subprocess.run(["python", "worship workflows/update_service_titles.py"], check=True, cwd="Worship Scripts", env=env_copy)
                            except Exception as e:
                                print(f"Error running update_service_titles.py: {e}")
                        else:
                            print(f"Could not find script at {update_titles_script}")

                        generate_desc_script = os.path.join("Youtube Processing", "generate_youtube_description.py")
                        if os.path.exists(generate_desc_script):
                            try:
                                print(f"Running {generate_desc_script}...")
                                subprocess.run(["python", "generate_youtube_description.py"], check=True, cwd="Youtube Processing", env=env_copy)
                            except Exception as e:
                                print(f"Error running generate_youtube_description.py: {e}")
                        else:
                            print(f"Could not find script at {generate_desc_script}")
                    else:
                        print(f"Worship Service Thumbnail detected and title/description already exist for {date_str}.")

                    print(f"Uploading title and description to Drive...")
                    import subprocess

                    title_uploaded = False
                    desc_uploaded = False
                    worship_title_text = None

                    if os.path.exists(title_path):
                        upload_to_drive(drive_service, title_path, "title.txt", folder_id)
                        title_uploaded = True
                        try:
                            with open(title_path, "r") as f:
                                worship_title_text = f.read().strip()
                        except Exception:
                            pass
                    else:
                        print(f"Title file not found at {title_path}")

                    if os.path.exists(desc_path):
                        upload_to_drive(drive_service, desc_path, "Description.txt", folder_id)
                        desc_uploaded = True
                    else:
                        print(f"Description file not found at {desc_path}")

                    if title_uploaded and desc_uploaded:
                        dispatch_event('worship_title_description_uploaded', {
                            'date': date_str,
                            'title': worship_title_text
                        })

                    print(f"Launching create_youtube_stream.py for {date_str}...")
                    script_path = os.path.join("Youtube Processing", "create_youtube_stream.py")
                    if os.path.exists(script_path):
                        try:
                            # We keep the file around so create_youtube_stream can upload it as thumbnail
                            subprocess.run(["python", script_path, date_str, file_path], check=True)
                        except Exception as e:
                            print(f"Error running create_youtube_stream.py: {e}")
                    else:
                        print(f"Could not find script at {script_path}")

                # If successful, remove it so the GitHub Action can commit the deletion
                os.remove(file_path)
                print(f"Removed {filename} from queue.")
            else:
                # upload_to_drive() failure is usually transient (network,
                # auth, API hiccup) - worth retrying next run, so leave it.
                print(f"Failed to process {filename}, leaving in queue.")
        else:
            # Filename structure itself doesn't parse (not just a bad ID) -
            # retrying won't ever fix that, so quarantine instead of
            # leaving it to fail silently forever.
            quarantine_file(file_path, filename, "does not match the expected TIMESTAMP---FOLDERID---FILENAME format")

if __name__ == "__main__":
    main()
