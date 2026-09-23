"""
Renders the Meetinghouse Newsletter card for the campus screens.

This used to be the one static card of the four: a sign-up code and a promise.
It now reads the week's actual newsletter and puts a few of its items on the
screen beside that code, so the card gives people a reason to scan instead of
asking them to take the church's word for it.

The chain:
    fccla.org/meetinghouse-newsletter   the archive list, injected by JS
      -> the most recent issue's conta.cc link
      -> the issue itself, which is plain server-rendered HTML
      -> Gemini picks the items worth putting on a screen
      -> 1920x1080 PNG -> every copy of the card in Drive

Gemini runs once per issue and nothing else moves that: the cache key is the
issue's URL and a fingerprint of its text, and the workflow commits that
cache the way the events card commits its own. The newsletter is weekly, so
that is one model call a week however often this runs.

That one call picks PICK_ITEMS items and caches all of them, though only
MAX_ITEMS are shown. The spares are what keep the card full between issues:
an item is dropped when its date passes, or when the events card starts
advertising the same thing, and the next one moves up in its place.

Two things keep a stale card off the screens. Items the newsletter gives a
date to are dropped once that date has passed, so a Saturday workday stops
being advertised on Sunday. Items the at-a-glance card is already showing
are dropped too, so one event doesn't take two slots in the rotation. And if nothing survives that -- or the archive
fetch, the issue fetch, or the model fails, or there's no API key -- the card
falls back to the static sign-up design it used to be. A plain card is a
worse card; a card still advertising last Saturday is a wrong one.

Env:
    GEMINI_API_KEY   required to pick items; without it the card falls back
    GDRIVE_OAUTH_JSON / GDRIVE_SERVICE_ACCOUNT_JSON   required to upload
    DRY_RUN          "1" renders but uploads nothing
    FORCE_REFRESH    "1" re-asks Gemini even when the issue hasn't changed
"""

import datetime
import hashlib
import html as html_mod
import json
import os
import re
import subprocess
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
HTML_PATH = os.path.join(HERE, 'newsletter-ad.html')
PNG_PATH = os.path.join(HERE, 'newsletter-ad.png')
STATE_PATH = os.path.join(HERE, 'newsletter_ad_state.json')

REPO_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
UPLOADER_DIR = os.path.join(REPO_ROOT, 'Worship Scripts', 'worship workflows')
ROTATION_DIR = os.path.join(REPO_ROOT, 'Utilities', 'Rotation')

SIGNUP_URL = 'https://www.fccla.org/meetinghouse-newsletter'
# The same page carries the sign-up form and the archive of past issues, so
# the QR target and the thing we scrape are one URL. Named twice because they
# are two different jobs and either could move without the other.
ARCHIVE_URL = SIGNUP_URL

# Matches drive_cards.CARDS and cleanup_events_folder.py's protected list;
# see those for why copies are found by fragment rather than exact name.
CARD_FRAGMENT = 'meetinghouse-newsletter'

GEMINI_MODEL = 'gemini-3.5-flash'

# How many items fit on the screen and still read from across a room.
MAX_ITEMS = 4
# How many to ask for and cache. Gemini is only asked when a new issue goes
# out, so the spares are what keep the card full for the rest of the week:
# items drop off as their dates pass, and again if the events card starts
# advertising one of them. Without a bench each of those would shrink the
# card with nothing to put in the gap.
PICK_ITEMS = 6
# Below this the highlights column looks broken rather than sparse, and the
# static card is the better answer.
MIN_ITEMS = 2

# The events card's state file, which lists what that card is advertising.
# It's committed, so a checkout has it; it's at most an hour stale, since
# both cards run hourly.
EVENTS_STATE_PATH = os.path.join(REPO_ROOT, 'Utilities', 'Events Ad',
                                 'events_ad_state.json')
