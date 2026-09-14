import os
import sys
import json
import argparse
import requests
import time
from datetime import datetime
import dateutil.parser
import pytz
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

PLAYLIST_ID = "PLGtiSp5WvUc_I0M_vvfSdGY9dJ43ZofXs"

# Distinct exit code for "the title/description pipeline hasn't confirmed
# this week's content yet". Callers (create_pending_stream.py,
# upload_queue_to_drive.py) treat this as "leave the thumbnail pending and
# try again next run" rather than a hard failure.
EXIT_NOT_READY = 3

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

def get_combined_description():
    desc_path = os.path.join("Youtube Processing", "Description.txt")
    if not os.path.exists(desc_path):
        print(f"Generated description boilerplate not found at {desc_path}")
        return "Join us for our Sunday Service!"

    with open(desc_path, 'r') as f:
        desc = f.read().strip()

    return desc

def get_title():
    title_path = os.path.join("Worship Scripts", "service-titles", "title.txt")
    if os.path.exists(title_path):
        with open(title_path, 'r') as f:
            title = f.read().strip()
            if title:
                return title

    return "Sunday Service"

def _state_target_date(path):
    """The target_date recorded in a *_state.json file, or None. These are
    written by update_service_titles.py (service_titles_state.json) and
    generate_youtube_description.py (description_state.json) and record which
    service date the current title.txt / Description.txt were generated for.
    title.txt and Description.txt are never cleared between weeks, so the
    state file is the only reliable signal that what's on disk is actually
    this week's content and not a stale carry-over."""
    try:
        with open(path) as f:
            return json.load(f).get("target_date")
    except Exception:
        return None

def verify_content_ready(target_date, today):
    """Return (ok, reason). ok is True only when ALL of the following hold:

      1. target_date is the current service week -- not already in the past,
         and not more than ~8 days out (the pipeline only ever generates for
         the *coming* Sunday, so anything outside that window means we'd be
         about to reuse a stale carry-over that happens to match).
      2. BOTH service_titles_state.json and description_state.json record
         target_date -- i.e. title.txt AND Description.txt were regenerated
         for THIS service, this week, not carried over from a prior week.
      3. title.txt / Description.txt are actually non-empty and not the
         generic placeholders.

    This is the hard gate that keeps a wrong/stale title or description off a
    real YouTube stream: if it can't be confirmed, no stream is created and
    the caller retries on a later run once the pipeline has caught up.
    `today` is the current date in the service's timezone.
    """
    want = target_date.isoformat()

    # 1. target_date must be this coming Sunday's service, give or take.
    days_out = (target_date - today).days
    if days_out < -1:
        return False, f"service date {want} is in the past ({-days_out} days ago) -- not creating a stream for it"
    if days_out > 8:
        return False, (f"service date {want} is {days_out} days out; the title/description "
                       f"pipeline only generates for the coming Sunday, so this can't be "
                       f"confirmed as this week's content yet")

    # 2. Both halves of the content confirmed generated for this service.
    title_state = _state_target_date(os.path.join("Worship Scripts", "service_titles_state.json"))
    if title_state != want:
        return False, (f"title.txt is not confirmed for {want} "
                       f"(service_titles_state.json target_date={title_state!r})")

    desc_state = _state_target_date(os.path.join("Youtube Processing", "description_state.json"))
    if desc_state != want:
        return False, (f"Description.txt is not confirmed for {want} "
                       f"(description_state.json target_date={desc_state!r})")

    # 3. The files themselves are real.
    title = get_title()
    if not title or title.strip() == "Sunday Service":
        return False, "title.txt is missing or empty despite the state file matching"

    description = get_combined_description()
    if not description or description.strip() == "Join us for our Sunday Service!":
        return False, "Description.txt is missing or empty despite the state file matching"

    return True, ""

# A 403 from the YouTube API is a mixed bag. A broadcast that was just
# inserted isn't always addressable as a video yet, which surfaces as a
# generic "forbidden" and clears on its own seconds later -- the most likely
# explanation for the 09-11-2026 failure. A blown daily quota also arrives as
# a 403 and will not clear no matter how long we wait. Retry the first kind,
# give up immediately on the second.
NON_RETRYABLE_REASONS = {"quotaExceeded", "dailyLimitExceeded"}


