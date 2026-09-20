"""
Regenerates the "at a glance" events card for the campus screens and queues
it for upload to the Events_Ads Drive folder.

The card is a single 1920x1080 PNG listing every upcoming *non-recurring*
event at fccla.org/calendar -- the concerts and special events -- with the
weekly Sunday Worship Service left off, since a standing service doesn't
need advertising on a loop that already runs during it.

Where the data comes from:
    fccla.org/calendar renders its events client-side (an Elfsight widget),
    so a plain GET returns markup with no events in it. The widget does emit
    one schema.org <script type="application/ld+json"> Event block per event,
    which is far more stable to read than its generated class names -- so we
    drive headless Chrome over the page, dump the rendered DOM, and parse the
    JSON-LD out of it. Chrome is needed to render the PNG anyway.

Gemini, and when it does *not* run:
    Deciding "is this a recurring service or a one-off event" is the one
    judgement call here, so Gemini makes it (and supplies the category tag
    on each card). That call is gated twice over, because this runs daily
    and the calendar changes maybe monthly:

      * A fingerprint of the scraped page (every event's name, start, venue
        and status) is stored in events_ad_state.json. If the page is
        byte-for-byte unchanged in substance since the last run, the cached
        classification is reused and no Gemini call happens at all.
      * Even when the page *has* changed, only events whose (name, start)
        key isn't already classified are sent. Adding one concert costs one
        small call, not a re-read of the whole calendar.

    Same shape as draft_upcoming_events.py's cache. Delete the state file to
    force a full re-classification.

What still happens on an unchanged page:
    Time passes even when the calendar doesn't, so an event can drop off the
    card without the page changing. The rendered card's own contents get a
    second fingerprint; if *that* is unchanged the run stops before
    rendering, and nothing is queued. If it changed (an event passed), the
    card re-renders and re-uploads from the cached classification -- still no
    Gemini call.

Upload path:
    Writes the PNG into Utilities/uploads_queue/ under the name the Events Ad
    drop zone uses, and the existing process_uploads.yml picks it up on push.
    The card keeps one stable Drive filename so upload_queue_to_drive.py
    replaces it in place rather than piling up copies, and
    cleanup_events_folder.py skips it by that same name instead of trashing
    it when the first event on it passes.

Env:
    GEMINI_API_KEY   required, unless every event is already classified
    FORCE_REFRESH    "1" ignores both fingerprints (re-classify + re-render)
    DRY_RUN          "1" renders but queues nothing
"""

import os
import re
import sys
import json
import html
import hashlib
import datetime
import zoneinfo
import subprocess

CALENDAR_URL = 'https://fccla.org/calendar'
HERE = os.path.dirname(os.path.abspath(__file__))
STATE_PATH = os.path.join(HERE, 'events_ad_state.json')
HTML_PATH = os.path.join(HERE, 'events-at-a-glance.html')
PNG_PATH = os.path.join(HERE, 'events-at-a-glance.png')

REPO_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
QUEUE_DIR = os.path.join(REPO_ROOT, 'Utilities', 'uploads_queue')

# The Events Ad drop zone on the upload dashboard; upload_queue_to_drive.py
# parses this out of the queued filename.
EVENTS_FOLDER_ID = '17-0kiqBKa0k5ofW6gOPrVbHl7nqanuQz'
# Stable on purpose -- see the module docstring. cleanup_events_folder.py's
# PROTECTED_NAME_PREFIXES matches this stem, so renaming it here without
# renaming it there would let the card get auto-trashed.
# Published several times over, under sort-key prefixes. Content Display
# plays the folder in filename order, so one copy of a card appears once per
# lap; these land between the event flyers so an info card comes up roughly
# every other poster. The prefixes are fitted to the flyers currently in the
# folder -- as those come and go the interleave drifts, and the fix is to
# re-pick these letters. cleanup_events_folder.py matches the descriptive
# part as a fragment, so every copy is protected without listing them all.
CARD_FILENAMES = (
    'C-Events-At-A-Glance.png',
    'N-Events-At-A-Glance.png',
)