# Two titles this long or longer count as the same happening when one
# contains the other. Short titles ("Men's Group") are too generic for
# containment to mean anything.
CONTAINMENT_FLOOR = 12
# Failing that, the share of meaningful words two titles have in common that
# makes them the same event. The calendar and the newsletter rephrase around
# each other -- "Min Jin Lee discusses 'American Hagwon'" against "A
# Conversation with Min Jin Lee about American Hagwon" -- and neither
# contains the other. 0.6 clears that pair and still leaves "Men's Group" and
# "Young Men's Group Retreat" apart, which is the direction to err in:
# dropping a real item costs a slot, a duplicate only wastes one.
OVERLAP_FLOOR = 0.6
# Connective and announcement words, which say nothing about which event this
# is. Stripping them is most of what lets two house styles line up.
STOPWORDS = frozenset(
    'a an the and or of in on at to for with from by is its our we this '
    'presents present discusses discuss signs sign talks talk about '
    'featuring feat live plus night day'.split())

LOCAL_TZ_NAME = 'America/Los_Angeles'

CHROME_CANDIDATES = [
    os.environ.get('CHROME_BIN') or '',
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    '/usr/bin/google-chrome',
    '/usr/bin/google-chrome-stable',
    '/usr/bin/chromium-browser',
    '/usr/bin/chromium',
]

USER_AGENT = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
              'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36')

MONTHS = {m: i for i, m in enumerate(
    ['january', 'february', 'march', 'april', 'may', 'june', 'july', 'august',
     'september', 'october', 'november', 'december'], start=1)}


# --------------------------------------------------------------------------
# Chrome
# --------------------------------------------------------------------------

def find_chrome():
    for path in CHROME_CANDIDATES:
        if path and os.path.exists(path):
            return path
    raise RuntimeError('No Chrome binary found. Set CHROME_BIN, or install google-chrome.')


def chrome_run(args, capture, timeout=180):
    # Chrome writes GPU and display noise to stderr on every headless run, so
    # it's dropped; each caller checks for the output it actually wanted.
    cmd = [find_chrome(), '--headless', '--disable-gpu', '--no-sandbox',
           '--hide-scrollbars', '--force-device-scale-factor=1'] + args
    env = dict(os.environ, TZ=LOCAL_TZ_NAME)
    return subprocess.run(cmd, capture_output=capture, text=capture,
                          stderr=None if capture else subprocess.DEVNULL,
                          env=env, timeout=timeout, check=False)


def dump_dom(url):
    """The page's DOM after its JS has run.

    The archive list is injected client-side -- the served HTML has no issue
    links at all -- so a plain fetch of this page reads as an empty archive
    rather than as a failure. It has to come from a browser.
    """
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
# The archive, and the latest issue
# --------------------------------------------------------------------------

def parse_title_date(title):
    """The date out of "The Meetinghouse This Week - September 17th, 2026"."""
    m = re.search(r'([A-Za-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?[,\s]+(\d{4})', title or '')
    if not m:
        return None
    month = MONTHS.get(m.group(1).lower())
    if not month:
        return None
    try:
        return datetime.date(int(m.group(3)), month, int(m.group(2)))
    except ValueError:
        return None


def parse_archive(dom):
    """Every issue linked from the archive page, newest first.

    The page claims to list them most-recent-first and currently does, but
    the card would be wrong for a week if that ever slipped, so the dates in
    the link text decide the order. Links without a parseable date keep their
    document position behind the dated ones.
    """
    issues = []
    for i, m in enumerate(re.finditer(
            r'(?is)<a[^>]+href="(https://conta\.cc/[^"]+)"[^>]*>(.*?)</a>', dom)):
        title = html_mod.unescape(re.sub(r'(?s)<[^>]+>', '', m.group(2)))
        title = re.sub(r'\s+', ' ', title).strip()
        if not title:
            continue
        issues.append({'url': m.group(1), 'title': title,
                       'date': parse_title_date(title), 'pos': i})
    issues.sort(key=lambda e: (e['date'] is None,
                               -e['date'].toordinal() if e['date'] else 0,
                               e['pos']))
    return issues


def fetch_issue(url, timeout=60):
    """The issue's HTML. conta.cc is a redirect to the Constant Contact copy,
    which is server-rendered, so this needs no browser."""
    req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    return raw.decode('utf-8', errors='replace')


