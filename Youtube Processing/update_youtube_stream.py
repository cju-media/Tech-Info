import os
import json
import sys
import dateutil.parser
from datetime import datetime
import pytz
import re
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google.auth.exceptions import RefreshError

PLAYLIST_ID = "PLGtiSp5WvUc_I0M_vvfSdGY9dJ43ZofXs"

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

def get_current_title():
    title_path = os.path.join("..", "Worship Scripts", "service-titles", "title.txt")
    if os.path.exists(title_path):
        with open(title_path, 'r') as f:
            title = f.read().strip()
            if title:
                return title
    return None

def main():
    service = get_youtube_service()
    if not service:
        return

    upcoming_streams = get_upcoming_streams(service)
    if not upcoming_streams:
        print("No eligible streams found in the playlist.")
        return

    la_tz = pytz.timezone('America/Los_Angeles')
    now_la = datetime.now(la_tz)

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

        combined_desc = get_combined_description()
        if not combined_desc:
            print("  Could not read description. Skipping.")
            continue

        # Fall back to the existing YouTube title if title.txt is missing/empty
        # (e.g. before the Service Titles Checker has processed this week's
        # PDF) so we never blank out or overwrite a good title with nothing.
        new_title = get_current_title() or stream['title']

        desc_changed = stream['description'].strip() != combined_desc.strip()
        title_changed = stream['title'].strip() != new_title.strip()

        if not desc_changed and not title_changed:
            print("  Title and description are already up to date.")
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
                            'description': combined_desc,
                            'categoryId': stream['categoryId'],
                            'tags': stream['tags']
                        }
                    }
                ).execute()
                print("  Successfully updated YouTube title/description.")
                print("  Stopping further updates to prevent modifying multiple streams.")
                break
            except HttpError as e:
                print(f"  Failed to update title/description: {e}")

if __name__ == '__main__':
    main()
