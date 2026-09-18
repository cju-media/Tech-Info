"""
Renders the Los Angeles weather card for the campus screens: today's
conditions large, then the next ten days small underneath.

Data: Open-Meteo (open-meteo.com). Chosen over the NWS API because it
returns more than a week of daily forecast in one call and needs no API
key -- nothing new to add to the repo's secrets. Coordinates are the
church itself (540 S Commonwealth Ave), not "Los Angeles" generally, since
downtown and the westside can differ by ten degrees on a June morning.

Timezone is requested as America/Los_Angeles and the API echoes it back,
so every timestamp here is already church-local. Nothing in this file
converts one timezone to another -- see generate_events_ad.py for what
happens when you let a UTC runner decide what "today" means.

Weather icons are hand-drawn inline SVG rather than an icon font or emoji:
the same reason the events card inlines its QR. A CI runner has no emoji
font worth the name, and a webfont that fails to load leaves a row of
blank boxes on every screen on campus with nothing to notice it.

No Gemini here. The forecast is already structured; there is no judgement
call to make.

Env:
    DRY_RUN   "1" renders but writes nothing to the upload queue
"""

import os
import io
import sys
import json
import datetime
import zoneinfo
import subprocess
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
HTML_PATH = os.path.join(HERE, 'weather-at-a-glance.html')
PNG_PATH = os.path.join(HERE, 'weather-at-a-glance.png')

REPO_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
QUEUE_DIR = os.path.join(REPO_ROOT, 'Utilities', 'uploads_queue')
# The Events Ad drop zone -- the same flat folder the event flyers and the
# events at-a-glance card live in. upload_queue_to_drive.py parses this out
# of the queued filename.
EVENTS_FOLDER_ID = '17-0kiqBKa0k5ofW6gOPrVbHl7nqanuQz'
# Stable on purpose. cleanup_events_folder.py's PROTECTED_NAME_PREFIXES has
# to match this stem or the card gets auto-trashed: it prints ten dates, so
# the vision read would trash it the day after whichever one it picked.
# Matching that list also makes upload_queue_to_drive.py replace the card in
# place rather than stacking a new copy every few hours.
CARD_FILENAME = 'FCCLA-Upcoming-Forecast.png'

TZ = zoneinfo.ZoneInfo('America/Los_Angeles')
LAT, LON = 34.0614, -118.2839          # 540 S Commonwealth Ave
FORECAST_DAYS = 11                     # today + the ten shown underneath
API = (
    'https://api.open-meteo.com/v1/forecast'
    f'?latitude={LAT}&longitude={LON}'
    '&current=temperature_2m,apparent_temperature,relative_humidity_2m,'
    'weather_code,wind_speed_10m'
    '&daily=weather_code,temperature_2m_max,temperature_2m_min,'
    'precipitation_probability_max,uv_index_max,sunrise,sunset'
    '&temperature_unit=fahrenheit&wind_speed_unit=mph'
    '&timezone=America%2FLos_Angeles'
    f'&forecast_days={FORECAST_DAYS}'
)

CHROME_CANDIDATES = [
    os.environ.get('CHROME_BIN') or '',
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    '/usr/bin/google-chrome',
    '/usr/bin/google-chrome-stable',
    '/usr/bin/chromium-browser',
    '/usr/bin/chromium',
]

# WMO code -> (label for the card, icon key). Open-Meteo documents the full
# table; codes not listed here fall back to a plain cloud rather than
# rendering an empty tile.
WMO = {
    0:  ('Clear', 'sun'),
    1:  ('Mainly Clear', 'sun-cloud'),
    2:  ('Partly Cloudy', 'sun-cloud'),
    3:  ('Overcast', 'cloud'),
    45: ('Fog', 'fog'),
    48: ('Freezing Fog', 'fog'),
    51: ('Light Drizzle', 'drizzle'),
    53: ('Drizzle', 'drizzle'),
    55: ('Heavy Drizzle', 'drizzle'),
    56: ('Freezing Drizzle', 'drizzle'),
    57: ('Freezing Drizzle', 'drizzle'),
    61: ('Light Rain', 'rain'),
    63: ('Rain', 'rain'),
    65: ('Heavy Rain', 'rain'),
    66: ('Freezing Rain', 'rain'),
    67: ('Freezing Rain', 'rain'),
    71: ('Light Snow', 'snow'),
    73: ('Snow', 'snow'),
    75: ('Heavy Snow', 'snow'),
    77: ('Snow Grains', 'snow'),
    80: ('Light Showers', 'rain'),
    81: ('Showers', 'rain'),
    82: ('Heavy Showers', 'rain'),
    85: ('Snow Showers', 'snow'),
    86: ('Snow Showers', 'snow'),
    95: ('Thunderstorm', 'storm'),
    96: ('Thunderstorm', 'storm'),
    99: ('Thunderstorm', 'storm'),
}