def issue_text(raw_html):
    """The issue as readable text, one block per visual line.

    A Constant Contact issue is nested layout tables; the block boundaries
    that survive are the ones the tags imply, so those become newlines and
    everything else collapses.
    """
    s = re.sub(r'(?is)<(script|style|head)[^>]*>.*?</\1>', ' ', raw_html)
    s = re.sub(r'(?i)<(br|/p|/div|/td|/tr|/h[1-6]|/li)[^>]*>', '\n', s)
    text = html_mod.unescape(re.sub(r'(?s)<[^>]+>', ' ', s))
    # Constant Contact litters zero-width joiners through the copy.
    text = text.replace('﻿', '').replace('​', '')
    lines = [re.sub(r'[ \t]+', ' ', line).strip() for line in text.split('\n')]
    return '\n'.join(line for line in lines if line)


def events_on_the_other_card():
    """The event names the at-a-glance card is currently showing.

    The newsletter writes up the same concerts and talks that are on the
    church calendar, so without this the rotation shows the same event twice
    in four slots. Missing or unreadable state means no exclusions rather
    than a failed run -- a duplicate is a wasted slot, not a wrong card.
    """
    try:
        with open(EVENTS_STATE_PATH, encoding='utf-8') as f:
            state = json.load(f)
    except (OSError, ValueError):
        print('  No events-card state to read; not excluding anything.')
        return []
    names = [str(e.get('name') or '').strip()
             for e in (state.get('card_events') or [])]
    return [n for n in names if n]


def normalise_title(title):
    """A title reduced to what's worth comparing across two sources.

    The calendar and the newsletter name the same event differently -- "A
    Conversation with Mother Agapia" against "Conversation With Mother
    Agapia" -- so case, punctuation and leading articles all go.
    """
    text = re.sub(r"[^a-z0-9 ]+", ' ', (title or '').lower())
    text = re.sub(r'\s+', ' ', text).strip()
    for article in ('the ', 'a ', 'an '):
        if text.startswith(article):
            text = text[len(article):]
            break
    return text


def significant_tokens(title):
    """The words in a title that say which event it is.

    Single characters go with the stopwords: stripping punctuation turns
    every possessive into a stray "s", and counting those as shared
    vocabulary matched "Men's Group" to "Young Men's Group Retreat".
    """
    return {w for w in normalise_title(title).split()
            if len(w) > 1 and w not in STOPWORDS}


def token_overlap(one, other):
    """How much of two titles' meaningful vocabulary is shared, 0 to 1.

    Two words have to match before this says anything at all: a single shared
    word is a coincidence, and acting on it would drop real items.
    """
    first, second = significant_tokens(one), significant_tokens(other)
    if len(first) < 2 or len(second) < 2:
        return 0.0
    shared = first & second
    if len(shared) < 2:
        return 0.0
    return len(shared) / len(first | second)


def same_happening(one, other):
    """Whether two titles name the same event, across two house styles."""
    left, right = normalise_title(one), normalise_title(other)
    if not left or not right:
        return False
    if left == right:
        return True
    short, long = sorted((left, right), key=len)
    if len(short) >= CONTAINMENT_FLOOR and short in long:
        return True
    return token_overlap(one, other) >= OVERLAP_FLOOR


def drop_duplicates(items, excluded):
    """Items the at-a-glance card is already advertising, removed.

    The prompt asks Gemini to steer around these, and it mostly does. This is
    the backstop for the cases it can't see: items cached from before an
    event was added to the calendar.
    """
    kept = []
    for item in items:
        match = next((e for e in excluded if same_happening(item['title'], e)), None)
        if match:
            print(f"  Already on the events card, dropping: {item['title']}")
            continue
        kept.append(item)
    return kept


def text_fingerprint(text):
    """The cache key: the issue's text, and nothing else.

    A new Meetinghouse is the only thing that costs a model call. What the
    events card happens to be advertising deliberately stays out of this --
    the calendar moves several times a week and the newsletter doesn't, so
    keying on it would turn one call a week into several. The exclusions are
    applied by the post-filter instead, and PICK_ITEMS is what covers the
    gap a filtered item leaves.
    """
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


