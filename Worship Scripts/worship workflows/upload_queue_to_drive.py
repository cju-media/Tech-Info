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

import service_keys

QUEUE_DIR = 'Utilities/uploads_queue'
FAILED_QUEUE_DIR = os.path.join(QUEUE_DIR, '_failed')
THUMBNAILS_DEST_PARENT_FOLDER_ID = '1KI_KifGRzRnafb5Z0IuXmdrgIEyB5_3f'
SERMON_DEST_PARENT_FOLDER_ID = '1Ji2Bbe7vWTcaRCpdQOjzwQgxsIoOWdy4'

# Where a worship-service thumbnail waits when it arrives before its OW does
# -- see defer_stream_creation() below and
# "Youtube Processing/create_pending_stream.py", which finishes the job. One
# subfolder per service (pending_stream/<service key>/), so Christmas Eve's
# three thumbnails can all be waiting at once.
PENDING_STREAM_DIR = os.path.join("Youtube Processing", "pending_stream")


def pending_slot(date_str, stream_time):
    """pending_stream/<service key>/ for this service."""
    return os.path.join(PENDING_STREAM_DIR, service_keys.service_key(parse_flexible_date(date_str), stream_time))


def drive_folder_name(date_str, stream_time):
    """The dated Drive folder a worship thumbnail goes in: "12-27-2026" for the
    usual 10:30 service, "12-24-2026 5:30pm" for any other time, so Christmas
    Eve's services each keep their own thumbnail (a thumbnail replaces every
    image in its folder)."""
    time = service_keys.normalize_time(stream_time)
    if not time or time == service_keys.DEFAULT_TIME:
        return date_str
    date = parse_flexible_date(date_str)
    return f"{date.strftime('%m-%d-%Y') if date else date_str} {service_keys.display_time(time)}"

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

def parse_flexible_date(date_str):
    """Parse the handful of date formats that reach this pipeline (the
    dashboard sidecar sends MM-DD-YYYY, but the filename-regex fallback can
    produce M-D-YY and slash separators). Returns a date, or None."""
    for fmt in ("%m-%d-%Y", "%m-%d-%y", "%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%Y/%m/%d"):
        try:
            return datetime.datetime.strptime(date_str, fmt).date()
        except (ValueError, TypeError):
            continue
    return None


def ows_link_date(text):
    """The date embedded in an fccla.org/ows/<M-D-YY> program link inside
    text, as a date, or None. The description boilerplate always carries
    this dated link, so it's a reliable way to tell which Sunday a given
    Description.txt was generated for."""
    m = re.search(r'fccla\.org/ows/(\d{1,2})-(\d{1,2})-(\d{2,4})', text or '')
    if not m:
        return None
    month, day, year = (int(x) for x in m.groups())
    if year < 100:
        year += 2000
    try:
        return datetime.date(year, month, day)
    except ValueError:
        return None


def complete_override(key, title, description):
    """(title, description) to hand create_youtube_stream.py as an explicit
    override, or (None, None) for "use the generated text".

    Both typed: use them. Only one typed: fill the other half from this
    service's generated text once it's ready -- the typed half must not be
    dropped in favour of the generated one. Not ready yet: (None, None), and
    the caller waits with what was typed."""
    if title and description:
        return title, description
    if not (title or description):
        return None, None
    if not key or not service_keys.content_ready(key)[0]:
        return None, None
    return (title or service_keys.title_for(key),
            description or service_keys.read_text(os.path.join(service_keys.service_dir(key), "Description.txt")))


# ---------------------------------------------------------------- which service

UNASSIGNED_DIR = os.path.join(QUEUE_DIR, '_unassigned')
GEMINI_MODEL = 'gemini-3.5-flash'          # same model as update_service_titles.py


def upcoming_services(today):
    """(key, date, time) for every generated service from today through
    LOOKAHEAD_DAYS out, in start order."""
    last = today + datetime.timedelta(days=service_keys.LOOKAHEAD_DAYS)
    return [s for s in service_keys.list_services() if today <= s[1] <= last]


