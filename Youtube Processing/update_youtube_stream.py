import os
import json
import sys
import hashlib
import subprocess
import dateutil.parser
from datetime import datetime, timedelta
import pytz
import re
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google.auth.exceptions import RefreshError

PLAYLIST_ID = "PLGtiSp5WvUc_I0M_vvfSdGY9dJ43ZofXs"
PUSH_STATE_FILE = "description_push_state.json"

def get_youtube_service():
    creds_json = os.environ.get('YOUTUBE_CREDENTIALS_JSON')
    if not creds_json:
        print("Error: YOUTUBE_CREDENTIALS_JSON environment variable not found.")
        return None

    try:
        creds_info = json.loads(creds_json)
        creds = Credentials.from_authorized_user_info(creds_info)
        service = build('youtube', 'v3', credentials=creds)
        return service
    except Exception as e:
        print(f"Error authenticating with YouTube: {e}")
        return None

def get_upcoming_streams(service):
    upcoming_streams = []

    try:
        playlist_response = service.playlistItems().list(
            part='snippet',
            playlistId=PLAYLIST_ID,
            maxResults=50
        ).execute()

        video_ids = [item['snippet']['resourceId']['videoId'] for item in playlist_response.get('items', [])]
        if not video_ids:
            return upcoming_streams

        video_response = service.videos().list(
            part='snippet,liveStreamingDetails',
            id=','.join(video_ids)
        ).execute()

        for video in video_response.get('items', []):
            snippet = video['snippet']

            # Allow updating both upcoming AND live/recently ended broadcasts if pushed
            if snippet.get('liveBroadcastContent') in ['upcoming', 'live', 'none'] and 'liveStreamingDetails' in video:
                scheduled_start_time = video['liveStreamingDetails'].get('scheduledStartTime')
                if scheduled_start_time:
                    upcoming_streams.append({
                        'id': video['id'],
                        'title': snippet['title'],
                        'description': snippet['description'],
                        'scheduledStartTime': scheduled_start_time,
                        'categoryId': snippet.get('categoryId'),
                        'tags': snippet.get('tags', [])
                    })

    except HttpError as e:
        print(f"An HTTP error occurred getting streams: {e}")
        sys.exit(1)
    except RefreshError as e:
        print("ERROR: YouTube API OAuth Token has expired or been revoked.")
        sys.exit(1)
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        sys.exit(1)

    return upcoming_streams

def get_combined_description():
    desc_path = "Description.txt"
    timings_path = "timings.txt"

    if not os.path.exists(desc_path):
        print(f"Generated description boilerplate not found at {desc_path}")
        return None

    with open(desc_path, 'r') as f:
        desc = f.read().strip()

    if os.path.exists(timings_path):
        with open(timings_path, 'r') as f:
            timings = f.read().strip()
        if timings:
            desc = desc + "\n\n" + timings

    return desc

def load_push_state():
    """Tracks, per video ID, the hash of the last combined description we
    actually applied to that stream on YouTube. This lets us push a given
    timings.txt/Description.txt content exactly once per stream: once the
    hash matches, later runs (the hourly chain, or a same-day retrigger)
    leave the live description alone even if it now differs from
    combined_desc -- that difference is presumably a manual fix made
    directly on YouTube (e.g. correcting a mistimed chapter), and we must
    not stomp on it. A new push of timings.txt changes the hash and clears
    us to update again.
    """
    if os.path.exists(PUSH_STATE_FILE):
        try:
            with open(PUSH_STATE_FILE, 'r') as f:
                return json.load(f)
        except Exception as e:
            print(f"Error reading {PUSH_STATE_FILE}: {e}")
    return {}

def save_push_state(state):
    try:
        with open(PUSH_STATE_FILE, 'w') as f:
            json.dump(state, f)
    except Exception as e:
        print(f"Error writing {PUSH_STATE_FILE}: {e}")

def content_hash(text):
    return hashlib.sha256(text.strip().encode('utf-8')).hexdigest()

def get_current_title():
    title_path = os.path.join("..", "Worship Scripts", "service-titles", "title.txt")
    if os.path.exists(title_path):
        with open(title_path, 'r') as f:
            title = f.read().strip()
            if title:
                return title
    return None

TITLE_STATE_PATH = os.path.join("..", "Worship Scripts", "service_titles_state.json")
DESC_STATE_PATH = "description_state.json"
LA_TZ = pytz.timezone('America/Los_Angeles')