# --------------------------------------------------------------------------
# Gemini
# --------------------------------------------------------------------------

def build_highlights_prompt(issue, text, today, excluded=()):
    body = text[:14000]
    avoid = ''
    if excluded:
        listing = '\n'.join(f'  - {name}' for name in excluded)
        avoid = (
            '\nAnother card in the same rotation is already advertising these '
            'events, so skip any newsletter item that is the same happening -- '
            'even if the newsletter names it differently. Showing it twice '
            'wastes a slot:\n' + listing + '\n')
    return f"""This is one issue of a church's weekly email newsletter, "The Meetinghouse". It went out on {issue.get('date') or 'an unknown date'}. Today is {today}.

The church shows a rotating set of cards on screens around its campus. One of those cards advertises this newsletter, and should carry a few of its items so passers-by can see what's actually in it.

Pick the {PICK_ITEMS} items most worth a stranger's attention as they walk past a screen. Only the first few are shown at a time -- the rest are held in reserve and move up as earlier ones pass, so all {PICK_ITEMS} should be worth showing on their own. Prefer things somebody could still turn up to or act on: upcoming gatherings, classes, concerts, volunteer calls, drives. Skip anything that already happened -- recaps, thank-yous, "last Sunday we..." -- and skip the weekly Sunday worship service, which has a card of its own. Skip pure administrative notices.

For each item give:
  "title": the item's name, 40 characters or fewer, in title case. No trailing punctuation.
  "when":  when it happens, as it would be said aloud and 34 characters or fewer -- e.g. "Saturday, Sept 26 | 11am", "Tuesdays | 7pm". Empty string if the newsletter gives no time.
  "date":  the date it happens as YYYY-MM-DD, if the newsletter states or clearly implies one. Empty string otherwise. For something recurring, the next occurrence. This is used to drop the item once it's past, so leave it empty rather than guessing.
  "blurb": one plain sentence of at most 85 characters saying what it is. No exclamation marks. Don't repeat the title.

Order them soonest first.
{avoid}
Newsletter:
{body}

Respond with ONLY compact JSON, no markdown fences, no commentary, matching exactly this schema:
{{"items": [{{"title": "Braiding Sweetgrass Book Group", "when": "Tuesdays | 7pm | Zoom", "date": "2026-09-23", "blurb": "Rev. Michael leads a weekly conversation on Robin Wall Kimmerer's book."}}]}}"""


def gemini_highlights(issue, text, api_key, today, excluded=()):
    """The items to put on the card, as Gemini picked them.

    Returns [] on anything unexpected rather than raising: the caller's
    fallback is the old static card, which is always better than no card.
    """
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        print('  google-genai not installed; falling back to the static card.')
        return []

    client = genai.Client(api_key=api_key,
                          http_options=types.HttpOptions(timeout=600000))
    print(f"  Asking Gemini for the highlights of {issue['title']!r}...")
    try:
        resp = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=build_highlights_prompt(issue, text, today, excluded))
    except Exception as exc:                      # noqa: BLE001 - see docstring
        print(f'  Gemini call failed ({exc.__class__.__name__}); '
              f'falling back to the static card.')
        return []

    raw = (resp.text or '').strip()
    raw = re.sub(r'^```(?:json)?|```$', '', raw, flags=re.M).strip()
    try:
        parsed = json.loads(raw)
    except ValueError:
        print('  Could not parse Gemini JSON; falling back to the static card.')
        return []
    return [item for item in
            (clean_item(row) for row in (parsed.get('items') or [])) if item]


