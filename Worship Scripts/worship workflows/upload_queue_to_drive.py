import os
import json
import io
import shutil
import re
import requests
import time
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

# Where a worship-service thumbnail waits when it arrives before this week's
# OW does -- see defer_stream_creation() below and
# "Youtube Processing/create_pending_stream.py", which finishes the job.
PENDING_STREAM_DIR = os.path.join("Youtube Processing", "pending_stream")
PENDING_STREAM_META = os.path.join(PENDING_STREAM_DIR, "meta.json")

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


def dispatch_event(event_type, client_payload, max_retries=4, backoff_seconds=3):
    """Fire a repository_dispatch event so imessage_notifications.yml (or
    anything else) can react. Mirrors Youtube Processing/create_youtube_stream.py.

    Retries with exponential backoff on transient failures (network errors,
    429 rate limiting, and 5xx GitHub API outages) so a momentary blip in
    GitHub's API doesn't silently swallow the notification."""
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

    for attempt in range(1, max_retries + 1):
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=15)
        except requests.exceptions.RequestException as e:
            if attempt < max_retries:
                wait = backoff_seconds * (2 ** (attempt - 1))
                print(f"Error dispatching '{event_type}' github event (attempt {attempt}/{max_retries}): {e}. Retrying in {wait}s...")
                time.sleep(wait)
                continue
            print(f"Error dispatching '{event_type}' github event after {max_retries} attempts: {e}")
            return

        if response.status_code == 204:
            print(f"Successfully dispatched '{event_type}' github event.")
            return

        retryable = response.status_code == 429 or response.status_code >= 500
        if retryable and attempt < max_retries:
            wait = backoff_seconds * (2 ** (attempt - 1))
            print(f"Failed to dispatch '{event_type}' github event (attempt {attempt}/{max_retries}): "
                  f"{response.status_code} {response.text}. Retrying in {wait}s...")
            time.sleep(wait)
            continue

        print(f"Failed to dispatch '{event_type}' github event: {response.status_code} {response.text}")
        return

def content_matches_target_week(date_str):
    """True if title.txt on disk was actually generated for date_str
    (MM-DD-YYYY), not carried over from an earlier week that never got
    cleared -- see service_titles_state.json's target_date, written by
    update_service_titles.py. create_pending_stream.py duplicates this
    rather than importing across script directories, matching this
    codebase's existing pattern of each standalone script owning its own
    small helpers (see e.g. get_title() in create_youtube_stream.py and
    update_youtube_stream.py)."""
    state_path = os.path.join("Worship Scripts", "service_titles_state.json")
    if not os.path.exists(state_path):
        return False
    try:
        with open(state_path) as f:
            state = json.load(f)
    except Exception:
        return False
    target = state.get("target_date")
    if not target:
        return False
    try:
        wanted = datetime.datetime.strptime(date_str, "%m-%d-%Y").date().isoformat()
    except ValueError:
        return False
    return target == wanted


def description_matches_target_week(date_str):
    """Same idea as content_matches_target_week() but for Description.txt,
    which is regenerated by a separate script (generate_youtube_description.py)
    that can lag a step behind title.txt within the same hourly run -- see
    description_state.json, written alongside Description.txt."""
    state_path = os.path.join("Youtube Processing", "description_state.json")
    if not os.path.exists(state_path):
        return False
    try:
        with open(state_path) as f:
            state = json.load(f)
    except Exception:
        return False
    target = state.get("target_date")
    if not target:
        return False
    try:
        wanted = datetime.datetime.strptime(date_str, "%m-%d-%Y").date().isoformat()
    except ValueError:
        return False
    return target == wanted


