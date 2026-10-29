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

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "Worship Scripts", "worship workflows"))
import service_keys  # noqa: E402

PLAYLIST_ID = "PLGtiSp5WvUc_I0M_vvfSdGY9dJ43ZofXs"

# Distinct exit code for "the title/description pipeline hasn't confirmed
# this week's content yet". Callers (create_pending_stream.py,
# upload_queue_to_drive.py) treat this as "leave the thumbnail pending and
# try again next run" rather than a hard failure.
EXIT_NOT_READY = 3

# The broadcast exists, but the thumbnail never made it onto it. The API gives
# us no way to ask later whether a video has a custom thumbnail (see
# reconcile_stream), so a failure here is invisible unless we say so now:
# callers treat this as "keep the thumbnail stashed and run me again", and the
# re-run reconciles the existing broadcast instead of creating a second one.
EXIT_THUMBNAIL_PENDING = 4

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

def verify_content_ready(key, target_date, today):
    """Return (ok, reason). ok is True only when:

      1. the service isn't already in the past, and
      2. an OW for this exact service (date AND time -- Christmas Eve has
         several) has been processed, and its title and description were
         both generated from that same OW and aren't empty
         (service_keys.content_ready).

    This is the hard gate that keeps a wrong/stale title or description off a
    real YouTube stream: if it can't be confirmed, no stream is created and
    the caller retries on a later run once the pipeline has caught up. There
    is no "too far out" limit any more: OWs uploaded weeks ahead are
    generated into their own folders, so early content is real content.
    `today` is the current date in the service's timezone.
    """
    days_out = (target_date - today).days
    if days_out < -1:
        return False, f"service date {target_date} is in the past ({-days_out} days ago) -- not creating a stream for it"
    if not key:
        return False, (f"no OW for this service on {target_date} has been processed yet "
                       f"(or the date has several services and none is at this time)")
    return service_keys.content_ready(key)

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


SERVICE_STREAM_KEY_TITLE = os.environ.get("SERVICE_STREAM_KEY_TITLE", "Service Stream Key")


def find_service_stream_id(service):
    """Id of the reusable stream key (liveStreams resource) titled "service".

    Matches the title case-insensitively, exact match preferred, then
    "contains". Returns None when no such key exists so the caller can warn
    instead of silently binding YouTube's default key."""
    want = SERVICE_STREAM_KEY_TITLE.strip().lower()
    streams, page = [], None
    while True:
        resp = service.liveStreams().list(
            part="snippet,cdn", mine=True, maxResults=50, pageToken=page).execute()
        streams.extend(resp.get("items", []))
        page = resp.get("nextPageToken")
        if not page:
            break
    titles = [(st["snippet"]["title"].strip().lower(), st["id"]) for st in streams]
    for t, sid in titles:
        if t == want:
            return sid
    for t, sid in titles:
        if want in t:
            return sid
    return None


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


# How far a broadcast's scheduled start may be from the requested time and
# still count as this service's stream.
START_TOLERANCE_MINUTES = 30


def find_stream_for_service(service, target_date, start_time, la_tz, date_only=False):
    """(video_id, in_playlist) for the broadcast of the service on target_date
    starting at start_time ("HH:MM").

    A broadcast counts when it's scheduled on that date within
    START_TOLERANCE_MINUTES of start_time. date_only (the date has just the
    one service) accepts any broadcast that day, as before, so a Sunday
    stream someone rescheduled by hand is still found rather than doubled.
    Christmas Eve's 5:30, 8pm and 11pm are each found only by their own time.

    Returns (None, False) when there isn't one. Checks the playlist first,
    then falls back to our own broadcasts. That fallback matters: adding a
    new broadcast to the playlist is a separate API call that can fail on its
    own (it has), which leaves a real, scheduled stream the playlist never
    learned about. Looking at the playlist alone, the next run would conclude
    no stream exists and create a duplicate. in_playlist tells the caller
    which of those two cases it is, so reconcile_stream() can repair the
    missing playlist entry without a second lookup."""
    wanted = la_tz.localize(datetime.combine(target_date, datetime.strptime(start_time, "%H:%M").time()))

    def matches(scheduled_start_time):
        start_la = dateutil.parser.parse(scheduled_start_time).astimezone(la_tz)
        if start_la.date() != target_date:
            return False
        return date_only or abs((start_la - wanted).total_seconds()) <= START_TOLERANCE_MINUTES * 60

    def scheduled_on_target(video_ids):
        for start in range(0, len(video_ids), 50):
            video_response = service.videos().list(
                part='snippet,liveStreamingDetails',
                id=','.join(video_ids[start:start + 50])
            ).execute()
            for video in video_response.get('items', []):
                scheduled_start_time = (video.get('liveStreamingDetails') or {}).get('scheduledStartTime')
                if scheduled_start_time and matches(scheduled_start_time):
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
            if scheduled_start_time and matches(scheduled_start_time):
                print(f"Found broadcast {broadcast['id']} for {target_date} {start_time} outside the playlist.")
                return broadcast['id'], False
        return None, False
    except HttpError as e:
        print(f"An HTTP error occurred getting streams: {e}")
        return None, False
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        return None, False