def clean_item(row):
    """One model row, trimmed to what the card can actually show."""
    if not isinstance(row, dict):
        return None
    title = re.sub(r'\s+', ' ', str(row.get('title') or '')).strip().rstrip('.!')
    if not title:
        return None
    blurb = re.sub(r'\s+', ' ', str(row.get('blurb') or '')).strip()
    when = re.sub(r'\s+', ' ', str(row.get('when') or '')).strip()
    date = str(row.get('date') or '').strip()
    if date:
        try:
            datetime.date.fromisoformat(date)
        except ValueError:
            date = ''
    # The lengths are asked for in the prompt and enforced here: a model that
    # runs long should give a clipped line, never a blown-out layout.
    return {'title': clip(title, 60), 'when': clip(when, 40),
            'blurb': clip(blurb, 120), 'date': date}


def clip(text, limit):
    """Trim to `limit` characters, on a word boundary.

    The prompt asks for short strings and the model mostly obliges; this is
    the backstop for when it doesn't. A blind slice cuts mid-word -- "Friends
    of the Holy La" -- which reads as a broken card rather than a long title,
    so back up to whitespace and mark the cut.
    """
    text = (text or '').strip()
    if len(text) <= limit:
        return text
    cut = text[:limit].rstrip()
    space = cut.rfind(' ')
    if space > limit * 0.6:
        cut = cut[:space]
    return cut.rstrip(' ,;:-\u2013\u2014') + '\u2026'


def drop_past(items, today):
    """Items whose stated date has gone by, removed.

    The newsletter is weekly and this card is rendered far more often than
    that, so without this the screens would spend the back half of each week
    advertising things that already happened.
    """
    kept = []
    for item in items:
        if item['date'] and datetime.date.fromisoformat(item['date']) < today:
            print(f"  Past, dropping: {item['title']} ({item['date']})")
            continue
        kept.append(item)
    return kept


def order_items(items):
    """Dated items soonest first, undated ones after in the model's order."""
    return [it for _, it in sorted(
        enumerate(items),
        key=lambda pair: (not pair[1]['date'], pair[1]['date'] or '', pair[0]))]


# --------------------------------------------------------------------------
# State
# --------------------------------------------------------------------------

def load_state():
    try:
        with open(STATE_PATH, encoding='utf-8') as f:
            state = json.load(f)
    except (OSError, ValueError):
        return {'issue_url': None, 'issue_fingerprint': None, 'items': []}
    state.setdefault('items', [])
    return state


def save_state(state):
    with open(STATE_PATH, 'w', encoding='utf-8') as f:
        json.dump(state, f, indent=2, sort_keys=True)


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------

def build_qr_data_uri(url, scale=4):
    """The sign-up link as an inline SVG data URI.

    Inlined, with the quiet zone baked into the SVG rather than left to CSS,
    for the same reasons as the other cards: a code fetched at render time
    that failed would be a blank square on every screen, and restyling the
    page shouldn't be able to make it unscannable.
    """
    if not url:
        return ''
    try:
        import segno
    except ImportError:
        print('  segno not installed; rendering the card without a QR code.')
        return ''
    return segno.make(url, error='m').svg_data_uri(
        scale=scale, border=4, dark='#1A1A1A', light='#FFFFFF')


def esc(value):
    return html_mod.escape(value or '', quote=True)


def format_issue_date(date):
    """"September 17, 2026" -- built by hand because the no-pad day format
    is spelled differently on every platform."""
    if not date:
        return ''
    return f'{date:%B} {date.day}, {date.year}'


def build_items_html(items):
    rows = []
    for item in items:
        when = (f'<div class="when">{esc(item["when"])}</div>'
                if item.get('when') else '')
        blurb = (f'<div class="blurb">{esc(item["blurb"])}</div>'
                 if item.get('blurb') else '')
        rows.append(f"""    <li class="item">
      <div class="item-body">
        <div class="item-title">{esc(item['title'])}</div>
{when}
{blurb}
      </div>
    </li>""")
    return '\n'.join(rows)


