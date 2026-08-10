"""Read-only diagnostic: print the actual title/status of the video(s) in the
Sermon Series YouTube playlist that migrate_videos.py matched against, plus
the video scheduled for 08-09-2026 in the create_sermon_series.py playlist.
No writes performed.
"""
import os
import json
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials

MIGRATE_PLAYLIST_ID = 'PLGtiSp5WvUc9v95hvMCERRUvWBXJJsGrP'
SERIES_PLAYLIST_ID = 'PLGtiSp5WvUc_I0M_vvfSdGY9dJ43ZofXs'


def get_youtube_service():
    creds_json = os.environ.get('YOUTUBE_CREDENTIALS_JSON')
    creds_info = json.loads(creds_json)
    creds = Credentials.from_authorized_user_info(creds_info)
    return build('youtube', 'v3', credentials=creds)


def dump_playlist(service, playlist_id, label):
    print(f"\n=== {label} ({playlist_id}) ===")
    next_page_token = None
    while True:
        resp = service.playlistItems().list(
            part='snippet',
            playlistId=playlist_id,
            maxResults=50,
            pageToken=next_page_token
        ).execute()
        video_ids = [item['snippet']['resourceId']['videoId'] for item in resp.get('items', [])]
        if video_ids:
            vresp = service.videos().list(part='snippet,status,liveStreamingDetails', id=','.join(video_ids)).execute()
            for v in vresp.get('items', []):
                sched = v.get('liveStreamingDetails', {}).get('scheduledStartTime')
                print(f"  id={v['id']}  title={v['snippet']['title']!r}  privacy={v['status']['privacyStatus']}  scheduledStart={sched}")
        next_page_token = resp.get('nextPageToken')
        if not next_page_token:
            break


def main():
    service = get_youtube_service()
    dump_playlist(service, MIGRATE_PLAYLIST_ID, "migrate_videos.py playlist")
    dump_playlist(service, SERIES_PLAYLIST_ID, "create_sermon_series.py playlist")


if __name__ == "__main__":
    main()