def _error_reason(e):
    """The YouTube API's machine-readable reason for an HttpError, or ''."""
    try:
        payload = json.loads(e.content.decode("utf-8"))
        return payload.get("error", {}).get("errors", [{}])[0].get("reason", "")
    except Exception:
        return ""


def _is_retryable(e):
    if not isinstance(e, HttpError):
        return False
    status = getattr(e.resp, "status", None) or 0
    if status == 429 or status >= 500:
        return True
    if status == 403:
        return _error_reason(e) not in NON_RETRYABLE_REASONS
    return False


def attempt(what, done, call, max_retries=4, backoff_seconds=3):
    """Run one API call, retrying failures that plausibly clear on their own.

    Returns (result, ok). A failure here is reported and swallowed: callers
    use this for steps that decorate a broadcast which already exists, and no
    single one of them is worth abandoning the rest of the run over."""
    for attempt_no in range(1, max_retries + 1):
        try:
            result = call()
            print(done)
            return result, True
        except Exception as e:
            if attempt_no < max_retries and _is_retryable(e):
                wait = backoff_seconds * (2 ** (attempt_no - 1))
                print(f"{what} failed (attempt {attempt_no}/{max_retries}): {e}. Retrying in {wait}s...")
                time.sleep(wait)
                continue
            print(f"Warning: {what} failed: {e}")
            return None, False
    return None, False


def find_stream_for_date(service, target_date, la_tz):
    """(video_id, in_playlist) for the broadcast scheduled on target_date.

    Returns (None, False) when there isn't one. Checks the playlist first,
    then falls back to our own broadcasts. That fallback matters: adding a
    new broadcast to the playlist is a separate API call that can fail on its
    own (it has), which leaves a real, scheduled stream the playlist never
    learned about. Looking at the playlist alone, the next run would conclude
    no stream exists and create a duplicate. in_playlist tells the caller
    which of those two cases it is, so reconcile_stream() can repair the
    missing playlist entry without a second lookup."""
    def scheduled_on_target(video_ids):
        for start in range(0, len(video_ids), 50):
            video_response = service.videos().list(
                part='snippet,liveStreamingDetails',
                id=','.join(video_ids[start:start + 50])
            ).execute()
            for video in video_response.get('items', []):
                scheduled_start_time = (video.get('liveStreamingDetails') or {}).get('scheduledStartTime')
                if not scheduled_start_time:
                    continue
                start_time_la = dateutil.parser.parse(scheduled_start_time).astimezone(la_tz)
                if start_time_la.date() == target_date:
                    return video['id']
        return None

    try:
        playlist_response = service.playlistItems().list(
            part='snippet',
            playlistId=PLAYLIST_ID,
            maxResults=50
        ).execute()

        video_ids = [item['snippet']['resourceId']['videoId'] for item in playlist_response.get('items', [])]
        found = scheduled_on_target(video_ids) if video_ids else None
        if found:
            return found, True

        broadcast_response = service.liveBroadcasts().list(
            part='id,snippet',
            mine=True,
            maxResults=50
        ).execute()
        for broadcast in broadcast_response.get('items', []):
            scheduled_start_time = broadcast.get('snippet', {}).get('scheduledStartTime')
            if not scheduled_start_time:
                continue
            start_time_la = dateutil.parser.parse(scheduled_start_time).astimezone(la_tz)
            if start_time_la.date() == target_date:
                print(f"Found broadcast {broadcast['id']} for {target_date} outside the playlist.")
                return broadcast['id'], False
        return None, False
    except HttpError as e:
        print(f"An HTTP error occurred getting streams: {e}")
        return None, False
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        return None, False

