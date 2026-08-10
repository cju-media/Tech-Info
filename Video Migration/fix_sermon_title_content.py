"""One-off: correct the stale Sermon Series Title text file content in Drive
for 08-09-2026. The file currently reads
'Radical Love - Rev. Michael Lehman || FCCLA Sermon' (stale, generated Aug 3
before the source title was corrected Aug 5). The correct value is
'Kin-dom Economics - Rev. Michael Lehman || FCCLA Sermon'.
"""
import os
import io
import json
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload, MediaIoBaseDownload
from google.oauth2.credentials import Credentials

TARGET_FOLDER_ID = '1W2gpCIZ9zXOFonDwVrl3CrzSHuxEIHgp'
TITLE_FILE_ID = '14M19IOTSxRbcsDwGOn7L4bjIROimBW0v'
CORRECT_CONTENT = 'Kin-dom Economics - Rev. Michael Lehman || FCCLA Sermon'


def get_drive_service():
    oauth_json = os.environ.get('GDRIVE_OAUTH_JSON')
    creds_dict = json.loads(oauth_json)
    creds = Credentials.from_authorized_user_info(creds_dict, scopes=['https://www.googleapis.com/auth/drive'])
    return build('drive', 'v3', credentials=creds)


def read_file(service, file_id):
    request = service.files().get_media(fileId=file_id)
    fh = io.BytesIO()
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return fh.getvalue().decode('utf-8')


def main():
    service = get_drive_service()

    current = read_file(service, TITLE_FILE_ID)
    print(f"Current content: {current!r}")

    media = MediaIoBaseUpload(io.BytesIO(CORRECT_CONTENT.encode('utf-8')), mimetype='text/plain', resumable=True)
    service.files().update(fileId=TITLE_FILE_ID, media_body=media, supportsAllDrives=True).execute()
    print(f"Updated to: {CORRECT_CONTENT!r}")

    verify = read_file(service, TITLE_FILE_ID)
    print(f"Verified content now: {verify!r}")


if __name__ == "__main__":
    main()