def build_html(issue=None, items=()):
    """The card. With no items this is the static sign-up card it always was."""
    items = list(items)
    qr_uri = build_qr_data_uri(SIGNUP_URL)
    # One code for both jobs: SIGNUP_URL is the archive of past issues and
    # the sign-up form on the same page, which is why the card can promise
    # reading and subscribing without a second code.
    alt = 'QR code to read the Meetinghouse Newsletter and subscribe'
    if not items:
        qr = (f'<img class="qr" src="{qr_uri}" alt="{alt}">') if qr_uri else ''
        return STATIC_SHELL.format(qr=qr)

    qr = (f'<img class="side-qr" src="{qr_uri}" alt="{alt}">') if qr_uri else ''
    dated = format_issue_date((issue or {}).get('date'))
    stamp = f'<div class="issue">{esc(dated)}</div>' if dated else ''
    return ISSUE_SHELL.format(items=build_items_html(items), qr=qr, issue=stamp)


SHARED_CSS = """
  :root{{
    --crimson:#99001A; --crimson-deep:#6E0013; --ivory:#F7F3EC; --ink:#1A1A1A;
    --muted:#6B5F58; --gold:#C8A04B; --rule:rgba(26,26,26,.12);
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
  h1{{font-family:Cinzel,Georgia,serif;font-weight:700;font-size:62px;
    line-height:1;letter-spacing:.015em;}}
  .header-right{{text-align:right;line-height:1.5}}
  .header-right .site{{font-family:Cinzel,Georgia,serif;font-size:29px;
    font-weight:500;letter-spacing:.06em;}}
  .header-right .addr{{font-size:19px;color:rgba(255,255,255,.75);letter-spacing:.05em;}}

  footer{{flex:0 0 auto;padding:22px 70px 26px;display:flex;
    align-items:center;justify-content:space-between;
    font-size:19px;color:var(--muted);letter-spacing:.04em;}}
  footer .tag{{font-family:Cinzel,Georgia,serif;font-size:25px;font-weight:700;
    color:var(--crimson);letter-spacing:.05em;}}
  footer .note{{font-size:17px;color:#9A8F88}}
"""

HEADER_HTML = """<header>
  <div>
    <div class="eyebrow">First Congregational Church of Los Angeles</div>
    <h1>Meetinghouse Newsletter</h1>
  </div>
  <div class="header-right">
    <div class="site">fccla.org</div>
    <div class="addr">540 S Commonwealth Ave &middot; Los Angeles</div>
  </div>
</header>"""

FOOTER_HTML = """<footer>
  <div class="tag">Delivered to your inbox every week</div>
  <div class="note">Weekly Sunday Worship, 10:30 AM in the Sanctuary</div>
</footer>"""

FONTS_HTML = """<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Cinzel:wght@500;700&family=Source+Sans+3:wght@300;400;600;700&display=swap" rel="stylesheet">"""


ISSUE_SHELL = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Meetinghouse Newsletter</title>
""" + FONTS_HTML + """
<style>""" + SHARED_CSS + """
  main{{flex:1 1 auto;display:flex;gap:56px;padding:34px 70px 10px;min-height:0;}}

  .highlights{{flex:1 1 auto;min-width:0;display:flex;flex-direction:column;}}
  .kicker{{display:flex;align-items:baseline;gap:20px;
    padding-bottom:14px;border-bottom:3px solid var(--gold);}}
  .kicker .lead{{font-family:Cinzel,Georgia,serif;font-size:42px;font-weight:700;
    color:var(--crimson);letter-spacing:.02em;}}
  .kicker .issue{{font-size:22px;color:var(--muted);letter-spacing:.06em;
    text-transform:uppercase;font-weight:600;}}

  .items{{list-style:none;flex:1 1 auto;display:flex;flex-direction:column;
    justify-content:center;gap:34px;min-height:0;padding:6px 0 10px;}}
  .item{{display:flex;gap:22px;align-items:stretch;}}
  .item-body{{flex:1 1 auto;min-width:0;border-left:7px solid var(--crimson);
    padding:4px 0 4px 22px;}}
  .item:nth-child(even) .item-body{{border-left-color:var(--gold)}}
  .item-title{{font-family:Cinzel,Georgia,serif;font-weight:700;font-size:40px;
    line-height:1.14;color:var(--ink);
    display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;
    overflow:hidden;}}
  .when{{font-size:24px;font-weight:700;color:var(--crimson-deep);
    letter-spacing:.05em;margin-top:7px;}}
  .blurb{{font-size:26px;line-height:1.32;color:var(--muted);margin-top:7px;
    display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;
    overflow:hidden;}}

  aside{{flex:0 0 430px;display:flex;flex-direction:column;align-items:center;
    justify-content:center;text-align:center;background:#fff;
    border:1px solid var(--rule);border-top:6px solid var(--crimson);
    border-radius:4px;padding:34px 30px;
    box-shadow:0 2px 12px rgba(110,0,19,.08);}}
  aside .ask{{font-family:Cinzel,Georgia,serif;font-size:37px;font-weight:700;
    line-height:1.16;color:var(--ink);}}
  aside .sub{{font-size:25px;line-height:1.3;color:var(--muted);
    margin-top:10px;letter-spacing:.02em;}}
  .side-qr{{width:286px;height:286px;background:#fff;border-radius:8px;
    margin:22px 0 20px;box-shadow:0 4px 18px rgba(26,26,26,.16);}}
  aside .url{{font-size:23px;font-weight:700;color:var(--crimson);
    letter-spacing:.03em;line-height:1.3;}}