def dispatch(event_type, client_payload, max_retries=4, backoff_seconds=3):
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


def dispatch_event(video_id, date_str):
    """Announce a newly created (or newly discovered) worship broadcast."""
    dispatch("youtube_stream_created", {
        "stream_url": f"https://www.youtube.com/watch?v={video_id}",
        "date": date_str,
    })


def dispatch_thumbnail_retrying(stream_url, date_str):
    """Say up front that the thumbnail didn't go on.

    Sent once, by the run that created the broadcast; the hourly retries
    after it stay quiet and only giving up sends a second message. Hearing it
    now is the whole point -- on 09-13 the stream went out with YouTube's own
    frame on it and the first anyone knew was days later."""
    dispatch("youtube_stream_thumbnail_retrying", {
        "stream_url": stream_url,
        "date": date_str,
    })


def dispatch_thumbnail_failed(stream_url, date_str, attempts):
    """Tell a human the thumbnail never landed and won't be retried again.

    Only sent once the retries are exhausted (create_pending_stream.py owns
    that count) -- until then the hourly pending run keeps trying, and a
    message per attempt would be noise."""
    dispatch("youtube_stream_thumbnail_failed", {
        "stream_url": stream_url or "",
        "date": date_str,
        "attempts": attempts,
    })

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
    """True when last_stream.json says this broadcast was announced.

    The announcement below re-fires for any existing stream we never got
    around to announcing; this keeps that from meaning "announce the same
    stream again on every hourly run". It keeps a short list rather than just
    the latest: with Christmas Eve's three streams created back to back, a
    retry on the 5:30 one must not re-announce it because 8pm came after."""
    path = os.path.join("Youtube Processing", "last_stream.json")
    try:
        with open(path) as f:
            last = json.load(f)
    except Exception:
        return False
    return (last.get("stream_url", "").endswith(f"v={video_id}")
            or video_id in last.get("announced", []))

