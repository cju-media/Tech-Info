"""
Cleans up the "Events_Ads" Google Drive folder (the flat folder the upload
dashboard's Events Ad zone drops flyers into -- see Utilities/uploads/index.html)
by moving flyers for events that have already happened to Drive's Trash.

There's no date metadata anywhere for these files: filenames are arbitrary
(whatever the uploader's file was called) and Drive's own "modified" time is
just the upload time, not the event date. The only place the date lives is
printed as text on the flyer graphic itself, so this asks Gemini's vision
model to read it off each image.

Files are TRASHED, never permanently deleted -- Drive keeps trashed items
for ~30 days, so a misread is recoverable. Anything Gemini can't confidently
find a date on (an evergreen graphic, an unusual layout) is left alone.

Google Drive only lets a personal (non-Shared-Drive) file's OWNER trash or
delete it -- Editor/writer access, however it's granted, does not include
canTrash/canDelete (confirmed via the API's own reported capabilities after
a real grant still 403'd). So flyers uploaded straight into Drive by a
person (rather than through the dashboard pipeline, which uploads as this
script's own Drive account and therefore owns what it uploads) can never be
auto-trashed. For those, this notifies Cameron once via iMessage + email
with the filename and a link so he can delete it by hand, then remembers
that file's ID (events_cleanup_notified_ids.json) so it doesn't nag every
day the file remains stuck.

Auth: same pattern as upload_queue_to_drive.py -- prefers the service
account (GDRIVE_SERVICE_ACCOUNT_JSON), which has been explicitly granted
edit access on this folder, over GDRIVE_OAUTH_JSON.
"""

import os
import re
import json
import smtplib
import datetime
import zoneinfo
import requests
import time
from email.message import EmailMessage

from google import genai
from google.genai import types
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials

EVENTS_FOLDER_ID = '17-0kiqBKa0k5ofW6gOPrVbHl7nqanuQz'
NOTIFIED_STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'events_cleanup_notified_ids.json')

DATE_PROMPT = """This image is a promotional flyer for a church event, posted to a Google Drive folder that a human periodically clears out once the event has passed.

Find the calendar date(s) the event actually takes place, printed somewhere on the flyer (e.g. "Sunday, August 9th, 2026"). If the flyer spans multiple days (e.g. a multi-day series or a date range), use the LAST day mentioned.

Respond with ONLY compact JSON, no markdown fences, no commentary, matching exactly this schema:
{"has_date": true or false, "last_date": "YYYY-MM-DD" or null}

Set "has_date" to false (and "last_date" to null) if the flyer has no explicit calendar date on it -- e.g. an evergreen graphic like a generic "Give" or "Welcome" ad, or a recurring/ongoing announcement with no specific date."""


def get_drive_service():
    # Prefer the service account (drivereader@worship-scripts-fetcher) over
    # GDRIVE_OAUTH_JSON here: it's been explicitly granted edit access on the
    # Events_Ads folder specifically so this script can trash files it
    # doesn't own (uploaded by a person directly, not through the pipeline) --
    # something the OAuth account was denied with insufficientFilePermissions
    # (see the Aug 2026 run). Other scripts in this repo (e.g.
    # upload_queue_to_drive.py) still need GDRIVE_OAUTH_JSON's user quota for
    # *creating* files, so this preference is local to this script.
    service_account_json = os.environ.get('GDRIVE_SERVICE_ACCOUNT_JSON')
    if service_account_json:
        try:
            creds_dict = json.loads(service_account_json)
            creds = service_account.Credentials.from_service_account_info(
                creds_dict, scopes=['https://www.googleapis.com/auth/drive']
            )
            print(f"Using GDRIVE_SERVICE_ACCOUNT_JSON for authentication ({creds_dict.get('client_email')}).")
            return build('drive', 'v3', credentials=creds)
        except Exception as e:
            print(f"Error parsing GDRIVE_SERVICE_ACCOUNT_JSON: {e}")

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

    print("Warning: Neither GDRIVE_SERVICE_ACCOUNT_JSON nor GDRIVE_OAUTH_JSON is set.")
    return None


