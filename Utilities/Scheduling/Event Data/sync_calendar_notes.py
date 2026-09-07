"""
Pulls the notes/description from each published Outlook calendar event
into column L ("Event Notes") of the master Tech Schedule Google Sheet,
on the row for the matching event.

The notes come straight from the calendar's published .ics feed (every
event's DESCRIPTION), so nothing is scraped. The only hard part is
pairing a sheet row ("Wedding Ceremony" on 9/6) with the calendar event
that has a very different title ("Ceremony: Laura Goodall & Tim Katlic
Wedding"), especially when a date has several events. That match is done
by Gemini and cached in calendar_notes_state.json, so each run only asks
Gemini about rows it hasn't matched before.

Notes are written RAW, overwriting column L every run - the calendar is
the source of truth, so manual edits in that column are replaced. The
only edit to the text is _strip_boilerplate(), which removes the Teams
meeting block and Google Calendar invite/RSVP footer that Outlook and
Google append to synced events. Rows with no confident calendar match
are left blank; any leftover dashboard HYPERLINK from the earlier
backfill_calendar_links experiment is cleared.

Auth:
    GDRIVE_SERVICE_ACCOUNT_JSON  Sheets read/write (Editor on the sheet)
    GEMINI_API_KEY               row<->event matching (same key the
                                 worship-scripts workflows use)

Env:
    DRY_RUN   "1" (default) = log the plan, write nothing, save no state.
              "0" = apply to the sheet and persist the match cache.
"""

import os
import re
import json
import sys
import datetime
import urllib.request

# googleapiclient / google.oauth2 are imported lazily in get_sheets_service()
# so other modules can reuse the ICS helpers here without the Sheets deps.

SHEET_ID = '1UC8vgy89W14bVEWROqdUc9VgkMTGykC5ZZJqSDmi2-A'
GID = 251348517

ICS_URL = (
    'https://outlook.office365.com/owa/calendar/'
    '1cdc6f8e83d94192aa75834226ba21ed@fccla.org/'
    '72aa544f4e464dca8cb81da616cd100718347546125700610007/calendar.ics'
)

NOTES_COLUMN = 'L'
NOTES_COLUMN_INDEX = 11        # 0-based, column L
HEADER_LABEL = 'Event Notes'
MAX_CELL_CHARS = 45000         # Sheets hard limit is 50k

STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          'calendar_notes_state.json')

DASHBOARD_MARKER = 'cju-media.github.io/Tech-Info/#'
DATE_RE = re.compile(r'(\d{1,2}/\d{1,2}/\d{2,4})')
GEMINI_MODEL = 'gemini-3.5-flash'
GEMINI_BATCH = 120            # rows per matching call

SCOPES = ['https://www.googleapis.com/auth/spreadsheets']


# --------------------------------------------------------------------------
# ICS parsing
# --------------------------------------------------------------------------

def _unescape_ics(value):
    out = []
    i = 0
    while i < len(value):
        ch = value[i]
        if ch == '\\' and i + 1 < len(value):
            nxt = value[i + 1]
            out.append({'n': '\n', 'N': '\n', ',': ',', ';': ';', '\\': '\\'}.get(nxt, nxt))
            i += 2
        else:
            out.append(ch)
            i += 1
    return ''.join(out)


def _tidy(text):
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    text = re.sub(r'[ \t]+\n', '\n', text)              # trailing spaces
    text = re.sub(r'\n{3,}', '\n\n', text)              # >2 blank lines
    text = re.sub(r'(?:\n[ \t]*[*·•\-]?[ \t]*)+$', '', text)  # trailing empty bullets
    return text.strip()


# Everything from here to the end of the note: Outlook/Teams and Google
# Calendar append these invite/meeting footers to every synced event.
_FOOTER_CUTS = [
    r'\n_{10,}\nMicrosoft Teams\b',
    r'\nMicrosoft Teams Need help\?',
    r'\n_{10,}\n(?=(?:[^\n]*\n){0,3}?[^\n]*Microsoft Teams)',
    r'\nWhen\n(?=\w+day\b|\w+day,|\w{3,9} \d{1,2},? \d{4})',
    r'\nYou have been invited by .+? to attend an event named ',
    r'\nInvitation from Google Calendar<',
    r'\nView all guest info<https://calendar\.google\.com',
    r'\n~-~-~-~',
    r'\nGoing \(\w+\)\?',
]
# Google Meet / RSVP scaffolding lines that sit inside the note body.
_JUNK_LINE = re.compile(
    r'^(?:Join with Google Meet|Meeting link|Join by phone|More phone numbers|'
    r'Description|CHANGED|This event has been updated|Changed: .*|'
    r'\(US\) \+.*PIN:.*|meet\.google\.com/\S+|https?://meet\.google\.com/\S+)\s*$'
)


