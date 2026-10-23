"""
Moves rows checked out on event-draft.html into the master Tech Schedule
Google Sheet, then removes them from event_draft.json.

The sheet runs newest-first with a month-divider row ("OCTOBER") above each
month's block, so each row is inserted into its month block in date order
(creating the divider when that month has no block yet). Columns A-K are
written; column L (Event Notes) belongs to sync_calendar_notes.py and is left
alone. New rows are explicitly un-struck / default-colored so they never
inherit the red strikethrough of a past row next to them.

Rows already on the sheet (same date + same name) are skipped, so a repeated
click or a re-run is harmless.

Input (env):
    DRAFT_ROWS            JSON list of {"key": str, "cells": [11 strings]}
                          from the page; key is the row's original
                          "date|name|note" so it can be dropped from the draft.
    GDRIVE_SERVICE_ACCOUNT_JSON  Sheets read/write (Editor on the sheet)
    DRY_RUN               "1" = report only, no sheet or draft changes.
"""

import json
import os
import re
import sys
import datetime

from dateutil import parser as dateutil_parser

SHEET_ID = '1UC8vgy89W14bVEWROqdUc9VgkMTGykC5ZZJqSDmi2-A'
GID = 251348517
NUM_COLS = 11  # A..K; L is calendar-owned
DRAFT_PATH = 'event_draft.json'
DATE_RE = re.compile(r'(\d{1,2}/\d{1,2}/\d{2,4})')
MONTHS = ['JANUARY', 'FEBRUARY', 'MARCH', 'APRIL', 'MAY', 'JUNE', 'JULY',
          'AUGUST', 'SEPTEMBER', 'OCTOBER', 'NOVEMBER', 'DECEMBER']


def parse_date(cell):
    m = DATE_RE.search(cell or '')
    if not m:
        return None
    try:
        return dateutil_parser.parse(m.group(1)).date()
    except (ValueError, OverflowError):
        return None


def divider_month(row):
    """Month index (0-11) if this is a month-divider row, else None."""
    first = (row[0] if row else '').strip()
    if not first or DATE_RE.search(first):
        return None
    if any(str(c).strip() for c in row[1:NUM_COLS]):
        return None
    word = first.split()[0].upper()
    return MONTHS.index(word) if word in MONTHS else None


def norm(s):
    return re.sub(r'\s+', ' ', str(s)).strip().lower()


def get_service():
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    blob = os.environ.get('GDRIVE_SERVICE_ACCOUNT_JSON')
    if not blob:
        print('GDRIVE_SERVICE_ACCOUNT_JSON is not set; cannot authenticate.')
        sys.exit(1)
    creds = service_account.Credentials.from_service_account_info(
        json.loads(blob), scopes=['https://www.googleapis.com/auth/spreadsheets'])
    return build('sheets', 'v4', credentials=creds)


def sheet_title(service):
    meta = service.spreadsheets().get(
        spreadsheetId=SHEET_ID, fields='sheets(properties(sheetId,title))').execute()
    for s in meta['sheets']:
        if s['properties']['sheetId'] == GID:
            return s['properties']['title']
    raise ValueError(f'No tab with sheetId {GID}')


def read_grid(service, title):
    res = service.spreadsheets().values().get(
        spreadsheetId=SHEET_ID, range=f"'{title}'!A:K").execute()
    return [[str(c) for c in r] for r in res.get('values', [])]


def clean_cells(cells):
    cells = [('' if c is None else str(c)) for c in cells][:NUM_COLS]
    return cells + [''] * (NUM_COLS - len(cells))


def insertion_index(grid, d):
    """0-based grid index at which a row dated `d` goes (before it), and
    whether a month divider must be created there first."""
    month_idx = d.month - 1
    # Find this month's divider whose block is for this year: the block is
    # identified by the dated rows beneath it.
    div_at = None
    for i, row in enumerate(grid):
        if i == 0 or divider_month(row) != month_idx:
            continue
        block = next((parse_date(grid[j][0] if grid[j] else '')
                      for j in range(i + 1, min(i + 8, len(grid)))
                      if parse_date(grid[j][0] if grid[j] else '')), None)
        if block is None or (block.year, block.month) == (d.year, d.month):
            div_at = i
            break
    if div_at is not None:
        j = div_at + 1
        while j < len(grid):
            row = grid[j]
            if divider_month(row) is not None:
                break
            rd = parse_date(row[0] if row else '')
            if rd is not None and rd < d:
                break
            j += 1
        return j, False

    # No block for this month: new divider goes above the first row that is
    # older than this month (newest-first), i.e. at the first dated row or
    # divider belonging to an earlier month.
    for i in range(1, len(grid)):
        row = grid[i]
        rd = parse_date(row[0] if row else '')
        if rd is not None and (rd.year, rd.month) < (d.year, d.month):
            # step back over that month's divider if it sits directly above
            return (i - 1 if i > 1 and divider_month(grid[i - 1]) is not None else i), True
    return len(grid), True


