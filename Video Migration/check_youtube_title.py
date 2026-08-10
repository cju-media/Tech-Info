"""Read-only: find the livestream scheduled for 2026-08-09 in the Sunday
Service playlist that create_youtube_stream.py uses."""
import os
import json
import dateutil.parser
import zoneinfo
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials

SERIES_PLAYLIST_ID = 'PLGtiSp5WvUc_I0M_vvfSdGY9dJ43ZofXs'


def get_youtube_service():
    creds_json = os.environ.get('YOUTUBE_CREDENTIALS_JSON')
    creds_info = json.loads(creds_json)
    creds = Credentials.from_authorized_user_info(creds_info)
    return build('youtube', 'v3', credentials=creds)


def main():
    service = get_youtube_service()
    la_tz = zoneinfo.ZoneInfo('America/Los_Angeles')

    resp = service.playlistItems().list(part='snippet', playlistId=SERIES_PLAYLIST_ID, maxResults=50).execute()
    video_ids = [item['snippet']['resourceId']['videoId'] for item in resp.get('items', [])]
    vresp = service.videos().list(part='snippet,liveStreamingDetails,status', id=','.join(video_ids)).execute()
    for v in vresp.get('items', []):
        sched = v.get('liveStreamingDetails', {}).get('scheduledStartTime')
        sched_la = None
        if sched:
            sched_la = dateutil.parser.parse(sched).astimezone(la_tz)
        print(f"id={v['id']}  title={v['snippet']['title']!r}  privacy={v['status']['privacyStatus']}  scheduledStart={sched}  ({sched_la.date() if sched_la else None})")


if __name__ == "__main__":
    main()