TZ = zoneinfo.ZoneInfo('America/Los_Angeles')
WINDOW_DAYS = 120          # how far ahead to advertise
MAX_CARDS = 9              # a 3x3 grid is what fits legibly at 1920x1080
GEMINI_MODEL = 'gemini-3.5-flash'
# Bumped whenever the card's markup changes in a way that should reach the
# screens (a new element, a restyle, a layout fix). It feeds card_fingerprint,
# which otherwise only hashes event data -- so without this, redesigning the
# card on an unchanged calendar would render locally and then quietly decide
# there was nothing to publish. Bump it, or the change never ships.
#   1  initial 3x3 grid
#   2  "scan for tickets" QR in the header
#   3  QR caption dropped; banner text centred on the y-axis
#   4  "Events through <date>" dropped from the footer
RENDER_VERSION = 4
CHROME_CANDIDATES = [
    os.environ.get('CHROME_BIN') or '',
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    '/usr/bin/google-chrome',
    '/usr/bin/chromium-browser',
    '/usr/bin/chromium',
]


# --------------------------------------------------------------------------
# Pure helpers (importable without Chrome or the Gemini client, for tests)
# --------------------------------------------------------------------------

def parse_ld_events(dom_html):
    """Every schema.org Event in the rendered DOM, as
    {name, start, venue, status} with entities decoded.

    The widget emits one ld+json block per event (plus a few non-Event blocks
    for the site itself, which are skipped)."""
    events = []
    for raw in re.findall(
        r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        dom_html, re.S | re.I,
    ):
        try:
            data = json.loads(raw.strip())
        except (ValueError, TypeError):
            continue
        for node in (data if isinstance(data, list) else [data]):
            if not isinstance(node, dict) or node.get('@type') != 'Event':
                continue
            start = node.get('startDate')
            name = node.get('name')
            if not start or not name:
                continue
            location = node.get('location') or {}
            venue = location.get('name') if isinstance(location, dict) else None
            events.append({
                # Names arrive HTML-escaped ("&quot;Joyride&quot;") and
                # sometimes with stray leading space.
                'name': html.unescape(str(name)).strip(),
                'start': str(start),
                'venue': html.unescape(str(venue)).strip() if venue else '',
                'status': str(node.get('eventStatus') or ''),
            })
    return events


def smart_quotes(text):
    """Straight double quotes -> typographic pairs. The feed escapes them as
    &quot;, so every title arrives with straight ones; apostrophes already
    come through curled."""
    return re.sub(r'"([^"]*)"', '\u201c\\1\u201d', text or '')


def parse_start(start):
    """The ISO start stamp as a datetime in church-local time, or None.

    Always converted to America/Los_Angeles, never left on whatever offset
    the feed happened to use. The calendar widget renders its dates in the
    *browser's* timezone, so the same 7pm concert comes back as
    "19:00:00-07:00" from a machine in LA and "02:00:00+00:00" (the next
    day!) from a UTC CI runner. Printing that verbatim put every event on
    the card a day late at 2 in the morning."""
    try:
        dt = datetime.datetime.fromisoformat(start)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=TZ)
    return dt.astimezone(TZ)


def is_cancelled(status):
    return 'cancel' in (status or '').lower()


def upcoming_events(events, now, window_days=WINDOW_DAYS):
    """Events that haven't started yet and fall inside the advertising
    window, soonest first. Cancelled events are dropped -- the widget keeps
    them listed with an EventCancelled status."""
    horizon = now + datetime.timedelta(days=window_days)
    out = []
    for e in events:
        dt = parse_start(e['start'])
        if dt is None or is_cancelled(e['status']):
            continue
        if now <= dt <= horizon:
            out.append(dict(e, _dt=dt))
    out.sort(key=lambda e: e['_dt'])
    return out


def event_key(event):
    """Stable identity for the classification cache. Name + start, because a
    series like "Sunday Worship Service" repeats by name and a renamed event
    genuinely deserves re-classifying."""
    return f"{event['name']}|{event['start']}"


def page_fingerprint(events):
    """Hash of everything scraped off the page. Gates the Gemini call: equal
    fingerprint means the calendar hasn't changed since the last run."""
    payload = sorted(
        f"{e['name']}|{e['start']}|{e.get('venue', '')}|{e.get('status', '')}"
        for e in events
    )
    return hashlib.sha256('\n'.join(payload).encode('utf-8')).hexdigest()


def card_fingerprint(cards):
    """Hash of what actually gets drawn. Gates re-rendering and re-uploading,
    which must still happen when an event merely passes on an otherwise
    unchanged page -- or when the card's design changes underneath an
    unchanged calendar (hence RENDER_VERSION)."""
    payload = [f'v{RENDER_VERSION}']
    payload += [f"{c['name']}|{c['start']}|{c['venue']}|{c['category']}" for c in cards]
    return hashlib.sha256('\n'.join(payload).encode('utf-8')).hexdigest()


