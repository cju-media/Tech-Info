import os
import sys
import json
import re
import requests
import datetime
import zoneinfo
import pypdf
import io
import shutil
from google import genai
from google.genai import types
from googleapiclient.discovery import build
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials
from googleapiclient.http import MediaIoBaseUpload

import hymn_lyrics
import service_keys

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


def is_stream_live():
    """Return True if a YouTube broadcast is live right now, False if not,
    or None when YouTube can't be asked (no credentials, API error)."""
    creds_json = os.environ.get('YOUTUBE_CREDENTIALS_JSON')
    if not creds_json:
        print("YOUTUBE_CREDENTIALS_JSON not set; can't check for a live stream.")
        return None
    try:
        creds = Credentials.from_authorized_user_info(json.loads(creds_json))
        youtube = build('youtube', 'v3', credentials=creds)
        response = youtube.liveBroadcasts().list(
            part='id,snippet,status',
            broadcastStatus='active',
            broadcastType='all',
            maxResults=5
        ).execute()
    except Exception as e:
        print(f"Error checking YouTube for a live stream: {e}")
        return None
    items = response.get('items', [])
    for b in items:
        print(f"Live broadcast in progress: {b['snippet'].get('title')} ({b['id']})")
    return bool(items)


# Old single-service runs wrote straight into service-titles/; now each OW is
# generated into its own Worship Scripts/services/<key>/ folder (see
# service_keys.py) and service-titles/ is the "live set", a copy of whichever
# service is next. The paths below are relative to "Worship Scripts", where the
# workflow runs this.
LIVE_TITLES_DIR = "service-titles"
LIVE_STATE_FILE = "service_titles_state.json"
OW_API_URL = "https://api.github.com/repos/cju-media/OW/contents/OWs"


def coming_sunday(now_pt):
    return (now_pt + datetime.timedelta(days=6 - now_pt.weekday())).date()


def pick_ows(ows_contents, today, ow_filename=None, sunday=None):
    """{service key: OW listing item} for the OWs to keep generated.

    Every OW dated from today through LOOKAHEAD_DAYS out, so OWs uploaded
    weeks early (all of Christmas Eve's, and the Sunday after) are ready long
    before their services. Several files for one service (a draft and a final
    that the index hasn't deduped yet) resolve to the final, else a non-draft.
    OW_FILENAME pins one exact PDF instead (manual dispatch); one with no
    readable date in its name is taken as the coming Sunday's, as before."""
    pdfs = [o for o in ows_contents if o.get("name", "").lower().endswith(".pdf")]
    if ow_filename:
        item = next((o for o in pdfs if o["name"] == ow_filename), None)
        if not item:
            return {}
        parsed = service_keys.parse_ow_filename(item["name"])
        date, time = parsed if parsed else (sunday, None)
        return {service_keys.service_key(date, time): item}

    last = today + datetime.timedelta(days=service_keys.LOOKAHEAD_DAYS)
    by_key = {}
    for item in pdfs:
        parsed = service_keys.parse_ow_filename(item["name"])
        if not parsed or not (today <= parsed[0] <= last):
            continue
        by_key.setdefault(service_keys.service_key(*parsed), []).append(item)

    def rank(item):
        name = item["name"].lower()
        return ("final" not in name, "draft" in name, item["name"])

    return {key: sorted(items, key=rank)[0] for key, items in by_key.items()}