def list_image_files(service, folder_id):
    query = f"'{folder_id}' in parents and trashed=false and mimeType contains 'image/'"
    files = []
    page_token = None
    while True:
        results = service.files().list(
            q=query, spaces='drive', fields='nextPageToken, files(id, name, mimeType)',
            pageToken=page_token, supportsAllDrives=True, includeItemsFromAllDrives=True,
        ).execute()
        files.extend(results.get('files', []))
        page_token = results.get('nextPageToken')
        if not page_token:
            break
    return files


def download_file(service, file_id):
    return service.files().get_media(fileId=file_id, supportsAllDrives=True).execute()


def extract_event_date(client, image_bytes, mime_type):
    """Ask Gemini to read the event date off a flyer image. Returns a
    datetime.date, or None if it couldn't find one (or the call failed)."""
    try:
        response = client.models.generate_content(
            model='gemini-3.5-flash',
            contents=[
                types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
                DATE_PROMPT,
            ],
        )
        if not response or not response.text:
            print("  No response from Gemini.")
            return None

        text = response.text.strip()
        # Strip markdown fences if the model added them despite instructions.
        text = re.sub(r'^```(?:json)?\s*|\s*```$', '', text.strip())

        data = json.loads(text)
        if not data.get('has_date'):
            print("  Gemini found no explicit date on this flyer; leaving it alone.")
            return None

        return datetime.date.fromisoformat(data['last_date'])
    except Exception as e:
        print(f"  Could not determine a date for this flyer: {e}")
        return None


def load_notified_state():
    if os.path.exists(NOTIFIED_STATE_PATH):
        try:
            with open(NOTIFIED_STATE_PATH, 'r') as f:
                return json.load(f)
        except Exception as e:
            print(f"Could not read notified-state file, starting fresh: {e}")
    return {}


def save_notified_state(state):
    with open(NOTIFIED_STATE_PATH, 'w') as f:
        json.dump(state, f, indent=2, sort_keys=True)


