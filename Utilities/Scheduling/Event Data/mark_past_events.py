"""
Marks past-dated rows on the master Tech Schedule Google Sheet as done:
strikethrough text + red font color, applied to the whole row.

The header row (row 1) is never touched, no matter what. Section-header
rows (e.g. "SEPTEMBER") have no parseable date in column A and are
skipped automatically. Rows are only ever added to; nothing here reverts
formatting on rows whose date is edited back into the future.

Auth: uses the existing GDRIVE_SERVICE_ACCOUNT_JSON secret (already used
by the sermon series workflow) with the Sheets API. That service account
must be shared as an Editor on this spreadsheet, and the Google Sheets
API must be enabled on the same GCP project as the Drive API.
"""

import os
import re
import json
import sys
import urllib.request
import datetime
import zoneinfo

from dateutil import parser as dateutil_parser
from googleapiclient.discovery import build
from google.oauth2 import service_account

SHEET_ID = '1UC8vgy89W14bVEWROqdUc9VgkMTGykC5ZZJqSDmi2-A'
GID = 251348517  # doubles as the Sheets API numeric sheetId for this tab

DATE_RE = re.compile(r'(\d{1,2}/\d{1,2}/\d{2,4})')


def fetch_rows():
    url = f'https://docs.google.com/spreadsheets/d/{SHEET_ID}/gviz/tq?gid={GID}&headers=0'
    with urllib.request.urlopen(url) as response:
        data = response.read().decode('utf-8')

    match = re.search(r'google\.visualization\.Query\.setResponse\((.*)\);', data)
    if not match:
        raise ValueError("Could not find JSON data in Google Sheet response")

    payload = json.loads(match.group(1))
    return payload['table']['rows']


def find_past_event_rows(rows, today):
    """Return 0-based row indices (matching the Sheets API grid) whose
    event date has already passed. Row 0 (the header) is never included."""
    past_rows = []

    for index, row in enumerate(rows):
        if index == 0:
            continue  # never touch the header row

        cells = row.get('c') or []
        date_cell = cells[0]['v'] if cells and cells[0] else None
        if not date_cell or not isinstance(date_cell, str):
            continue

        date_match = DATE_RE.search(date_cell)
        if not date_match:
            continue  # e.g. section headers like "SEPTEMBER" - no date, skip

        try:
            event_date = dateutil_parser.parse(date_match.group(1)).date()
        except (ValueError, OverflowError):
            continue

        if event_date < today:
            past_rows.append(index)

    return past_rows


def get_sheets_service():
    service_account_json = os.environ.get('GDRIVE_SERVICE_ACCOUNT_JSON')
    if not service_account_json:
        print("GDRIVE_SERVICE_ACCOUNT_JSON is not set; cannot authenticate.")
        return None

    creds_dict = json.loads(service_account_json)
    creds = service_account.Credentials.from_service_account_info(
        creds_dict, scopes=['https://www.googleapis.com/auth/spreadsheets']
    )
    print(f"Authenticating as service account: {creds_dict.get('client_email')}")
    return build('sheets', 'v4', credentials=creds)


def build_format_request(row_index):
    return {
        "repeatCell": {
            "range": {
                "sheetId": GID,
                "startRowIndex": row_index,
                "endRowIndex": row_index + 1,
            },
            "cell": {
                "userEnteredFormat": {
                    "textFormat": {
                        "strikethrough": True,
                        "foregroundColor": {"red": 1, "green": 0, "blue": 0},
                    }
                }
            },
            "fields": "userEnteredFormat.textFormat.strikethrough,userEnteredFormat.textFormat.foregroundColor",
        }
    }


def main():
    is_dry_run = os.environ.get('DRY_RUN', '1') == '1'
    tz = zoneinfo.ZoneInfo("America/Los_Angeles")
    today = datetime.datetime.now(tz).date()

    print(f"Checking for past-dated rows as of {today} (America/Los_Angeles)...")
    rows = fetch_rows()
    past_rows = find_past_event_rows(rows, today)

    if not past_rows:
        print("No past-dated rows found.")
        return

    print(f"Found {len(past_rows)} past-dated row(s): "
          f"sheet rows {[r + 1 for r in past_rows]}")

    if is_dry_run:
        print("DRY RUN: not applying formatting. Set DRY_RUN=0 to apply.")
        return

    service = get_sheets_service()
    if not service:
        print("No Sheets service available; aborting.")
        sys.exit(1)

    requests = [build_format_request(r) for r in past_rows]

    try:
        service.spreadsheets().batchUpdate(
            spreadsheetId=SHEET_ID, body={"requests": requests}
        ).execute()
    except Exception as e:
        print(f"Failed to apply formatting: {e}")
        sys.exit(1)

    print(f"Applied strikethrough + red font to {len(requests)} row(s).")


if __name__ == "__main__":
    main()