def dispatch_event(video_id, date_str, max_retries=4, backoff_seconds=3):
    """Fire a repository_dispatch event so imessage_notifications.yml can react.

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

    payload = {
        "event_type": "youtube_stream_created",
        "client_payload": {
            "stream_url": f"https://www.youtube.com/watch?v={video_id}",
            "date": date_str
        }
    }

    for attempt in range(1, max_retries + 1):
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=15)
        except requests.exceptions.RequestException as e:
            if attempt < max_retries:
                wait = backoff_seconds * (2 ** (attempt - 1))
                print(f"Error dispatching github event (attempt {attempt}/{max_retries}): {e}. Retrying in {wait}s...")
                time.sleep(wait)
                continue
            print(f"Error dispatching github event after {max_retries} attempts: {e}")
            return

        if response.status_code == 204:
            print("Successfully dispatched github event.")
            return

        retryable = response.status_code == 429 or response.status_code >= 500
        if retryable and attempt < max_retries:
            wait = backoff_seconds * (2 ** (attempt - 1))
            print(f"Failed to dispatch github event (attempt {attempt}/{max_retries}): "
                  f"{response.status_code} {response.text}. Retrying in {wait}s...")
            time.sleep(wait)
            continue

        print(f"Failed to dispatch github event: {response.status_code} {response.text}")
        return

def reconcile_stream(service, video_id, in_playlist, thumbnail_path=None):
    """Repair the follow-up steps an earlier run left undone.

    Creating the broadcast is one API call; the category, the playlist entry
    and the thumbnail are each a separate call that can fail on its own. A
    re-run used to just see "a stream already exists" and return, so whatever
    failed stayed failed forever -- re-running was not a remedy for anything.
    Now it checks what is actually true of the stream and fixes only the gaps.

    thumbnail_path is only acted on when the caller passes it (--reconcile),
    because the API gives no way to tell a custom thumbnail from YouTube's own
    generated one -- checking would mean re-uploading it on every run."""
    repaired = []

    response, ok = attempt(
        f"reading video {video_id}",
        f"Read current metadata for {video_id}.",
        lambda: service.videos().list(part="snippet", id=video_id).execute()
    )
    items = (response or {}).get("items", []) if ok else []
    if items:
        snippet = items[0]["snippet"]
        if snippet.get("categoryId") != "29":
            # videos.update replaces the whole snippet, so edit the live one.
            # Sending a freshly built body would blank whatever it omits.
            snippet["categoryId"] = "29"
            _, fixed = attempt(
                f"setting the category on {video_id}",
                "Repaired the video category.",
                lambda: service.videos().update(
                    part="snippet",
                    body={"id": video_id, "snippet": snippet}
                ).execute()
            )
            if fixed:
                repaired.append("category")

    if not in_playlist:
        _, fixed = attempt(
            f"adding {video_id} to playlist {PLAYLIST_ID}",
            f"Repaired playlist membership ({PLAYLIST_ID}).",
            lambda: service.playlistItems().insert(
                part="snippet",
                body={
                    "snippet": {
                        "playlistId": PLAYLIST_ID,
                        "resourceId": {
                            "kind": "youtube#video",
                            "videoId": video_id
                        }
                    }
                }
            ).execute()
        )
        if fixed:
            repaired.append("playlist")

    if thumbnail_path and os.path.exists(thumbnail_path):
        _, fixed = attempt(
            f"re-uploading the thumbnail for {video_id}",
            "Re-uploaded the thumbnail.",
            lambda: service.thumbnails().set(
                videoId=video_id,
                media_body=MediaFileUpload(thumbnail_path, mimetype='image/jpeg', resumable=True)
            ).execute()
        )
        if fixed:
            repaired.append("thumbnail")

    if repaired:
        print(f"Reconciled {video_id}: {', '.join(repaired)}.")
    else:
        print(f"Nothing to reconcile on {video_id}.")
    return repaired


def last_stream_recorded(video_id):
    """True when last_stream.json already points at this broadcast.

    The announcement below re-fires for any existing stream we never got
    around to announcing; this keeps that from meaning "announce the same
    stream again on every hourly run"."""
    path = os.path.join("Youtube Processing", "last_stream.json")
    try:
        with open(path) as f:
            return json.load(f).get("stream_url", "").endswith(f"v={video_id}")
    except Exception:
        return False

