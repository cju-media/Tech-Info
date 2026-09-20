"""
Renders the "upcoming service" card for the campus screens from YouTube.

Finds the next scheduled broadcast on the worship playlist and builds the
card around that stream's own thumbnail. The thumbnails the team makes are
already complete advertisements -- series, sermon title, date, service times,
church name -- so this deliberately frames the thumbnail rather than
restating it. The header carries the scheduled day and time because that
comes from the API and is authoritative even if a thumbnail is stale.

Between a service ending and the next being created there is nothing
scheduled, so the card keeps showing the most recent past service, labelled
"Past Service" -- a real service with a real thumbnail says more than a
generic card. The channel card (avatar, name, subscriber count, subscribe
button) is the last resort: an empty playlist, or a thumbnail that can't be
downloaded.

Playlist, channel credentials and the YouTube client all come from
Youtube Processing/update_youtube_stream.py rather than being redeclared
here. update_youtube_stream.get_upcoming_streams() is deliberately NOT
reused: it also returns ended broadcasts ('none') because it exists to
backfill descriptions, and it drops the thumbnails. What this needs is the
single next broadcast, with its images.

Images are downloaded and inlined as base64 data URIs rather than linked, so
the render doesn't depend on Chrome reaching YouTube's CDN at screenshot
time -- a card whose thumbnail silently failed to load would be a white box
on every screen on campus.

Uploads straight to Drive, like the weather card and unlike the events card:
this regenerates hourly and the queue pipeline publishes by committing the
PNG to git.

Env:
    YOUTUBE_CREDENTIALS_JSON                          required
    GDRIVE_OAUTH_JSON / GDRIVE_SERVICE_ACCOUNT_JSON   required to upload
    DRY_RUN   "1" renders but uploads nothing
"""

import os
import sys
import base64
import datetime
import zoneinfo
import subprocess
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
HTML_PATH = os.path.join(HERE, 'service-ad.html')
PNG_PATH = os.path.join(HERE, 'service-ad.png')

REPO_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
UPLOADER_DIR = os.path.join(REPO_ROOT, 'Worship Scripts', 'worship workflows')
ROTATION_DIR = os.path.join(REPO_ROOT, 'Utilities', 'Rotation')
YOUTUBE_DIR = os.path.join(REPO_ROOT, 'Youtube Processing')

TZ = zoneinfo.ZoneInfo('America/Los_Angeles')
EVENTS_FOLDER_ID = '17-0kiqBKa0k5ofW6gOPrVbHl7nqanuQz'
# Stable on purpose, and on cleanup_events_folder.py's protected list: the
# thumbnail prints a service date, so the vision read would trash this card
# the day after the service it advertises -- exactly when the next one is
# about to replace it anyway.
# Identified by a fragment of the filename rather than an exact name:
# renumber_rotation.py renames every copy into the folder's running
# order ("03 - LA-Weather-Forecast.png"), so an exact name would go
# stale the first time the rotation shifted.
CARD_FRAGMENT = 'upcoming-service'

# A broadcast stays on the card until this long after its scheduled start, so
# the screen keeps advertising the service while it's actually happening
# instead of flipping to the generic channel card mid-worship.
GRACE = datetime.timedelta(hours=3)

# ...and the card starts pointing at the stream this long BEFORE the start.
# The card only changes when it's regenerated, and the Sunday timer fires at
# 10:27 for a 10:30 service: with no lead-in that run still saw "not started
# yet" and rendered "Upcoming Service" with a subscribe code, leaving the
# wrong card up until the 10:42 run -- twelve minutes into the service. The
# lead-in also matches reality, since the stream's waiting room is live
# before the service begins.
LEAD = datetime.timedelta(minutes=15)

CHROME_CANDIDATES = [
    os.environ.get('CHROME_BIN') or '',
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    '/usr/bin/google-chrome',
    '/usr/bin/google-chrome-stable',
    '/usr/bin/chromium-browser',
    '/usr/bin/chromium',
]


# --------------------------------------------------------------------------
# Pure helpers
# --------------------------------------------------------------------------

def parse_api_time(stamp):
    """An RFC3339 stamp from the API as church-local time, or None.

    YouTube returns UTC ('...Z'); the card prints service times, so it has to
    be converted rather than printed as-is."""
    if not stamp:
        return None
    try:
        dt = datetime.datetime.fromisoformat(str(stamp).replace('Z', '+00:00'))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt.astimezone(TZ)


