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
import sys
import json
import smtplib
import datetime
import zoneinfo
import time
from email.message import EmailMessage

# The third-party stack is only needed for the Drive/Gemini/GitHub calls in
# main() and its helpers. Guard the imports so the pure helpers
# (parse_drive_created_date, extract_event_date's parsing, the misread-year
# check) stay importable for unit tests without the full client libraries.
# main() genuinely needs them and fails loudly at call time if they're absent.
try:
    import requests
    from google import genai
    from google.genai import types
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    from google.oauth2 import service_account
    from google.oauth2.credentials import Credentials
except ImportError:
    pass

EVENTS_FOLDER_ID = '17-0kiqBKa0k5ofW6gOPrVbHl7nqanuQz'
NOTIFIED_STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'events_cleanup_notified_ids.json')

# Gemini reads the event date off the flyer graphic, and flyers often print
# the date with no year ("Friday, September 5"), so the model has to guess it
# -- and sometimes guesses a past year for an upcoming event (this fired four
# bogus "delete this" alerts on 2026-08-31). Drive's createdTime is the upload
# time, so a flyer whose read event date lands this many days *before* it was
# uploaded is almost certainly a misread year: skip it rather than act on it.
SUSPECT_MISREAD_DAYS = 180


class FlyerDateReadError(Exception):
    """The Gemini API call to read a flyer's date failed at the transport/quota
    layer (429, 5xx, network, timeout) -- distinct from the call succeeding but
    finding no usable date, which is a plain None."""

def build_date_prompt(today):
    """The flyer date prompt, anchored to today so the model can resolve a
    year-less date ("Friday, September 5") to the right year instead of
    guessing one at random -- guessed past years were firing bogus
    "delete this" alerts (2026-08-31)."""
    return f"""This image is a promotional flyer for a church event, posted to a Google Drive folder that a human periodically clears out once the event has passed.

Today's date is {today:%Y-%m-%d}.

Find the calendar date(s) the event actually takes place, printed somewhere on the flyer (e.g. "Sunday, August 9th, 2026"). If the flyer spans multiple days (e.g. a multi-day series or a date range), use the LAST day mentioned.

Many flyers print only a month and day with no year (e.g. "Friday, September 5"). When the year is not printed, infer the one that places the event closest to today's date: these flyers are for an event in the coming weeks or one that happened in the last few weeks, never one years in the past or future. Do not default to the current year if a nearby year fits better, and never return a date more than a year away from today unless that year is explicitly printed on the flyer.

Respond with ONLY compact JSON, no markdown fences, no commentary, matching exactly this schema:
{{"has_date": true or false, "last_date": "YYYY-MM-DD" or null}}

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
            q=query, spaces='drive', fields='nextPageToken, files(id, name, mimeType, createdTime)',
            pageToken=page_token, supportsAllDrives=True, includeItemsFromAllDrives=True,
        ).execute()
        files.extend(results.get('files', []))
        page_token = results.get('nextPageToken')
        if not page_token:
            break
    return files


def download_file(service, file_id):
    return service.files().get_media(fileId=file_id, supportsAllDrives=True).execute()


def extract_event_date(client, image_bytes, mime_type, today):
    """Ask Gemini to read the event date off a flyer image.

    Returns a datetime.date, or None if the call succeeded but there's no
    usable date (evergreen graphic, or the model's response didn't parse).
    Raises FlyerDateReadError if the API call itself failed -- so the caller
    can tell "no date here" apart from "we never got to look"."""
    try:
        response = client.models.generate_content(
            model='gemini-3.5-flash',
            contents=[
                types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
                build_date_prompt(today),
            ],
        )
    except Exception as e:
        raise FlyerDateReadError(str(e)) from e

    if not response or not response.text:
        print("  No response from Gemini.")
        return None

    text = response.text.strip()
    # Strip markdown fences if the model added them despite instructions.
    text = re.sub(r'^```(?:json)?\s*|\s*```$', '', text.strip())

    try:
        data = json.loads(text)
        if not data.get('has_date'):
            print("  Gemini found no explicit date on this flyer; leaving it alone.")
            return None
        return datetime.date.fromisoformat(data['last_date'])
    except (ValueError, TypeError, KeyError) as e:
        print(f"  Could not parse a date out of Gemini's response ({e!r}); leaving it alone.")
        return None


def parse_drive_created_date(created_time):
    """Drive's createdTime is RFC 3339 (e.g. '2026-08-29T12:34:56.789Z').
    Return just the calendar date, or None if it's missing/unparseable."""
    if not created_time or not isinstance(created_time, str):
        return None
    try:
        return datetime.date.fromisoformat(created_time[:10])
    except ValueError:
        return None