def service_label(key):
    date, time = service_keys.parse_service_key(key)
    when = date.strftime('%a %b ') + str(date.day) + ' ' + service_keys.display_time(time or service_keys.DEFAULT_TIME)
    title = service_keys.title_for(key)
    return f"{when} -- {title}" if title else when


def service_from_filename(filename, candidates):
    """The service a thumbnail's name points at ("..._12-24-26_530pm.jpg"), or None."""
    m = re.search(r'(\d{1,2})[-/._](\d{1,2})[-/._](\d{2,4})', filename)
    if not m:
        return None
    date = parse_flexible_date(f"{m.group(1)}-{m.group(2)}-{m.group(3)}")
    if not date:
        return None
    time = service_keys.parse_time_text(filename[m.end():])
    key = service_keys.resolve_key(date, time)
    return key if key in [c[0] for c in candidates] else None


def ask_gemini_for_service(image_path, candidates):
    """Have Gemini read the thumbnail and say which candidate service it's for.
    Returns a key, "" when it can't tell, or None when it couldn't be asked."""
    api_key = os.environ.get('GEMINI_API_KEY')
    if not api_key:
        print("GEMINI_API_KEY isn't set; can't read the thumbnail to find its service.")
        return None
    try:
        from google import genai
        from google.genai import types
        with open(image_path, 'rb') as fh:
            image = fh.read()
        listing = "\n".join(f"{key}: {service_label(key)}" for key, _, _ in candidates)
        prompt = ("This is a church livestream thumbnail. Which of these services is it for? Use the date, "
                  "time and any service name printed on it (e.g. a children's service, a candlelight "
                  "service, 11pm), compared with each service's date, time and title.\n\n"
                  f"{listing}\n\nReply with only the key from the start of the matching line, or NONE "
                  "if the image doesn't clearly match exactly one of them.")
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=[types.Part.from_bytes(data=image, mime_type='image/jpeg'), prompt])
        answer = (response.text or '').strip()
    except Exception as e:
        print(f"Couldn't ask Gemini which service the thumbnail is for: {e}")
        return None
    keys = [key for key, _, _ in candidates if key in answer]
    print(f"Gemini read the thumbnail as: {answer!r}")
    return keys[0] if len(keys) == 1 else ""


def detect_service(image_path, filename, meta, today):
    """Which service a thumbnail uploaded without its livestream settings
    touched belongs to: (date MM-DD-YYYY, time HH:MM), or None to hold it.

    In order: a date/time in the filename; Gemini reading the image against
    the upcoming services' dates, times and titles; and only when Gemini
    couldn't be asked at all, the dashboard's suggestion (what its settings
    panel showed). When Gemini looked and couldn't tell, it's held rather
    than guessed: a thumbnail on the wrong one of Christmas Eve's streams
    is worse than a text asking which it is. With no services generated at
    all (nothing to choose from), the panel's date and time stand as sent."""
    candidates = upcoming_services(today)
    if not candidates:
        return meta.get('date'), meta.get('time') or service_keys.DEFAULT_TIME
    suggested = meta.get('suggested')
    key = service_from_filename(filename, candidates)
    how = "its filename"
    # Gemini is asked even when only one service is generated: a thumbnail
    # uploaded early may be for one whose OW isn't posted yet, and it mustn't
    # replace the one service's thumbnail just for being the only option.
    if not key and candidates:
        answer = ask_gemini_for_service(image_path, candidates)
        if answer is None:
            key, how = (suggested if suggested in [c[0] for c in candidates] else None), "the dashboard's suggestion"
        else:
            key, how = answer or None, "the image"
    if not key:
        return None
    date, time = service_keys.parse_service_key(key)
    print(f"{filename} is for {key} ({service_label(key)}), from {how}.")
    return date.strftime('%m-%d-%Y'), time or service_keys.DEFAULT_TIME