def _strip_boilerplate(text):
    text = _tidy(text)
    cut = len(text)
    for pattern in _FOOTER_CUTS:
        m = re.search(pattern, text)
        if m:
            cut = min(cut, m.start())
    text = text[:cut]
    text = text.replace('\nDescription\nCHANGED', '')
    text = '\n'.join(ln for ln in text.split('\n') if not _JUNK_LINE.match(ln))
    text = re.sub(r'[ \t]*<(?:https?|mailto):[^>\s]+>', '', text)  # <url> / <mailto:> tokens
    return _tidy(text)


def fetch_events():
    """Return {event_id: {date: 'YYYY-MM-DD', summary, location, description}}.

    event_id is '<UID>::<date>' - recurring series (RCS On-Site Visit, NEO
    Fest, staff meetings) reuse one UID across every occurrence, so the
    date is needed to key each instance separately and stably."""
    req = urllib.request.Request(ICS_URL, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req) as response:
        raw = response.read().decode('utf-8')

    raw = raw.replace('\r\n', '\n')
    raw = re.sub(r'\n[ \t]', '', raw)  # unfold continuation lines

    events = {}
    for block in re.findall(r'BEGIN:VEVENT\n(.*?)\nEND:VEVENT', raw, re.S):
        def field(name):
            m = re.search(rf'^{name}(?:;[^:\n]*)?:(.*)$', block, re.M)
            return _unescape_ics(m.group(1)) if m else ''

        dt = re.search(r'^DTSTART(?:;[^:\n]*)?:(\d{8})', block, re.M)
        if not dt:
            continue
        iso = f'{dt.group(1)[:4]}-{dt.group(1)[4:6]}-{dt.group(1)[6:8]}'

        uid = field('UID').strip() or f'nouid-{len(events)}'
        event_id = f'{uid}::{iso}'
        if event_id in events and not field('DESCRIPTION').strip():
            continue  # keep the instance that actually carries notes
        events[event_id] = {
            'date': iso,
            'summary': field('SUMMARY').strip(),
            'location': field('LOCATION').strip(),
            'description': _strip_boilerplate(field('DESCRIPTION')),
        }
    return events


# --------------------------------------------------------------------------
# Sheet helpers
# --------------------------------------------------------------------------

def get_sheets_service():
    blob = os.environ.get('GDRIVE_SERVICE_ACCOUNT_JSON')
    if not blob:
        return None
    from googleapiclient.discovery import build
    from google.oauth2 import service_account
    creds = service_account.Credentials.from_service_account_info(
        json.loads(blob), scopes=SCOPES
    )
    print(f"Authenticating as service account: {json.loads(blob).get('client_email')}")
    return build('sheets', 'v4', credentials=creds)


def get_sheet_props(service):
    meta = service.spreadsheets().get(
        spreadsheetId=SHEET_ID,
        fields='sheets(properties(sheetId,title,gridProperties(columnCount)))',
    ).execute()
    for sheet in meta.get('sheets', []):
        props = sheet.get('properties', {})
        if props.get('sheetId') == GID:
            return props['title'], props.get('gridProperties', {}).get('columnCount', 0)
    raise ValueError(f"No tab with sheetId {GID}")


def ensure_column_exists(service):
    _title, count = get_sheet_props(service)
    if count >= NOTES_COLUMN_INDEX + 1:
        return
    missing = (NOTES_COLUMN_INDEX + 1) - count
    print(f"Adding {missing} column(s) to reach {NOTES_COLUMN}.")
    service.spreadsheets().batchUpdate(
        spreadsheetId=SHEET_ID,
        body={'requests': [{'appendDimension': {
            'sheetId': GID, 'dimension': 'COLUMNS', 'length': missing}}]},
    ).execute()


def read_grid(service, title):
    result = service.spreadsheets().values().get(
        spreadsheetId=SHEET_ID,
        range=f"'{title}'!A:L",
        valueRenderOption='FORMULA',
    ).execute()
    return result.get('values', [])


def fetch_grid_gviz():
    url = f'https://docs.google.com/spreadsheets/d/{SHEET_ID}/gviz/tq?gid={GID}&headers=0'
    with urllib.request.urlopen(url) as response:
        data = response.read().decode('utf-8')
    payload = json.loads(re.search(r'setResponse\((.*)\);', data).group(1))
    return [[(c['v'] if c else '') for c in row['c']] for row in payload['table']['rows']]


# --------------------------------------------------------------------------
# Row extraction + matching
# --------------------------------------------------------------------------

def normalize_name(name):
    return re.sub(r'\s+', ' ', str(name)).strip().lower()


def parse_iso_date(cell):
    m = DATE_RE.search(str(cell))
    if not m:
        return None
    mm, dd, yy = m.group(1).split('/')
    if len(yy) == 2:
        yy = '20' + yy
    try:
        return datetime.date(int(yy), int(mm), int(dd)).isoformat()
    except ValueError:
        return None