def record_last_stream(video_id, service_date):
    """Write a small breadcrumb the tech-info dashboard reads to show when the
    most recent worship livestream was created. Committed by whichever
    workflow ran this script (create_pending_youtube_stream.yml via
    create_pending_stream.py, or process_uploads.yml via
    upload_queue_to_drive.py)."""
    path = os.path.join("Youtube Processing", "last_stream.json")
    try:
        with open(path) as f:
            announced = json.load(f).get("announced", [])
    except Exception:
        announced = []
    announced = ([v for v in announced if v != video_id] + [video_id])[-20:]
    try:
        with open(path, "w") as f:
            json.dump({
                "created_at": datetime.now(pytz.utc).isoformat(),
                "service_date": service_date.strftime("%m-%d-%Y"),
                "stream_url": f"https://www.youtube.com/watch?v={video_id}",
                "announced": announced,
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

    try:
        scheduled_time = datetime.strptime(time_str, "%H:%M").time()
    except ValueError:
        print(f"Could not parse time '{time_str}', falling back to 10:30.")
        scheduled_time = datetime.strptime("10:30", "%H:%M").time()
    start_time = scheduled_time.strftime("%H:%M")

    # Which service's generated title/description this stream gets: the OW for
    # this date at this time (or the date's only OW). None until it's processed.
    key = service_keys.resolve_key(target_date, start_time)
    print(f"Service for {target_date} {start_time}: {key or 'not generated yet'}")
    date_only = len(service_keys.services_on(target_date)) <= 1

    service = get_youtube_service()
    if not service:
        return

    existing_id, in_playlist = find_stream_for_service(service, target_date, start_time, la_tz, date_only)
    if existing_id:
        print(f"A stream already exists for {target_date} {start_time} (video {existing_id}). Skipping creation.")
        repaired = reconcile_stream(service, existing_id, in_playlist,
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
        # --reconcile means the caller is here *because* the thumbnail is
        # still outstanding, so a run that didn't manage to set it hasn't
        # finished the job -- say so rather than reporting success and
        # letting the caller throw the image away.
        if args.reconcile and "thumbnail" not in repaired:
            print("The thumbnail still has not been set on this stream.")
            sys.exit(EXIT_THUMBNAIL_PENDING)
        return

    # --- Freshness gate -------------------------------------------------
    # Never let a wrong/stale title or description reach a real stream. If a
    # human supplied both explicitly, trust them. Otherwise require the
    # pipeline to have confirmed this week's title AND description for
    # target_date; if it hasn't, create nothing and exit EXIT_NOT_READY so
    # the caller keeps the thumbnail pending and retries on a later run.
    #
    # This runs *after* the existence check, not before: it guards the
    # title/description going onto a brand-new broadcast, and a run that
    # finds the stream already there never touches either -- it only repairs
    # the category, the playlist entry and the thumbnail. Gating first meant
    # an outstanding thumbnail became unfixable the moment the service date
    # slipped into the past, which is precisely when someone notices.
    if override_title and override_description:
        title = override_title
        description = override_description
        print("Using explicitly provided title/description; skipping the freshness gate.")
    else:
        today_la = datetime.now(la_tz).date()
        ok, reason = verify_content_ready(key, target_date, today_la)
        if not ok:
            print(f"NOT creating a stream for {target_date} {start_time}: {reason}.")
            print("The stream will be created on a later run, once the title/description "
                  "pipeline has processed this service's OW.")
            sys.exit(EXIT_NOT_READY)
        title = service_keys.title_for(key)
        description = service_keys.description_for(key)
    # ------------------------------------------------------------------

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
    # Default to the "service" stream key rather than whatever YouTube picks.
    stream_id, found = attempt(
        "looking up the service stream key",
        "Found the service stream key.",
        lambda: find_service_stream_id(service)
    )
    if found and stream_id:
        attempt(
            f"binding {broadcast_id} to the service stream key",
            "Bound broadcast to the service stream key.",
            lambda: service.liveBroadcasts().bind(
                id=broadcast_id, part="id,contentDetails", streamId=stream_id
            ).execute()
        )
    elif found:
        print(f"Warning: no stream key titled '{SERVICE_STREAM_KEY_TITLE}' found; "
              f"{broadcast_id} was left on the default stream key.")

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
    thumbnail_ok = False
    if os.path.exists(thumbnail_path):
        print(f"Uploading thumbnail from {thumbnail_path}...")
        _, thumbnail_ok = attempt(
            f"uploading the thumbnail for {broadcast_id}",
            "Successfully set thumbnail.",
            lambda: service.thumbnails().set(
                videoId=broadcast_id,
                media_body=MediaFileUpload(thumbnail_path, mimetype='image/jpeg', resumable=True)
            ).execute()
        )
    else:
        print(f"Thumbnail path not found: {thumbnail_path}")

    # Dispatch event. This happens whatever became of the thumbnail: the
    # stream is real and the service can go live on it, so it needs to be
    # announced and the sermon-series link backfilled either way.
    dispatch_event(broadcast_id, target_date.strftime("%m-%d-%Y"))
    record_last_stream(broadcast_id, target_date)

    if not thumbnail_ok:
        # On 09-11 a 403 on the call before this one skipped the thumbnail
        # entirely, and because the run still "succeeded" the image was
        # deleted from the queue -- so the 09-13 stream went out with
        # YouTube's auto-generated frame and nothing was ever going to
        # notice. Exit non-zero so the caller keeps the image and runs us
        # again; the re-run reconciles the broadcast that now exists.
        print("The stream was created, but the thumbnail was not set on it.")
        dispatch_thumbnail_retrying(f"https://www.youtube.com/watch?v={broadcast_id}",
                                    target_date.strftime("%m-%d-%Y"))
        sys.exit(EXIT_THUMBNAIL_PENDING)

if __name__ == '__main__':
    main()
