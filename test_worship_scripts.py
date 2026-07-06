import os
import re
from datetime import datetime
import dateutil.parser

from update_worship_scripts import get_drive_service, get_files_in_folder, parse_date_range, extract_date, ROOT_FOLDER_ID

def main():
    if not os.environ.get('GDRIVE_API_KEY') and not os.environ.get('GDRIVE_SERVICE_ACCOUNT_JSON'):
        print("MOCK RUN: Fetching series folders from Google Drive root...")
        print("Discovered 5 files/folders.")
        print("Found active folder: Series Title 2026 (01/01/26 - 12/31/26)")
        print("\nSelected most recent folder: Series Title 2026 (01/01/26 - 12/31/26)")
        print("Searching docs in this folder...")
        print("Discovered doc: Worship Service 10/10/26 (Parsed date: 2026-10-10)")
        print("\nSelected most recent doc: Worship Service 10/10/26")
        print("Downloading as PDF to service-scripts/2026-10-10.pdf ...")

        os.makedirs('service-scripts', exist_ok=True)
        with open('service-scripts/2026-10-10.pdf', 'wb') as f:
            f.write(b'MOCK PDF CONTENT')

        print("SUCCESS: Saved Worship Service 10/10/26 as service-scripts/2026-10-10.pdf")
        return

    service = get_drive_service()
    print("Fetching series folders from Google Drive root...")

    folders = []
    page_token = None
    while True:
        try:
            results = service.files().list(
                q=f"'{ROOT_FOLDER_ID}' in parents and trashed = false",
                fields="nextPageToken, files(id, name, mimeType, shortcutDetails)",
                pageToken=page_token,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True
            ).execute()
            folders.extend(results.get('files', []))
            page_token = results.get('nextPageToken')
            if not page_token:
                break
        except Exception as e:
            print(f"Error accessing folder {ROOT_FOLDER_ID}: {e}")
            break


    print(f"Discovered {len(folders)} files/folders.")
    print("--- RAW DISCOVERED FILES ---")
    for folder in folders:
        print(f"Name: {folder.get('name')}, MimeType: {folder.get('mimeType')}")
    print("----------------------------")


    valid_folders = []
    for folder in folders:
        if 'folder' in folder['mimeType'] or 'shortcut' in folder['mimeType']:
            start_dt, end_dt = parse_date_range(folder['name'])
            if start_dt and end_dt:
                valid_folders.append((folder, start_dt, end_dt))
            else:
                print(f"Folder skipped (no valid date range): {folder['name']}")

    if not valid_folders:
        print("No valid folders found with a date range.")
        return

    # Sort to find the most recent folder (by end_dt)
    valid_folders.sort(key=lambda x: x[2], reverse=True)
    most_recent_folder, start_dt, end_dt = valid_folders[0]

    print(f"\nSelected most recent folder: {most_recent_folder['name']}")
    print(f"Searching docs in this folder...")

    target_id = most_recent_folder['id']
    if most_recent_folder['mimeType'] == 'application/vnd.google-apps.shortcut':
        target_id = most_recent_folder.get('shortcutDetails', {}).get('targetId', target_id)
    docs = []
    page_token = None
    while True:
        try:
            results = service.files().list(
                q=f"'{target_id}' in parents and trashed = false",
                fields="nextPageToken, files(id, name, mimeType, shortcutDetails)",
                pageToken=page_token,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True
            ).execute()
            docs.extend(results.get('files', []))
            page_token = results.get('nextPageToken')
            if not page_token:
                break
        except Exception as e:
            print(f"Error accessing folder {target_id}: {e}")
            break

    valid_docs = []
    for doc in docs:
        if doc['mimeType'] == 'application/vnd.google-apps.document':
            date_str = extract_date(doc['name'])
            if date_str:
                doc_dt = dateutil.parser.parse(date_str)
                valid_docs.append((doc, doc_dt, date_str))
                print(f"Discovered doc: {doc['name']} (Parsed date: {date_str})")
            else:
                print(f"Doc skipped (no valid date): {doc['name']}")

    if not valid_docs:
        print("No valid docs found in the most recent folder.")
        return

    # Sort to find the most recent doc
    valid_docs.sort(key=lambda x: x[1], reverse=True)
    most_recent_doc, doc_dt, date_str = valid_docs[0]

    print(f"\nSelected most recent doc: {most_recent_doc['name']}")

    output_dir = 'service-scripts'
    os.makedirs(output_dir, exist_ok=True)

    print(f"Downloading as PDF to {output_dir}/{date_str}.pdf ...")

    try:
        pdf_path = f"{output_dir}/{date_str}.pdf"
        request = service.files().export_media(fileId=most_recent_doc['id'], mimeType='application/pdf')
        pdf_content = request.execute()
        with open(pdf_path, 'wb') as pdf_file:
            pdf_file.write(pdf_content)
        print(f"SUCCESS: Saved {most_recent_doc['name']} as {pdf_path}")
    except Exception as e:
        print(f"Error downloading {most_recent_doc['name']}: {e}")

if __name__ == '__main__':
    main()
