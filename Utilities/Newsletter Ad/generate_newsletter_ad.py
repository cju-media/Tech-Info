"""
Renders the Meetinghouse Newsletter sign-up card for the campus screens.

The only static card of the four: there's nothing to fetch and nothing that
goes out of date, so it renders the same image every run. It still runs on a
schedule rather than once by hand, so that a copy deleted from Drive comes
back on its own -- and so a change to the wording or the sign-up URL reaches
the screens the same way every other card's changes do.

publish_card() skips a copy whose bytes already match, so a run that changes
nothing costs one Drive listing and no writes at all.

Env:
    GDRIVE_OAUTH_JSON / GDRIVE_SERVICE_ACCOUNT_JSON   required to upload
    DRY_RUN   "1" renders but uploads nothing
"""

import os
import sys
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
HTML_PATH = os.path.join(HERE, 'newsletter-ad.html')
PNG_PATH = os.path.join(HERE, 'newsletter-ad.png')

REPO_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
UPLOADER_DIR = os.path.join(REPO_ROOT, 'Worship Scripts', 'worship workflows')
ROTATION_DIR = os.path.join(REPO_ROOT, 'Utilities', 'Rotation')

SIGNUP_URL = 'https://www.fccla.org/meetinghouse-newsletter'
# Matches drive_cards.CARDS and cleanup_events_folder.py's protected list;
# see those for why copies are found by fragment rather than exact name.
CARD_FRAGMENT = 'meetinghouse-newsletter'

CHROME_CANDIDATES = [
    os.environ.get('CHROME_BIN') or '',
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    '/usr/bin/google-chrome',
    '/usr/bin/google-chrome-stable',
    '/usr/bin/chromium-browser',
    '/usr/bin/chromium',
]


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


def build_html():
    qr_uri = build_qr_data_uri(SIGNUP_URL)
    qr = (f'<img class="qr" src="{qr_uri}" alt="QR code to subscribe to the '
          f'Meetinghouse Newsletter">') if qr_uri else ''
    return HTML_SHELL.format(qr=qr)


HTML_SHELL = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Meetinghouse Newsletter</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Cinzel:wght@500;700&family=Source+Sans+3:wght@300;400;600;700&display=swap" rel="stylesheet">
<style>
  :root{{
    --crimson:#99001A; --ivory:#F7F3EC; --ink:#1A1A1A;
    --muted:#6B5F58; --gold:#C8A04B;
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

  main{{flex:1 1 auto;display:flex;align-items:center;justify-content:center;
    padding:30px 70px 50px;}}
  .cta{{display:flex;flex-direction:column;align-items:center;text-align:center}}
  .lead{{font-family:Cinzel,Georgia,serif;font-size:58px;font-weight:700;
    color:var(--ink);letter-spacing:.01em;line-height:1.15;}}
  .qr{{width:300px;height:300px;background:#fff;border-radius:8px;
    margin-top:46px;box-shadow:0 4px 18px rgba(26,26,26,.18);}}
  .url{{font-family:Cinzel,Georgia,serif;font-size:32px;font-weight:700;
    color:var(--crimson);letter-spacing:.04em;margin-top:22px}}

  footer{{flex:0 0 auto;padding:22px 70px 26px;display:flex;
    align-items:center;justify-content:space-between;
    font-size:19px;color:var(--muted);letter-spacing:.04em;}}
  footer .tag{{font-family:Cinzel,Georgia,serif;font-size:25px;font-weight:700;
    color:var(--crimson);letter-spacing:.05em;}}
  footer .note{{font-size:17px;color:#9A8F88}}
</style>
</head>
<body>

<header>
  <div>
    <div class="eyebrow">First Congregational Church of Los Angeles</div>
    <h1>Meetinghouse Newsletter</h1>
  </div>
  <div class="header-right">
    <div class="site">fccla.org</div>
    <div class="addr">540 S Commonwealth Ave &middot; Los Angeles</div>
  </div>
</header>

<main>
  <div class="cta">
    <div class="lead">Subscribe for Weekly News &amp; Events</div>
    {qr}
    <div class="url">fccla.org/meetinghouse-newsletter</div>
  </div>
</main>

<footer>
  <div class="tag">Delivered to your inbox every week</div>
  <div class="note">Weekly Sunday Worship, 10:30 AM in the Sanctuary</div>
</footer>

</body>
</html>
"""


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


def main():
    dry_run = os.environ.get('DRY_RUN') == '1'
    print('Rendering the newsletter card...')
    with open(HTML_PATH, 'w', encoding='utf-8') as f:
        f.write(build_html())
    render_png(HTML_PATH, PNG_PATH)
    print(f'  Wrote {os.path.basename(PNG_PATH)} ({os.path.getsize(PNG_PATH)} bytes)')
    publish(PNG_PATH, dry_run)
    print('Done.')


if __name__ == '__main__':
    main()
