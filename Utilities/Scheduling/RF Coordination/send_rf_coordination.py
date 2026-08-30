import sys
import os
import json
import time
import smtplib
from email.message import EmailMessage
from datetime import datetime, timedelta
import re
import urllib.request
import urllib.parse
import math

# ---------------------------------------------------------------------------
# Location of the church production space (540 S Commonwealth Ave, LA 90020).
# RF / wireless-mic interference is a concern for anything happening within
# roughly a mile of here, especially Lafayette Park (one block away).
# ---------------------------------------------------------------------------
LAT_540 = 34.0645671
LON_540 = -118.2855647

NEAR_RADIUS_MI = 1.0          # "close enough to coordinate frequencies"
WATCH_RADIUS_MI = 1.6        # "worth listing so a human can judge"
UPCOMING_HORIZON_DAYS = 60   # how far ahead to list nearby public events
RECUR_HORIZON_DAYS = 80      # how far ahead to project annually-recurring events
RECUR_LOOKBACK_YEARS = 8     # how many prior years to mine for recurring events
RECUR_GRACE_DAYS = 12        # also surface events that (per past years) may have
                            # just started -- their permit still isn't in the feed
MAX_LIVE_GEOCODES = 80       # per-run cap on Census geocoder calls

LA_PERMITS_URL = "https://data.lacity.org/resource/8spw-3fhx.json?$limit=50000"

# Lafayette Park (the RAP park one block from the church). LA Rec & Parks does
# not publish a machine-readable calendar for it -- their site's only event
# calendar link is disabled -- so "the Lafayette Park calendar" is assembled here
# from three places:
#   1. permits pulled for the park's own address out of the LADBS feed above,
#   2. the RAP department news/events page (citywide, hand-curated, tiny),
#   3. the MacArthur Park / Levitt VIBE free-concert series (~0.6 mi, big PA).
LAFAYETTE_PARK_LAT = 34.06184
LAFAYETTE_PARK_LON = -118.28322
RAP_EVENTS_URL = "https://recreation.parks.lacity.gov/news-events-and-information"
MACLA_CALENDAR_URL = "https://mac-la.org/calendar/"

BROWSER_HEADERS = {
    'User-Agent': ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                   '(KHTML, like Gecko) Chrome/124.0 Safari/537.36'),
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
}

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
GEOCODE_CACHE_PATH = os.path.join(SCRIPT_DIR, "geocode_cache.json")


def http_get(url, timeout=30):
    """GET a URL with browser-ish headers (some city sites 403 a bare UA)."""
    try:
        req = urllib.request.Request(url, headers=BROWSER_HEADERS)
        return urllib.request.urlopen(req, timeout=timeout).read().decode('utf-8', 'replace')
    except Exception as e:
        print(f"Fetch failed for {url}: {e}")
        return ""

# Distinctive place names within ~1 mi of the church. Generic ordinals like
# "7th" are deliberately excluded here because they match half the city; they
# only help once combined with coordinates.
NEAR_KEYWORDS = [
    'lafayette park', 'la fayette park', 'lafayette pk', 'laffayette',
    's commonwealth', 'south commonwealth', 'commonwealth ave', 'commonwealth blvd',
    'commonwealth blv', '540 commonwealth', 'macarthur park', 'mac arthur park',
    'westlake', 'rampart', 'bonnie brae', 'carondelet', 'coronado st',
    'bimini', 'park view st', 'parkview st', 'hoover st and 6th', 'alvarado st at wilshire',
    "2100 w 7th", "2100 wilshire",
]
# ZIP codes the church straddles / immediately borders.
NEAR_ZIPS = re.compile(r'\b(90020|90057)\b')

# Multilingual "public gathering that could carry RF / amplified audio" terms.
GATHERING_TERMS = [
    'festiv', 'feria', 'fiesta', 'fest ', 'carnaval', 'carnival', ' fair', 'street fair',
    'block party', 'block-party', 'parade', 'procession', 'desfile', ' rally', 'marcha',
    'farmers market', 'night market', 'street market', 'concert', 'concierto', 'live music',
    'loud music', 'amplified', 'sound stage', ' stage ', 'dj ', 'band ', 'mariachi',
    'mechanical rides', ' rides', 'juegos mecanicos', 'fireworks', 'pyro', 'celebration',
    'celebracion', 'anniversary', 'aniversario', 'independen', 'independencia', ' pride ',
    'fun run', ' 5k', ' 10k', 'marathon', 'maraton', 'cinco de mayo', 'fiestas patrias',
    'dia de', 'día de', 'quincea', 'street closure', 'road closure', 'road clos',
]

# ---------------------------------------------------------------------------
# Geometry / parsing helpers
# ---------------------------------------------------------------------------