def extract_rows(grid):
    """Return [(sheet_row, iso_date, event_name, current_L)]."""
    rows = []
    for index, row in enumerate(grid):
        if index == 0:
            continue
        iso = parse_iso_date(row[0] if row else '')
        if not iso:
            continue
        name = str(row[1]).strip() if len(row) > 1 and row[1] is not None else ''
        if not name:
            continue
        current = str(row[NOTES_COLUMN_INDEX]).strip() if len(row) > NOTES_COLUMN_INDEX and row[NOTES_COLUMN_INDEX] is not None else ''
        rows.append((index + 1, iso, name, current))
    return rows


def load_state():
    try:
        with open(STATE_FILE) as f:
            data = json.load(f)
            data.setdefault('matches', {})
            return data
    except (FileNotFoundError, json.JSONDecodeError):
        return {'matches': {}}


def gemini_match(unmatched, events, api_key):
    """unmatched: list of (key, iso_date, name). events: uid->info.
    Returns {key: uid or 'NONE'}."""
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=600000))
    resolved = {}

    for start in range(0, len(unmatched), GEMINI_BATCH):
        chunk = unmatched[start:start + GEMINI_BATCH]
        dates = {iso for _k, iso, _n in chunk}
        window = set()
        for iso in dates:
            d = datetime.date.fromisoformat(iso)
            for delta in (-2, -1, 0, 1, 2):
                window.add((d + datetime.timedelta(days=delta)).isoformat())
        relevant = sorted(
            (eid for eid, e in events.items() if e['date'] in window),
            key=lambda eid: events[eid]['date'],
        )
        handles = {f'E{n}': eid for n, eid in enumerate(relevant)}

        row_lines = [f'R{i}\t{iso}\t{name}' for i, (_k, iso, name) in enumerate(chunk)]
        event_lines = [
            f'{h}\t{events[eid]["date"]}\t{events[eid]["summary"]}\t{events[eid]["location"]}'
            for h, eid in handles.items()
        ]
        prompt = (
            "You match rows from a church tech-schedule spreadsheet to events on the "
            "church's Outlook calendar. Titles differ (e.g. row 'Wedding Ceremony' vs "
            "calendar 'Ceremony: Jane & John Wedding'); a date often has several events.\n\n"
            "For each ROW, pick the ONE calendar EVENT that is the same real-world event. "
            "The dates must be the same or within one day. If nothing clearly matches, use NONE. "
            "Do not guess between two plausible events - use NONE.\n\n"
            "ROWS (id<TAB>date<TAB>name):\n" + "\n".join(row_lines) +
            "\n\nEVENTS (id<TAB>date<TAB>title<TAB>location):\n" + "\n".join(event_lines) +
            '\n\nReply with ONLY a JSON object mapping each ROW id to an EVENT id or "NONE". '
            'Example: {"R0":"E4","R1":"NONE"}'
        )

        print(f"  Gemini matching rows {start + 1}-{start + len(chunk)} "
              f"against {len(handles)} events...")
        response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
        text = (response.text or '').strip()
        blob = re.search(r'\{.*\}', text, re.S)
        if not blob:
            print(f"  Gemini returned no JSON; leaving this batch unmatched.\n{text[:300]}")
            continue
        try:
            mapping = json.loads(blob.group(0))
        except json.JSONDecodeError:
            print("  Could not parse Gemini JSON; leaving this batch unmatched.")
            continue

        for i, (key, _iso, _name) in enumerate(chunk):
            handle = str(mapping.get(f'R{i}', 'NONE')).strip()
            resolved[key] = handles.get(handle, 'NONE')

    return resolved


# --------------------------------------------------------------------------

