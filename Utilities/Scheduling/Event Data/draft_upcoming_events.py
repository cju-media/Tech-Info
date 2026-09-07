"""
Builds a copy-paste draft of upcoming events for the Tech Availability
sheet and writes it to event_draft.json (rendered by event-draft.html).

Nothing here touches the live sheet - the output is a starting point the
user pastes in and edits.

Sources:
  * The published Outlook calendar .ics feed (via sync_calendar_notes.fetch_events)
    for real events. Gemini turns each into sheet columns (name, venue,
    call time, type, timeline) and drops anything that isn't a crew event
    (internal meetings, "In-Office", tentative holds, cancellations).
  * Synthesised weekly "Worship Service" rows for every Sunday in the
    window, since those never appear on the Outlook calendar. Format is
    copied verbatim from existing sheet rows.

Events already on the sheet are skipped: the calendar_notes_state.json
match cache says which calendar events map to an existing row, and the
current sheet rows for nearby dates are shown to Gemini as a backstop.

The Gemini classification is cached in event_draft_state.json, keyed by a
fingerprint of the candidate events, so a run whose candidates haven't
changed makes no Gemini call (it still rebuilds the JSON so dates and
worship rows roll forward).

Window: the next ~3 months.

Env:
    GEMINI_API_KEY   required to classify/format calendar events
    DRY_RUN          "1" prints the draft, "0" (default here it doesn't
                     matter - it never writes the sheet) also writes
                     event_draft.json
"""

import os
import re
import sys
import json
import hashlib
import datetime

from sync_calendar_notes import (
    fetch_events, fetch_grid_gviz, normalize_name, parse_iso_date,
    GEMINI_MODEL, STATE_FILE,
)

WINDOW_DAYS = 92
OUT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'event_draft.json')
# Caches the last Gemini classification keyed by a fingerprint of the
# candidate events. The candidate set changes only when a calendar event
# is added/edited or gets onto the sheet, so most daily runs reuse this
# and make no Gemini call. Delete the file to force a re-run.
DRAFT_STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'event_draft_state.json')
NOTE_EXCERPT = 1400
GEMINI_BATCH = 40

WEEKDAY = ['MO', 'TU', 'WE', 'TH', 'FR', 'SA', 'SU']
COLUMNS = ['Date', 'Name of Event', 'Spaces Used', 'Call Times', 'Event Type',
           '#', 'Availability', 'Assignment', 'Schedule', 'Tech', 'PAYCOM CODE']

# Worship Service template, matching existing sheet rows exactly.
WORSHIP = {
    'venue': 'Sanctuary',
    'call_times': '8:45am-12:30pm',
    'type': 'Worship Service',
    'count': '2',
    'assignment': '(Live) \n(Stream)',
    'paycom': 'Leave Blank',
}


def sheet_date(d):
    return f'{WEEKDAY[d.weekday()]} {d.month}/{d.day}/{d.year}'


def load_matched_event_ids():
    try:
        with open(STATE_FILE) as f:
            matches = json.load(f).get('matches', {})
        return {v for v in matches.values() if v and v != 'NONE'}
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def read_sheet_rows():
    """{iso_date: [event name, ...]} for dated rows on the sheet."""
    by_date = {}
    for row in fetch_grid_gviz():
        iso = parse_iso_date(row[0] if row else '')
        if not iso:
            continue
        name = str(row[1]).strip() if len(row) > 1 and row[1] else ''
        if name:
            by_date.setdefault(iso, []).append(name)
    return by_date


