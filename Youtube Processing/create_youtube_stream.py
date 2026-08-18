import os
import sys
import json
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

def stream_exists_for_date(service, target_date, la_tz):
    try:
        playlist_response = service.playlistItems().list(
            part='snippet',
            playlistId=PLAYLIST_ID,
            maxResults=50
        ).execute()

        video_ids = [item['snippet']['resourceId']['videoId'] for item in playlist_response.get('items', [])]
        if not video_ids:
            return False

        video_response = service.videos().list(
            part='snippet,liveStreamingDetails',
            id=','.join(video_ids)
        ).execute()

        for video in video_response.get('items', []):
            if 'liveStreamingDetails' in video:
                scheduled_start_time = video['liveStreamingDetails'].get('scheduledStartTime')
                if scheduled_start_time:
                    start_time_utc = dateutil.parser.parse(scheduled_start_time)
                    start_time_la = start_time_utc.astimezone(la_tz)
                    if start_time_la.date() == target_date:
                        return True
        return False
    except HttpError as e:
        print(f"An HTTP error occurred getting streams: {e}")
        return False
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        return False

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

def main():
    if len(sys.argv) < 3:
        print("Usage: python create_youtube_stream.py <MM-DD-YYYY> <thumbnail_path> [HH:MM]")
        return

    date_str = sys.argv[1]
    thumbnail_path = sys.argv[2]
    time_str = sys.argv[3] if len(sys.argv) > 3 and sys.argv[3].strip() else "10:30"

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

    service = get_youtube_service()
    if not service:
        return

    if stream_exists_for_date(service, target_date, la_tz):
        print(f"A stream already exists in the playlist for {target_date}. Skipping creation.")
        return

    title = get_title()
    description = get_combined_description()

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
                    "enableDvr": True
                }
            }
        ).execute()

        broadcast_id = broadcast_insert_response["id"]
        print(f"Successfully created broadcast with ID: {broadcast_id}")

        # Update category
        video_update_response = service.videos().update(
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
        print(f"Successfully set category to Activism and Non Profit.")

        # Add to playlist
        playlist_insert_response = service.playlistItems().insert(
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
        print(f"Successfully added to playlist {PLAYLIST_ID}")

        # Upload Thumbnail
        if os.path.exists(thumbnail_path):
            print(f"Uploading thumbnail from {thumbnail_path}...")
            media = MediaFileUpload(thumbnail_path, mimetype='image/jpeg', resumable=True)
            thumbnail_response = service.thumbnails().set(
                videoId=broadcast_id,
                media_body=media
            ).execute()
            print("Successfully set thumbnail.")
        else:
            print(f"Thumbnail path not found: {thumbnail_path}")

        # Dispatch event
        dispatch_event(broadcast_id, target_date.strftime("%m-%d-%Y"))

    except HttpError as e:
        print(f"Failed to create stream or set metadata: {e}")

if __name__ == '__main__':
    main()
