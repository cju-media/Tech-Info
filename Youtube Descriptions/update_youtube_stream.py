import os
import json
import dateutil.parser
from datetime import datetime
import pytz
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

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
        # Get videos from the playlist
        playlist_response = service.playlistItems().list(
            part='snippet',
            playlistId=PLAYLIST_ID,
            maxResults=50
        ).execute()

        for item in playlist_response.get('items', []):
            video_id = item['snippet']['resourceId']['videoId']

            # Fetch video details to check if it's an upcoming live stream
            video_response = service.videos().list(
                part='snippet,liveStreamingDetails',
                id=video_id
            ).execute()

            if not video_response.get('items'):
                continue

            video = video_response['items'][0]
            snippet = video['snippet']

            # Check if it's an upcoming live broadcast
            if snippet.get('liveBroadcastContent') == 'upcoming' and 'liveStreamingDetails' in video:
                scheduled_start_time = video['liveStreamingDetails'].get('scheduledStartTime')
                if scheduled_start_time:
                    upcoming_streams.append({
                        'id': video_id,
                        'title': snippet['title'],
                        'description': snippet['description'],
                        'scheduledStartTime': scheduled_start_time,
                        'categoryId': snippet.get('categoryId'),
                        'tags': snippet.get('tags', [])
                    })

    except HttpError as e:
        print(f"An HTTP error occurred getting streams: {e}")

    return upcoming_streams

def format_date_for_ows(dt):
    # Format date as M-DD-YY
    return f"{dt.month}-{dt.day:02d}-{dt.strftime('%y')}"

def get_combined_description(service_date_str, dt):
    # Read boilerplate
    boiler_path = "DescriptionBoiler.txt"
    if not os.path.exists(boiler_path):
        print(f"Boilerplate not found at {boiler_path}")
        return None

    with open(boiler_path, 'r') as f:
        boilerplate = f.read()

    # Read generated description
    desc_path = os.path.join("Processed Scripts", service_date_str, f"Description {service_date_str}.txt")
    if not os.path.exists(desc_path):
        print(f"Generated description not found at {desc_path}")
        return None

    with open(desc_path, 'r') as f:
        generated_desc = f.read()

    # Update order of worship URL in boilerplate
    ows_date_str = format_date_for_ows(dt)
    boilerplate = boilerplate.replace("[DATE OF SERVICE]", ows_date_str)

    # Combine
    combined = f"{generated_desc.strip()}\n\n{boilerplate.strip()}"
    return combined

def main():
    service = get_youtube_service()
    if not service:
        return

    upcoming_streams = get_upcoming_streams(service)
    if not upcoming_streams:
        print("No upcoming streams found in the playlist.")
        return

    la_tz = pytz.timezone('America/Los_Angeles')
    now_la = datetime.now(la_tz)

    for stream in upcoming_streams:
        print(f"Checking stream: {stream['title']} (ID: {stream['id']})")

        # Parse start time and convert to LA timezone
        start_time_utc = dateutil.parser.parse(stream['scheduledStartTime'])
        start_time_la = start_time_utc.astimezone(la_tz)

        # Determine the service date
        service_date = start_time_la.date()
        service_date_str = service_date.strftime('%Y-%m-%d')
        print(f"  Service date: {service_date_str}")

        # Stop updating on the day of the service (after 12:00 AM)
        # That means if the current date is >= the service date, skip it.
        if now_la.date() >= service_date:
            print("  Today is the service day (or past). Skipping updates.")
            continue

        # Get combined description
        combined_desc = get_combined_description(service_date_str, start_time_la)
        if not combined_desc:
            print("  Could not generate combined description. Skipping.")
            continue

        # Check if they match
        if stream['description'].strip() == combined_desc.strip():
            print("  Description is already up to date.")
        else:
            print("  Description does not match. Updating...")

            try:
                # Update the video
                service.videos().update(
                    part='snippet',
                    body={
                        'id': stream['id'],
                        'snippet': {
                            'title': stream['title'],
                            'description': combined_desc,
                            'categoryId': stream['categoryId'],
                            'tags': stream['tags']
                        }
                    }
                ).execute()
                print("  Successfully updated YouTube description.")
            except HttpError as e:
                print(f"  Failed to update description: {e}")

if __name__ == '__main__':
    main()