def gemini_draft(events, sheet_by_date, api_key):
    """events: list of (event_id, info). Returns {event_id: {name, venue,
    call_times, type, count, schedule}} for the ones that belong on the
    crew schedule."""
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=600000))
    drafted = {}

    for start in range(0, len(events), GEMINI_BATCH):
        chunk = events[start:start + GEMINI_BATCH]
        blocks = []
        nearby_names = set()
        for eid, info in chunk:
            d = datetime.date.fromisoformat(info['date'])
            for delta in (-1, 0, 1):
                for nm in sheet_by_date.get((d + datetime.timedelta(days=delta)).isoformat(), []):
                    nearby_names.add(nm)
            note = info['description'][:NOTE_EXCERPT]
            blocks.append(
                f'--- id: {eid}\n'
                f'date: {info["date"]}\n'
                f'title: {info["summary"]}\n'
                f'location: {info["location"]}\n'
                f'notes: {note}'
            )

        prompt = (
            "You prepare rows for a church production/tech crew schedule from Outlook "
            "calendar events.\n\n"
            "For EACH event below decide if it needs tech/AV crew (concerts, weddings, "
            "memorials, talks, rehearsals, screenings, big rentals, festivals). SKIP: internal "
            "staff meetings, one-on-one meetings, \"In-Office\"/PTO markers, tentative or "
            "\"soft hold\" bookings, and anything whose title starts with \"Canceled\". Also "
            "SKIP any event that clearly already appears in this list of rows already on the "
            f"schedule for nearby dates: {sorted(nearby_names)}\n\n"
            "For events you keep, return sheet fields:\n"
            "- name: short event name (e.g. 'Wedding Ceremony', 'Organ Concert', "
            "'\"<Artist>\" Concert')\n"
            "- venue: from the location, using the church's short names (Sanctuary, Shatto, "
            "Forecourt, Stuart Hall, Barnum, Buy-out, Full Campus); combine with '/' if a few\n"
            "- call_times: the crew call window as 'h:mmam-h:mmpm' (lowercase, no spaces). Use "
            "the earliest load-in/setup/sound-check time from the notes timeline through the "
            "off-campus/departure time. If the notes have no timeline, use the event's own "
            "start and end.\n"
            "- type: one of Worship Service, Concert, Wedding, Talk, Rehearsal, Memorial, "
            "Graduation, Festival, Convention, Special Event, Screening, Meeting\n"
            "- count: 2 if it is livestreamed or streamed, otherwise 1\n"
            "- schedule: the timeline as short lines ('5pm Load in\\n7pm Doors\\n...'), copied "
            "from the notes; '' if the notes have none\n\n"
            "Reply with ONLY a JSON object mapping kept event ids to "
            '{"name","venue","call_times","type","count","schedule"}. '
            'Omit skipped events entirely.\n\n'
            + "\n\n".join(blocks)
        )

        print(f"  Gemini drafting events {start + 1}-{start + len(chunk)}...")
        resp = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
        text = (resp.text or '').strip()
        blob = re.search(r'\{.*\}', text, re.S)
        if not blob:
            print(f"  no JSON back; skipping this batch\n{text[:300]}")
            continue
        try:
            mapping = json.loads(blob.group(0))
        except json.JSONDecodeError:
            print("  could not parse Gemini JSON; skipping this batch")
            continue
        for eid, fields in mapping.items():
            if isinstance(fields, dict) and eid in dict(chunk):
                drafted[eid] = fields

    return drafted


def candidates_fingerprint(candidates):
    """Stable hash of everything gemini_draft() feeds the model, so an
    unchanged candidate set can reuse the last classification."""
    basis = [
        [eid, info['date'], info['summary'], info['location'], info['description']]
        for eid, info in sorted(candidates)
    ]
    return hashlib.sha256(json.dumps(basis, sort_keys=True).encode()).hexdigest()