def best_thumbnail(thumbnails):
    """The largest thumbnail YouTube offers, preferring the 16:9 ones.

    'maxres' and 'standard' are 16:9; 'high' and below are 4:3 with the image
    letterboxed inside, which looks like a mistake when it fills a 16:9 card.
    Ordered best-first, so a broadcast whose custom thumbnail hasn't finished
    processing still gets something."""
    for key in ('maxres', 'standard', 'high', 'medium', 'default'):
        url = (thumbnails or {}).get(key, {}).get('url')
        if url:
            return url
    return None


def pick_next_broadcast(videos, now):
    """The broadcast to put on the card, or None if the playlist has none.

    The soonest one still to come or under way, and failing that the most
    recent past one. That last case is the placeholder between a service
    ending and the next being created: a real service with a real thumbnail
    says more than a generic channel card, and the labels make clear it has
    already happened."""
    upcoming, past = [], []
    for video in videos or []:
        details = video.get('liveStreamingDetails') or {}
        start = parse_api_time(details.get('scheduledStartTime'))
        if start is None:
            continue
        # Ended, or long enough past that it can't still be running.
        if details.get('actualEndTime') or start + GRACE < now:
            past.append((start, video))
        else:
            upcoming.append((start, video))

    if upcoming:
        start, video = min(upcoming, key=lambda pair: pair[0])
    elif past:
        start, video = max(past, key=lambda pair: pair[0])
    else:
        return None
    snippet = video.get('snippet') or {}
    return {
        'id': video.get('id'),
        'title': (snippet.get('title') or '').strip(),
        'start': start,
        'thumbnail': best_thumbnail(snippet.get('thumbnails')),
    }


def format_when(start, now):
    """'Sunday, September 20 at 10:30 AM', with a friendlier lead-in when the
    service is today or tomorrow -- that's the version people act on."""
    if start is None:
        return ''
    day = start.date()
    today = now.date()
    if day == today:
        lead = 'Today'
    elif day == today + datetime.timedelta(days=1):
        lead = 'Tomorrow'
    else:
        lead = start.strftime('%A, %B %-d')
    return f"{lead} at {start.strftime('%-I:%M %p')}"


def subscribe_url(channel):
    """The channel's subscribe link, or '' if we don't know the channel.

    ?sub_confirmation=1 makes YouTube open the subscribe dialog straight
    away rather than just landing the visitor on the channel page -- the
    difference between a scan that subscribes and one that doesn't. Prefers
    the @handle because it survives; falls back to the channel id."""
    if not channel:
        return ''
    handle = (channel.get('handle') or '').strip()
    if handle.startswith('@'):
        return f'https://www.youtube.com/{handle}?sub_confirmation=1'
    cid = (channel.get('id') or '').strip()
    if cid:
        return f'https://www.youtube.com/channel/{cid}?sub_confirmation=1'
    return ''


def watch_url(service):
    """The broadcast's own watch link, or '' if we don't have its id."""
    vid = ((service or {}).get('id') or '').strip()
    return f'https://www.youtube.com/watch?v={vid}' if vid else ''


def build_qr_data_uri(url, scale=4):
    """The subscribe link as an inline SVG data URI.

    Inlined, and with the quiet zone baked into the SVG rather than left to
    CSS padding, for the same reasons as the events card: a QR fetched at
    render time that failed would be a blank square on every screen, and a
    restyle of the footer shouldn't be able to make the code unscannable."""
    if not url:
        return ''
    try:
        import segno
    except ImportError:
        print('  segno not installed; rendering the card without a QR code.')
        return ''
    return segno.make(url, error='m').svg_data_uri(
        scale=scale, border=4, dark='#1A1A1A', light='#FFFFFF')


def stream_is_current(start, now):
    """True once the card should point at the broadcast rather than sell the
    time: from LEAD before the scheduled start onward.

    Both the heading/line and the QR target key off this, so the card can't
    end up saying "Watch on YouTube" beside a code that subscribes."""
    return start is not None and now >= start - LEAD