def defer_stream_creation(file_path, date_str, stream_time):
    """Stash a worship-service thumbnail whose title/description aren't
    ready yet (this week's OW hasn't been posted) so
    "Youtube Processing/create_pending_stream.py" can finish creating the
    stream once they are, instead of publishing it now with a stale or
    generic placeholder title/description."""
    os.makedirs(PENDING_STREAM_DIR, exist_ok=True)
    ext = os.path.splitext(file_path)[1] or ".jpg"
    thumb_dest = os.path.join(PENDING_STREAM_DIR, f"thumbnail{ext}")
    shutil.move(file_path, thumb_dest)
    with open(PENDING_STREAM_META, "w") as f:
        json.dump({"date": date_str, "time": stream_time, "thumbnail_path": thumb_dest}, f, indent=2)
    print(f"Stashed thumbnail for {date_str} in {PENDING_STREAM_DIR}; the stream will be created once title/description are ready.")


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
        if filename.endswith('.meta.json'):
            # Sidecar metadata for a Worship Service Thumbnail upload (written by
            # the upload dashboard's settings panel) -- consumed as a side effect
            # of processing its paired image below, never uploaded on its own.
            continue

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
            # Defined here (not just inside the branch below) so later checks
            # can safely reference them for every folder type, not only
            # worship-service-thumbnail/sermon-series jpgs.
            date_str = None
            worship_meta_path = None
            stream_meta = None

            if not DRIVE_ID_PATTERN.match(folder_id):
                quarantine_file(file_path, filename,
                                 f"parsed folder_id '{folder_id}' doesn't look like a real Drive ID "
                                 f"(this would just fail the upload and get stuck retrying every run)")
                continue

            if folder_id in [THUMBNAILS_DEST_PARENT_FOLDER_ID, SERMON_DEST_PARENT_FOLDER_ID] and original_filename.lower().endswith(('.jpg', '.jpeg')):
                date_str = None
                stream_meta = None
                worship_meta_path = None

                if folder_id == SERMON_DEST_PARENT_FOLDER_ID:
                    # Sermon series thumbnails always target the upcoming Sunday folder
                    date_str = get_upcoming_sunday()
                    print(f"Sermon Series Thumbnail detected. Using upcoming Sunday date: {date_str}")
                else:
                    # Worship service thumbnails: prefer an explicit date (and
                    # optional time/title/description) from a sidecar metadata
                    # file written by the upload dashboard's settings panel over
                    # guessing the date from the filename.
                    worship_meta_path = file_path + '.meta.json'
                    if os.path.exists(worship_meta_path):
                        try:
                            with open(worship_meta_path, 'r') as mf:
                                stream_meta = json.load(mf)
                        except Exception as e:
                            print(f"Could not parse upload settings for {filename}: {e}")
                            stream_meta = None

                    if stream_meta and stream_meta.get('date'):
                        date_str = stream_meta['date']
                        print(f"Using date from upload settings: {date_str}")
                    else:
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
                deferred = False

                # If successful, check if it's a worship service thumbnail to create stream
                if parts[1] == THUMBNAILS_DEST_PARENT_FOLDER_ID and date_str:
                    title_path = os.path.join("Worship Scripts", "service-titles", "title.txt")
                    desc_path = os.path.join("Youtube Processing", "Description.txt")

                    # An explicit title/description from the upload settings panel
                    # always wins over whatever's already on disk.
                    meta_title = stream_meta.get('title', '').strip() if stream_meta else ''
                    meta_desc = stream_meta.get('description', '').strip() if stream_meta else ''
                    stream_time = (stream_meta.get('time', '').strip() if stream_meta else '') or '10:30'

                    if meta_title:
                        print(f"Using title from upload settings for {date_str}: {meta_title}")
                        os.makedirs(os.path.dirname(title_path), exist_ok=True)
                        with open(title_path, "w") as f:
                            f.write(meta_title)

                    if meta_desc:
                        print(f"Using description from upload settings for {date_str}.")
                        os.makedirs(os.path.dirname(desc_path), exist_ok=True)
                        with open(desc_path, "w") as f:
                            f.write(meta_desc)

                    # Whatever's already on disk only counts as usable if it
                    # was actually generated for THIS week -- title.txt and
                    # Description.txt are never cleared between weeks, so
                    # without this check a thumbnail uploaded before this
                    # week's OW would silently reuse last week's (real, but
                    # wrong) title/description instead of waiting for it.
                    title_is_current = content_matches_target_week(date_str)
                    desc_is_current = description_matches_target_week(date_str)

                    need_title = not meta_title and not (os.path.exists(title_path) and title_is_current)
                    need_desc = not meta_desc and not (os.path.exists(desc_path) and desc_is_current)

                    if need_title or need_desc:
                        print(f"Worship Service Thumbnail detected but title/description missing or stale. Executing text generation scripts for {date_str}...")
                        import subprocess

                        env_copy = os.environ.copy()
                        env_copy['FORCE_UPDATE'] = 'true'

                        if need_title:
                            update_titles_script = os.path.join("Worship Scripts", "worship workflows", "update_service_titles.py")
                            if os.path.exists(update_titles_script):
                                try:
                                    print(f"Running {update_titles_script}...")
                                    subprocess.run(["python", "worship workflows/update_service_titles.py"], check=True, cwd="Worship Scripts", env=env_copy)
                                except Exception as e:
                                    print(f"Error running update_service_titles.py: {e}")
                            else:
                                print(f"Could not find script at {update_titles_script}")

                        if need_desc:
                            generate_desc_script = os.path.join("Youtube Processing", "generate_youtube_description.py")
                            if os.path.exists(generate_desc_script):
                                try:
                                    print(f"Running {generate_desc_script}...")
                                    subprocess.run(["python", "generate_youtube_description.py"], check=True, cwd="Youtube Processing", env=env_copy)
                                except Exception as e:
                                    print(f"Error running generate_youtube_description.py: {e}")
                            else:
                                print(f"Could not find script at {generate_desc_script}")

                        # Re-check now that generation has (maybe) run --
                        # this is the OW-already-posted case, so it usually
                        # catches up right here in the same pass.
                        title_is_current = content_matches_target_week(date_str)
                        desc_is_current = description_matches_target_week(date_str)
                    else:
                        print(f"Worship Service Thumbnail detected: using existing/provided title and description for {date_str}.")

                    title_ready = bool(meta_title) or (os.path.exists(title_path) and title_is_current)
                    desc_ready = bool(meta_desc) or (os.path.exists(desc_path) and desc_is_current)

                    if not (title_ready and desc_ready):
                        # This week's OW still isn't posted, so there's
                        # nothing real to give this stream yet. Stash the
                        # thumbnail instead of creating a stream with a
                        # stale or generic placeholder title/description --
                        # "Youtube Processing/create_pending_stream.py"
                        # (chained off the same hourly title/description
                        # pipeline) finishes this once the real content lands.
                        print(f"Title/description for {date_str} aren't ready yet (this week's OW likely hasn't been posted). Deferring stream creation until they are.")
                        defer_stream_creation(file_path, date_str, stream_time)
                        deferred = True
                    else:
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

                        print(f"Launching create_youtube_stream.py for {date_str} at {stream_time}...")
                        script_path = os.path.join("Youtube Processing", "create_youtube_stream.py")
                        if os.path.exists(script_path):
                            try:
                                # We keep the file around so create_youtube_stream can upload it as thumbnail
                                subprocess.run(["python", script_path, date_str, file_path, stream_time], check=True)
                            except Exception as e:
                                print(f"Error running create_youtube_stream.py: {e}")
                        else:
                            print(f"Could not find script at {script_path}")

                # If successful, remove it so the GitHub Action can commit the deletion
                # (unless it was stashed for later -- defer_stream_creation()
                # already moved it out of the queue in that case).
                if not deferred:
                    os.remove(file_path)
                    print(f"Removed {filename} from queue.")
                if worship_meta_path and os.path.exists(worship_meta_path):
                    os.remove(worship_meta_path)
                    print(f"Removed {os.path.basename(worship_meta_path)} from queue.")
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
