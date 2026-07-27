import os
import json
import sys
import re
from datetime import datetime, timezone
import dateutil.parser
from pythonosc.dispatcher import Dispatcher
from pythonosc.osc_server import BlockingOSCUDPServer
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google.auth.exceptions import RefreshError

OSC_IP = "0.0.0.0"
OSC_PORT = 8000
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

def get_live_stream(service):
    # First attempt: search user's active live broadcasts directly
    try:
        request = service.liveBroadcasts().list(
            part="id",
            broadcastStatus="active",
            broadcastType="all"
        )
        response = request.execute()
        if response.get('items'):
            video_id = response['items'][0]['id']
            video_response = service.videos().list(
                part="snippet,liveStreamingDetails",
                id=video_id
            ).execute()
            if video_response.get('items'):
                return video_response['items'][0]
    except Exception as e:
        print(f"liveBroadcasts search failed or unavailable, falling back to playlist: {e}")

    # Fallback to playlist search (as used elsewhere in the repo)
    try:
        playlist_response = service.playlistItems().list(
            part='snippet',
            playlistId=PLAYLIST_ID,
            maxResults=50
        ).execute()

        video_ids = [item['snippet']['resourceId']['videoId'] for item in playlist_response.get('items', [])]
        if not video_ids:
            return None

        video_response = service.videos().list(
            part='snippet,liveStreamingDetails',
            id=','.join(video_ids)
        ).execute()

        for video in video_response.get('items', []):
            snippet = video.get('snippet', {})
            if snippet.get('liveBroadcastContent') == 'live' and 'liveStreamingDetails' in video:
                if video['liveStreamingDetails'].get('actualStartTime'):
                    return video

    except Exception as e:
        print(f"An error occurred getting streams from playlist: {e}")

    return None

def add_timestamp_to_description(description, elapsed_str):
    lines = description.split('\n')
    timestamp_pattern = re.compile(r'^(\d{1,2}:)?\d{1,2}:\d{2}\s+')

    last_timestamp_idx = -1
    for i, line in enumerate(lines):
        if timestamp_pattern.match(line.strip()):
            last_timestamp_idx = i

    if last_timestamp_idx != -1:
        # Find the next non-empty line after the last timestamp
        for i in range(last_timestamp_idx + 1, len(lines)):
            if lines[i].strip():
                lines[i] = f"{elapsed_str} {lines[i].strip()}"
                return '\n'.join(lines), True
        return description, False

    # If no timestamps exist, we need to find the start of the sections block.
    # The boilerplate ends after the social media links. Let's find the last line containing a link.
    last_link_idx = -1
    for i, line in enumerate(lines):
        if 'http://' in line or 'https://' in line:
            last_link_idx = i

    # The first non-empty line after the last link is the first section
    start_idx = last_link_idx + 1 if last_link_idx != -1 else 0
    for i in range(start_idx, len(lines)):
        if lines[i].strip():
            lines[i] = f"{elapsed_str} {lines[i].strip()}"
            return '\n'.join(lines), True

    return description, False

def handle_osc_message(address, *args):
    print(f"Received OSC message on {address} with arguments: {args}")

    service = get_youtube_service()
    if not service:
        print("Could not get YouTube service.")
        return

    video = get_live_stream(service)
    if not video:
        print("No active live stream found.")
        return

    actual_start_time = video['liveStreamingDetails'].get('actualStartTime')
    if not actual_start_time:
        print("No actual start time found on the stream.")
        return

    # Calculate elapsed time
    start_time = dateutil.parser.parse(actual_start_time)
    now = datetime.now(timezone.utc)
    elapsed = now - start_time

    total_seconds = int(elapsed.total_seconds())
    if total_seconds < 0:
        total_seconds = 0

    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)

    if hours > 0:
        elapsed_str = f"{hours}:{minutes:02d}:{seconds:02d}"
    else:
        elapsed_str = f"{minutes}:{seconds:02d}"

    print(f"Calculated elapsed time: {elapsed_str}")

    snippet = video.get('snippet', {})
    current_desc = snippet.get('description', '')

    new_desc, changed = add_timestamp_to_description(current_desc, elapsed_str)

    if changed:
        print(f"Adding timestamp '{elapsed_str}' to description...")
        snippet['description'] = new_desc

        try:
            # We must send back the full snippet object so we don't overwrite metadata!
            service.videos().update(
                part='snippet',
                body={
                    'id': video['id'],
                    'snippet': snippet
                }
            ).execute()
            print("Successfully updated YouTube description.")
        except HttpError as e:
            print(f"Failed to update description: {e}")
    else:
        print("Could not find a suitable line to add a timestamp to, or description is fully timestamped.")

def main():
    dispatcher = Dispatcher()
    dispatcher.set_default_handler(handle_osc_message)

    print(f"Starting OSC server on {OSC_IP}:{OSC_PORT}...")
    server = BlockingOSCUDPServer((OSC_IP, OSC_PORT), dispatcher)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Stopping OSC server.")

if __name__ == "__main__":
    main()