def hold_unassigned(file_path, filename, meta_path, today):
    """Keep a thumbnail whose service couldn't be worked out, and ask a human."""
    os.makedirs(UNASSIGNED_DIR, exist_ok=True)
    shutil.move(file_path, os.path.join(UNASSIGNED_DIR, filename))
    if meta_path and os.path.exists(meta_path):
        shutil.move(meta_path, os.path.join(UNASSIGNED_DIR, os.path.basename(meta_path)))
    options = "; ".join(service_label(k) for k, _, _ in upcoming_services(today)) or "none generated yet"
    print(f"Couldn't tell which service {filename} is for; holding it in {UNASSIGNED_DIR}.")
    dispatch_event('worship_thumbnail_needs_service', {'filename': filename, 'services': options})


def defer_stream_creation(file_path, date_str, stream_time, title=None, description=None):
    """Stash a worship-service thumbnail whose title/description aren't
    ready yet (its OW hasn't been posted) in its service's own slot so
    "Youtube Processing/create_pending_stream.py" can finish creating the
    stream once they are, instead of publishing it now with a stale or
    generic placeholder title/description.

    title/description are only set when a human typed an explicit override
    into the upload dashboard -- they're carried through so the pending run
    uses exactly what was entered rather than re-deriving from disk."""
    slot = pending_slot(date_str, stream_time)
    os.makedirs(slot, exist_ok=True)
    ext = os.path.splitext(file_path)[1] or ".jpg"
    thumb_dest = os.path.join(slot, f"thumbnail{ext}")
    shutil.move(file_path, thumb_dest)
    meta = {"date": date_str, "time": stream_time, "thumbnail_path": thumb_dest}
    if title:
        meta["title"] = title
    if description:
        meta["description"] = description
    with open(os.path.join(slot, "meta.json"), "w") as f:
        json.dump(meta, f, indent=2)
    print(f"Stashed thumbnail for {date_str} {stream_time} in {slot}; the stream will be created once title/description are ready.")


def upload_title_and_description(drive_service, folder_id, date_str, title=None, description=None, key=None):
    """Put the service's title.txt and Description.txt next to the thumbnail in its dated Drive
    folder, and announce it. Called when the stream is created: here, or by
    "Youtube Processing/create_pending_stream.py" when the stream had to wait for them.
    key: the service whose generated files to upload. title/description: a typed override to
    upload instead."""
    title_path = os.path.join(service_keys.titles_dir(key), "title.txt") if key else ""
    desc_path = os.path.join(service_keys.service_dir(key), "Description.txt") if key else ""
    if title or description:
        import tempfile
        work = tempfile.mkdtemp()
        for text, name in ((title, "title.txt"), (description, "Description.txt")):
            if text:
                with open(os.path.join(work, name), "w") as f:
                    f.write(text)
        if title:
            title_path = os.path.join(work, "title.txt")
        if description:
            desc_path = os.path.join(work, "Description.txt")
    print(f"Uploading title and description to Drive...")

    title_uploaded = False
    desc_uploaded = False
    worship_title_text = None

    if title_path and os.path.exists(title_path):
        upload_to_drive(drive_service, title_path, "title.txt", folder_id, same_name=True)
        title_uploaded = True
        try:
            with open(title_path, "r") as f:
                worship_title_text = f.read().strip()
        except Exception:
            pass
    else:
        print(f"Title file not found at {title_path}")

    if desc_path and os.path.exists(desc_path):
        upload_to_drive(drive_service, desc_path, "Description.txt", folder_id, same_name=True)
        desc_uploaded = True
    else:
        print(f"Description file not found at {desc_path}")

    if title_uploaded and desc_uploaded:
        dispatch_event('worship_title_description_uploaded', {
            'date': date_str,
            'title': worship_title_text
        })
    return title_uploaded and desc_uploaded


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

# The "at a glance" events card (generate_events_ad.py) is re-rendered on a
# schedule and always keeps the same filename. Drive does not overwrite by
# name -- it would hold a dozen "Events-At-A-Glance.png" files
# and Content Display would cycle every one of them -- and
# cleanup_events_folder.py deliberately never trashes this card, so nothing
# would ever reap the stale copies either. Update the existing file's content
# in place instead. Reusing cleanup's own predicate keeps the two ends of that
# arrangement from drifting apart.
try:
    from cleanup_events_folder import is_protected_flyer