def load_draft_state():
    try:
        with open(DRAFT_STATE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_draft_state(state):
    with open(DRAFT_STATE_FILE, 'w') as f:
        json.dump(state, f, indent=2, sort_keys=True)


def build_rows(worship_dates, drafted, events_by_id):
    """Return a list of {kind, cells, note} newest-first, with month dividers."""
    entries = []  # (date, cells, note)

    for iso in worship_dates:
        d = datetime.date.fromisoformat(iso)
        entries.append((d, [
            sheet_date(d), 'Worship Service', WORSHIP['venue'], WORSHIP['call_times'],
            WORSHIP['type'], WORSHIP['count'], '', WORSHIP['assignment'], '', '',
            WORSHIP['paycom'],
        ], 'weekly worship service (not on the Outlook calendar)'))

    for eid, f in drafted.items():
        info = events_by_id[eid]
        d = datetime.date.fromisoformat(info['date'])
        etype = str(f.get('type', '')).strip()
        paycom = 'Weddings' if etype == 'Wedding' else ''
        count = str(f.get('count', '1')).strip() or '1'
        entries.append((d, [
            sheet_date(d),
            str(f.get('name', info['summary'])).strip(),
            str(f.get('venue', '')).strip(),
            str(f.get('call_times', '')).strip(),
            etype,
            count,
            '',
            '',
            str(f.get('schedule', '')).strip(),
            '',
            paycom,
        ], f'from calendar: {info["summary"]}'))

    entries.sort(key=lambda e: (e[0], e[2][1]), reverse=True)

    rows = []
    current_month = None
    for d, cells, note in entries:
        month = d.strftime('%B').upper()
        if month != current_month:
            rows.append({'kind': 'divider', 'cells': [month] + [''] * 10, 'note': ''})
            current_month = month
        rows.append({'kind': 'event', 'cells': cells, 'note': note})
    return rows


def main():
    api_key = os.environ.get('GEMINI_API_KEY')
    today = datetime.date.today()
    end = today + datetime.timedelta(days=WINDOW_DAYS)
    print(f"Draft window: {today} .. {end}")

    events = fetch_events()
    matched = load_matched_event_ids()
    sheet_by_date = read_sheet_rows()

    in_window = {
        eid: info for eid, info in events.items()
        if today.isoformat() <= info['date'] <= end.isoformat()
    }
    candidates = [
        (eid, info) for eid, info in sorted(in_window.items(), key=lambda kv: kv[1]['date'])
        if eid not in matched
    ]
    print(f"{len(in_window)} calendar events in window, {len(candidates)} not already on the sheet.")

    fingerprint = candidates_fingerprint(candidates)
    draft_state = load_draft_state()
    drafted = {}
    called_gemini = False
    if candidates:
        if not api_key:
            print("GEMINI_API_KEY not set; calendar events will be omitted from the draft.")
        elif draft_state.get('fingerprint') == fingerprint and 'drafted' in draft_state:
            candidate_ids = {eid for eid, _ in candidates}
            drafted = {eid: f for eid, f in draft_state['drafted'].items() if eid in candidate_ids}
            print(f"Candidate events unchanged since {draft_state.get('gemini_at', '?')}; "
                  f"reusing {len(drafted)} cached classification(s), no Gemini call.")
        else:
            drafted = gemini_draft(candidates, sheet_by_date, api_key)
            called_gemini = True
            print(f"Gemini kept {len(drafted)} as crew events.")
    if called_gemini:
        draft_state = {
            'fingerprint': fingerprint,
            'drafted': drafted,
            'gemini_at': datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds'),
        }

    # Sundays in the window with no worship service already on the sheet.
    worship_dates = []
    day = today + datetime.timedelta(days=(6 - today.weekday()) % 7)  # next Sunday (or today)
    while day <= end:
        iso = day.isoformat()
        existing = [n for n in sheet_by_date.get(iso, []) if 'worship service' in n.lower()]
        if not existing:
            worship_dates.append(iso)
        day += datetime.timedelta(days=7)
    print(f"{len(worship_dates)} Sunday(s) need a worship service row.")

    rows = build_rows(worship_dates, drafted, events)
    event_count = sum(1 for r in rows if r['kind'] == 'event')

    payload = {
        'generated': datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds'),
        'window': {'start': today.isoformat(), 'end': end.isoformat()},
        'columns': COLUMNS,
        'rows': rows,
        'counts': {'events': event_count, 'worship': len(worship_dates),
                   'from_calendar': len(drafted)},
    }

    print(f"\nDraft: {event_count} row(s) "
          f"({len(worship_dates)} worship + {len(drafted)} from calendar)")
    for r in rows:
        if r['kind'] == 'divider':
            print(f"  == {r['cells'][0]}")
        else:
            print(f"  {r['cells'][0]:<14} {r['cells'][1][:40]:<40} {r['cells'][3]:<18} {r['cells'][4]}")

    if os.environ.get('DRY_RUN') == '1':
        print("\nDRY RUN: not writing event_draft.json")
        return

    with open(OUT_FILE, 'w') as f:
        json.dump(payload, f, indent=2)
    print(f"\nWrote {os.path.basename(OUT_FILE)}")

    if called_gemini:
        save_draft_state(draft_state)
        print(f"Saved classification cache ({len(draft_state['drafted'])} event(s)).")


if __name__ == '__main__':
    main()