def describe(code):
    """(label, icon key) for a WMO code, never raising on an unknown one."""
    return WMO.get(code, ('Cloudy', 'cloud'))


# --------------------------------------------------------------------------
# Icons -- drawn on a 64x64 grid, coloured by CSS via currentColor/classes.
# --------------------------------------------------------------------------

_CLOUD = ('<path d="M20 44h25a10 10 0 0 0 1-20 14 14 0 0 0-26-4 9 9 0 0 0 0 24Z" '
          'fill="var(--cloud)"/>')

ICONS = {
    'sun': (
        '<circle cx="32" cy="32" r="13" fill="var(--sun)"/>'
        '<g stroke="var(--sun)" stroke-width="4" stroke-linecap="round">'
        '<path d="M32 6v7M32 51v7M6 32h7M51 32h7"/>'
        '<path d="M13.6 13.6l5 5M45.4 45.4l5 5M50.4 13.6l-5 5M18.6 45.4l-5 5"/>'
        '</g>'
    ),
    'sun-cloud': (
        '<circle cx="24" cy="23" r="10" fill="var(--sun)"/>'
        '<g stroke="var(--sun)" stroke-width="3.5" stroke-linecap="round">'
        '<path d="M24 3v6M24 37v3M4 23h6M38 23h6"/>'
        '<path d="M9.8 8.8l4 4M38.2 37.2l4 4M38.2 8.8l-4 4"/>'
        '</g>' + _CLOUD
    ),
    'cloud': _CLOUD,
    'fog': (
        _CLOUD +
        '<g stroke="var(--cloud)" stroke-width="4" stroke-linecap="round" opacity=".75">'
        '<path d="M14 52h36M20 60h24"/></g>'
    ),
    'drizzle': (
        _CLOUD +
        '<g stroke="var(--rain)" stroke-width="4" stroke-linecap="round">'
        '<path d="M24 51v4M36 51v4"/></g>'
    ),
    'rain': (
        _CLOUD +
        '<g stroke="var(--rain)" stroke-width="4" stroke-linecap="round">'
        '<path d="M22 50l-3 9M32 50l-3 9M42 50l-3 9"/></g>'
    ),
    'snow': (
        _CLOUD +
        '<g stroke="var(--rain)" stroke-width="3.5" stroke-linecap="round">'
        '<path d="M21 52v8M17 54l8 4M25 54l-8 4"/>'
        '<path d="M41 52v8M37 54l8 4M45 54l-8 4"/></g>'
    ),
    'storm': (
        _CLOUD +
        '<path d="M34 48l-11 9h8l-3 8 12-10h-8l2-7Z" fill="var(--bolt)"/>'
    ),
}


def icon_svg(key, size):
    body = ICONS.get(key, ICONS['cloud'])
    return (f'<svg class="ico" viewBox="0 0 64 64" width="{size}" height="{size}" '
            f'aria-hidden="true">{body}</svg>')


# --------------------------------------------------------------------------
# Data
# --------------------------------------------------------------------------

def fetch_forecast(url=API, timeout=30):
    req = urllib.request.Request(url, headers={'User-Agent': 'fccla-tech-info/1.0'})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode('utf-8'))


def parse_local(stamp):
    """Open-Meteo returns local wall time with no offset, because we asked it
    for America/Los_Angeles. Attach that zone rather than inventing one."""
    try:
        return datetime.datetime.fromisoformat(stamp).replace(tzinfo=TZ)
    except (ValueError, TypeError):
        return None