def haversine(lat1, lon1, lat2, lon2):
    R = 3959.87433
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2) * math.sin(dlat/2) + \
        math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * \
        math.sin(dlon/2) * math.sin(dlon/2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    return R * c


def parse_dt(value):
    """Parse the assorted date shapes the LA permit feed and the sheet use."""
    if not value:
        return None
    s = str(value).strip()
    if not s or s.lower() == 'none':
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d",
                "%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    m = re.search(r'(\d{4})-(\d{1,2})-(\d{1,2})', s)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass
    m = re.search(r'(\d{1,2})/(\d{1,2})/(\d{2,4})', s)
    if m:
        yr = int(m.group(3))
        if yr < 100:
            yr += 2000
        try:
            return datetime(yr, int(m.group(1)), int(m.group(2)))
        except ValueError:
            pass
    return None


def parse_time(time_str):
    if not time_str or time_str == 'None':
        return None, None

    matches = list(re.finditer(r'(\d{1,2})(?::(\d{2}))?\s*(am|pm|a|p)?', time_str, re.IGNORECASE))

    valid_matches = []
    for m in matches:
        h, mn, p = m.groups()
        if h:
            try:
                val = int(h)
                if val <= 12 or (val <= 23 and val != 30):
                    valid_matches.append(m)
            except ValueError:
                pass

    if not valid_matches:
        return None, None

    start_match = valid_matches[0]
    end_match = valid_matches[-1]

    h1, m1, p1 = start_match.groups()
    start_hour = int(h1) if h1 else 0
    start_min = int(m1) if m1 else 0

    h2, m2, p2 = end_match.groups()
    end_hour = int(h2) if h2 else start_hour
    end_min = int(m2) if m2 else 0

    if p1 and p1.lower().startswith('p') and start_hour != 12:
        start_hour += 12
    elif p1 and p1.lower().startswith('a') and start_hour == 12:
        start_hour = 0

    if p2 and p2.lower().startswith('p') and end_hour != 12:
        end_hour += 12
    elif p2 and p2.lower().startswith('a') and end_hour == 12:
        end_hour = 0

    if not p1 and p2:
        if p2.lower().startswith('p') and start_hour < 12 and start_hour + 12 <= end_hour:
            start_hour += 12

    if not p2 and p1:
        if p1.lower().startswith('p') and end_hour < 12:
            end_hour += 12

    if end_hour < start_hour and not p2:
        end_hour += 12

    return (start_hour, start_min), (end_hour, end_min)


# ---------------------------------------------------------------------------
# Geocoding (Census Bureau, no API key) with a persistent cache
# ---------------------------------------------------------------------------

_geocode_cache = None
_live_geocodes = 0


def _load_geocode_cache():
    global _geocode_cache
    if _geocode_cache is None:
        try:
            with open(GEOCODE_CACHE_PATH) as f:
                _geocode_cache = json.load(f)
        except (OSError, ValueError):
            _geocode_cache = {}
    return _geocode_cache


def save_geocode_cache():
    try:
        with open(GEOCODE_CACHE_PATH, 'w') as f:
            json.dump(_load_geocode_cache(), f, indent=1, sort_keys=True)
    except OSError as e:
        print(f"Could not write geocode cache: {e}")


ADDRESS_RE = re.compile(r'\d{2,6}\s+[NSEW]?\.?\s*[A-Za-z]', re.IGNORECASE)
CROSS_ST_RE = re.compile(r'\b(?:at|and|&|/|near)\b.*\b(?:st|street|ave|avenue|blvd|boulevard|dr|drive|pl|place|rd|road|way)\b', re.IGNORECASE)


def _looks_like_address(text):
    if not text:
        return False
    t = text.strip()
    if len(t) < 6 or len(t) > 160:
        return False
    return bool(ADDRESS_RE.search(t) or CROSS_ST_RE.search(t))


def geocode(text):
    """Return (lat, lon) for an address-ish string, or None. Cached on disk."""
    global _live_geocodes
    if not _looks_like_address(text):
        return None
    key = re.sub(r'\s+', ' ', text.strip().lower())
    cache = _load_geocode_cache()
    if key in cache:
        v = cache[key]
        return (v[0], v[1]) if v else None

    if _live_geocodes >= MAX_LIVE_GEOCODES:
        return None

    # Try the whole string first, then a regex-extracted "NNNN DIR NAME SUFF ZIP"
    # slice (handles records where the address is buried in a sentence).
    queries = []
    clean = text.strip()
    m = re.search(
        r'\d{2,6}\s+[NSEW]?\.?\s*[A-Za-z0-9 .]*?\b'
        r'(?:st|street|ave|avenue|blvd|boulevard|dr|drive|pl|place|rd|road|way|ct|court|ln|lane|ter|terrace|pkwy|parkway|hwy|highway)\b'
        r'\.?(?:\s*,?\s*\d{5})?',
        clean, re.IGNORECASE)
    if m:
        queries.append(m.group(0))
    queries.append(clean)

    for q in queries:
        q = q.strip().strip(',')
        if not q:
            continue
        if 'los angeles' not in q.lower() and re.search(r'\bca\b', q.lower()) is None:
            q = f"{q}, Los Angeles, CA"
        url = ("https://geocoding.geo.census.gov/geocoder/locations/onelineaddress?"
               + "address=" + urllib.parse.quote(q)
               + "&benchmark=Public_AR_Current&format=json")
        _live_geocodes += 1
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'rf-coordination-bot'})
            data = json.loads(urllib.request.urlopen(req, timeout=20).read().decode('utf-8'))
            matches = data.get('result', {}).get('addressMatches', [])
            if matches:
                coord = matches[0]['coordinates']
                lat, lon = float(coord['y']), float(coord['x'])
                cache[key] = [lat, lon]
                time.sleep(0.15)
                return lat, lon
        except Exception:
            # Don't cache transient failures; just skip this one for now.
            time.sleep(0.15)
            return None
        time.sleep(0.15)

    cache[key] = None
    return None