def _state_target_date(path):
    try:
        with open(path) as f:
            return json.load(f).get("target_date")
    except Exception:
        return None

def _generation_date(path):
    """The local (LA) date a state file was last written -- i.e. when that
    title.txt / Description.txt was generated. Prefers the git commit time
    (reliable across runners); falls back to the filesystem mtime."""
    directory = os.path.dirname(path) or "."
    base = os.path.basename(path)
    try:
        out = subprocess.run(
            ["git", "-C", directory, "log", "-1", "--format=%ct", "--", base],
            capture_output=True, text=True,
        ).stdout.strip()
        if out:
            return datetime.fromtimestamp(int(out), LA_TZ).date()
    except Exception:
        pass
    try:
        return datetime.fromtimestamp(os.path.getmtime(path), LA_TZ).date()
    except Exception:
        return None

def content_ready_for(service_date):
    """(ok, reason). ok is True only when BOTH title.txt and Description.txt:

      * carry state target_date == service_date (a date object) -- i.e. they
        were generated *for* this exact service, and
      * were generated *during* that service's own week -- the preceding
        Monday through the service Sunday itself.

    title.txt / Description.txt are never cleared between weeks and the
    playlist can hold more than one upcoming stream, so without this an
    hourly run can merge last week's (or next week's) title/description --
    and last week's chapter timings -- onto the wrong broadcast.
    """
    want = service_date.isoformat()
    # The service week is the preceding Monday .. that Sunday. Allow one extra
    # day on the early side purely for cron / timezone slop -- target_date ==
    # want already pins generation to this week (update_service_titles.py only
    # ever writes the *coming* Sunday, and never runs on a Sunday).
    week_start = service_date - timedelta(days=7)

    for label, state_path in (("title.txt", TITLE_STATE_PATH),
                              ("Description.txt", DESC_STATE_PATH)):
        got = _state_target_date(state_path)
        if got != want:
            return False, f"{label} was generated for {got!r}, not this service ({want})"
        gen = _generation_date(state_path)
        if gen is None:
            return False, f"can't determine when {label} was generated"
        if not (week_start <= gen <= service_date):
            return False, (f"{label} was generated {gen.isoformat()}, outside this service's "
                           f"week (need {(service_date - timedelta(days=6)).isoformat()}..{want})")
    return True, ""

def regenerate_worship_content():
    """Best-effort: run the title + description generators for the coming
    Sunday, the same way upload_queue_to_drive.py does when a thumbnail lands
    before the OW. Lets an out-of-date stream be corrected in the same run
    instead of waiting for the next hourly pass.

    No FORCE_UPDATE: update_service_titles.py already no-ops (no Gemini call)
    when this OW's SHA is already processed, so if the dedicated Service
    Titles Checker beat us to it this is nearly free and won't fight it for
    the commit. Logs and continues on any failure -- the caller re-checks
    content_ready_for() afterward."""
    for cmd, cwd in (
        (["python", "worship workflows/update_service_titles.py"], os.path.join("..", "Worship Scripts")),
        (["python", "generate_youtube_description.py"], "."),
    ):
        try:
            print(f"  Running {' '.join(cmd)} ...")
            subprocess.run(cmd, cwd=cwd, check=True)
        except Exception as e:
            print(f"  {cmd[-1]} failed: {e}")

