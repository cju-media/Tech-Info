"""Read-only diagnostic: check status of specific video IDs and dump the
migrate_videos.py playlist contents. No writes performed.
"""
import os
import json
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials

MIGRATE_PLAYLIST_ID = 'PLGtiSp5WvUc9v95hvMCERRUvWBXJJsGrP'
CHECK_VIDEO_IDS = ['_0XCjzd2M5k', 'Hn0N_jtrpEU']


def get_youtube_service():
    creds_json = os.environ.get('YOUTUBE_CREDENTIALS_JSON')
    creds_info = json.loads(creds_json)
    creds = Credentials.from_authorized_user_info(creds_info)
    return build('youtube', 'v3', credentials=creds)


def main():
    service = get_youtube_service()

    print(f"=== Direct videos().list check for {CHECK_VIDEO_IDS} ===")
    resp = service.videos().list(part='snippet,status', id=','.join(CHECK_VIDEO_IDS)).execute()
    found_ids = set()
    for v in resp.get('items', []):
        found_ids.add(v['id'])
        print(f"  FOUND id={v['id']}  title={v['snippet']['title']!r}  privacy={v['status']['privacyStatus']}")
    for vid in CHECK_VIDEO_IDS:
        if vid not in found_ids:
            print(f"  NOT FOUND (deleted or inaccessible): {vid}")

    print(f"\n=== Full playlist dump: {MIGRATE_PLAYLIST_ID} ===")
    next_page_token = None
    while True:
        resp = service.playlistItems().list(
            part='snippet,status',
            playlistId=MIGRATE_PLAYLIST_ID,
            maxResults=50,
            pageToken=next_page_token
        ).execute()
        for item in resp.get('items', []):
            vid_id = item['snippet']['resourceId']['videoId']
            title = item['snippet']['title']
            print(f"  playlistItem id={item['id']}  videoId={vid_id}  title={title!r}")
        next_page_token = resp.get('nextPageToken')
        if not next_page_token:
            break


if __name__ == "__main__":
    main()