def main():
    is_dry_run = os.environ.get('DRY_RUN', '1') == '1'
    api_key = os.environ.get('GEMINI_API_KEY')

    print("Fetching calendar .ics feed...")
    events = fetch_events()
    if not events:
        print("No events in the feed; aborting.")
        sys.exit(1)
    feed_dates = sorted(e['date'] for e in events.values())
    feed_min, feed_max = feed_dates[0], feed_dates[-1]
    print(f"{len(events)} events, {feed_min} .. {feed_max}")

    service = get_sheets_service()
    if not service and not is_dry_run:
        print("GDRIVE_SERVICE_ACCOUNT_JSON not set; cannot write.")
        sys.exit(1)

    title = None
    if service:
        title, _ = get_sheet_props(service)
        grid = read_grid(service, title)
    else:
        print("No credentials; reading the sheet via the public gviz endpoint (dry run).")
        grid = fetch_grid_gviz()

    header = grid[0] if grid else []
    header_current = str(header[NOTES_COLUMN_INDEX]).strip() if len(header) > NOTES_COLUMN_INDEX else ''
    header_needs_write = header_current != HEADER_LABEL

    rows = extract_rows(grid)
    lo = (datetime.date.fromisoformat(feed_min) - datetime.timedelta(days=1)).isoformat()
    hi = (datetime.date.fromisoformat(feed_max) + datetime.timedelta(days=1)).isoformat()
    in_window = [r for r in rows if lo <= r[1] <= hi]
    print(f"{len(rows)} dated rows, {len(in_window)} within the feed's date range.")

    state = load_state()
    matches = state['matches']

    unmatched = []
    for _row, iso, name, _current in in_window:
        key = f'{iso}|{normalize_name(name)}'
        if key not in matches or (matches[key] != 'NONE' and matches[key] not in events):
            unmatched.append((key, iso, name))
    # de-dup keys (same date+name on multiple rows)
    seen = set()
    unmatched = [u for u in unmatched if not (u[0] in seen or seen.add(u[0]))]

    if unmatched:
        if api_key:
            print(f"Matching {len(unmatched)} new row(s) with Gemini ({GEMINI_MODEL})...")
            matches.update(gemini_match(unmatched, events, api_key))
        else:
            print(f"{len(unmatched)} row(s) need matching but GEMINI_API_KEY is not set; "
                  f"they stay blank this run.")
    else:
        print("All in-window rows already matched (from cache).")

    # Build the planned column-L writes.
    updates = []          # (sheet_row, event_name, uid, note_text)
    clears = []           # (sheet_row, event_name, reason)
    matched_count = 0
    note_rows = set()
    for sheet_row, iso, name, current in in_window:
        key = f'{iso}|{normalize_name(name)}'
        uid = matches.get(key, 'NONE')
        if uid != 'NONE' and uid in events:
            matched_count += 1
            note_rows.add(sheet_row)
            note = events[uid]['description']
            if len(note) > MAX_CELL_CHARS:
                note = note[:MAX_CELL_CHARS] + '\n[...truncated]'
            if current != note:
                updates.append((sheet_row, name, uid, note))

    # Clear any leftover dashboard HYPERLINK from the earlier experiment on
    # every dated row that isn't getting a note (including rows outside the
    # feed's date range, which the loop above never visits).
    for sheet_row, _iso, name, current in rows:
        if sheet_row in note_rows or not current:
            continue
        if DASHBOARD_MARKER in current or current.startswith('=HYPERLINK'):
            clears.append((sheet_row, name, 'leftover dashboard link, no calendar match'))

    print(f"\n{matched_count}/{len(in_window)} in-window rows matched to an event.")
    print(f"Header {NOTES_COLUMN}1: "
          f"{'set to ' + HEADER_LABEL if header_needs_write else 'already ' + repr(HEADER_LABEL)}")
    print(f"{len(updates)} note cell(s) to write, {len(clears)} cell(s) to clear.\n")
    for sheet_row, name, uid, note in updates[:25]:
        preview = note.replace('\n', ' / ')[:90]
        print(f"  {NOTES_COLUMN}{sheet_row}  {name!r}\n      {events[uid]['summary']!r} ({events[uid]['date']})\n      {preview}")
    if len(updates) > 25:
        print(f"  ... and {len(updates) - 25} more")
    for sheet_row, name, reason in clears[:15]:
        print(f"  clear {NOTES_COLUMN}{sheet_row}  {name!r}  ({reason})")

    if is_dry_run:
        print("\nDRY RUN: no sheet writes, match cache not saved. Set DRY_RUN=0 to apply.")
        return

    data = []
    if header_needs_write:
        data.append({'range': f"'{title}'!{NOTES_COLUMN}1", 'values': [[HEADER_LABEL]]})
    for sheet_row, _name, _uid, note in updates:
        data.append({'range': f"'{title}'!{NOTES_COLUMN}{sheet_row}", 'values': [[note]]})
    for sheet_row, _name, _reason in clears:
        data.append({'range': f"'{title}'!{NOTES_COLUMN}{sheet_row}", 'values': [['']]})

    if data:
        ensure_column_exists(service)
        for start in range(0, len(data), 400):
            service.spreadsheets().values().batchUpdate(
                spreadsheetId=SHEET_ID,
                body={'valueInputOption': 'RAW', 'data': data[start:start + 400]},
            ).execute()
        print(f"Wrote {len(data)} cell(s) to column {NOTES_COLUMN}.")
    else:
        print("Sheet already current; no cells written.")

    # Always persist the match cache (it may hold new matches even when the
    # sheet needed no writes, and the file may not exist yet).
    state['matches'] = matches
    state['updated'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f, indent=2, sort_keys=True)
    print(f"Saved {len(matches)} cached match(es) to {os.path.basename(STATE_FILE)}.")


if __name__ == '__main__':
    main()