</style>
</head>
<body>

""" + HEADER_HTML + """

<main>
  <div class="highlights">
    <div class="kicker">
      <div class="lead">Inside This Week</div>
{issue}
    </div>
    <ul class="items">
{items}
    </ul>
  </div>
  <aside>
    <div class="ask">Read the<br>Full Issue</div>
    <div class="sub">&amp; subscribe for next week</div>
{qr}
    <div class="url">fccla.org/<br>meetinghouse-newsletter</div>
  </aside>
</main>

""" + FOOTER_HTML + """

</body>
</html>
"""


STATIC_SHELL = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Meetinghouse Newsletter</title>
""" + FONTS_HTML + """
<style>""" + SHARED_CSS + """
  main{{flex:1 1 auto;display:flex;align-items:center;justify-content:center;
    padding:30px 70px 50px;}}
  .cta{{display:flex;flex-direction:column;align-items:center;text-align:center}}
  .lead{{font-family:Cinzel,Georgia,serif;font-size:58px;font-weight:700;
    color:var(--ink);letter-spacing:.01em;line-height:1.15;}}
  .qr{{width:300px;height:300px;background:#fff;border-radius:8px;
    margin-top:38px;box-shadow:0 4px 18px rgba(26,26,26,.18);}}
  .sub{{font-size:34px;line-height:1.3;color:var(--muted);
    margin-top:16px;letter-spacing:.02em;}}
  .url{{font-family:Cinzel,Georgia,serif;font-size:32px;font-weight:700;
    color:var(--crimson);letter-spacing:.04em;margin-top:22px}}
</style>
</head>
<body>

""" + HEADER_HTML + """

<main>
  <div class="cta">
    <div class="lead">Read the Full Issue</div>
    <div class="sub">&amp; subscribe for weekly news &amp; events</div>
    {qr}
    <div class="url">fccla.org/meetinghouse-newsletter</div>
  </div>
</main>

""" + FOOTER_HTML + """

</body>
</html>
"""


# --------------------------------------------------------------------------
# Publishing
# --------------------------------------------------------------------------

def publish(png_path, dry_run):
    """Refresh every copy of this card in Drive, found by fragment."""
    if UPLOADER_DIR not in sys.path:
        sys.path.insert(0, UPLOADER_DIR)
    if ROTATION_DIR not in sys.path:
        sys.path.insert(0, ROTATION_DIR)
    from upload_queue_to_drive import get_drive_service
    from drive_cards import publish_card

    drive = get_drive_service()
    if not drive:
        if dry_run:
            print(f'  DRY RUN: would refresh every copy of {CARD_FRAGMENT} '
                  f'(no Drive credentials here).')
            return True
        raise RuntimeError('No Drive credentials: set GDRIVE_OAUTH_JSON or '
                           'GDRIVE_SERVICE_ACCOUNT_JSON.')
    publish_card(drive, png_path, CARD_FRAGMENT, dry_run=dry_run)
    return True