def dispatch_event(event_type, client_payload, max_retries=4, backoff_seconds=3):
    """Fire a repository_dispatch event so imessage_notifications.yml can
    send an iMessage from the self-hosted macOS runner (this job runs on
    ubuntu, which can't). Mirrors upload_queue_to_drive.py.

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


def get_smtp_credentials():
    return {
        'email': os.environ.get('SMTP_EMAIL'),
        'password': os.environ.get('SMTP_PASSWORD'),
        'server': os.environ.get('SMTP_SERVER', 'smtp.gmail.com'),
        'port': int(os.environ.get('SMTP_PORT', 587)),
    }


def send_manual_deletion_email(file_name, file_url):
    creds = get_smtp_credentials()
    to_email = "cameron@cju.media"
    cc_email = "cjohnston@fccla.org"

    subject = f"Action needed: delete '{file_name}' from Events_Ads"
    body = (
        f"Hi Cameron,\n\n"
        f"The Events_Ads cleanup automation found a passed event flyer it can't delete itself "
        f"(Google Drive only lets a personal file's owner trash it, even with Editor access):\n\n"
        f"  {file_name}\n  {file_url}\n\n"
        f"Please delete it manually.\n\nBest,\nCam-Bot"
    )

    msg = EmailMessage()
    msg['Subject'] = subject
    msg['From'] = creds['email'] or "dry-run@example.com"
    msg['To'] = to_email
    msg['Cc'] = cc_email
    msg.set_content(body)

    if not creds['email'] or not creds['password']:
        print(f"DRY RUN (no SMTP creds): Would send manual-deletion email to {to_email} (CC: {cc_email})")
        print(f"Subject: {subject}\nBody:\n{body}")
        return

    try:
        with smtplib.SMTP(creds['server'], creds['port']) as server:
            server.starttls()
            server.login(creds['email'], creds['password'])
            server.send_message(msg, to_addrs=[to_email, cc_email])
        print(f"Successfully sent manual-deletion email to {to_email} (CC: {cc_email}).")
    except Exception as e:
        print(f"Failed to send manual-deletion email: {e}")


def notify_manual_deletion_needed(file_name, file_id):
    file_url = f"https://drive.google.com/file/d/{file_id}/view"
    dispatch_event('events_folder_manual_deletion_needed', {'file_name': file_name, 'file_url': file_url})
    send_manual_deletion_email(file_name, file_url)


def main():
    is_dry_run = os.environ.get('DRY_RUN', '1') == '1'
    tz = zoneinfo.ZoneInfo("America/Los_Angeles")
    today = datetime.datetime.now(tz).date()

    api_key = os.environ.get('GEMINI_API_KEY')
    if not api_key:
        print("GEMINI_API_KEY environment variable is missing.")
        return

    service = get_drive_service()
    if not service:
        print("No Drive service available; aborting.")
        return

    gemini_client = genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(timeout=600000)
    )

    print(f"Checking Events_Ads folder for passed events as of {today} (America/Los_Angeles)...")
    files = list_image_files(service, EVENTS_FOLDER_ID)
    notified_state = load_notified_state()

    if not files:
        print("No image files found in the folder.")
        save_notified_state({})  # nothing left in the folder, so nothing to remember
        return

    print(f"Found {len(files)} image file(s) to check.")

    trashed_count = 0
    for f in files:
        print(f"- {f['name']} ({f['id']})")
        try:
            image_bytes = download_file(service, f['id'])
        except HttpError as e:
            print(f"  Failed to download: {e}")
            continue

        event_date = extract_event_date(gemini_client, image_bytes, f['mimeType'])
        if event_date is None:
            continue

        if event_date >= today:
            print(f"  Event date {event_date} hasn't passed yet; leaving it alone.")
            continue

        print(f"  Event date {event_date} has passed.")
        if is_dry_run:
            print(f"  DRY RUN: would move '{f['name']}' to Trash. Set DRY_RUN=0 to apply.")
            continue

        try:
            service.files().update(fileId=f['id'], body={'trashed': True}, supportsAllDrives=True).execute()
            print(f"  Moved '{f['name']}' to Trash.")
            trashed_count += 1
        except HttpError as e:
            print(f"  Failed to trash '{f['name']}': {e}")
            # Print exactly what Drive thinks our access looks like, instead
            # of guessing blind at the cause of insufficientFilePermissions.
            try:
                meta = service.files().get(
                    fileId=f['id'],
                    fields='owners(emailAddress),permissions(emailAddress,role,type),capabilities(canTrash,canDelete,canEdit)',
                    supportsAllDrives=True,
                ).execute()
                print(f"  Diagnostic - owners: {meta.get('owners')}")
                print(f"  Diagnostic - permissions: {meta.get('permissions')}")
                print(f"  Diagnostic - capabilities: {meta.get('capabilities')}")
            except HttpError as diag_e:
                print(f"  Diagnostic fetch also failed: {diag_e}")

            if f['id'] in notified_state:
                print(f"  Already notified about '{f['name']}' on {notified_state[f['id']].get('notified_at')}; not re-notifying.")
            else:
                print(f"  Notifying Cameron that '{f['name']}' needs to be deleted manually...")
                notify_manual_deletion_needed(f['name'], f['id'])
                notified_state[f['id']] = {'name': f['name'], 'notified_at': today.isoformat()}

    # Forget any file we'd previously flagged that's no longer in the folder
    # (it got deleted, one way or another) so a reused Drive ID wouldn't be
    # mistaken for the old one, and the state file doesn't grow forever.
    current_ids = {f['id'] for f in files}
    notified_state = {fid: info for fid, info in notified_state.items() if fid in current_ids}
    save_notified_state(notified_state)

    print(f"Done. Trashed {trashed_count} file(s)." if not is_dry_run else "Done (dry run).")


if __name__ == "__main__":
    main()