def generate_service(key, item, headers, api_key, prompt_template):
    """Run one OW through Gemini into services/<key>/service-titles/ and record
    which OW it came from. Returns True when the folder was (re)written."""
    date, time = service_keys.parse_service_key(key)
    label = key if not time else f"{date.isoformat()} {service_keys.display_time(time)}"
    print(f"Generating service titles for {label} from {item['name']}...")

    try:
        pdf_response = requests.get(item["download_url"], headers=headers)
        pdf_response.raise_for_status()
        pdf_content = pdf_response.content
    except Exception as e:
        print(f"Error downloading {item['name']}: {e}")
        return False

    pdf_text = ""
    try:
        reader = pypdf.PdfReader(io.BytesIO(pdf_content))
        for page in reader.pages:
            text = page.extract_text()
            if text:
                pdf_text += text + "\n"
    except Exception as e:
        print(f"Error reading {item['name']}: {e}")
        return False

    prompt = f"{prompt_template}\n\nPDF Text:\n{pdf_text}"
    print("Sending prompt to Gemini...")
    client = genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(timeout=600000)
    )

    model_name = 'gemini-3.5-flash'

    try:
        gemini_response = client.models.generate_content(
            model=model_name,
            contents=prompt
        )
        if not gemini_response or not gemini_response.text:
            print("No response from Gemini.")
            return False

        result_text = gemini_response.text.strip()
    except Exception as e:
        print(f"Error querying Gemini: {e}")
        return False

    print("Parsing Gemini output and updating text files...")

    titles_dir = service_keys.titles_dir(key)
    os.makedirs(titles_dir, exist_ok=True)

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

            # Ensure trailing colons don't break file names (e.g. "title:" or "prelude1:")
            if file_key.endswith(":"):
                file_key = file_key[:-1]

            # Specifically handle case-insensitive "title" formatting if Gemini capitalizes it
            if file_key.lower() == "title":
                file_key = "title"

            filename = f"{file_key}.txt"
            filepath = os.path.join(titles_dir, filename)

            with open(filepath, "w") as f:
                f.write(content.strip())

    # Hymn lyrics, straight from the PDF text (not Gemini, so the words are exactly as printed),
    # for the ProPresenter Mac's sync_propresenter_hymns.py
    for lyric_key, verses in hymn_lyrics.extract(pdf_text).items():
        with open(os.path.join(titles_dir, f"{lyric_key}-lyrics.txt"), "w") as f:
            f.write(hymn_lyrics.to_file_text(verses))
        print(f"Wrote {lyric_key}-lyrics.txt ({len(verses)} verses)")

    service_keys.write_state(key, {
        "key": key,
        "date": date.isoformat(),
        "time": time,
        "ow_filename": item["name"],
        "ow_sha": item["sha"],
        "ow_key": service_keys.ow_index_key(date, time),
        "generated_at": datetime.datetime.now(service_keys.TZ).isoformat(timespec="seconds"),
    })
    return True


def remove_withdrawn_services(picked, today):
    """Drop the folder of an upcoming service whose OW is gone from cju-media/OW
    (deleted, or renamed to another time -- "5pm" corrected to "5:30pm"), so it
    can't become the live set or give a stream its text. Past services keep
    theirs for the archive."""
    last = today + datetime.timedelta(days=service_keys.LOOKAHEAD_DAYS)
    for key, date, _ in service_keys.list_services():
        if today <= date <= last and key not in picked:
            print(f"The OW for {key} is no longer in cju-media/OW; removing its generated folder.")
            shutil.rmtree(service_keys.service_dir(key), ignore_errors=True)


def drive_missing_live_files():
    drive_service = get_drive_service()
    if not drive_service:
        return False
    try:
        query = f"'{DRIVE_FOLDER_ID}' in parents and trashed = false"
        results = drive_service.files().list(q=query, supportsAllDrives=True, includeItemsFromAllDrives=True, fields="files(name)").execute()
        drive_files = [f['name'] for f in results.get('files', [])]
        for lf in os.listdir(LIVE_TITLES_DIR):
            if lf.endswith('.txt') and lf not in drive_files:
                print(f"File {lf} missing in Drive. Re-uploading the live set.")
                return True
    except Exception as e:
        print(f"Error checking drive: {e}")
    return False