def heuristic_recurring(name):
    """Fallback when Gemini can't be reached: the weekly service is the only
    recurring series this calendar has ever carried. Deliberately narrow --
    it's better to advertise one service by mistake than to silently drop a
    concert nobody hears about."""
    return 'worship service' in (name or '').lower()


def load_state():
    try:
        with open(STATE_PATH) as f:
            state = json.load(f)
    except (FileNotFoundError, ValueError):
        return {'page_fingerprint': None, 'card_fingerprint': None, 'classified': {}}
    state.setdefault('classified', {})
    return state


def save_state(state):
    with open(STATE_PATH, 'w') as f:
        json.dump(state, f, indent=2, sort_keys=True)
        f.write('\n')


# --------------------------------------------------------------------------
# Chrome
# --------------------------------------------------------------------------

def find_chrome():
    for path in CHROME_CANDIDATES:
        if path and os.path.exists(path):
            return path
    raise RuntimeError(
        'No Chrome binary found. Set CHROME_BIN, or install google-chrome.'
    )


def chrome_run(args, capture, timeout=180):
    # Chrome is chatty on stderr even on a clean run (GPU/display probes that
    # mean nothing headless), so it's dropped -- every call here checks its
    # real output instead, and raises if it's missing.
    cmd = [find_chrome(), '--headless', '--disable-gpu', '--no-sandbox',
           '--hide-scrollbars', '--force-device-scale-factor=1'] + args
    # The events widget localises to the browser's timezone, so run the
    # browser on church time and the scrape is byte-identical everywhere.
    env = dict(os.environ, TZ='America/Los_Angeles')
    return subprocess.run(cmd, capture_output=capture, text=capture,
                          stderr=None if capture else subprocess.DEVNULL,
                          env=env, timeout=timeout, check=False)


def dump_dom(url):
    """The page's DOM after its JS has run. The virtual time budget gives the
    events widget room to fetch and render before we snapshot it."""
    proc = chrome_run(['--virtual-time-budget=30000', '--dump-dom', url], capture=True)
    dom = proc.stdout or ''
    if len(dom) < 5000:
        raise RuntimeError(f'Chrome returned only {len(dom)} bytes of DOM for {url}')
    return dom


def render_png(html_path, png_path):
    chrome_run(['--window-size=1920,1080', '--virtual-time-budget=15000',
                f'--screenshot={png_path}', f'file://{html_path}'], capture=False)
    if not os.path.exists(png_path):
        raise RuntimeError('Chrome produced no screenshot')


# --------------------------------------------------------------------------
# Gemini
# --------------------------------------------------------------------------

def build_classify_prompt(events):
    listing = '\n'.join(
        f"{i}. {e['name']}  [starts {e['start']}, venue: {e.get('venue') or 'unknown'}]"
        for i, e in enumerate(events)
    )
    return f"""These are events scraped from a church's public calendar. The church puts an advertisement of its UPCOMING SPECIAL EVENTS on screens around campus, and needs the routine recurring services filtered out.

For each event decide:
  "recurring": true if it is one instance of a routine, repeating series the congregation already knows about (e.g. the weekly Sunday worship service, a standing weekly bible study or rehearsal). false if it is a distinct one-off happening -- a concert, an author talk, a fundraiser, a holiday special.
  "category": a SHORT uppercase label for a badge on the ad, 1-2 words, chosen to fit the event (e.g. "CONCERT", "SPECIAL EVENT", "AUTHOR TALK", "COMMUNITY").

A name that appears many times at the same time of day and venue is recurring. When genuinely unsure, answer false -- leaving a real event off the ad is worse than listing one extra.

Events:
{listing}

Respond with ONLY compact JSON, no markdown fences, no commentary, matching exactly this schema:
{{"events": [{{"i": 0, "recurring": true, "category": "WORSHIP"}}]}}

Include one object for every event, with "i" the number shown above."""


def gemini_classify(events, api_key):
    """{event_key: {"recurring": bool, "category": str}} for `events`."""
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key,
                          http_options=types.HttpOptions(timeout=600000))
    print(f"  Asking Gemini to classify {len(events)} event(s)...")
    resp = client.models.generate_content(
        model=GEMINI_MODEL, contents=build_classify_prompt(events)
    )
    text = (resp.text or '').strip()
    text = re.sub(r'^```(?:json)?|```$', '', text, flags=re.M).strip()
    try:
        parsed = json.loads(text)
    except ValueError:
        print('  Could not parse Gemini JSON; falling back to the heuristic.')
        return {}

    out = {}
    for row in parsed.get('events', []):
        try:
            event = events[int(row['i'])]
        except (KeyError, ValueError, TypeError, IndexError):
            continue
        category = str(row.get('category') or 'EVENT').strip().upper()[:18]
        out[event_key(event)] = {
            'recurring': bool(row.get('recurring')),
            'category': category or 'EVENT',
        }
    return out


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------