def round_temp(value):
    """Whole degrees. A forecast high of 80.3 is not accurate to a tenth and
    printing it that way just makes the card look unsure of itself."""
    return None if value is None else int(round(float(value)))


def build_model(data):
    """The card's contents: a `today` dict plus ten `days`."""
    cur = data.get('current') or {}
    daily = data.get('daily') or {}
    times = daily.get('time') or []
    if not times:
        raise RuntimeError('forecast contained no daily entries')

    code = cur.get('weather_code', daily['weather_code'][0])
    label, icon = describe(code)
    today = {
        'temp': round_temp(cur.get('temperature_2m')),
        'feels': round_temp(cur.get('apparent_temperature')),
        'humidity': cur.get('relative_humidity_2m'),
        'wind': round_temp(cur.get('wind_speed_10m')),
        'label': label,
        'icon': icon,
        'high': round_temp(daily['temperature_2m_max'][0]),
        'low': round_temp(daily['temperature_2m_min'][0]),
        'pop': daily.get('precipitation_probability_max', [None])[0],
        'uv': round_temp((daily.get('uv_index_max') or [None])[0]),
        'sunrise': parse_local((daily.get('sunrise') or [None])[0]),
        'sunset': parse_local((daily.get('sunset') or [None])[0]),
        'date': parse_local(times[0]),
    }

    days = []
    for i in range(1, len(times)):
        d = parse_local(times[i])
        if d is None:
            continue
        lbl, ico = describe(daily['weather_code'][i])
        days.append({
            'date': d,
            'label': lbl,
            'icon': ico,
            'high': round_temp(daily['temperature_2m_max'][i]),
            'low': round_temp(daily['temperature_2m_min'][i]),
            'pop': daily['precipitation_probability_max'][i],
        })
    return today, days[:10]


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
    # Chrome is noisy on stderr even on a clean headless run; the check that
    # matters is whether a screenshot actually appeared.
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                   timeout=180, check=False)
    if not os.path.exists(png_path):
        raise RuntimeError('Chrome produced no screenshot')


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------

def pop_text(pop):
    """Precipitation chance, shown only when it's worth reading. In LA a 0%
    row ten days running is just noise."""
    return f'{pop}%' if pop not in (None, 0) else ''


def build_html(today, days, now):
    tiles = []
    for d in days:
        pop = pop_text(d['pop'])
        tiles.append(
            '    <div class="day">\n'
            f'      <div class="dow">{d["date"].strftime("%a").upper()}</div>\n'
            f'      <div class="dnum">{d["date"].strftime("%-m/%-d")}</div>\n'
            f'      <div class="dico">{icon_svg(d["icon"], 78)}</div>\n'
            f'      <div class="dlabel">{d["label"]}</div>\n'
            f'      <div class="dtemp"><span class="hi">{d["high"]}&deg;</span>'
            f'<span class="lo">{d["low"]}&deg;</span></div>\n'
            f'      <div class="dpop">{pop}</div>\n'
            '    </div>'
        )

    sun = ''
    if today['sunrise'] and today['sunset']:
        sun = (f'{today["sunrise"].strftime("%-I:%M %p")}'
               f' &middot; {today["sunset"].strftime("%-I:%M %p")}')

    return HTML_SHELL.format(
        today_date=today['date'].strftime('%A, %B %-d'),
        temp=today['temp'],
        label=today['label'],
        feels=today['feels'],
        high=today['high'],
        low=today['low'],
        humidity=today['humidity'],
        wind=today['wind'],
        pop=today['pop'] if today['pop'] is not None else 0,
        uv=today['uv'] if today['uv'] is not None else '—',
        sun=sun,
        hero_icon=icon_svg(today['icon'], 250),
        days='\n'.join(tiles),
        updated=now.strftime('%-I:%M %p'),
    )