# ---------------------------------------------------------------------------
# Public-event classification
# ---------------------------------------------------------------------------

def _location_text(e):
    loc_val = e.get('location')
    parts = [
        str(loc_val) if loc_val else '',
        str(e.get('address_start') or ''),
        str(e.get('addr_dir') or ''),
        str(e.get('addr_name') or ''),
        str(e.get('addr_suff') or ''),
        str(e.get('zip_code') or ''),
    ]
    text = ' '.join(p for p in parts if p and p != 'None').strip()
    return re.sub(r'\s+', ' ', text)


def event_coords(e, allow_geocode=True):
    lat_lon = e.get('lat_lon')
    if isinstance(lat_lon, dict):
        lat = lat_lon.get('latitude')
        lon = lat_lon.get('longitude')
        try:
            if lat not in (None, 'None') and lon not in (None, 'None'):
                return float(lat), float(lon)
        except (TypeError, ValueError):
            pass
    if allow_geocode:
        return geocode(_location_text(e))
    return None


def near_church(e, allow_geocode=True):
    """Return (is_near, is_watch, distance_or_None, basis)."""
    coords = event_coords(e, allow_geocode=allow_geocode)
    if coords:
        dist = haversine(LAT_540, LON_540, coords[0], coords[1])
        return dist <= NEAR_RADIUS_MI, dist <= WATCH_RADIUS_MI, dist, 'coords'

    hay = _location_text(e).lower()
    if any(k in hay for k in NEAR_KEYWORDS) or NEAR_ZIPS.search(hay):
        return True, True, None, 'keyword'
    return False, False, None, 'none'


def is_public_gathering(e):
    hay = ' '.join(str(e.get(k) or '') for k in
                   ('event_name', 'work_desc', 'per_sub_type', 'location')).lower()
    if any(t in hay for t in GATHERING_TERMS):
        return True
    if 'public way' in str(e.get('per_sub_type') or '').lower():
        return True
    return False


def _is_usc(e):
    hay = (str(e.get('event_name') or '') + ' ' + _location_text(e)).lower()
    return 'usc' in hay or 'university of southern california' in hay or 'exposition park' in hay


_NAME_STOPWORDS = {'the', 'a', 'an', 'annual', 'los', 'angeles', 'la', 'downtown',
                   'greater', 'first', 'second', 'third', 'official', 'city', 'of'}


def _norm_name(s):
    s = re.sub(r"\b(19|20)\d{2}\b", ' ', str(s or ''))
    s = re.sub(r"\b\d+\s?(st|nd|rd|th)\b", ' ', s, flags=re.IGNORECASE)
    s = re.sub(r'[^a-z0-9]+', ' ', s.lower())
    tokens = [t for t in s.split() if t not in _NAME_STOPWORDS]
    return ' '.join(tokens).strip()


def _event_dict(e, start_dt, end_dt, distance):
    date_str = str(e.get('event_start_date') or '')
    return {
        "name": str(e.get('event_name') or 'LA City Special Event'),
        "date": date_str.split('T')[0] if 'T' in date_str else date_str,
        "location": _location_text(e).replace('\n', ', ') or 'Unknown',
        "type": "Street Closure / Festival (LA City Permit)",
        "distance_mi": round(distance, 2) if distance is not None else None,
        "permit": str(e.get('permitno') or ''),
        "source": "https://data.lacity.org/resource/8spw-3fhx",
        "start_dt": start_dt,
        "end_dt": end_dt,
    }


def fetch_public_permits():
    try:
        req = urllib.request.Request(LA_PERMITS_URL, headers={'User-Agent': 'Mozilla/5.0'})
        response = urllib.request.urlopen(req, timeout=60)
        return json.loads(response.read().decode('utf-8'))
    except Exception as e:
        print(f"Error fetching LA City data: {e}")
        return []


def collect_public_events(raw, today):
    """Return (all_future_events, nearby_upcoming_events)."""
    all_future = []
    nearby_upcoming = []
    horizon = today + timedelta(days=UPCOMING_HORIZON_DAYS)

    for e in raw:
        try:
            start = parse_dt(e.get('event_start_date'))
            end = parse_dt(e.get('event_end_date')) or start
            if start is None:
                start = end
            if start is None:
                continue
            if end is None:
                end = start
            if end.year > 2100:
                continue

            public_start = start.replace(hour=0, minute=0, second=0, microsecond=0)
            public_end = end.replace(hour=23, minute=59, second=0, microsecond=0)

            if public_end < today:
                continue

            is_near, is_watch, dist, _ = near_church(e)
            evt = _event_dict(e, public_start, public_end, dist)
            all_future.append(evt)

            if not is_watch:
                continue
            if _is_usc(e):
                continue
            if not is_public_gathering(e):
                continue
            if public_start > horizon:
                continue
            nearby_upcoming.append(evt)
        except Exception:
            continue

    nearby_upcoming.sort(key=lambda x: x["start_dt"])
    return all_future, nearby_upcoming