def build_qr_data_uri(url, scale=4):
    """The calendar URL as an inline SVG data URI.

    Inlined rather than linked so the card renders identically whether Chrome
    has network or not -- a QR fetched from a CDN that fails to load would
    leave a blank square on every screen on campus, and nothing would notice.

    The quiet zone is baked into the SVG (border=4, the spec minimum) instead
    of being left to CSS padding, so restyling the header can't quietly make
    the code unscannable. Error correction 'm' (~15%) survives the glare and
    off-angle phone shots these screens actually get.
    """
    try:
        import segno
    except ImportError:
        print('  segno not installed; rendering the card without a QR code.')
        return ''
    return segno.make(url, error='m').svg_data_uri(
        scale=scale, border=4, dark='#1A1A1A', light='#FFFFFF',
    )


CARD_TEMPLATE = """  <div class="card {tone}">
    <div class="date"><div class="mon">{mon}</div><div class="day">{day}</div><div class="dow">{dow}</div></div>
    <div class="body">
      <span class="tag">{category}</span>
      <div class="title">{title}</div>
      <div class="meta"><span class="time">{time}</span><span class="dot">&bull;</span><span class="venue">{venue}</span></div>
    </div>
  </div>"""


def format_time(dt):
    return dt.strftime('%-I:%M %p')