def service_labels(start, now):
    """(heading, when-line) for a broadcast, before and after it begins.

    The card stays up for GRACE past the scheduled start so the screens keep
    advertising a service that's actually happening -- but "Sunday, September
    20 at 10:30 AM" then reads as an invitation to something the reader has
    already missed the start of. Once it's under way the card stops selling
    the time and starts pointing at the stream.
    """
    if not stream_is_current(start, now):
        return 'Upcoming Service', format_when(start, now)
    # From the lead-in through the grace window it's today's, and still worth
    # joining. After that the same card stays up as the placeholder until the
    # next stream is created, so it has to stop calling itself today's.
    # `now < start` keeps the lead-in on the right side of midnight for a
    # late-evening broadcast.
    if now <= start + GRACE and (start.date() == now.date() or now < start):
        return "Today's Service", 'Watch on YouTube'
    return 'Past Service', 'Watch on YouTube'


def subscriber_text(count):
    """YouTube's own rounding: 1.2K, 3.4M. The API returns an exact string but
    the channel page shows it rounded, and the card should match what people
    see when they get there."""
    try:
        n = int(count)
    except (TypeError, ValueError):
        return ''
    if n >= 1_000_000:
        return f'{n / 1_000_000:.1f}M subscribers'.replace('.0M', 'M')
    if n >= 1_000:
        return f'{n / 1_000:.1f}K subscribers'.replace('.0K', 'K')
    return f'{n} subscriber' + ('' if n == 1 else 's')


# --------------------------------------------------------------------------
# Network
# --------------------------------------------------------------------------

def download(url, timeout=30):
    req = urllib.request.Request(url, headers={'User-Agent': 'fccla-tech-info/1.0'})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read(), resp.headers.get('Content-Type', 'image/jpeg')


def data_uri(url):
    """An image URL as an inline base64 data URI, or '' if it can't be had.

    Inlined so the render never depends on Chrome reaching YouTube's CDN; a
    thumbnail that silently failed to load would be a white box on the wall."""
    if not url:
        return ''
    try:
        raw, mime = download(url)
    except Exception as e:
        print(f'  Could not download {url}: {e}')
        return ''
    mime = (mime or 'image/jpeg').split(';')[0].strip()
    return f'data:{mime};base64,' + base64.b64encode(raw).decode('ascii')


def youtube_service():
    if YOUTUBE_DIR not in sys.path:
        sys.path.insert(0, YOUTUBE_DIR)
    from update_youtube_stream import get_youtube_service
    return get_youtube_service()


def playlist_id():
    if YOUTUBE_DIR not in sys.path:
        sys.path.insert(0, YOUTUBE_DIR)
    from update_youtube_stream import PLAYLIST_ID
    return PLAYLIST_ID


def fetch_next_service(service, now):
    """The next broadcast on the worship playlist, with its thumbnail."""
    items = service.playlistItems().list(
        part='snippet', playlistId=playlist_id(), maxResults=50,
    ).execute().get('items', [])
    video_ids = [
        i['snippet']['resourceId']['videoId'] for i in items
        if (i.get('snippet') or {}).get('resourceId', {}).get('videoId')
    ]
    if not video_ids:
        return None
    videos = service.videos().list(
        part='snippet,liveStreamingDetails', id=','.join(video_ids),
    ).execute().get('items', [])
    return pick_next_broadcast(videos, now)


def fetch_channel(service):
    """Avatar, name and subscriber count for the fallback card."""
    items = service.channels().list(
        part='snippet,statistics', mine=True,
    ).execute().get('items', [])
    if not items:
        return None
    snippet = items[0].get('snippet') or {}
    stats = items[0].get('statistics') or {}
    handle = snippet.get('customUrl') or ''
    return {
        'id': items[0].get('id') or '',
        'title': snippet.get('title') or 'First Congregational Church',
        'handle': handle if handle.startswith('@') else (f'@{handle}' if handle else ''),
        'avatar': best_thumbnail(snippet.get('thumbnails')),
        'subscribers': subscriber_text(stats.get('subscriberCount')),
        'hidden_subs': stats.get('hiddenSubscriberCount', False),
    }


# --------------------------------------------------------------------------
# Chrome
# --------------------------------------------------------------------------

def find_chrome():
    for path in CHROME_CANDIDATES:
        if path and os.path.exists(path):
            return path
    raise RuntimeError('No Chrome binary found. Set CHROME_BIN, or install google-chrome.')