def find_recurring_gaps(raw, today, nearby_upcoming, all_future):
    """Annually-recurring nearby gatherings that are NOT yet in the feed for this year."""
    window_end = today + timedelta(days=RECUR_HORIZON_DAYS)
    known = {_norm_name(ev["name"]) for ev in nearby_upcoming}
    known |= {_norm_name(ev["name"]) for ev in all_future
              if ev["start_dt"] >= today and ev["start_dt"] <= window_end}

    candidates = {}
    for e in raw:
        try:
            start = parse_dt(e.get('event_start_date'))
            if start is None:
                continue
            if start.year >= today.year or start.year < today.year - RECUR_LOOKBACK_YEARS:
                continue
            if not is_public_gathering(e):
                continue
            # Cheap proximity check only (no geocoding across 10k+ historical rows).
            is_near, is_watch, dist, _ = near_church(e, allow_geocode=False)
            if not is_watch:
                continue
            if _is_usc(e):
                continue

            key = _norm_name(e.get('event_name') or _location_text(e))
            if not key or key in known:
                continue

            entry = candidates.get(key)
            if entry is None:
                entry = {
                    "name": str(e.get('event_name') or 'Unnamed recurring event'),
                    "location": _location_text(e) or 'Unknown',
                    "last_seen": start,
                    "dates": [],
                }
                candidates[key] = entry
            entry["dates"].append(start)
            if start > entry["last_seen"]:
                entry["last_seen"] = start
                entry["location"] = _location_text(e) or entry["location"]
                entry["name"] = str(e.get('event_name') or entry["name"])
        except Exception:
            continue

    # Confidence filter: seen in >= 2 distinct prior years, OR seen last year.
    # Then refine proximity with a geocode (only ~a dozen candidates, so cheap)
    # and drop anything that turns out to be beyond the watch radius.
    result = []
    for entry in candidates.values():
        years = sorted({d.year for d in entry["dates"]})
        if not (len(years) >= 2 or max(years) >= today.year - 1):
            continue
        info = project_recurrence(entry["dates"], today, window_end)
        if info is None:
            continue
        entry.update(info)
        entry["years"] = years
        entry["projected_date"] = info["projected"]
        coords = geocode(entry["location"])
        if coords:
            dist = haversine(LAT_540, LON_540, coords[0], coords[1])
            if dist > WATCH_RADIUS_MI:
                continue
            entry["distance_mi"] = round(dist, 2)
        else:
            entry["distance_mi"] = None
        result.append(entry)
    result.sort(key=lambda x: x["projected_date"])
    return result


def assess_feed_freshness(raw, today):
    """The permit feed periodically stalls; detect it by comparing the count of
    permits in the next 60 days against the same calendar window a year ago."""
    def count_in_window(start_day, end_day):
        n = 0
        for e in raw:
            d = parse_dt(e.get('event_start_date'))
            if d and start_day <= d <= end_day:
                n += 1
        return n

    upcoming = count_in_window(today, today + timedelta(days=UPCOMING_HORIZON_DAYS))
    prior = count_in_window(today - timedelta(days=365),
                            today - timedelta(days=365) + timedelta(days=UPCOMING_HORIZON_DAYS))

    stale = prior >= 20 and upcoming < max(5, 0.30 * prior)
    return {
        "upcoming_count": upcoming,
        "prior_year_count": prior,
        "stale": stale,
    }


# ---------------------------------------------------------------------------
# "Lafayette Park calendar" -- assembled from the permit feed + two web pages
# ---------------------------------------------------------------------------

def _at_lafayette_park(e):
    hay = _location_text(e).lower()
    if (re.search(r'la\s*f+[ae]y?[ae]?tte\s*(park|pk|rec)', hay)
            or '625 s lafayette' in hay or '625 lafayette' in hay
            or '625 s. lafayette' in hay or '615 s. la fayette' in hay):
        return True
    coords = event_coords(e, allow_geocode=False)
    if coords:
        d = haversine(LAFAYETTE_PARK_LAT, LAFAYETTE_PARK_LON, coords[0], coords[1])
        if d <= 0.20:
            return True
    return False