except ImportError:  # pragma: no cover - only if the module moves
    def is_protected_flyer(name):
        return False


def find_file_id_by_name(service, folder_id, name):
    """Drive id of the non-trashed file with this exact name in folder_id, or
    None. Newest first, so a folder that somehow already holds duplicates
    updates the most recent and leaves the rest for a human."""
    safe_name = name.replace("\\", "\\\\").replace("'", "\\'")
    try:
        results = service.files().list(
            q=f"name='{safe_name}' and '{folder_id}' in parents and trashed=false",
            spaces='drive', fields='files(id, name)', orderBy='createdTime desc',
            supportsAllDrives=True, includeItemsFromAllDrives=True,
        ).execute()
        files = results.get('files', [])
        return files[0]['id'] if files else None
    except Exception as e:
        # Fall through to a plain create rather than losing the upload.
        print(f"Could not look up an existing '{name}' in {folder_id}: {e}")
        return None


def identical_file_in_folder(service, folder_id, name, local_path):
    """True if a non-trashed file with this exact name AND identical content
    (same MD5) already lives in folder_id. Idempotency backstop so a queue
    entry that gets processed twice (see the concurrency gate on
    process_uploads.yml) doesn't create a duplicate in Drive -- while still
    allowing a genuinely new flyer that happens to reuse a filename to upload
    alongside the old one, the way Drive normally would."""
    safe_name = name.replace("\\", "\\\\").replace("'", "\\'")
    query = f"name='{safe_name}' and '{folder_id}' in parents and trashed=false"
    try:
        import hashlib
        with open(local_path, "rb") as fh:
            local_md5 = hashlib.md5(fh.read()).hexdigest()
        results = service.files().list(
            q=query, spaces='drive', fields='files(id, md5Checksum)',
            supportsAllDrives=True, includeItemsFromAllDrives=True,
        ).execute()
        return any(f.get('md5Checksum') == local_md5 for f in results.get('files', []))
    except Exception as e:
        # If the check itself fails, don't block the upload -- fall through
        # and let create() proceed as it did before.
        print(f"Could not check for an existing copy of '{name}' in {folder_id}: {e}")
        return False


def images_in_folder(service, folder_id):
    """The non-trashed images in folder_id, newest first ({id, name, modifiedTime})."""
    results = service.files().list(
        q=f"'{folder_id}' in parents and mimeType contains 'image/' and trashed=false",
        spaces='drive', fields='files(id, name, modifiedTime)', orderBy='modifiedTime desc',
        supportsAllDrives=True, includeItemsFromAllDrives=True,
    ).execute()
    return results.get('files', [])


def parse_drive_time(value):
    """An RFC 3339 time from Drive ("...Z") or an ISO time with an offset, as an aware datetime."""
    return datetime.datetime.fromisoformat(value.replace('Z', '+00:00'))


def newer_image_in_folder(service, folder_id, chosen_at):
    """Name of an image in folder_id changed after chosen_at, or None. A title-graphics pick
    that waited for its week must not replace a thumbnail uploaded on the dashboard after it
    was picked."""
    try:
        chosen = parse_drive_time(chosen_at)
        for f in images_in_folder(service, folder_id):
            if f.get('modifiedTime') and parse_drive_time(f['modifiedTime']) > chosen:
                return f['name']
    except Exception as e:
        print(f"Could not check {folder_id} for a newer thumbnail (replacing anyway): {e}")
    return None


def replace_images(service, folder_id, original_filename, media):
    """This Sunday's thumbnail is the new one: overwrite the folder's image in place (a same-named
    one first, else the newest) and move any others to the trash. migrate_videos.py gives the
    sermon video the first image it finds there, so there must be only one. Returns False when
    the folder holds no image yet (the caller creates one)."""
    images = images_in_folder(service, folder_id)
    if not images:
        return False
    keep = next((f for f in images if f['name'] == original_filename), images[0])
    service.files().update(
        fileId=keep['id'],
        body={'name': original_filename},
        media_body=media,
        supportsAllDrives=True,
    ).execute()
    print(f"Replaced '{keep['name']}' in Drive with '{original_filename}' in place ({keep['id']}); "
          f"earlier versions stay in its version history.")
    for f in images:
        if f is keep:
            continue
        try:
            service.files().update(fileId=f['id'], body={'trashed': True}, supportsAllDrives=True).execute()
            print(f"Moved the older thumbnail '{f['name']}' to the Drive trash.")
        except Exception as e:
            print(f"Could not move '{f['name']}' to the trash: {e}")
    return True


