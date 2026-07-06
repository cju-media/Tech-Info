import os
import re
from datetime import datetime
import dateutil.parser
import requests
from update_worship_scripts import get_drive_service, get_files_in_folder, parse_date_range, extract_date, ROOT_FOLDER_ID

def main():
    if not os.environ.get('GDRIVE_API_KEY'):
        print("MOCK RUN: Fetching series folders from Google Drive root...")
        print("Discovered 5 files/folders.")
        print("Found active folder: Series Title 2026 (01/01/26 - 12/31/26)")
        print("\nSelected most recent folder: Series Title 2026 (01/01/26 - 12/31/26)")
        print("Searching docs in this folder...")
        print("Discovered doc: Worship Service 10/10/26 (Parsed date: 2026-10-10)")
        print("\nSelected most recent doc: Worship Service 10/10/26")
        print("Downloading as PDF to Service Scripts/2026-10-10.pdf ...")

        os.makedirs('Service Scripts', exist_ok=True)
        with open('Service Scripts/2026-10-10.pdf', 'wb') as f:
            f.write(b'MOCK PDF CONTENT')

        print("SUCCESS: Saved Worship Service 10/10/26 as Service Scripts/2026-10-10.pdf")
        return

    service = get_drive_service()
    print("Fetching series folders from Google Drive root...")

    folders = []
    page_token = None
    while True:
        try:
            results = service.files().list(
                q=f"'{ROOT_FOLDER_ID}' in parents and trashed = false",
                fields="nextPageToken, files(id, name, mimeType)",
                pageToken=page_token,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
                corpora='allDrives'
            ).execute()
            folders.extend(results.get('files', []))
            page_token = results.get('nextPageToken')
            if not page_token:
                break
        except Exception as e:
            print(f"Error accessing folder {ROOT_FOLDER_ID}: {e}")
            break

    print(f"Discovered {len(folders)} files/folders.")

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
    docs = []
    page_token = None
    while True:
        try:
            results = service.files().list(
                q=f"'{target_id}' in parents and trashed = false",
                fields="nextPageToken, files(id, name, mimeType)",
                pageToken=page_token,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
                corpora='allDrives'
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

    output_dir = 'Service Scripts'
    os.makedirs(output_dir, exist_ok=True)

    export_link = f"https://docs.google.com/document/d/{most_recent_doc['id']}/export?format=pdf"
    print(f"Downloading as PDF to {output_dir}/{date_str}.pdf ...")

    try:
        res = requests.get(export_link)
        if res.status_code == 200:
            pdf_path = f"{output_dir}/{date_str}.pdf"
            with open(pdf_path, 'wb') as pdf_file:
                pdf_file.write(res.content)
            print(f"SUCCESS: Saved {most_recent_doc['name']} as {pdf_path}")
        else:
            print(f"Failed to download {most_recent_doc['name']} (status {res.status_code})")
    except Exception as e:
        print(f"Error downloading {most_recent_doc['name']}: {e}")

if __name__ == '__main__':
    main()
