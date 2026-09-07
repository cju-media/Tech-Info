"""
Adds a "Calendar" column (column L) to the master Tech Schedule Google
Sheet, one HYPERLINK per dated row pointing at that event's modal on the
GitHub Pages dashboard:

    https://cju-media.github.io/Tech-Info/#<element-id>

The dashboard (root index.html) reads location.hash on load and calls
openModal() for the matching event, so the link opens straight to that
event. The <element-id> is built with the SAME formula the dashboard and
send_weekly_schedule.py use:

    element_id = "<sanitized date> - <sanitized event name>"
    sanitized date  = lower(re.sub(r'[/\\s]+', '-', column A))
    sanitized name  = lower(re.sub(r'[^a-z0-9]+', '-', column B)).strip('-')

If the dashboard's anchor scheme in index.html ever changes, element_id()
below has to change with it (see index.html renderEvents / openModal, and
Utilities/Scheduling/Event Data/send_weekly_schedule.py).

Only column L is ever written. A row is updated only when L is empty or
already holds one of our dashboard links with a stale target; anything
else in L is left alone and logged.

Auth: GDRIVE_SERVICE_ACCOUNT_JSON (same secret the sermon-series and
mark-past-events workflows use). The service account must be an Editor on
the spreadsheet and the Sheets API must be enabled on its GCP project.

Env:
    GDRIVE_SERVICE_ACCOUNT_JSON  service account credentials (required to write)
    DRY_RUN                      "1" (default) = log only, "0" = apply
"""

import os
import re
import json
import sys
import urllib.request

from googleapiclient.discovery import build
from google.oauth2 import service_account

SHEET_ID = '1UC8vgy89W14bVEWROqdUc9VgkMTGykC5ZZJqSDmi2-A'
GID = 251348517  # the "master schedule" tab

DASHBOARD_BASE = 'https://cju-media.github.io/Tech-Info/#'
LINK_COLUMN = 'L'          # first free column after K ("PAYCOM CODE")
LINK_COLUMN_INDEX = 11     # 0-based, matches column L
LINK_LABEL = 'View'
HEADER_LABEL = 'Calendar'

DATE_RE = re.compile(r'(\d{1,2}/\d{1,2}/\d{2,4})')
# Pull the target out of an existing =HYPERLINK("url", "label") formula.
HYPERLINK_URL_RE = re.compile(r'HYPERLINK\(\s*"([^"]*)"', re.IGNORECASE)

SCOPES = ['https://www.googleapis.com/auth/spreadsheets']


def element_id(date_str, event_name):
    """Replicates the anchor id built in index.html and
    send_weekly_schedule.py. Keep the three in sync."""
    sanitized_name = re.sub(r'[^a-z0-9]+', '-', str(event_name).lower()).strip('-')
    sanitized_date = re.sub(r'[/\s]+', '-', str(date_str)).lower()
    return f"{sanitized_date}-{sanitized_name}"


def get_sheets_service():
    service_account_json = os.environ.get('GDRIVE_SERVICE_ACCOUNT_JSON')
    if not service_account_json:
        return None
    creds_dict = json.loads(service_account_json)
    creds = service_account.Credentials.from_service_account_info(
        creds_dict, scopes=SCOPES
    )
    print(f"Authenticating as service account: {creds_dict.get('client_email')}")
    return build('sheets', 'v4', credentials=creds)


def fetch_rows_gviz():
    """Read-only fallback for local dry runs without credentials. Returns
    an A:K value grid (column L is simply absent, which the planner treats
    as empty)."""
    url = f'https://docs.google.com/spreadsheets/d/{SHEET_ID}/gviz/tq?gid={GID}&headers=0'
    with urllib.request.urlopen(url) as response:
        data = response.read().decode('utf-8')
    match = re.search(r'google\.visualization\.Query\.setResponse\((.*)\);', data)
    if not match:
        raise ValueError("Could not find JSON data in Google Sheet response")
    payload = json.loads(match.group(1))
    rows = []
    for row in payload['table']['rows']:
        rows.append([(cell['v'] if cell else '') for cell in row['c']])
    return rows


def get_sheet_props(service):
    """Return (title, column_count) for the GID tab."""
    meta = service.spreadsheets().get(
        spreadsheetId=SHEET_ID,
        fields='sheets(properties(sheetId,title,gridProperties(columnCount)))',
    ).execute()
    for sheet in meta.get('sheets', []):
        props = sheet.get('properties', {})
        if props.get('sheetId') == GID:
            column_count = props.get('gridProperties', {}).get('columnCount', 0)
            return props['title'], column_count
    raise ValueError(f"No tab with sheetId {GID} in spreadsheet {SHEET_ID}")


def ensure_column_exists(service):
    """The sheet ships with exactly 11 columns (A-K); column L has to be
    added to the grid before any value can be written to it."""
    _title, column_count = get_sheet_props(service)
    if column_count >= LINK_COLUMN_INDEX + 1:
        return
    missing = (LINK_COLUMN_INDEX + 1) - column_count
    print(f"Sheet has {column_count} columns; adding {missing} to reach column {LINK_COLUMN}.")
    service.spreadsheets().batchUpdate(
        spreadsheetId=SHEET_ID,
        body={'requests': [{
            'appendDimension': {'sheetId': GID, 'dimension': 'COLUMNS', 'length': missing}
        }]},
    ).execute()