def upload_to_drive(service, file_path, original_filename, folder_id, skip_if_exists=False, replace=False,
                    same_name=False):
    """replace: this image replaces every image in folder_id, a Sunday's dated thumbnail folder
    (see replace_images). Dashboard thumbnail uploads and title-graphics picks send
    {"replace": true} in their .meta.json sidecar.
    same_name: overwrite a file with this name in folder_id in place (title.txt, Description.txt)."""
    print(f"Uploading {original_filename} to Google Drive folder {folder_id}...")

    if skip_if_exists and not replace and identical_file_in_folder(service, folder_id, original_filename, file_path):
        print(f"An identical '{original_filename}' already exists in {folder_id}; skipping (already processed).")
        return True

    try:
        # Determine mimetype automatically if possible, otherwise default
        import mimetypes
        mime_type, _ = mimetypes.guess_type(file_path)
        if not mime_type:
            mime_type = 'application/octet-stream'

        media = MediaIoBaseUpload(io.BytesIO(open(file_path, "rb").read()), mimetype=mime_type, resumable=True)

        if replace:
            try:
                if replace_images(service, folder_id, original_filename, media):
                    return True
            except Exception as e:
                # Fall through to a plain create rather than losing the upload.
                print(f"Could not replace the thumbnail in {folder_id}: {e}")

        if same_name or is_protected_flyer(original_filename):
            existing_id = find_file_id_by_name(service, folder_id, original_filename)
            if existing_id:
                service.files().update(
                    fileId=existing_id,
                    media_body=media,
                    supportsAllDrives=True,
                ).execute()
                print(f"Replaced the existing '{original_filename}' in Drive "
                      f"in place ({existing_id}).")
                return True

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
            stream_time = '10:30'

            if not DRIVE_ID_PATTERN.match(folder_id):
                quarantine_file(file_path, filename,
                                 f"parsed folder_id '{folder_id}' doesn't look like a real Drive ID "
                                 f"(this would just fail the upload and get stuck retrying every run)")
                continue

            if folder_id in [THUMBNAILS_DEST_PARENT_FOLDER_ID, SERMON_DEST_PARENT_FOLDER_ID] and original_filename.lower().endswith(('.jpg', '.jpeg')):
                date_str = None
                stream_meta = None
                # Optional sidecar metadata: the upload dashboard's settings panel writes one
                # for worship service thumbnails (date/time/title/description). The dashboard
                # (both thumbnail zones) and the title-graphics picker (fccla/auto_build.py)
                # add {"replace": true}; the picker also sends "chosen_at", when it was picked.
                worship_meta_path = file_path + '.meta.json'
                if os.path.exists(worship_meta_path):
                    try:
                        with open(worship_meta_path, 'r') as mf:
                            stream_meta = json.load(mf)
                    except Exception as e:
                        print(f"Could not parse upload settings for {filename}: {e}")
                        stream_meta = None

                if folder_id == THUMBNAILS_DEST_PARENT_FOLDER_ID and stream_meta and stream_meta.get('auto'):
                    # Uploaded without the livestream settings touched: work out
                    # which service it is (see detect_service) rather than taking
                    # the panel's default on trust.
                    today = datetime.datetime.now(service_keys.TZ).date()
                    found = detect_service(file_path, original_filename, stream_meta, today)
                    if not found:
                        hold_unassigned(file_path, filename, worship_meta_path, today)
                        continue
                    stream_meta['date'], stream_meta['time'] = found

                if folder_id == SERMON_DEST_PARENT_FOLDER_ID:
                    # Sermon series thumbnails always target the upcoming Sunday folder
                    date_str = get_upcoming_sunday()
                    print(f"Sermon Series Thumbnail detected. Using upcoming Sunday date: {date_str}")
                else:
                    # Worship service thumbnails: prefer an explicit date (and
                    # optional time/title/description) from the sidecar over
                    # guessing the date from the filename.
                    if stream_meta and stream_meta.get('date'):
                        date_str = stream_meta['date']
                        print(f"Using date from upload settings: {date_str}")
                    else:
                        date_pattern = re.compile(r'(\d{1,4}[-/]\d{1,2}[-/]\d{1,4})')
                        match = date_pattern.search(original_filename)
                        if match:
                            date_str = match.group(1)
                            print(f"Found date {date_str} in {original_filename}")

                stream_time = (stream_meta.get('time', '').strip() if stream_meta else '') or '10:30'
                if date_str:
                    subfolder = (drive_folder_name(date_str, stream_time)
                                 if folder_id == THUMBNAILS_DEST_PARENT_FOLDER_ID else date_str)
                    print(f"Redirecting {original_filename} to subfolder '{subfolder}' of {folder_id}")
                    try:
                        new_folder_id = get_or_create_date_folder(drive_service, folder_id, subfolder)
                        if new_folder_id:
                            folder_id = new_folder_id
                    except Exception as e:
                        print(f"Error redirecting folder for {original_filename}: {e}")

            # skip_if_exists: if this queued file (for any reason) gets
            # processed a second time, an identical copy already in Drive
            # means "already done" -- don't create a duplicate. Only the
            # queued upload itself is guarded; the title.txt / Description.txt
            # uploads below are meant to refresh in place.
            # replace only ever applies inside a Sunday's dated subfolder: never to a parent
            # folder, where it would put every other image in the trash.
            replace = bool(stream_meta and stream_meta.get('replace')) and folder_id != parts[1]
            chosen_at = stream_meta.get('chosen_at') if replace else None
            newer = newer_image_in_folder(drive_service, folder_id, chosen_at) if chosen_at else None
            if newer:
                print(f"Not using {original_filename}: '{newer}' was uploaded for {date_str} after it "
                      f"was picked ({chosen_at}), so that one stays.")
                os.remove(file_path)
                if worship_meta_path and os.path.exists(worship_meta_path):
                    os.remove(worship_meta_path)
                continue
            if upload_to_drive(drive_service, file_path, original_filename, folder_id, skip_if_exists=True, replace=replace):
                deferred = False

                # If successful, check if it's a worship service thumbnail to create stream
                if parts[1] == THUMBNAILS_DEST_PARENT_FOLDER_ID and date_str:
                    # A title/description the uploader actually typed into the
                    # settings panel wins over the generated one (but see the
                    # stale-pre-fill guard just below).
                    meta_title = stream_meta.get('title', '').strip() if stream_meta else ''
                    meta_desc = stream_meta.get('description', '').strip() if stream_meta else ''

                    # The dashboard's Livestream Settings panel pre-fills Title and
                    # Description from the repo's *current* title.txt/Description.txt.
                    # A thumbnail uploaded before its OW is processed pre-fills
                    # with another service's values, and if the uploader doesn't edit them
                    # they arrive here looking like a deliberate override -- which used
                    # to get written straight onto the stream (last week's title + last
                    # week's dated OW link). Catch that: the description boilerplate
                    # always carries a dated fccla.org/ows/<date> link, so if it points
                    # at a different date than this upload, the "override" is a stale
                    # echo. Drop both fields and fall back to the generated / defer path.
                    target_date = parse_flexible_date(date_str)
                    override_ows_date = ows_link_date(meta_desc)
                    if meta_desc and target_date and override_ows_date and override_ows_date != target_date:
                        print(f"Ignoring title/description from upload settings for {date_str}: "
                              f"its program link is for {override_ows_date.isoformat()}, not this "
                              f"service -- treating it as a stale pre-fill and using the generated "
                              f"title/description pipeline instead.")
                        meta_title = ''
                        meta_desc = ''

                    # Which service this thumbnail is for: the OW for this date at
                    # this time (Christmas Eve has several), or the date's only one.
                    key = service_keys.resolve_key(date_str, stream_time)
                    ready, reason = service_keys.content_ready(key) if key else (False, "its OW hasn't been processed")
                    if not ready and not (meta_title and meta_desc):
                        # Usually the OW was posted moments ago and the hourly
                        # pipeline hasn't reached it: catch up now. Both scripts
                        # only do work that's outstanding, so this is cheap when
                        # there's nothing new.
                        print(f"Title/description for {date_str} {stream_time} aren't ready ({reason}). "
                              f"Running the title and description generators...")
                        import subprocess
                        for cmd, cwd in ((["python", "worship workflows/update_service_titles.py"], "Worship Scripts"),
                                         (["python", "generate_youtube_description.py"], "Youtube Processing")):
                            try:
                                subprocess.run(cmd, check=True, cwd=cwd)
                            except Exception as e:
                                print(f"Error running {cmd[-1]}: {e}")
                        key = service_keys.resolve_key(date_str, stream_time)
                        ready, reason = service_keys.content_ready(key) if key else (False, "its OW hasn't been processed")

                    override_title, override_desc = complete_override(key, meta_title, meta_desc)

                    if not ready and not override_title:
                        # The OW for this service still isn't posted, so there's
                        # nothing real to give this stream yet. Stash the
                        # thumbnail instead of creating a stream with a
                        # stale or generic placeholder title/description --
                        # "Youtube Processing/create_pending_stream.py"
                        # (chained off the same hourly title/description
                        # pipeline) finishes this once the real content lands.
                        print(f"Title/description for {date_str} {stream_time} aren't ready yet ({reason}). "
                              f"Deferring stream creation until they are.")
                        defer_stream_creation(file_path, date_str, stream_time,
                                              title=meta_title or None, description=meta_desc or None)
                        deferred = True
                    else:
                        import subprocess
                        upload_title_and_description(drive_service, folder_id, date_str,
                                                     title=override_title, description=override_desc, key=key)

                        print(f"Launching create_youtube_stream.py for {date_str} at {stream_time}...")
                        script_path = os.path.join("Youtube Processing", "create_youtube_stream.py")
                        if os.path.exists(script_path):
                            # We keep the file around so create_youtube_stream can upload it as thumbnail.
                            cmd = ["python", script_path, date_str, file_path, stream_time]
                            if override_title and override_desc:
                                # Human-entered override: pass it through so
                                # create_youtube_stream.py trusts it as-is
                                # rather than re-checking the generated text.
                                cmd += ["--title", override_title, "--description", override_desc]
                            if replace:
                                # A replacement thumbnail: the stream may exist already
                                # with the earlier one, so have it re-uploaded rather
                                # than just skipped.
                                cmd.append("--reconcile")
                            try:
                                result = subprocess.run(cmd)
                                rc = result.returncode
                            except Exception as e:
                                print(f"Error running create_youtube_stream.py: {e}")
                                rc = 1
                            # EXIT_NOT_READY (3) = the freshness gate refused because
                            # this service's title/description isn't confirmed yet;
                            # EXIT_THUMBNAIL_PENDING (4) = the stream was created but
                            # the thumbnail didn't stick to it; any other non-zero = a
                            # real failure. In every case, don't drop the thumbnail --
                            # stash it so the pending-stream run retries. For 4 that
                            # retry reconciles the stream that now exists rather than
                            # creating another one.
                            if rc != 0:
                                if rc == 3:
                                    print(f"create_youtube_stream.py's freshness gate refused {date_str} {stream_time}; deferring.")
                                elif rc == 4:
                                    print(f"The stream for {date_str} {stream_time} was created but its thumbnail was not set; "
                                          f"stashing the image so the pending-stream run retries it.")
                                else:
                                    print(f"create_youtube_stream.py failed (exit {rc}); deferring so it retries.")
                                defer_stream_creation(file_path, date_str, stream_time,
                                                      title=meta_title or None, description=meta_desc or None)
                                deferred = True
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