def main():
    service = get_youtube_service()
    if not service:
        return

    upcoming_streams = get_upcoming_streams(service)
    if not upcoming_streams:
        print("No eligible streams found in the playlist.")
        return

    push_state = load_push_state()
    # Prune entries for videos no longer in the playlist window so the file
    # doesn't grow forever.
    current_ids = {s['id'] for s in upcoming_streams}
    pruned_state = {k: v for k, v in push_state.items() if k in current_ids}
    if pruned_state != push_state:
        push_state = pruned_state
        save_push_state(push_state)

    la_tz = pytz.timezone('America/Los_Angeles')
    now_la = datetime.now(la_tz)

    # regenerate_worship_content() is expensive (PDF + Gemini); run it at
    # most once per invocation no matter how many streams are stale.
    regenerated_this_run = False

    for stream in upcoming_streams:
        print(f"Checking stream: {stream['title']} (ID: {stream['id']})")

        start_time_utc = dateutil.parser.parse(stream['scheduledStartTime'])
        start_time_la = start_time_utc.astimezone(la_tz)

        service_date = start_time_la.date()
        service_date_str = service_date.strftime('%Y-%m-%d')
        print(f"  Service date: {service_date_str}")

        force_update = str(os.environ.get('FORCE_UPDATE', 'false')).lower() == 'true'

        # If a timings.txt was pushed, we usually want to update even if it's the service day
        recently_modified = False
        for p in ["Description.txt", "timings.txt", os.path.join("..", "Worship Scripts", "service-titles", "title.txt")]:
            if os.path.exists(p):
                mtime = os.path.getmtime(p)
                if (datetime.now().timestamp() - mtime) < 3600:
                    recently_modified = True

        # If it is past the service date, only allow updates if the service was literally today
        # This prevents recently_modified from overwriting months of historical videos
        if now_la.date() > service_date:
            print("  This stream is from a past date. Skipping updates to prevent overwriting history.")
            continue

        if now_la.date() == service_date and not (force_update or recently_modified):
            print("  Today is the service day, but no manual force or recent modifications detected. Skipping updates.")
            continue
        elif now_la.date() == service_date:
            reason = "FORCE_UPDATE is enabled" if force_update else "files were modified recently"
            print(f"  Today is the service day, and {reason}. Proceeding...")

        # Hard gate: only touch this stream if title.txt AND Description.txt
        # were generated for -- and during -- THIS stream's service week. If
        # they weren't, try regenerating them once, then re-check; if it
        # still can't be confirmed, skip the stream entirely rather than risk
        # pushing the wrong title/description (and last week's chapter timings).
        ok, reason = content_ready_for(service_date)
        if not ok and not regenerated_this_run:
            print(f"  Content not confirmed for {service_date_str}: {reason}. Regenerating title/description...")
            regenerate_worship_content()
            regenerated_this_run = True
            ok, reason = content_ready_for(service_date)
        if not ok:
            print(f"  Skipping {service_date_str}: {reason}. Not risking a wrong title/description.")
            continue

        combined_desc = get_combined_description()
        if not combined_desc:
            print("  Could not read description. Skipping.")
            continue

        desc_hash = content_hash(combined_desc)
        already_pushed = push_state.get(stream['id']) == desc_hash

        if already_pushed:
            # This exact generated content was already applied to this
            # stream once. Don't touch the description again -- if it now
            # differs from combined_desc, that's a manual edit and it stays.
            description_to_send = stream['description']
            desc_changed = False
            print("  This description content was already pushed once for this stream. Leaving the live description untouched (preserving any manual edits).")
        else:
            description_to_send = combined_desc
            desc_changed = stream['description'].strip() != combined_desc.strip()

        # Fall back to the existing YouTube title if title.txt is missing/empty
        # (e.g. before the Service Titles Checker has processed this week's
        # PDF) so we never blank out or overwrite a good title with nothing.
        new_title = get_current_title() or stream['title']
        title_changed = stream['title'].strip() != new_title.strip()

        if not desc_changed and not title_changed:
            print("  Title and description are already up to date.")
            if not already_pushed and stream['description'].strip() == combined_desc.strip():
                # Nothing needed pushing, but the live description already
                # matches this generated content (e.g. it was set at stream
                # creation time). Record it now so a future manual edit is
                # recognized as such instead of being clobbered as "stale".
                push_state[stream['id']] = desc_hash
                save_push_state(push_state)
        else:
            if title_changed:
                print(f"  Title does not match (was: {stream['title']!r}, now: {new_title!r}). Updating...")
            if desc_changed:
                print("  Description does not match. Updating...")

            try:
                service.videos().update(
                    part='snippet',
                    body={
                        'id': stream['id'],
                        'snippet': {
                            'title': new_title,
                            'description': description_to_send,
                            'categoryId': stream['categoryId'],
                            'tags': stream['tags']
                        }
                    }
                ).execute()
                print("  Successfully updated YouTube title/description.")
                if not already_pushed:
                    push_state[stream['id']] = desc_hash
                    save_push_state(push_state)
                print("  Stopping further updates to prevent modifying multiple streams.")
                break
            except HttpError as e:
                print(f"  Failed to update title/description: {e}")

if __name__ == '__main__':
    main()