def lafayette_park_report(raw, today):
    """Permits at Lafayette Park itself: recent history, what's on the books,
    and anything that recurs here but has no permit filed yet this year."""
    history_years = today.year - 3
    horizon = today + timedelta(days=RECUR_HORIZON_DAYS)

    upcoming, history = [], []
    by_name = {}

    for e in raw:
        if not _at_lafayette_park(e):
            continue
        start = parse_dt(e.get('event_start_date'))
        end = parse_dt(e.get('event_end_date')) or start
        if start is None:
            continue
        row = {
            "name": str(e.get('event_name') or 'Unnamed permit'),
            "date": start.strftime('%Y-%m-%d'),
            "work": str(e.get('work_desc') or '').strip(),
            "start": start,
        }
        if (end or start) >= today:
            upcoming.append(row)
        elif start.year >= history_years:
            history.append(row)

        if start.year >= today.year - RECUR_LOOKBACK_YEARS:
            by_name.setdefault(_norm_name(row["name"]), []).append(start)

    def _dedupe(rows):
        seen, out = set(), []
        for r in rows:
            k = (r["date"], _norm_name(r["name"]))
            if k in seen:
                continue
            seen.add(k)
            out.append(r)
        return out

    upcoming = _dedupe(sorted(upcoming, key=lambda r: r["start"]))
    history = _dedupe(sorted(history, key=lambda r: r["start"], reverse=True))

    have_this_year = {_norm_name(r["name"]) for r in upcoming}
    expected = []
    for key, dates in by_name.items():
        if key in have_this_year:
            continue
        prior = [d for d in dates if d.year < today.year]
        if not prior:
            continue
        info = project_recurrence(prior, today, horizon)
        if info is None:
            continue
        info["label"] = _display_name_for(raw, key)
        expected.append(info)
    expected.sort(key=lambda x: x["projected"])
    return {"upcoming": upcoming, "history": history, "expected": expected}


def project_recurrence(prior_dates, today, horizon):
    """Given the dates an event happened in past years, project it forward.

    Projects EVERY prior month/day (not just the latest year's), applies a grace
    window backwards so an event that likely started a few days ago still shows,
    and flags when the historical dates jump around year to year.
    """
    grace = today - timedelta(days=RECUR_GRACE_DAYS)
    projs = []
    for d in prior_dates:
        for yr in (today.year, today.year + 1):
            try:
                p = d.replace(year=yr)
            except ValueError:
                continue
            if grace <= p <= horizon:
                projs.append(p)
    if not projs:
        return None
    projs.sort()
    soonest = projs[0]
    doys = sorted(d.timetuple().tm_yday for d in prior_dates)
    earliest = min(prior_dates, key=lambda x: x.timetuple().tm_yday)
    latest = max(prior_dates, key=lambda x: x.timetuple().tm_yday)
    return {
        "projected": soonest,
        "date_varies": (doys[-1] - doys[0]) > 21,
        "hist_earliest": earliest,
        "hist_latest": latest,
        "imminent": soonest <= today + timedelta(days=3),
        "years": sorted({d.year for d in prior_dates}),
    }


def _display_name_for(raw, norm_key):
    for e in raw:
        n = str(e.get('event_name') or '')
        if n and _norm_name(n) == norm_key:
            return n
    return norm_key.title()


def fetch_rap_events(today):
    """LA Rec & Parks department news/events page: a small hand-curated citywide
    list. Parsed for 'Month DD - Title, Location' bullet items."""
    import html as _html
    text = http_get(RAP_EVENTS_URL)
    if not text:
        return None  # fetch failed -- distinct from "no events listed"

    out = []
    for m in re.finditer(r'<li[^>]*>(.*?)</li>', text, re.S):
        item = _html.unescape(re.sub(r'<[^>]+>', ' ', m.group(1)))
        item = re.sub(r'\s+', ' ', item).strip()
        mm = re.match(r'^([A-Z][a-z]+)\s+(\d{1,2})(?:\s*&\s*\d{1,2})?\s*[-–—]\s*(.+)$', item)
        if not mm:
            continue
        month_name, day, rest = mm.groups()
        try:
            mnum = datetime.strptime(month_name[:3], '%b').month
        except ValueError:
            continue

        when = None
        for yr in (today.year, today.year + 1):
            try:
                cand = datetime(yr, mnum, int(day))
            except ValueError:
                break
            if cand >= today - timedelta(days=2):
                when = cand
                break
        if when is None or when > today + timedelta(days=150):
            continue

        if ',' in rest:
            name, loc = rest.rsplit(',', 1)
        else:
            name, loc = rest, ''
        name, loc = name.strip(' .'), loc.strip(' .')
        blob = f"{name} {loc}".lower()
        near = any(k in blob for k in (
            'lafayette', 'macarthur', 'mac arthur', 'commonwealth', 'westlake',
            'rampart', 'koreatown', 'wilshire center', 'shatto'))
        out.append({
            "name": name,
            "location": loc or 'Location TBD',
            "date": when.strftime('%Y-%m-%d'),
            "start_dt": when.replace(hour=0, minute=0),
            "end_dt": when.replace(hour=23, minute=59),
            "near": near,
            "distance_mi": None,
            "permit": "",
            "source": "LA Rec & Parks events page",
        })
    return out