def desired_formula(url):
    return f'=HYPERLINK("{url}", "{LINK_LABEL}")'


def plan_updates(rows):
    """Given the A:L value grid (FORMULA render), return (updates, skips,
    duplicates). updates is a list of (sheet_row, event_name, url); skips
    is a list of (sheet_row, reason, current_value)."""
    updates = []
    skips = []
    seen_ids = {}
    duplicates = []

    for index, row in enumerate(rows):
        sheet_row = index + 1
        if index == 0:
            continue  # header row handled separately

        date_cell = row[0] if len(row) > 0 else ''
        date_match = DATE_RE.search(str(date_cell))
        if not date_match:
            continue  # month divider / blank / non-dated row

        event_name = str(row[1]).strip() if len(row) > 1 and row[1] is not None else ''
        if not event_name:
            skips.append((sheet_row, 'no event name in column B', ''))
            continue

        eid = element_id(date_cell, event_name)
        url = DASHBOARD_BASE + eid
        seen_ids.setdefault(eid, []).append(sheet_row)

        current = str(row[LINK_COLUMN_INDEX]).strip() if len(row) > LINK_COLUMN_INDEX and row[LINK_COLUMN_INDEX] is not None else ''

        if not current:
            updates.append((sheet_row, event_name, url))
            continue

        existing_url_match = HYPERLINK_URL_RE.search(current)
        if existing_url_match and existing_url_match.group(1).startswith(DASHBOARD_BASE):
            if existing_url_match.group(1) != url:
                updates.append((sheet_row, event_name, url))
            # else: already correct, nothing to do
            continue

        skips.append((sheet_row, 'column L holds non-dashboard content', current))

    for eid, sheet_rows in seen_ids.items():
        if len(sheet_rows) > 1:
            duplicates.append((eid, sheet_rows))

    return updates, skips, duplicates


def main():
    is_dry_run = os.environ.get('DRY_RUN', '1') == '1'

    service = get_sheets_service()
    if not service and not is_dry_run:
        print("GDRIVE_SERVICE_ACCOUNT_JSON is not set; cannot write the sheet.")
        sys.exit(1)

    title = None
    if service:
        title, _column_count = get_sheet_props(service)
        print(f"Reading '{title}'!A:L ...")
        result = service.spreadsheets().values().get(
            spreadsheetId=SHEET_ID,
            range=f"'{title}'!A:L",
            valueRenderOption='FORMULA',
        ).execute()
        rows = result.get('values', [])
    else:
        print("No credentials; reading via the public gviz endpoint for this dry run.")
        rows = fetch_rows_gviz()
    print(f"Got {len(rows)} row(s).")

    header_row = rows[0] if rows else []
    header_current = str(header_row[LINK_COLUMN_INDEX]).strip() if len(header_row) > LINK_COLUMN_INDEX else ''
    header_needs_write = header_current != HEADER_LABEL

    updates, skips, duplicates = plan_updates(rows)

    if duplicates:
        print(f"\n{len(duplicates)} duplicate anchor id(s) - these rows share a link "
              f"and will all open the first matching event:")
        for eid, sheet_rows in duplicates:
            print(f"  {eid}  ->  rows {sheet_rows}")

    if skips:
        print(f"\n{len(skips)} row(s) skipped:")
        for sheet_row, reason, current in skips:
            detail = f" (has: {current!r})" if current else ""
            print(f"  row {sheet_row}: {reason}{detail}")

    print(f"\nHeader L1: {'will set to ' + HEADER_LABEL if header_needs_write else 'already ' + repr(HEADER_LABEL)}")
    print(f"{len(updates)} row link(s) to write:")
    for sheet_row, event_name, url in updates[:40]:
        print(f"  L{sheet_row}: {event_name!r} -> {url}")
    if len(updates) > 40:
        print(f"  ... and {len(updates) - 40} more")

    if not updates and not header_needs_write:
        print("\nNothing to do.")
        return

    if is_dry_run:
        print("\nDRY RUN: not writing. Set DRY_RUN=0 to apply.")
        return

    ensure_column_exists(service)

    data = []
    if header_needs_write:
        data.append({'range': f"'{title}'!{LINK_COLUMN}1", 'values': [[HEADER_LABEL]]})
    for sheet_row, _event_name, url in updates:
        data.append({
            'range': f"'{title}'!{LINK_COLUMN}{sheet_row}",
            'values': [[desired_formula(url)]],
        })

    try:
        service.spreadsheets().values().batchUpdate(
            spreadsheetId=SHEET_ID,
            body={'valueInputOption': 'USER_ENTERED', 'data': data},
        ).execute()
    except Exception as e:
        print(f"Failed to write links: {e}")
        sys.exit(1)

    print(f"\nWrote {len(data)} cell(s) to column {LINK_COLUMN}.")


if __name__ == '__main__':
    main()