HTML_SHELL = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Los Angeles Weather</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Cinzel:wght@500;700&family=Source+Sans+3:wght@200;300;400;600;700&display=swap" rel="stylesheet">
<style>
  :root{{
    --crimson:#99001A; --crimson-deep:#6E0013; --ivory:#F7F3EC;
    --ink:#1A1A1A; --muted:#6B5F58; --gold:#C8A04B; --rule:rgba(26,26,26,.12);
    --sun:#E8A317; --cloud:#9AA7B4; --rain:#4A7FB5; --bolt:#E8A317;
  }}
  *{{box-sizing:border-box;margin:0;padding:0}}
  html,body{{width:1920px;height:1080px}}
  body{{background:var(--ivory);color:var(--ink);
    font-family:"Source Sans 3","Helvetica Neue",Arial,sans-serif;
    display:flex;flex-direction:column;overflow:hidden;}}

  header{{background:var(--crimson);color:#fff;padding:20px 60px 18px;
    display:flex;align-items:center;justify-content:space-between;
    border-bottom:6px solid var(--gold);flex:0 0 auto;}}
  .eyebrow{{font-size:17px;letter-spacing:.32em;text-transform:uppercase;
    font-weight:600;color:rgba(255,255,255,.72);margin-bottom:8px;}}
  h1{{font-family:Cinzel,Georgia,serif;font-weight:700;font-size:58px;
    line-height:1;letter-spacing:.015em;}}
  .header-right{{text-align:right;line-height:1.5}}
  .header-right .hdate{{font-family:Cinzel,Georgia,serif;font-size:27px;
    font-weight:500;letter-spacing:.05em;}}
  .header-right .addr{{font-size:17px;color:rgba(255,255,255,.75);letter-spacing:.05em;}}

  main{{flex:1 1 auto;padding:26px 60px 0;display:flex;flex-direction:column;gap:22px;}}

  /* ---- today ---- */
  .hero{{background:#fff;border:1px solid var(--rule);border-top:5px solid var(--crimson);
    border-radius:5px;box-shadow:0 2px 12px rgba(110,0,19,.07);
    flex:0 0 336px;display:flex;align-items:center;gap:36px;padding:22px 46px;}}
  .hero .ico{{flex:0 0 auto}}
  .now{{flex:0 0 auto;display:flex;flex-direction:column;justify-content:center}}
  .now .temp{{font-size:168px;font-weight:200;line-height:.86;
    letter-spacing:-.04em;color:var(--ink);}}
  .now .cond{{font-family:Cinzel,Georgia,serif;font-size:37px;font-weight:700;
    color:var(--crimson);margin-top:10px;}}
  .now .feels{{font-size:23px;color:var(--muted);margin-top:5px}}
  .hilo{{margin-left:46px;text-align:left;flex:0 0 auto}}
  .hilo .pair{{font-size:58px;font-weight:300;letter-spacing:-.02em}}
  .hilo .pair .h{{color:var(--crimson-deep);font-weight:600}}
  .hilo .pair .l{{color:var(--muted);margin-left:20px}}
  .hilo .cap{{font-size:17px;letter-spacing:.18em;text-transform:uppercase;
    color:var(--muted);margin-top:4px}}
  .stats{{margin-left:auto;flex:0 0 560px;display:grid;grid-template-columns:1fr 1fr;
    gap:22px 40px;border-left:1px solid var(--rule);padding-left:52px}}
  .stat .k{{font-size:14px;letter-spacing:.16em;text-transform:uppercase;
    color:var(--muted);font-weight:600}}
  .stat .v{{font-size:31px;font-weight:300;color:var(--ink);line-height:1.15}}
  .stat.wide{{grid-column:1 / -1}}
  .stat.wide .v{{font-size:24px}}

  /* ---- ten days ---- */
  .tenday{{flex:1 1 auto;display:grid;grid-template-columns:repeat(10,1fr);
    gap:14px;min-height:0;}}
  .day{{background:#fff;border:1px solid var(--rule);border-top:4px solid var(--gold);
    border-radius:5px;box-shadow:0 2px 8px rgba(110,0,19,.05);
    display:flex;flex-direction:column;align-items:center;
    justify-content:space-evenly;padding:22px 8px;min-height:0;}}
  .dow{{font-size:24px;font-weight:700;letter-spacing:.14em;color:var(--crimson)}}
  .dnum{{font-size:17px;color:var(--muted);letter-spacing:.04em}}
  .dico{{margin:5px 0 2px;line-height:0}}
  .dlabel{{font-size:16px;color:var(--muted);text-align:center;line-height:1.25;
    min-height:2.5em;display:flex;align-items:center;padding:0 2px}}
  .dtemp{{font-size:33px;font-weight:300;white-space:nowrap}}
  .dtemp .hi{{color:var(--crimson-deep);font-weight:600}}
  .dtemp .lo{{color:var(--muted);margin-left:11px}}
  .dpop{{font-size:16px;font-weight:600;color:var(--rain);min-height:1.2em}}

  footer{{flex:0 0 auto;padding:16px 60px 20px;display:flex;
    align-items:center;justify-content:space-between;
    font-size:17px;color:var(--muted);letter-spacing:.04em;}}
  footer .cta{{font-family:Cinzel,Georgia,serif;font-size:22px;font-weight:700;
    color:var(--crimson);letter-spacing:.05em;}}
  footer .note{{font-size:15px;color:#9A8F88}}
</style>
</head>
<body>

<header>
  <div>
    <div class="eyebrow">First Congregational Church of Los Angeles</div>
    <h1>Upcoming Forecast</h1>
  </div>
  <div class="header-right">
    <div class="hdate">{today_date}</div>
    <div class="addr">540 S Commonwealth Ave &middot; Los Angeles</div>
  </div>
</header>

<main>

  <section class="hero">
    {hero_icon}
    <div class="now">
      <div class="temp">{temp}&deg;</div>
      <div class="cond">{label}</div>
      <div class="feels">Feels like {feels}&deg;</div>
    </div>
    <div class="hilo">
      <div class="pair"><span class="h">{high}&deg;</span><span class="l">{low}&deg;</span></div>
      <div class="cap">Today's High / Low</div>
    </div>
    <div class="stats">
      <div class="stat"><div class="k">Humidity</div><div class="v">{humidity}%</div></div>
      <div class="stat"><div class="k">Wind</div><div class="v">{wind} mph</div></div>
      <div class="stat"><div class="k">Rain Chance</div><div class="v">{pop}%</div></div>
      <div class="stat"><div class="k">UV Index</div><div class="v">{uv}</div></div>
      <div class="stat wide"><div class="k">Sunrise &middot; Sunset</div><div class="v">{sun}</div></div>
    </div>
  </section>

  <section class="tenday">
{days}
  </section>

</main>

<footer>
  <div class="cta">Next 10 Days</div>
  <div class="note">Forecast from Open-Meteo &middot; Updated {updated}</div>
</footer>

</body>
</html>
"""


def queue_card(png_path, dry_run):
    """Hand the card to process_uploads.yml, which pushes it to Drive."""
    os.makedirs(QUEUE_DIR, exist_ok=True)
    # This regenerates every few hours; if a run lands before the queue is
    # drained, only the newest forecast should reach the screens.
    for stale in os.listdir(QUEUE_DIR):
        if stale.endswith(f'---{CARD_FILENAME}'):
            print(f'  Replacing a forecast still queued from an earlier run: {stale}')
            if not dry_run:
                os.remove(os.path.join(QUEUE_DIR, stale))

    ts = int(datetime.datetime.now(datetime.timezone.utc).timestamp() * 1000)
    dest = os.path.join(QUEUE_DIR, f'{ts}---{EVENTS_FOLDER_ID}---{CARD_FILENAME}')
    if dry_run:
        print(f'  DRY RUN: would queue {os.path.basename(dest)}')
        return None
    with open(png_path, 'rb') as src, open(dest, 'wb') as out:
        out.write(src.read())
    print(f'  Queued {os.path.basename(dest)}')
    return dest


def main():
    dry_run = os.environ.get('DRY_RUN') == '1'
    now = datetime.datetime.now(TZ)

    print('Fetching the Los Angeles forecast...')
    today, days = build_model(fetch_forecast())
    print(f"Now {today['temp']}° {today['label']}, "
          f"high {today['high']}° / low {today['low']}°; "
          f"{len(days)} more day(s) on the card.")

    with open(HTML_PATH, 'w', encoding='utf-8') as f:
        f.write(build_html(today, days, now))
    render_png(HTML_PATH, PNG_PATH)
    print(f'  Wrote {os.path.basename(PNG_PATH)} ({os.path.getsize(PNG_PATH)} bytes)')

    queue_card(PNG_PATH, dry_run)
    print('Done.')


if __name__ == '__main__':
    main()