def is_suspected_misread(event_date, created_date):
    """True if a flyer's Gemini-read event date lands so far before the file
    was uploaded to Drive that it's almost certainly a wrong-year read rather
    than a genuinely-passed event (nobody uploads a flyer months after the
    event). Returns False when we can't tell (no upload date)."""
    if event_date is None or created_date is None:
        return False
    return (created_date - event_date).days > SUSPECT_MISREAD_DAYS


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


def send_alert_email(subject, body):
    creds = get_smtp_credentials()
    to_email = "cameron@cju.media"
    cc_email = "cjohnston@fccla.org"

    msg = EmailMessage()
    msg['Subject'] = subject
    msg['From'] = creds['email'] or "dry-run@example.com"
    msg['To'] = to_email
    msg['Cc'] = cc_email
    msg.set_content(body)

    if not creds['email'] or not creds['password']:
        print(f"DRY RUN (no SMTP creds): Would email {to_email} (CC: {cc_email})")
        print(f"Subject: {subject}\nBody:\n{body}")
        return

    try:
        with smtplib.SMTP(creds['server'], creds['port']) as server:
            server.starttls()
            server.login(creds['email'], creds['password'])
            server.send_message(msg, to_addrs=[to_email, cc_email])
        print(f"Successfully emailed {to_email} (CC: {cc_email}).")
    except Exception as e:
        print(f"Failed to send email: {e}")


def notify_manual_deletion_needed(file_name, file_id):
    file_url = f"https://drive.google.com/file/d/{file_id}/view"
    dispatch_event('events_folder_manual_deletion_needed', {'file_name': file_name, 'file_url': file_url})
    send_alert_email(
        f"Action needed: delete '{file_name}' from Events_Ads",
        f"Hi Cameron,\n\n"
        f"The Events_Ads cleanup automation found a passed event flyer it can't delete itself "
        f"(Google Drive only lets a personal file's owner trash it, even with Editor access):\n\n"
        f"  {file_name}\n  {file_url}\n\n"
        f"Please delete it manually.\n\nBest,\nCam-Bot"
    )


def notify_cleanup_failed(summary):
    """The run couldn't read any flyer dates (usually Gemini quota/billing).
    Without this the job just exits green -- which masked ~12 days of quota
    exhaustion in Aug 2026."""
    dispatch_event('events_folder_cleanup_failed', {'message': summary})
    send_alert_email(
        "Events_Ads cleanup failed to read any flyers",
        f"Hi Cameron,\n\n{summary}\n\n"
        f"The folder was NOT cleaned this run. Most often this is the Gemini API key "
        f"being out of quota/credits -- check billing at https://ai.studio/ .\n\nBest,\nCam-Bot"
    )


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
    attempted_reads = 0
    read_failures = 0
    for f in files:
        print(f"- {f['name']} ({f['id']})")
        try:
            image_bytes = download_file(service, f['id'])
        except HttpError as e:
            print(f"  Failed to download: {e}")
            continue

        attempted_reads += 1
        try:
            event_date = extract_event_date(gemini_client, image_bytes, f['mimeType'], today)
        except FlyerDateReadError as e:
            read_failures += 1
            print(f"  Could not reach Gemini to read this flyer: {e}")
            continue

        if event_date is None:
            continue

        created_date = parse_drive_created_date(f.get('createdTime'))
        if is_suspected_misread(event_date, created_date):
            print(f"  Gemini read an event date of {event_date}, but this file wasn't uploaded "
                  f"until {created_date} -- {(created_date - event_date).days} days later. That's "
                  f"almost certainly a misread year, so skipping it (no trash, no alert).")
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

    # If every flyer we managed to hand to Gemini came back as an API failure,
    # the run did nothing -- fail loudly (red X + alert) instead of exiting
    # green, which is how ~12 days of quota exhaustion went unnoticed in Aug
    # 2026.
    if attempted_reads and read_failures == attempted_reads:
        summary = (f"Events_Ads cleanup could not read a date from any of {attempted_reads} "
                   f"flyer(s) -- every Gemini call failed.")
        print(f"\n{summary}")
        if not is_dry_run:
            notify_cleanup_failed(summary)
        sys.exit(1)

    if read_failures:
        print(f"Note: {read_failures} of {attempted_reads} flyer(s) couldn't be read this run; "
              f"they'll be retried next run.")

    print(f"Done. Trashed {trashed_count} file(s)." if not is_dry_run else "Done (dry run).")


if __name__ == "__main__":
    main()