def promote_live_set(now_pt, force_update):
    """Copy the next service into service-titles/ (and its state file) when it
    isn't there already, and upload it to Drive for ProPresenter."""
    key = service_keys.current_service(now_pt)
    if not key:
        print("No upcoming service has been generated yet; leaving service-titles/ as it is.")
        return

    state = service_keys.read_state(key)
    live_state = {}
    if os.path.exists(LIVE_STATE_FILE):
        try:
            with open(LIVE_STATE_FILE, "r") as f:
                live_state = json.load(f)
        except json.JSONDecodeError:
            pass

    same = (live_state.get("key") == key
            and live_state.get("last_processed_sha") == state.get("ow_sha")
            and os.path.isdir(LIVE_TITLES_DIR))
    if same and not force_update:
        if not drive_missing_live_files():
            print(f"service-titles/ already holds {key} and Drive has every file. No updates needed.")
            return
    else:
        print(f"Making {key} the live service ({state.get('ow_filename')}).")
        service_keys.copy_titles(service_keys.titles_dir(key), LIVE_TITLES_DIR)
        # Timings are no longer cleared here: the ones pushed after a stream
        # are filed under that stream's own service by update_youtube_stream.py,
        # so a new live service can't pick up the last one's.
        # target_date (and the new target_time/key) record which service this
        # copy is for, so downstream consumers (create_sermon_series.py, the
        # dashboard) can tell fresh content from stale leftovers.
        live_state = {
            "last_processed_sha": state.get("ow_sha"),
            "target_date": state.get("date"),
            "target_time": state.get("time"),
            "key": key,
        }
        try:
            with open(LIVE_STATE_FILE, "w") as f:
                json.dump(live_state, f)
            print("Updated state file successfully.")
        except Exception as e:
            print(f"Error writing state file: {e}")
            return

    print("Uploading the live service-title files to Google Drive...")
    drive_service = get_drive_service()
    if drive_service:
        for filename in os.listdir(LIVE_TITLES_DIR):
            if filename.endswith(".txt"):
                upload_to_drive(drive_service, os.path.join(LIVE_TITLES_DIR, filename), filename)
    else:
        print("Skipping Google Drive upload due to missing credentials.")

    print("Update complete.")
    with open(".sync_success", "w") as f:
        f.write("true")


def main():
    tz = zoneinfo.ZoneInfo("America/Los_Angeles")
    now_pt = datetime.datetime.now(tz)
    today = now_pt.date()

    # OW_FILENAME pins an exact PDF in cju-media/OW/OWs (manual dispatch), for
    # when the upload isn't named for its date.
    ow_filename = (os.environ.get("OW_FILENAME") or "").strip()
    force_update = str(os.environ.get("FORCE_UPDATE", "false")).lower() == "true"

    headers = {}
    github_token = os.environ.get("GITHUB_TOKEN")
    if github_token:
        headers["Authorization"] = f"Bearer {github_token}"

    try:
        api_response = requests.get(OW_API_URL, headers=headers)
        api_response.raise_for_status()
        ows_contents = api_response.json()
    except Exception as e:
        print(f"Error fetching OWs contents: {e}")
        return

    picked = pick_ows(ows_contents, today, ow_filename, coming_sunday(now_pt))
    if ow_filename and not picked:
        print(f"No PDF named {ow_filename} in OWs. Exiting.")
        return
    if not ow_filename:
        remove_withdrawn_services(picked, today)
    listing = ", ".join(f"{k} ({v['name']})" for k, v in sorted(picked.items()))
    print(f"OWs to keep generated: {listing or 'none'}")

    todo = {}
    for key, item in sorted(picked.items()):
        state = service_keys.read_state(key) or {}
        if state.get("ow_sha") == item["sha"] and not force_update:
            continue
        todo[key] = item

    if todo:
        try:
            with open("worship-prompt.txt", "r") as f:
                prompt_template = f.read()
        except Exception as e:
            print(f"Error reading prompt file worship-prompt.txt: {e}")
            return
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            print("GEMINI_API_KEY environment variable is missing.")
            return
        for key, item in todo.items():
            # One OW failing (a bad PDF, a Gemini hiccup) mustn't hold up the
            # others; it's retried on the next hourly run.
            generate_service(key, item, headers, api_key, prompt_template)
    else:
        print("Every upcoming OW is already generated.")

    # Never swap the live title files out while a service is streaming live
    # (generating the per-service folders above is fine: nothing live reads
    # them). If YouTube can't be asked, fall back to the old "no updates on
    # Sunday" rule.
    live = is_stream_live()
    if live:
        print("A live stream is in progress. Not changing service-titles/.")
        return
    if live is None and now_pt.weekday() == 6:
        print("Couldn't confirm the stream is off and today is Sunday. Not changing service-titles/.")
        return

    promote_live_set(now_pt, force_update)


if __name__ == "__main__":
    main()