def main():
    dry = os.environ.get('DRY_RUN', '0') == '1'
    incoming = json.loads(os.environ['DRAFT_ROWS'])
    service = get_service()
    title = sheet_title(service)
    grid = read_grid(service, title)

    on_sheet = {(parse_date(r[0] if r else ''), norm(r[1]) if len(r) > 1 else '')
                for r in grid}

    moved_keys, skipped, to_add = [], [], []
    for item in incoming:
        cells = clean_cells(item['cells'])
        d = parse_date(cells[0])
        if d is None or not cells[1].strip():
            skipped.append((item['key'], 'needs a date like "SU 12/6/2026" and a name'))
            continue
        if (d, norm(cells[1])) in on_sheet:
            skipped.append((item['key'], 'already on the sheet'))
            moved_keys.append(item['key'])  # nothing left to move; drop from draft
            continue
        to_add.append((d, cells, item['key']))

    # Newest first, so inserting a lower row never shifts a higher target.
    to_add.sort(key=lambda t: t[0], reverse=True)
    for d, cells, key in to_add:
        print(f'  + {cells[0]} | {cells[1]}')
    for key, why in skipped:
        print(f'  - skipped {key}: {why}')

    if dry or not to_add:
        print('DRY RUN: no changes.' if dry else 'Nothing to add.')
        write_result(moved_keys if not dry else [], skipped)
        return

    # Apply one row at a time against a locally-updated grid so each
    # insertion index accounts for the rows already inserted.
    for d, cells, key in to_add:
        idx, need_divider = insertion_index(grid, d)
        reqs, rows_in = [], []
        if need_divider:
            rows_in.append([MONTHS[d.month - 1]] + [''] * (NUM_COLS - 1))
        rows_in.append(cells)
        n = len(rows_in)

        reqs.append({'insertDimension': {
            'range': {'sheetId': GID, 'dimension': 'ROWS',
                      'startIndex': idx, 'endIndex': idx + n},
            'inheritFromBefore': False}})
        reqs.append({'updateCells': {
            'range': {'sheetId': GID, 'startRowIndex': idx, 'endRowIndex': idx + n,
                      'startColumnIndex': 0, 'endColumnIndex': NUM_COLS},
            'rows': [{'values': [cell_value(v, col) for col, v in enumerate(r)]}
                     for r in rows_in],
            'fields': 'userEnteredValue'}})
        # Don't inherit a past row's red strikethrough.
        reqs.append({'repeatCell': {
            'range': {'sheetId': GID, 'startRowIndex': idx, 'endRowIndex': idx + n},
            'cell': {'userEnteredFormat': {'textFormat': {
                'strikethrough': False,
                'foregroundColorStyle': {'themeColor': 'TEXT'}}}},
            'fields': 'userEnteredFormat.textFormat.strikethrough,'
                      'userEnteredFormat.textFormat.foregroundColorStyle'}})
        if need_divider:
            reqs.append({'repeatCell': {
                'range': {'sheetId': GID, 'startRowIndex': idx, 'endRowIndex': idx + 1},
                'cell': {'userEnteredFormat': {'textFormat': {'bold': True}}},
                'fields': 'userEnteredFormat.textFormat.bold'}})
        service.spreadsheets().batchUpdate(
            spreadsheetId=SHEET_ID, body={'requests': reqs}).execute()
        grid[idx:idx] = rows_in
        moved_keys.append(key)
        print(f'Inserted {cells[0]} | {cells[1]} at sheet row {idx + 1}'
              + (' (new month divider)' if need_divider else ''))

    write_result(moved_keys, skipped)


def cell_value(v, col):
    if col == 5 and re.fullmatch(r'\d+', v.strip()):  # "#" column is numeric
        return {'userEnteredValue': {'numberValue': int(v)}}
    if v == '':
        return {}
    return {'userEnteredValue': {'stringValue': v}}


def write_result(moved_keys, skipped):
    """Drop moved rows (and any month divider left with no events) from the draft."""
    if os.environ.get('DRY_RUN', '0') == '1' or not moved_keys:
        return
    with open(DRAFT_PATH, encoding='utf-8') as f:
        draft = json.load(f)
    gone = set(moved_keys)
    kept = [r for r in draft['rows']
            if r.get('kind') != 'event' or row_key(r) not in gone]
    out = []
    for i, r in enumerate(kept):
        if r.get('kind') == 'divider':
            nxt = kept[i + 1] if i + 1 < len(kept) else None
            if nxt is None or nxt.get('kind') == 'divider':
                continue
        out.append(r)
    draft['rows'] = out
    with open(DRAFT_PATH, 'w', encoding='utf-8') as f:
        json.dump(draft, f, indent=2, ensure_ascii=False)
        f.write('\n')
    print(f'Removed {len(moved_keys)} row(s) from {DRAFT_PATH}.')


def row_key(r):
    return f"{r['cells'][0]}|{r['cells'][1]}|{r.get('note', '')}"


if __name__ == '__main__':
    main()