def fetch_maccla_concerts(today):
    """MacArthur Park / Levitt VIBE free-concert series. The calendar page emits
    a Google-Calendar 'Add to Calendar' link per show with exact datetimes."""
    text = http_get(MACLA_CALENDAR_URL)
    if not text:
        return None  # fetch failed -- distinct from "no concerts listed"

    out, seen = [], set()
    for m in re.finditer(r'calendar\.google\.com/calendar/render\?([^"\'\s<>]+)', text):
        qs = m.group(1).replace('&#038;', '&').replace('&amp;', '&')
        params = urllib.parse.parse_qs(qs)
        dates = params.get('dates', [''])[0]
        dm = re.match(r'(\d{8}T\d{6})/(\d{8}T\d{6})', dates)
        if not dm:
            continue
        try:
            start = datetime.strptime(dm.group(1), '%Y%m%dT%H%M%S')
            end = datetime.strptime(dm.group(2), '%Y%m%dT%H%M%S')
        except ValueError:
            continue
        if end < today:
            continue
        name = (params.get('text', ['MacArthur Park concert'])[0]).replace('—', '-').strip()
        loc = params.get('location', ['MacArthur Park, Los Angeles, CA'])[0].strip()
        key = (name, start)
        if key in seen:
            continue
        seen.add(key)
        dist = haversine(LAT_540, LON_540, 34.0578, -118.2785)
        out.append({
            "name": name,
            "location": loc,
            "date": start.strftime('%Y-%m-%d'),
            "start_dt": start,
            "end_dt": end,
            "distance_mi": round(dist, 2),
            "permit": "",
            "source": "MacLA / Levitt VIBE",
        })
    out.sort(key=lambda x: x["start_dt"])
    return out


def merge_nearby(*lists):
    """Combine event dicts from several sources, newest-source-wins on dupes."""
    merged = {}
    for lst in lists:
        for ev in lst:
            key = (_norm_name(ev["name"]), ev["date"][:7])
            merged.setdefault(key, ev)
    return sorted(merged.values(), key=lambda x: x["start_dt"])


# ---------------------------------------------------------------------------
# FCCLA sheet (unchanged behaviour)
# ---------------------------------------------------------------------------

def fetch_events_from_sheet():
    sheet_id = '1UC8vgy89W14bVEWROqdUc9VgkMTGykC5ZZJqSDmi2-A'
    gid = '251348517'
    url = f'https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq?gid={gid}&headers=0'

    try:
        response = urllib.request.urlopen(url)
        data = response.read().decode('utf-8')

        match = re.search(r'google\.visualization\.Query\.setResponse\((.*)\);', data)
        if not match:
            raise ValueError("Could not find JSON data in Google Sheet response")

        json_data = json.loads(match.group(1))
        rows = json_data['table']['rows']

        events = []
        for index, row in enumerate(rows):
            cells = [cell['v'] if cell else None for cell in row['c']]
            cells += [None] * (10 - len(cells))
            events.append(cells)

        return events
    except Exception as e:
        print(f"Failed to fetch FCCLA events: {e}")
        return []


def get_upcoming_fccla_events(events):
    upcoming = []
    current_date = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    two_weeks = current_date + timedelta(days=14)

    for row in events:
        if not row[0] or not isinstance(row[0], str):
            continue

        date_str = row[0].strip()
        match = re.search(r'\d{1,2}/\d{1,2}/\d{2,4}', date_str)
        if match:
            date_part = match.group(0)
            dt = parse_dt(date_part)
            if dt is None:
                continue

            if current_date <= dt <= two_weeks:
                call_times = str(row[3]) if row[3] else ""
                start_t, end_t = parse_time(call_times)

                start_dt = dt
                end_dt = dt

                if start_t:
                    start_dt = dt.replace(hour=start_t[0], minute=start_t[1])
                if end_t:
                    end_dt = dt.replace(hour=end_t[0], minute=end_t[1])

                if not start_t:
                    end_dt = dt.replace(hour=23, minute=59)

                upcoming.append({
                    "date": date_part,
                    "name": row[1] if row[1] else "Unknown Event",
                    "spaces": row[2] if row[2] else "",
                    "times": call_times,
                    "start_dt": start_dt,
                    "end_dt": end_dt
                })
    return upcoming


def export_json(events_list, filepath):
    exportable = []
    for pe in events_list:
        evt_dict = dict(pe)
        evt_dict.pop("start_dt", None)
        evt_dict.pop("end_dt", None)
        exportable.append(evt_dict)

    with open(filepath, 'w') as f:
        json.dump(exportable, f, indent=2)
    print(f"Exported {len(exportable)} events to {filepath}")


def get_overlapping_events(fccla_events, public_events):
    overlaps = []
    for pe in public_events:
        p_start = pe["start_dt"]
        p_end = pe["end_dt"]

        is_overlapping = False
        for fe in fccla_events:
            f_start = fe["start_dt"] - timedelta(hours=3)
            f_end = fe["end_dt"] + timedelta(hours=3)
            if p_start <= f_end and p_end >= f_start:
                is_overlapping = True
                break

        if is_overlapping:
            overlaps.append(pe)

    return overlaps


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------