def record_last_stream(video_id, service_date):
    """Write a small breadcrumb the tech-info dashboard reads to show when the
    most recent worship livestream was created. Committed by whichever
    workflow ran this script (create_pending_youtube_stream.yml via
    create_pending_stream.py, or process_uploads.yml via
    upload_queue_to_drive.py)."""
    path = os.path.join("Youtube Processing", "last_stream.json")
    try:
        with open(path, "w") as f:
            json.dump({
                "created_at": datetime.now(pytz.utc).isoformat(),
                "service_date": service_date.strftime("%m-%d-%Y"),
                "stream_url": f"https://www.youtube.com/watch?v={video_id}",
            }, f, indent=2)
        print(f"Recorded last-stream breadcrumb to {path}")
    except Exception as e:
        print(f"Warning: could not write {path}: {e}")

def main():
    parser = argparse.ArgumentParser(description="Create a scheduled YouTube livestream for a worship service.")
    parser.add_argument("date", help="Service date (MM-DD-YYYY, also accepts M-D-YY and slash separators)")
    parser.add_argument("thumbnail", help="Path to the thumbnail image")
    parser.add_argument("time", nargs="?", default="10:30", help="Start time HH:MM (LA time), default 10:30")
    # An explicit title/description supplied by a human via the upload
    # dashboard's settings panel. When BOTH are given they're trusted as-is
    # and the freshness gate is skipped; otherwise the title/description come
    # from title.txt/Description.txt and MUST pass verify_content_ready().
    parser.add_argument("--title", default=None, help="Explicit, human-provided title (bypasses the freshness gate)")
    parser.add_argument("--description", default=None, help="Explicit, human-provided description (bypasses the freshness gate)")
    # Category and playlist membership are checked on every run; the thumbnail
    # can't be checked (see reconcile_stream), so re-uploading it is opt-in.
    parser.add_argument("--reconcile", action="store_true",
                        help="When the stream already exists, also re-upload the thumbnail")
    args = parser.parse_args()

    date_str = args.date
    thumbnail_path = args.thumbnail
    time_str = args.time if args.time and args.time.strip() else "10:30"
    override_title = args.title.strip() if args.title and args.title.strip() else None
    override_description = args.description.strip() if args.description and args.description.strip() else None

    la_tz = pytz.timezone('America/Los_Angeles')

    try:
        if '-' in date_str:
            parts = date_str.split('-')
            if len(parts[0]) == 4:
                target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            else:
                if len(parts[2]) == 2:
                    target_date = datetime.strptime(date_str, "%m-%d-%y").date()
                else:
                    target_date = datetime.strptime(date_str, "%m-%d-%Y").date()
        elif '/' in date_str:
            parts = date_str.split('/')
            if len(parts[0]) == 4:
                target_date = datetime.strptime(date_str, "%Y/%m/%d").date()
            else:
                if len(parts[2]) == 2:
                    target_date = datetime.strptime(date_str, "%m/%d/%y").date()
                else:
                    target_date = datetime.strptime(date_str, "%m/%d/%Y").date()
        else:
            print(f"Could not parse date string: {date_str}")
            return
    except Exception as e:
        print(f"Error parsing date {date_str}: {e}")
        return

    # --- Freshness gate -------------------------------------------------
    # Never let a wrong/stale title or description reach a real stream. If a
    # human supplied both explicitly, trust them. Otherwise require the
    # pipeline to have confirmed this week's title AND description for
    # target_date; if it hasn't, create nothing and exit EXIT_NOT_READY so
    # the caller keeps the thumbnail pending and retries on a later run.
    if override_title and override_description:
        title = override_title
        description = override_description
        print("Using explicitly provided title/description; skipping the freshness gate.")
    else:
        today_la = datetime.now(la_tz).date()
        ok, reason = verify_content_ready(target_date, today_la)
        if not ok:
            print(f"NOT creating a stream for {target_date}: {reason}.")
            print("The stream will be created on a later run, once the title/description "
                  "pipeline has processed this week's OW.")
            sys.exit(EXIT_NOT_READY)
        title = get_title()
        description = get_combined_description()
    # ------------------------------------------------------------------

    service = get_youtube_service()
    if not service:
        return

    existing_id, in_playlist = find_stream_for_date(service, target_date, la_tz)
    if existing_id:
        print(f"A stream already exists for {target_date} (video {existing_id}). Skipping creation.")
        reconcile_stream(service, existing_id, in_playlist,
                         thumbnail_path if args.reconcile else None)
        if not last_stream_recorded(existing_id):
            # The stream exists but was never announced -- a previous run
            # created the broadcast and then died partway through the
            # follow-up API calls. Finish that run's job now, or nothing
            # ever backfills the real link into the sermon-series
            # description and it ships with the placeholder.
            print("It was never announced, so dispatching the stream-created event now.")
            dispatch_event(existing_id, target_date.strftime("%m-%d-%Y"))
            record_last_stream(existing_id, target_date)
        return

    try:
        scheduled_time = datetime.strptime(time_str, "%H:%M").time()
    except ValueError:
        print(f"Could not parse time '{time_str}', falling back to 10:30.")
        scheduled_time = datetime.strptime("10:30", "%H:%M").time()

    # Target date at the requested (default 10:30 AM) LA time
    scheduled_start = la_tz.localize(datetime.combine(target_date, scheduled_time))
    # Convert to RFC 3339 format for API
    scheduled_start_iso = scheduled_start.isoformat()

    print(f"Creating stream '{title}' for {scheduled_start_iso}...")

    try:
        # Create broadcast
        broadcast_insert_response = service.liveBroadcasts().insert(
            part="snippet,status,contentDetails",
            body={
                "snippet": {
                    "title": title,
                    "description": description,
                    "scheduledStartTime": scheduled_start_iso
                },
                "status": {
                    "privacyStatus": "public",
                    "selfDeclaredMadeForKids": False
                },
                "contentDetails": {
                    "monitorStream": {
                        "enableMonitorStream": True
                    },
                    "enableAutoStart": False,
                    "enableAutoStop": False,
                    # Low latency for the live stream. "low" keeps DVR
                    # available (ultra-low latency would disable it).
                    "latencyPreference": "low",
                    "enableDvr": True
                }
            }
        ).execute()
    except HttpError as e:
        print(f"Failed to create the broadcast: {e}")
        return

    broadcast_id = broadcast_insert_response["id"]
    print(f"Successfully created broadcast with ID: {broadcast_id}")

    # Past this point the stream exists and the service can go live on it, so
    # every remaining call gets its own guard instead of sharing one try
    # block. Sharing one meant a single failure -- a 403 out of videos.update
    # on 09-11-2026, say -- silently skipped everything after it, including
    # the dispatch that backfills the real stream link into the sermon-series
    # description and the breadcrumb the dashboard reads. The stream went out
    # fine; the description shipped with the placeholder still in it.
    # Update category
    attempt(
        f"setting the video category on {broadcast_id}",
        "Successfully set category to Activism and Non Profit.",
        lambda: service.videos().update(
            part="snippet",
            body={
                "id": broadcast_id,
                "snippet": {
                    "title": title,
                    "description": description,
                    "categoryId": "29"
                }
            }
        ).execute()
    )

    # Add to playlist
    attempt(
        f"adding {broadcast_id} to playlist {PLAYLIST_ID}",
        f"Successfully added to playlist {PLAYLIST_ID}",
        lambda: service.playlistItems().insert(
            part="snippet",
            body={
                "snippet": {
                    "playlistId": PLAYLIST_ID,
                    "resourceId": {
                        "kind": "youtube#video",
                        "videoId": broadcast_id
                    }
                }
            }
        ).execute()
    )

    # Upload Thumbnail
    if os.path.exists(thumbnail_path):
        print(f"Uploading thumbnail from {thumbnail_path}...")
        attempt(
            f"uploading the thumbnail for {broadcast_id}",
            "Successfully set thumbnail.",
            lambda: service.thumbnails().set(
                videoId=broadcast_id,
                media_body=MediaFileUpload(thumbnail_path, mimetype='image/jpeg', resumable=True)
            ).execute()
        )
    else:
        print(f"Thumbnail path not found: {thumbnail_path}")

    # Dispatch event
    dispatch_event(broadcast_id, target_date.strftime("%m-%d-%Y"))
    record_last_stream(broadcast_id, target_date)

if __name__ == '__main__':
    main()