def render_png(html_path, png_path):
    cmd = [find_chrome(), '--headless', '--disable-gpu', '--no-sandbox',
           '--hide-scrollbars', '--force-device-scale-factor=1',
           '--window-size=1920,1080', '--virtual-time-budget=15000',
           f'--screenshot={png_path}', f'file://{html_path}']
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                   timeout=180, check=False)
    if not os.path.exists(png_path):
        raise RuntimeError('Chrome produced no screenshot')


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------

def build_html(service=None, channel=None, now=None):
    """The stream card when a broadcast is scheduled, else the channel card.

    Both carry a QR. Before a service begins, and on the channel card, it
    subscribes; once a service is under way the stream card's code goes to
    the broadcast itself, to match the "Watch on YouTube" line beside it."""
    now = now or datetime.datetime.now(TZ)
    channel = channel or {}

    def code(url, alt, cap):
        uri = build_qr_data_uri(url)
        return QR_TEMPLATE.format(uri=uri, alt=alt, cap=cap) if uri else ''

    subscribe = code(subscribe_url(channel),
                     'QR code to subscribe on YouTube', 'Scan to Subscribe')

    if service and service.get('thumb_uri'):
        # The stream card's right column carries a compact version of the
        # channel above the code -- the thumbnail says what the service is,
        # but nothing on it says whose channel to subscribe to.
        heading, when = service_labels(service.get('start'), now)
        # Same predicate as the heading, so the code and the line beside it
        # can never disagree: whenever the card says "Watch on YouTube" the
        # code goes to the broadcast, not a subscribe dialog.
        current = stream_is_current(service.get('start'), now)
        qr_block = (code(watch_url(service),
                         'QR code linking to the service broadcast', 'Scan to Watch')
                    if current and watch_url(service) else subscribe)
        side = SIDE_TEMPLATE.format(mini=mini_channel(channel), qr=qr_block)
        return HTML_SHELL.format(
            eyebrow='Join Us for Worship',
            heading=heading,
            when=when,
            body=STREAM_BODY.format(thumb=service['thumb_uri']),
            footer='Watch live at fccla.org/live',
            side=side if (qr_block or mini_channel(channel)) else '',
        )

    channel = channel or {}
    avatar = channel.get('avatar_uri') or ''
    avatar_html = (f'<img class="avatar" src="{avatar}" alt="">' if avatar
                   else '<div class="avatar avatar-blank"></div>')
    subs = '' if channel.get('hidden_subs') else (channel.get('subscribers') or '')
    return HTML_SHELL.format(
        eyebrow='Worship With Us',
        heading='Subscribe on YouTube',
        when='Next service to be announced',
        body=CHANNEL_BODY.format(
            avatar=avatar_html,
            name=channel.get('title') or 'First Congregational Church of Los Angeles',
            handle=channel.get('handle') or '',
            subs=subs,
        ),
        footer='Watch live at fccla.org/live',
        side=SIDE_TEMPLATE.format(mini='', qr=subscribe) if subscribe else '',
    )


MINI_CHANNEL = """<div class="mini">
        {avatar}
        <div class="mini-name">{name}</div>
        <div class="mini-handle">{handle}</div>
        <div class="mini-subs">{subs}</div>
      </div>"""

SIDE_TEMPLATE = """    <div class="side">
      {mini}{qr}
    </div>"""

QR_TEMPLATE = """<div class="qr">
      <img src="{uri}" alt="{alt}">
      <div class="qr-cap">{cap}</div>
    </div>"""

def mini_channel(channel):
    """A compact channel identity for the stream card's side column, or ''.

    The thumbnail already says what the service is; this says whose channel
    the QR subscribes you to, which nothing else on that card does."""
    channel = channel or {}
    name = (channel.get('title') or '').strip()
    if not name:
        return ''
    avatar = channel.get('avatar_uri') or ''
    avatar_html = (f'<img class="mini-avatar" src="{avatar}" alt="">' if avatar
                   else '<div class="mini-avatar avatar-blank"></div>')
    subs = '' if channel.get('hidden_subs') else (channel.get('subscribers') or '')
    return MINI_CHANNEL.format(
        avatar=avatar_html, name=name,
        handle=channel.get('handle') or '', subs=subs)


STREAM_BODY = """    <img class="thumb" src="{thumb}" alt="Upcoming service">"""

CHANNEL_BODY = """    <div class="channel">
      {avatar}
      <div class="cname">{name}</div>
      <div class="chandle">{handle}</div>
      <div class="subscribe">Subscribe</div>
      <div class="csubs">{subs}</div>
    </div>"""


