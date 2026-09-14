"""Push the corrected Sermon Series description onto an already-uploaded video.

migrate_videos.py sets a video's description once, at upload time, from the
"SS Description-<date>.txt" file in Drive. If that file was still carrying the
"YOUTUBE SERVICE LINK" placeholder at that moment -- which happens when
backfill_sermon_series_link.py hasn't run yet -- fixing the text files
afterwards does nothing for the video that already went public.
backfill_sermon_series_link.py only knows about git and Drive.

This is the missing last step: take the now-correct description file and put it
on the video. Idempotent, and it refuses to publish a description that still
has the placeholder in it.
"""

import os
import sys
import json
import argparse
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

STREAM_LINK_PLACEHOLDER = 'YOUTUBE SERVICE LINK'
LAST_UPLOAD_PATH = os.path.join("Utilities", "Video Migration", "last_upload.json")


def get_youtube_service():
    creds_json = os.environ.get('YOUTUBE_CREDENTIALS_JSON')
    if not creds_json:
        print("Error: YOUTUBE_CREDENTIALS_JSON environment variable not found.")
        return None

    try:
        creds_info = json.loads(creds_json)
        return build('youtube', 'v3', credentials=Credentials.from_authorized_user_info(creds_info))
    except Exception as e:
        print(f"Error authenticating with YouTube: {e}")
        return None


def read_description(date_str):
    path = os.path.join("Worship Scripts", "Sermon-Series", f"SS Description-{date_str}.txt")
    if not os.path.exists(path):
        print(f"No description file at {path}.")
        return None

    with open(path) as f:
        description = f.read().strip()

    if not description:
        print(f"{path} is empty.")
        return None

    if STREAM_LINK_PLACEHOLDER in description:
        print(f"{path} still contains the '{STREAM_LINK_PLACEHOLDER}' placeholder. "
              "Run the 'Backfill Sermon Series Stream Link' workflow first -- refusing "
              "to publish the placeholder.")
        return None

    return description


def video_id_for_date(date_str):
    """The migrated video for date_str, per the breadcrumb migrate_videos.py writes."""
    try:
        with open(LAST_UPLOAD_PATH) as f:
            last = json.load(f)
    except Exception as e:
        print(f"Could not read {LAST_UPLOAD_PATH}: {e}")
        return None

    if last.get("service_date") != date_str:
        print(f"{LAST_UPLOAD_PATH} records {last.get('service_date')}, not {date_str}. "
              "Pass --video-id explicitly for an older service.")
        return None

    url = last.get("video_url", "")
    return url.rsplit("v=", 1)[-1] if "v=" in url else None


def main():
    parser = argparse.ArgumentParser(
        description="Push the corrected Sermon Series description onto its YouTube video.")
    parser.add_argument("date", help="Service date (MM-DD-YYYY)")
    parser.add_argument("--video-id", help="Override the video id from last_upload.json")
    args = parser.parse_args()

    description = read_description(args.date)
    if not description:
        sys.exit(1)

    video_id = args.video_id or video_id_for_date(args.date)
    if not video_id:
        print("Could not determine which video to update.")
        sys.exit(1)

    service = get_youtube_service()
    if not service:
        sys.exit(1)

    # videos.update replaces the whole snippet, so start from the live one and
    # change only the description -- sending a bare snippet would blank the
    # title and category.
    response = service.videos().list(part="snippet", id=video_id).execute()
    items = response.get("items", [])
    if not items:
        print(f"No video found with id {video_id} (is it on this channel?).")
        sys.exit(1)

    snippet = items[0]["snippet"]
    print(f"Updating '{snippet.get('title')}' ({video_id})...")

    if snippet.get("description", "").strip() == description:
        print("The video already has this exact description. Nothing to do.")
        return

    snippet["description"] = description
    service.videos().update(part="snippet", body={"id": video_id, "snippet": snippet}).execute()
    print(f"Updated description for https://www.youtube.com/watch?v={video_id}")


if __name__ == "__main__":
    main()
