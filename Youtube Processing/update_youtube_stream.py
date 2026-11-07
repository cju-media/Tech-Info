import os
import json
import sys
import hashlib
import subprocess
import dateutil.parser
from datetime import datetime, timedelta
import pytz
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google.auth.exceptions import RefreshError

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "Worship Scripts", "worship workflows"))
import service_keys  # noqa: E402

PLAYLIST_ID = "PLGtiSp5WvUc_I0M_vvfSdGY9dJ43ZofXs"
PUSH_STATE_FILE = "description_push_state.json"
# Entries in the push state that aren't video IDs (never pruned).
TIMINGS_HASH_KEY = "_attributed_timings"

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
                        'actualEndTime': video['liveStreamingDetails'].get('actualEndTime'),
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

def stream_service(stream):
    """(service key or None, service date, start "HH:MM", start as LA datetime) for a stream."""
    start_la = dateutil.parser.parse(stream['scheduledStartTime']).astimezone(LA_TZ)
    start_time = start_la.strftime("%H:%M")
    return service_keys.resolve_key(start_la.date(), start_time), start_la.date(), start_time, start_la

def attribute_timings(streams, push_state):
    """File the timings.txt the booth's OSC server (or the dashboard's
    fallback upload) just pushed under the service of the stream that most
    recently ended, once per push.

    There's one timings.txt, but on Christmas Eve three streams end within
    six hours of each other: each stream's chapter list has to land in its
    own service's description, not whichever runs next. Returns the key it
    filed them under, or None."""
    timings = ""
    if os.path.exists("timings.txt"):
        with open("timings.txt") as f:
            timings = f.read().strip()
    if not timings:
        return None
    digest = content_hash(timings)
    if push_state.get(TIMINGS_HASH_KEY) == digest:
        return None

    now = datetime.now(LA_TZ)
    ended = [s for s in streams if s.get('actualEndTime')
             and now - dateutil.parser.parse(s['actualEndTime']) <= timedelta(days=7)]
    if not ended:
        print("timings.txt changed, but no stream has ended in the last week to file it under.")
        return None
    stream = max(ended, key=lambda s: dateutil.parser.parse(s['actualEndTime']))
    key = stream_service(stream)[0]
    if not key:
        print(f"timings.txt changed, but the stream that just ended ({stream['title']}) has no generated service to file it under.")
        return None
    with open(os.path.join(service_keys.service_dir(key), "timings.txt"), "w") as f:
        f.write(timings + "\n")
    push_state[TIMINGS_HASH_KEY] = digest
    save_push_state(push_state)
    print(f"Filed the pushed timings under {key} (the stream that ended last: {stream['title']}).")
    return key

LA_TZ = pytz.timezone('America/Los_Angeles')

def regenerate_worship_content():
    """Best-effort: run the title + description generators, the same way
    upload_queue_to_drive.py does when a thumbnail lands before the OW. Lets
    an out-of-date stream be corrected in the same run instead of waiting for
    the next hourly pass.

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
    pruned_state = {k: v for k, v in push_state.items() if k in current_ids or k.startswith("_")}
    if pruned_state != push_state:
        push_state = pruned_state
        save_push_state(push_state)

    attribute_timings(upcoming_streams, push_state)

    now_la = datetime.now(LA_TZ)
    force_update = str(os.environ.get('FORCE_UPDATE', 'false')).lower() == 'true'

    # regenerate_worship_content() is expensive (PDF + Gemini); run it at
    # most once per invocation no matter how many streams are stale.
    regenerated_this_run = False

    # Every stream gets the text generated for its own service (date AND
    # time), so it's safe to update all of them in one run -- Christmas Eve's
    # three streams, and next Sunday's created weeks ahead, alike.
    for stream in upcoming_streams:
        print(f"Checking stream: {stream['title']} (ID: {stream['id']})")

        key, service_date, start_time, start_la = stream_service(stream)
        print(f"  Service: {service_date.isoformat()} {start_time} -> {key or 'no generated service'}")

        # A finished service's description is history: leave it alone once
        # the day after has started in earnest. (Measured from the start
        # rather than by calendar date so the 11pm Christmas Eve stream still
        # gets the timings pushed just after midnight.)
        if now_la > start_la + timedelta(hours=18):
            print("  This stream is from a past date. Skipping updates to prevent overwriting history.")
            continue

        if not key:
            print("  No OW has been processed for this stream's service. Skipping.")
            continue
        folder = service_keys.service_dir(key)

        # If timings were pushed, we usually want to update even if it's the service day
        recently_modified = False
        for p in (os.path.join(folder, "Description.txt"), os.path.join(folder, "timings.txt"),
                  os.path.join(service_keys.titles_dir(key), "title.txt")):
            if os.path.exists(p):
                mtime = os.path.getmtime(p)
                if (datetime.now().timestamp() - mtime) < 3600:
                    recently_modified = True

        if now_la.date() >= service_date and not (force_update or recently_modified):
            print("  Today is the service day, but no manual force or recent modifications detected. Skipping updates.")
            continue
        elif now_la.date() >= service_date:
            reason = "FORCE_UPDATE is enabled" if force_update else "files were modified recently"
            print(f"  Today is the service day, and {reason}. Proceeding...")

        # Hard gate: only touch this stream once its service's title and
        # description were both generated from its current OW. If they
        # weren't, try regenerating them once, then re-check; if it still
        # can't be confirmed, skip the stream entirely rather than risk
        # pushing the wrong title/description.
        ok, reason = service_keys.content_ready(key)
        if not ok and not regenerated_this_run:
            print(f"  Content not confirmed for {key}: {reason}. Regenerating title/description...")
            regenerate_worship_content()
            regenerated_this_run = True
            ok, reason = service_keys.content_ready(key)
        if not ok:
            print(f"  Skipping {key}: {reason}. Not risking a wrong title/description.")
            continue

        combined_desc = service_keys.description_for(key)
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

        # Fall back to the existing YouTube title if title.txt is empty so we
        # never blank out or overwrite a good title with nothing.
        new_title = service_keys.title_for(key) or stream['title']
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
            except HttpError as e:
                print(f"  Failed to update title/description: {e}")

if __name__ == '__main__':
    main()