# --------------------------------------------------------------------------

def gather_items(state, today, force):
    """The items for this run, asking Gemini only when the issue is new.

    Every failure here returns [] and leaves the state alone, which renders
    the static card. Nothing in this function should be able to take the
    screens down.
    """
    try:
        issues = parse_archive(dump_dom(ARCHIVE_URL))
    except Exception as exc:                      # noqa: BLE001
        print(f'  Could not read the archive ({exc}); falling back.')
        return None, [], []
    if not issues:
        print('  No issues linked from the archive page; falling back.')
        return None, [], []

    issue = issues[0]
    print(f"  Latest issue: {issue['title']}")

    try:
        text = issue_text(fetch_issue(issue['url']))
    except Exception as exc:                      # noqa: BLE001
        print(f'  Could not read the issue ({exc}); falling back.')
        return issue, [], []
    if len(text) < 500:
        print(f'  Issue body was only {len(text)} characters; falling back.')
        return issue, [], []

    excluded = events_on_the_other_card()
    if excluded:
        print(f'  {len(excluded)} event(s) already on the events card.')
    fingerprint = text_fingerprint(text)
    cached = state.get('items') or []
    if (not force and cached
            and state.get('issue_url') == issue['url']
            and state.get('issue_fingerprint') == fingerprint):
        print(f'  Issue unchanged; reusing {len(cached)} cached item(s).')
        return issue, [clean_item(c) for c in cached if clean_item(c)], excluded

    api_key = os.environ.get('GEMINI_API_KEY')
    if not api_key:
        print('  GEMINI_API_KEY not set; falling back to the static card.')
        return issue, [], excluded

    items = gemini_highlights(issue, text, api_key, today, excluded)[:PICK_ITEMS]
    if items:
        state.update({'issue_url': issue['url'],
                      'issue_fingerprint': fingerprint,
                      'issue_title': issue['title'],
                      'items': items})
    return issue, items, excluded


def local_today():
    """Today in Los Angeles, whatever timezone the runner thinks it's in.

    The events card learned this the hard way: a UTC runner after 5pm Pacific
    is already on tomorrow's date, which would drop an item on the morning of
    the day it happens.
    """
    try:
        from zoneinfo import ZoneInfo
        return datetime.datetime.now(ZoneInfo(LOCAL_TZ_NAME)).date()
    except Exception:                             # noqa: BLE001
        return datetime.datetime.now(
            datetime.timezone(datetime.timedelta(hours=-8))).date()


def main():
    dry_run = os.environ.get('DRY_RUN') == '1'
    force = os.environ.get('FORCE_REFRESH') == '1'
    today = local_today()

    print('Rendering the newsletter card...')
    state = load_state()
    issue, items, excluded = gather_items(state, today, force)

    # Filter first, then take the four that survive. The cache holds up to
    # PICK_ITEMS, so an item that has passed or is already on the events card
    # is replaced by the next one rather than leaving a gap.
    items = drop_duplicates(drop_past(items, today), excluded)
    items = order_items(items)[:MAX_ITEMS]
    if 0 < len(items) < MIN_ITEMS:
        print(f'  Only {len(items)} item(s) left; using the static card.')
        items = []
    # The run log is the only record of what actually went up on the screens,
    # so say what was chosen rather than just how many.
    if items:
        print(f'  {len(items)} item(s) on the card:')
        for it in items:
            when = f" [{it['when']}]" if it['when'] else ''
            print(f"    - {it['title']}{when}")
    else:
        print('  Static sign-up card.')

    with open(HTML_PATH, 'w', encoding='utf-8') as f:
        f.write(build_html(issue, items))
    render_png(HTML_PATH, PNG_PATH)
    print(f'  Wrote {os.path.basename(PNG_PATH)} ({os.path.getsize(PNG_PATH)} bytes)')

    publish(PNG_PATH, dry_run)
    if not dry_run:
        save_state(state)
    print('Done.')


if __name__ == '__main__':
    main()