def _fmt_event(ev, indent="  "):
    dist = ""
    if ev.get("distance_mi") is not None:
        dist = f" (~{ev['distance_mi']} mi from the church)"
    lines = [
        f"{indent}- {ev['name']}{dist}",
        f"{indent}  Date: {ev['date']}",
        f"{indent}  Location: {ev['location']}",
    ]
    if ev.get("permit"):
        lines.append(f"{indent}  Permit: {ev['permit']}")
    return "\n".join(lines) + "\n"


def _fmt_recurrence(x):
    """One-line summary of a projected recurring event (dict from project_recurrence)."""
    label = x.get("label") or x.get("name") or "Recurring event"
    dist = f" (~{x['distance_mi']} mi)" if x.get("distance_mi") is not None else ""
    yrs = ", ".join(str(y) for y in x["years"])
    if x.get("date_varies"):
        when = (f"date moves year to year — historically "
                f"{x['hist_earliest'].strftime('%b %d')} to {x['hist_latest'].strftime('%b %d')}; "
                f"soonest projected {x['projected'].strftime('%b %d, %Y')}")
    else:
        when = f"expected ~{x['projected'].strftime('%b %d, %Y')}"
    flag = "  << CHECK NOW / IMMINENT" if x.get("imminent") else ""
    return f"{label}{dist} — {when} (previously {yrs}){flag}"


def build_email_body(fccla_events, overlapping_events, nearby_upcoming,
                     recurring_gaps, freshness, lafayette, rap_near,
                     rap_total, maccla, rap_failed=False, maccla_failed=False):
    body = "Hello,\n\n"
    body += "This is the automated weekly events / RF-coordination notification.\n\n"

    imminent = [x for x in lafayette["expected"] if x.get("imminent")]
    imminent += [g for g in recurring_gaps if g.get("imminent")]
    if imminent:
        body += "!! CHECK NOW !!\n"
        body += ("Based on prior years, these may be happening right now / within days "
                 "with no permit in the feed:\n")
        for x in imminent:
            body += f"  - {_fmt_recurrence(x)}\n"
        body += "\n"

    if freshness["stale"]:
        body += ("!! DATA WARNING !!\n"
                 "The LA City special-events permit feed looks stale right now: it lists only "
                 f"{freshness['upcoming_count']} permit(s) in the next {UPCOMING_HORIZON_DAYS} days, "
                 f"versus {freshness['prior_year_count']} in the same window last year. "
                 "Near-term festivals are probably missing from the sections below — "
                 "check https://data.lacity.org/d/8spw-3fhx and https://mac-la.org/calendar/ "
                 "manually.\n\n")

    body += "== Upcoming FCCLA events (next two weeks) ==\n"
    if fccla_events:
        for event in fccla_events:
            body += f"  - {event['date']}: {event['name']} ({event['times']}) in {event['spaces']}\n"
    else:
        body += "  No upcoming FCCLA events in the next two weeks.\n"

    body += "\n== Nearby public events overlapping an FCCLA event (+/- 3 hours) ==\n"
    if overlapping_events:
        for event in overlapping_events:
            body += _fmt_event(event)
    else:
        body += "  None.\n"

    body += (f"\n== All nearby public events in the next {UPCOMING_HORIZON_DAYS} days "
             f"(within ~{WATCH_RADIUS_MI} mi of 540 Commonwealth) ==\n")
    if nearby_upcoming:
        for event in nearby_upcoming:
            body += _fmt_event(event)
            if event.get("source"):
                body += f"    Source: {event['source']}\n"
    else:
        body += "  None found in the permit feed, RAP page, or MacArthur Park series.\n"

    # ---- Lafayette Park focus -------------------------------------------------
    body += "\n== Lafayette Park (one block away) ==\n"
    if lafayette["upcoming"]:
        body += "  On the books (permits filed):\n"
        for r in lafayette["upcoming"]:
            body += f"    - {r['date']}: {r['name']}"
            if r["work"]:
                body += f" — {r['work'][:80]}"
            body += "\n"
    else:
        body += "  On the books: nothing filed yet.\n"

    if lafayette["expected"]:
        body += "\n  Recurs here, but NO permit filed yet this year:\n"
        for x in lafayette["expected"]:
            body += "    - " + _fmt_recurrence(x) + "\n"

    if lafayette["history"]:
        body += f"\n  For reference, permits here since {datetime.now().year - 3}:\n"
        for r in lafayette["history"]:
            body += f"    - {r['date']}: {r['name']}\n"

    # ---- MacArthur Park / Levitt VIBE concerts ------------------------------
    body += "\n== MacArthur Park free concerts (Levitt VIBE, ~0.6 mi, big PA) ==\n"
    if maccla_failed:
        body += "  Could not reach https://mac-la.org/calendar/ this run — check it manually.\n"
    elif maccla:
        for c in maccla:
            body += (f"  - {c['date']} {c['start_dt'].strftime('%I:%M%p').lstrip('0').lower()}"
                     f"–{c['end_dt'].strftime('%I:%M%p').lstrip('0').lower()}: {c['name']}\n")
        body += "  Source: https://mac-la.org/calendar/\n"
    else:
        body += ("  No upcoming concerts listed at https://mac-la.org/calendar/ "
                 "(series usually runs summer only).\n")

    # ---- LA Rec & Parks department events ---------------------------------
    body += "\n== LA Rec & Parks department events ==\n"
    if rap_failed:
        body += f"  Could not reach {RAP_EVENTS_URL} this run — check it manually.\n"
    elif rap_near:
        for e in rap_near:
            body += f"  - {e['date']}: {e['name']} @ {e['location']}\n"
    else:
        body += (f"  None near the church "
                 f"({rap_total} citywide event(s) listed). Note: LA Rec & Parks has no\n"
                 f"  per-park calendar feed; this is their hand-curated department list.\n")

    body += ("\n== Annually-recurring events elsewhere near the church, NOT yet in "
             "the permit feed (verify manually) ==\n")
    if recurring_gaps:
        body += ("  These happened near the church in prior years around this date but have no\n"
                 "  2026 permit in the feed yet. They are likely to recur:\n\n")
        for g in recurring_gaps:
            body += f"  - {_fmt_recurrence(g)}\n    Location: {g['location']}\n\n"
    else:
        body += "  None.\n"

    body += ("\n-- Feed status: "
             f"{freshness['upcoming_count']} permits in the next {UPCOMING_HORIZON_DAYS} days "
             f"({freshness['prior_year_count']} a year ago).\n")
    body += "\nBest,\nCam-Bot\n"
    return body