HTML_SHELL = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Upcoming Service</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Cinzel:wght@500;700&family=Source+Sans+3:wght@300;400;600;700&display=swap" rel="stylesheet">
<style>
  :root{{
    --crimson:#99001A; --ivory:#F7F3EC; --ink:#1A1A1A;
    --muted:#6B5F58; --gold:#C8A04B; --yt:#FF0033;
  }}
  *{{box-sizing:border-box;margin:0;padding:0}}
  html,body{{width:1920px;height:1080px}}
  body{{background:var(--ivory);color:var(--ink);
    font-family:"Source Sans 3","Helvetica Neue",Arial,sans-serif;
    display:flex;flex-direction:column;overflow:hidden;}}

  header{{background:var(--crimson);color:#fff;padding:18px 60px 16px;
    display:flex;align-items:center;justify-content:space-between;
    border-bottom:6px solid var(--gold);flex:0 0 auto;}}
  .eyebrow{{font-size:17px;letter-spacing:.32em;text-transform:uppercase;
    font-weight:600;color:rgba(255,255,255,.72);margin-bottom:7px;}}
  h1{{font-family:Cinzel,Georgia,serif;font-weight:700;font-size:54px;
    line-height:1;letter-spacing:.015em;}}
  .when{{font-family:Cinzel,Georgia,serif;font-size:32px;font-weight:500;
    letter-spacing:.04em;text-align:right;}}

  main{{flex:1 1 auto;display:flex;align-items:center;justify-content:center;
    gap:44px;padding:20px 60px;min-height:0;}}

  /* the stream thumbnail is the advertisement -- let it dominate */
  /* height:100% rather than max-height: the source is 1280x720, and a max-
     only rule leaves it at natural size in the middle of a 1920 card.
     aspect-ratio + cover also crops the baked-in letterbox bars off the 4:3
     fallback thumbnails YouTube serves before a custom one has processed. */
  .thumb{{height:100%;max-width:100%;aspect-ratio:16/9;object-fit:cover;
    border-radius:6px;border:1px solid rgba(26,26,26,.15);
    box-shadow:0 6px 26px rgba(110,0,19,.18);}}

  /* fallback: the channel */
  /* Extra separation from the QR column, applied here rather than to main's
     gap so the stream card's thumbnail keeps its full width: the channel
     name is wide and low-contrast against the QR, and 44px left the two
     reading as one crowded block. */
  .channel{{display:flex;flex-direction:column;align-items:center;gap:18px;
    margin-right:96px}}
  .avatar{{width:340px;height:340px;border-radius:50%;object-fit:cover;
    border:5px solid #fff;box-shadow:0 6px 26px rgba(110,0,19,.18);}}
  .avatar-blank{{background:#E4DCD1}}
  .cname{{font-family:Cinzel,Georgia,serif;font-size:52px;font-weight:700;
    color:var(--ink);text-align:center;margin-top:8px;}}
  .chandle{{font-size:32px;color:var(--muted);letter-spacing:.04em}}
  .csubs{{font-size:27px;color:var(--muted);letter-spacing:.03em}}
  .subscribe{{background:var(--yt);color:#fff;font-size:35px;font-weight:700;
    letter-spacing:.06em;padding:18px 64px;border-radius:999px;margin-top:14px;
    box-shadow:0 4px 14px rgba(255,0,51,.28);}}

  .side{{flex:0 0 auto;display:flex;flex-direction:column;align-items:center;
    gap:52px;max-width:250px}}
  .mini{{display:flex;flex-direction:column;align-items:center;gap:7px}}
  .mini-avatar{{width:124px;height:124px;border-radius:50%;object-fit:cover;
    border:4px solid #fff;box-shadow:0 3px 14px rgba(110,0,19,.18)}}
  .mini-name{{font-family:Cinzel,Georgia,serif;font-size:21px;font-weight:700;
    color:var(--ink);text-align:center;line-height:1.24;margin-top:5px}}
  .mini-handle{{font-size:18px;color:var(--muted);letter-spacing:.03em}}
  .mini-subs{{font-size:16px;color:#9A8F88;letter-spacing:.02em}}

  /* The subscribe URL is a 33-module symbol -- half again as dense as the
     events card's -- so it needs real size to stay scannable. 204px puts it
     near 5px a module. */
  .qr{{flex:0 0 auto;display:flex;flex-direction:column;align-items:center;gap:12px}}
  .qr img{{display:block;width:204px;height:204px;background:#fff;
    border-radius:6px;box-shadow:0 3px 14px rgba(26,26,26,.18)}}
  .qr-cap{{font-family:Cinzel,Georgia,serif;font-size:25px;font-weight:700;
    color:var(--crimson);letter-spacing:.05em;white-space:nowrap;text-align:center}}

  footer{{flex:0 0 auto;padding:12px 60px 18px;display:flex;
    align-items:center;justify-content:space-between;
    font-size:17px;color:var(--muted);letter-spacing:.04em;}}
  footer .cta{{font-family:Cinzel,Georgia,serif;font-size:24px;font-weight:700;
    color:var(--crimson);letter-spacing:.05em;}}
  footer .note{{font-size:16px;color:#9A8F88}}
</style>
</head>
<body>

<header>
  <div>
    <div class="eyebrow">{eyebrow}</div>
    <h1>{heading}</h1>
  </div>
  <div class="when">{when}</div>
</header>

<main>
{body}
{side}
</main>

<footer>
  <div class="cta">{footer}</div>
  <div class="note">540 S Commonwealth Ave &middot; Los Angeles</div>
</footer>

</body>
</html>
"""


# --------------------------------------------------------------------------

def upload_to_drive(png_path, dry_run):
    """Refresh every copy of this card in Drive.

    Straight to Drive rather than through the git-backed upload queue: this
    regenerates hourly, and that pipeline publishes by committing the PNG.
    Copies are found by fragment so renumber_rotation.py's numbering
    survives."""
    if UPLOADER_DIR not in sys.path:
        sys.path.insert(0, UPLOADER_DIR)
    if ROTATION_DIR not in sys.path:
        sys.path.insert(0, ROTATION_DIR)
    from upload_queue_to_drive import get_drive_service
    from drive_cards import publish_card

    drive = get_drive_service()
    if not drive:
        # A dry run is meant to be runnable on a laptop with no secrets; a
        # real run without them must fail loudly rather than exit green
        # having published nothing.
        if dry_run:
            print(f'  DRY RUN: would refresh every copy of {CARD_FRAGMENT} '
                  f'(no Drive credentials here).')
            return True
        raise RuntimeError('No Drive credentials: set GDRIVE_OAUTH_JSON or '
                           'GDRIVE_SERVICE_ACCOUNT_JSON.')
    publish_card(drive, png_path, CARD_FRAGMENT, dry_run=dry_run)
    return True


def main():
    dry_run = os.environ.get('DRY_RUN') == '1'
    now = datetime.datetime.now(TZ)

    yt = youtube_service()
    if not yt:
        raise RuntimeError('No YouTube credentials: set YOUTUBE_CREDENTIALS_JSON.')

    # Fetched unconditionally: the subscribe QR is on both cards, so the
    # channel is needed even when a broadcast is scheduled. One extra quota
    # unit a run.
    channel = fetch_channel(yt) or {}
    sub_url = subscribe_url(channel)
    print(f"  Subscribe QR -> {sub_url or 'unavailable (no channel)'}")

    print('Looking for the next scheduled service...')
    upcoming = fetch_next_service(yt, now)

    if upcoming:
        print(f"  {upcoming['title']!r} at {upcoming['start']:%a %b %-d %-I:%M %p}")
        upcoming['thumb_uri'] = data_uri(upcoming.get('thumbnail'))
        if not upcoming['thumb_uri']:
            # Falling through to the channel card is better than publishing a
            # card with a hole where the advertisement should be.
            print('  Thumbnail unavailable; using the channel card instead.')
            upcoming = None
    # Needed on both cards now: the stream card carries a compact channel
    # preview beside the subscribe code.
    channel['avatar_uri'] = data_uri(channel.get('avatar'))
    if not upcoming:
        print('  Nothing scheduled; building the channel card.')

    with open(HTML_PATH, 'w', encoding='utf-8') as f:
        f.write(build_html(upcoming, channel, now))
    render_png(HTML_PATH, PNG_PATH)
    print(f'  Wrote {os.path.basename(PNG_PATH)} ({os.path.getsize(PNG_PATH)} bytes)')

    upload_to_drive(PNG_PATH, dry_run)
    print('Done.')


if __name__ == '__main__':
    main()