def build_html(cards, extra_count=0):
    rows = max(1, (len(cards) + 2) // 3)
    blocks = []
    for c in cards:
        dt = c['_dt']
        # Gold for the softer "special event" style badges, crimson for the
        # rest, so the grid reads as two kinds of thing at a glance.
        tone = 'special' if 'SPECIAL' in c['category'] else 'concert'
        blocks.append(CARD_TEMPLATE.format(
            tone=tone,
            mon=dt.strftime('%b').upper(),
            day=dt.day,
            dow=dt.strftime('%a').upper(),
            category=html.escape(c['category']),
            title=html.escape(smart_quotes(c['name'])),
            time=format_time(dt),
            venue=html.escape(c['venue'] or 'First Church'),
        ))

    # The footer used to carry "Events through <last date>", which existed so
    # cleanup_events_folder.py's vision read would expire the card on the
    # right day. The card is on that script's protected list now, so the line
    # was doing nothing but ageing the ad in the reader's eye.
    more = (f" &middot; +{extra_count} more at fccla.org/calendar") if extra_count else ''

    qr_uri = build_qr_data_uri(CALENDAR_URL)
    qr_block = QR_TEMPLATE.format(uri=qr_uri) if qr_uri else ''

    return HTML_SHELL.format(
        rows=rows,
        cards='\n\n'.join(blocks),
        more=more,
        qr=qr_block,
    )


QR_TEMPLATE = """<div class="qr">
      <img src="{uri}" alt="QR code linking to fccla.org/calendar">
    </div>"""


HTML_SHELL = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Upcoming Events at a Glance</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Cinzel:wght@500;700&family=Source+Sans+3:wght@300;400;600;700&display=swap" rel="stylesheet">
<style>
  :root{{
    --crimson:#99001A; --crimson-deep:#6E0013; --ivory:#F7F3EC;
    --ink:#1A1A1A; --muted:#6B5F58; --gold:#C8A04B; --rule:rgba(26,26,26,.12);
  }}
  *{{box-sizing:border-box;margin:0;padding:0}}
  html,body{{width:1920px;height:1080px}}
  body{{background:var(--ivory);color:var(--ink);
    font-family:"Source Sans 3","Helvetica Neue",Arial,sans-serif;
    display:flex;flex-direction:column;overflow:hidden;}}
  header{{background:var(--crimson);color:#fff;padding:22px 70px 20px;
    display:flex;align-items:center;justify-content:space-between;
    border-bottom:6px solid var(--gold);flex:0 0 auto;}}
  .eyebrow{{font-size:19px;letter-spacing:.34em;text-transform:uppercase;
    font-weight:600;color:rgba(255,255,255,.72);margin-bottom:10px;}}
  h1{{font-family:Cinzel,Georgia,serif;font-weight:700;font-size:67px;
    line-height:1;letter-spacing:.015em;}}
  .header-right{{display:flex;align-items:center;gap:28px;
    text-align:right;line-height:1.5}}
  .qr{{background:#fff;border-radius:6px;padding:10px;flex:0 0 auto;
    text-align:center;box-shadow:0 2px 8px rgba(0,0,0,.18)}}
  .qr img{{display:block;width:168px;height:168px}}
  .header-right .site{{font-family:Cinzel,Georgia,serif;font-size:29px;
    font-weight:500;letter-spacing:.06em;color:#fff;}}
  .header-right .addr{{font-size:19px;color:rgba(255,255,255,.75);letter-spacing:.05em;}}
  main{{flex:1 1 auto;padding:34px 70px 0;display:grid;
    grid-template-columns:repeat(3,1fr);grid-template-rows:repeat({rows},1fr);gap:26px;}}
  .card{{background:#fff;border:1px solid var(--rule);border-top:5px solid var(--crimson);
    border-radius:4px;padding:20px 26px 18px;display:flex;gap:24px;
    box-shadow:0 2px 10px rgba(110,0,19,.07);min-height:0;}}
  .card.special{{border-top-color:var(--gold)}}
  .date{{flex:0 0 106px;text-align:center;padding-top:2px}}
  .mon{{font-size:23px;font-weight:700;letter-spacing:.17em;
    text-transform:uppercase;color:var(--crimson);}}
  .day{{font-family:Cinzel,Georgia,serif;font-weight:700;font-size:66px;
    line-height:.96;color:var(--ink);}}
  .dow{{font-size:16px;letter-spacing:.13em;text-transform:uppercase;
    color:var(--muted);margin-top:5px;}}
  .body{{flex:1 1 auto;min-width:0;display:flex;flex-direction:column}}
  .tag{{display:inline-block;align-self:flex-start;font-size:13px;font-weight:700;
    letter-spacing:.17em;text-transform:uppercase;padding:3px 10px;
    border-radius:2px;margin-bottom:8px;
    background:rgba(153,0,26,.09);color:var(--crimson);}}
  .special .tag{{background:rgba(200,160,75,.18);color:#8A6A1F}}
  .title{{font-family:Cinzel,Georgia,serif;font-weight:700;font-size:22px;
    line-height:1.19;color:var(--ink);display:-webkit-box;-webkit-line-clamp:4;
    -webkit-box-orient:vertical;overflow:hidden;}}
  .meta{{margin-top:auto;padding-top:11px;border-top:1px solid var(--rule);
    display:flex;align-items:baseline;gap:12px;font-size:20px;color:var(--muted);}}
  .meta .time{{font-weight:700;color:var(--crimson-deep);font-size:22px}}
  .meta .dot{{color:var(--rule)}}
  footer{{flex:0 0 auto;padding:22px 70px 26px;display:flex;align-items:center;
    justify-content:space-between;font-size:19px;color:var(--muted);letter-spacing:.04em;}}
  footer .cta{{font-family:Cinzel,Georgia,serif;font-size:25px;font-weight:700;
    color:var(--crimson);letter-spacing:.05em;}}
  footer .note{{font-size:17px;color:#9A8F88}}
</style>
</head>
<body>

<header>
  <div>
    <div class="eyebrow">First Congregational Church of Los Angeles</div>
    <h1>Upcoming Events</h1>
  </div>
  <div class="header-right">
    <div>
      <div class="site">fccla.org/calendar</div>
      <div class="addr">540 S Commonwealth Ave &middot; Los Angeles</div>
    </div>
{qr}
  </div>
</header>

<main>

{cards}

</main>

<footer>
  <div class="cta">Tickets &amp; details at fccla.org/calendar</div>
  <div class="note">Weekly Sunday Worship, 10:30 AM in the Sanctuary{more}</div>
</footer>

</body>
</html>
"""


# --------------------------------------------------------------------------

def queue_card(png_path, dry_run):
    os.makedirs(QUEUE_DIR, exist_ok=True)
    # Clear any earlier card still waiting in the queue -- if two runs land
    # before process_uploads.yml drains it, only the newest should upload.
    for stale in os.listdir(QUEUE_DIR):
        if any(stale.endswith(f'---{name}') for name in CARD_FILENAMES):
            print(f"  Replacing card still queued from an earlier run: {stale}")
            if not dry_run:
                os.remove(os.path.join(QUEUE_DIR, stale))

    queued = []
    for name in CARD_FILENAMES:
        ts = int(datetime.datetime.now(datetime.timezone.utc).timestamp() * 1000)
        dest = os.path.join(QUEUE_DIR, f'{ts}---{EVENTS_FOLDER_ID}---{name}')
        if dry_run:
            print(f"  DRY RUN: would queue {os.path.basename(dest)}")
            continue
        with open(png_path, 'rb') as src, open(dest, 'wb') as out:
            out.write(src.read())
        print(f"  Queued {os.path.basename(dest)}")
        queued.append(dest)
    return queued


def main():
    dry_run = os.environ.get('DRY_RUN') == '1'
    force = os.environ.get('FORCE_REFRESH') == '1'
    now = datetime.datetime.now(TZ)
    state = load_state()

    print(f"Scraping {CALENDAR_URL} ...")
    scraped = parse_ld_events(dump_dom(CALENDAR_URL))
    if not scraped:
        print('No schema.org events found on the page -- the widget may have '
              'changed. Leaving the existing card alone.')
        sys.exit(1)
    print(f"Found {len(scraped)} event(s) on the page.")

    candidates = upcoming_events(scraped, now)
    print(f"{len(candidates)} upcoming within {WINDOW_DAYS} days.")

    fp = page_fingerprint(scraped)
    page_changed = force or fp != state.get('page_fingerprint')
    # Only ever holds verdicts Gemini actually gave us. Heuristic fallbacks are
    # recomputed each run and deliberately never written here: caching one
    # would make a single keyless or failed run permanent, and Gemini would
    # never get another chance at that event.
    classified = dict(state.get('classified') or {})

    if not page_changed:
        print('Calendar unchanged since the last run.')

    # An event is sent to Gemini only if it has no cached verdict. On an
    # unchanged page that set is empty, so no call happens -- and an event
    # whose classification failed last run is retried rather than stranded.
    unknown = [e for e in candidates if event_key(e) not in classified]
    if not unknown:
        print('Every upcoming event is already classified; no Gemini call.')
    else:
        api_key = os.environ.get('GEMINI_API_KEY')
        if api_key:
            try:
                classified.update(gemini_classify(unknown, api_key))
            except Exception as e:
                print(f"  Gemini classification failed ({e}); using the heuristic.")
        else:
            print(f"  GEMINI_API_KEY not set; using the heuristic for "
                  f"{len(unknown)} event(s).")

    cards = []
    for e in candidates:
        # Fall back per event, so one unclassified newcomer doesn't discard
        # Gemini's verdicts on everything else.
        verdict = classified.get(event_key(e)) or {
            'recurring': heuristic_recurring(e['name']),
            'category': 'EVENT',
        }
        if verdict.get('recurring'):
            continue
        cards.append(dict(e, category=verdict.get('category') or 'EVENT'))

    extra = max(0, len(cards) - MAX_CARDS)
    cards = cards[:MAX_CARDS]
    print(f"{len(cards)} non-recurring event(s) on the card"
          + (f" (+{extra} beyond the grid)" if extra else ""))

    if not cards:
        print('Nothing non-recurring to advertise; leaving the existing card alone.')
        return

    card_fp = card_fingerprint(cards)
    if not force and card_fp == state.get('card_fingerprint') and os.path.exists(PNG_PATH):
        print('Card contents identical to the last run; nothing to re-render or upload.')
        # The page fingerprint may still have moved (an event outside the
        # window changed), so persist it to keep the Gemini gate accurate.
        state.update({'page_fingerprint': fp, 'classified': classified})
        save_state(state)
        return

    print('Rendering card...')
    with open(HTML_PATH, 'w', encoding='utf-8') as f:
        f.write(build_html(cards, extra))
    render_png(HTML_PATH, PNG_PATH)
    print(f"  Wrote {os.path.basename(PNG_PATH)} "
          f"({os.path.getsize(PNG_PATH)} bytes)")

    queue_card(PNG_PATH, dry_run)

    if not dry_run:
        # Only commit the fingerprints once the card they describe is actually
        # queued, so a crash mid-render doesn't convince the next run there's
        # nothing to do.
        state.update({
            'page_fingerprint': fp,
            'card_fingerprint': card_fp,
            'classified': classified,
            'updated_at': now.isoformat(timespec='seconds'),
        })
        save_state(state)

    print('Done.')


if __name__ == '__main__':
    main()