def send_rf_email(body, to_email="cjohnston@fccla.org"):
    smtp_email = os.environ.get('SMTP_EMAIL')
    smtp_password = os.environ.get('SMTP_PASSWORD')
    smtp_server = os.environ.get('SMTP_SERVER', 'smtp.mail.me.com')
    smtp_port_env = os.environ.get('SMTP_PORT')
    smtp_port = int(smtp_port_env) if smtp_port_env else 587

    msg = EmailMessage()
    msg['Subject'] = 'System Notification: Upcoming Events & RF Coordination'
    msg['From'] = smtp_email or 'rf-bot@example.com'
    msg['To'] = to_email
    msg.set_content(body)

    if not smtp_email or not smtp_password:
        print("DRY RUN: Missing SMTP credentials. Would send email with content:\n")
        print(body)
        return

    try:
        server = smtplib.SMTP(smtp_server, smtp_port)
        server.starttls()
        server.login(smtp_email, smtp_password)
        server.send_message(msg, to_addrs=[to_email])
        server.quit()
        print(f"Successfully sent RF coordination email to {to_email}")
    except Exception as e:
        print(f"Failed to send email: {e}")
        sys.exit(1)


def main():
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

    # 1. FCCLA events for the next two weeks.
    raw_events = fetch_events_from_sheet()
    fccla_events = get_upcoming_fccla_events(raw_events)

    # 2. Public permits (single fetch, reused).
    raw_permits = fetch_public_permits()

    # 3. Classify: everything future, and everything nearby+upcoming.
    all_future, nearby_upcoming = collect_public_events(raw_permits, today)

    # 4. Detect a stale feed and infer recurring events missing from it.
    freshness = assess_feed_freshness(raw_permits, today)
    recurring_gaps = find_recurring_gaps(raw_permits, today, nearby_upcoming, all_future)

    # 5. The "Lafayette Park calendar": park permits + RAP page + MacArthur Park.
    lafayette = lafayette_park_report(raw_permits, today)
    _laf_names = ({_norm_name(x["label"]) for x in lafayette["expected"]}
                  | {_norm_name(r["name"]) for r in lafayette["upcoming"]})
    recurring_gaps = [g for g in recurring_gaps
                      if _norm_name(g["name"]) not in _laf_names]
    rap_events = fetch_rap_events(today)
    rap_failed = rap_events is None
    rap_events = rap_events or []
    rap_near = [e for e in rap_events if e["near"]]
    maccla = fetch_maccla_concerts(today)
    maccla_failed = maccla is None
    maccla = maccla or []
    save_geocode_cache()

    horizon = today + timedelta(days=UPCOMING_HORIZON_DAYS)
    combined_nearby = merge_nearby(
        nearby_upcoming,
        rap_near,
        [e for e in maccla if e["start_dt"] <= horizon],
    )

    # 6. Export JSON snapshots.
    export_json(all_future, os.path.join(SCRIPT_DIR, "all_la_events.json"))
    export_json(combined_nearby, os.path.join(SCRIPT_DIR, "public_events.json"))

    # 7. Overlap section (kept for continuity), now across all nearby sources.
    overlapping = get_overlapping_events(fccla_events, combined_nearby)

    # 8. Compose + send.
    body = build_email_body(fccla_events, overlapping, combined_nearby,
                            recurring_gaps, freshness, lafayette,
                            rap_near, len(rap_events), maccla,
                            rap_failed, maccla_failed)
    send_rf_email(body)


if __name__ == "__main__":
    main()
