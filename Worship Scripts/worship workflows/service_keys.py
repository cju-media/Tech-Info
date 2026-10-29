"""Which service a piece of content belongs to, for dates with more than one.

Most weeks there is one service, on Sunday at 10:30, and an OW is named for its
date alone ("11.1.26 OW Draft.pdf"). Christmas Eve has several, each with its
own OW named for its time too ("12.24.26 5:30pm OW.pdf", "12.24.26_11pm.pdf").
Everything generated from an OW -- the service-title files, Description.txt,
chapters.txt, the timings pushed after the stream -- lives in a folder per
service under Worship Scripts/services/<key>/:

    2026-12-27        an OW with no time in its name (the 10:30 service)
    2026-12-24_1730   an OW for 5:30pm

The old single-service paths (Worship Scripts/service-titles/, Youtube
Processing/Description.txt and chapters.txt, and their *_state.json files) are
now the "live set": a copy of the next service that hasn't finished yet (see
current_service). ProPresenter, the booth's OSC chapter list and the dashboard
keep reading those paths and follow Christmas Eve service by service.

Shared by scripts in Worship Scripts/ and Youtube Processing/ (they add this
directory to sys.path), unlike the small per-script helpers elsewhere: these
rules have to agree everywhere or a stream gets the wrong service's text.
Standard library only -- the workflows that use it install different packages.
"""
import datetime
import json
import os
import re
import shutil
import zoneinfo

TZ = zoneinfo.ZoneInfo("America/Los_Angeles")
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SERVICES_DIR = os.path.join(REPO_ROOT, "Worship Scripts", "services")

DEFAULT_TIME = "10:30"
# A service stays "current" until this long after it starts (the OW site uses
# the same rule to pick which Christmas Eve program to show).
LIVE_WINDOW = datetime.timedelta(hours=3)
NEXT_SERVICE_LEAD = datetime.timedelta(hours=1)
# OWs further out than this aren't processed yet.
LOOKAHEAD_DAYS = 30

STATE_FILE = "state.json"              # written by update_service_titles.py
DESCRIPTION_STATE_FILE = "description.json"  # written by generate_youtube_description.py
TITLES_SUBDIR = "service-titles"

_DATE_RE = re.compile(r"^(\d{1,2})[-.](\d{1,2})[-.](\d{2,4})")
# "5pm", "5:30pm", "530pm", "5:30 PM" -- the same rule as cju-media/OW's index.
_TIME_RE = re.compile(r"(\d{1,2})(?::?(\d{2}))?\s*(am|pm)", re.I)


# ---------------------------------------------------------------- parsing

def parse_ow_filename(name):
    """(date, "HH:MM" or None) for an OW filename, or None if it has no date.

    The time is only looked for after the date, so the date's own digits can't
    be read as one."""
    m = _DATE_RE.match(name or "")
    if not m:
        return None
    month, day, year = (int(x) for x in m.groups())
    if year < 100:
        year += 2000
    try:
        date = datetime.date(year, month, day)
    except ValueError:
        return None
    return date, parse_time_text(name[m.end():])


def parse_time_text(text):
    """"HH:MM" (24-hour) for the first "5pm"/"5:30pm"/"530pm" in text, else None."""
    m = _TIME_RE.search(text or "")
    if not m:
        return None
    hour = int(m.group(1))
    minute = int(m.group(2) or 0)
    pm = m.group(3).lower() == "pm"
    if not (1 <= hour <= 12 and 0 <= minute < 60):
        return None
    if pm and hour < 12:
        hour += 12
    if not pm and hour == 12:
        hour = 0
    return f"{hour:02d}:{minute:02d}"


def normalize_time(value):
    """"HH:MM" for "17:30", "1730", "5:30pm" and the like; None for blank or unreadable."""
    value = (value or "").strip()
    if not value:
        return None
    m = re.fullmatch(r"(\d{1,2}):?(\d{2})", value)
    if m:
        hour, minute = int(m.group(1)), int(m.group(2))
        if hour < 24 and minute < 60:
            return f"{hour:02d}:{minute:02d}"
        return None
    return parse_time_text(value)


def parse_date(value):
    """A date from the formats this pipeline passes around (MM-DD-YYYY from the
    dashboard, M-D-YY from filenames, ISO from state files), or None."""
    if isinstance(value, datetime.date):
        return value
    for fmt in ("%m-%d-%Y", "%m-%d-%y", "%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%Y/%m/%d"):
        try:
            return datetime.datetime.strptime((value or "").strip(), fmt).date()
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------- keys

def service_key(date, time=None):
    """"2026-12-27" for an untimed service, "2026-12-24_1730" for a timed one."""
    date = parse_date(date)
    time = normalize_time(time) if time else None
    return date.isoformat() + (f"_{time.replace(':', '')}" if time else "")


def parse_service_key(key):
    """(date, "HH:MM" or None) for a service key, or None."""
    m = re.fullmatch(r"(\d{4}-\d{2}-\d{2})(?:_(\d{2})(\d{2}))?", key or "")
    if not m:
        return None
    date = datetime.date.fromisoformat(m.group(1))
    return date, (f"{m.group(2)}:{m.group(3)}" if m.group(2) else None)


def ow_index_key(date, time=None):
    """The cju-media/OW index key, which is also the program link's slug:
    "12-27-26", "12-24-26-8pm", "12-24-26-530pm"."""
    key = f"{date.month}-{date.day}-{date.year % 100:02d}"
    if time:
        hour, minute = (int(x) for x in time.split(":"))
        suffix = "pm" if hour >= 12 else "am"
        key += f"-{hour % 12 or 12}{f'{minute:02d}' if minute else ''}{suffix}"
    return key


def display_time(time):
    """"5:30pm", "8pm" for "17:30", "20:00"."""
    hour, minute = (int(x) for x in time.split(":"))
    return f"{hour % 12 or 12}{f':{minute:02d}' if minute else ''}{'pm' if hour >= 12 else 'am'}"


def service_start(date, time=None):
    """When a service starts, as an aware LA datetime (untimed means 10:30)."""
    hour, minute = (int(x) for x in (time or DEFAULT_TIME).split(":"))
    return datetime.datetime.combine(date, datetime.time(hour, minute), tzinfo=TZ)


# ---------------------------------------------------------------- folders

def service_dir(key):
    return os.path.join(SERVICES_DIR, key)


def titles_dir(key):
    return os.path.join(service_dir(key), TITLES_SUBDIR)


def _read_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def _write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def read_state(key):
    return _read_json(os.path.join(service_dir(key), STATE_FILE))


def write_state(key, data):
    _write_json(os.path.join(service_dir(key), STATE_FILE), data)


def read_description_state(key):
    return _read_json(os.path.join(service_dir(key), DESCRIPTION_STATE_FILE))


def write_description_state(key, data):
    _write_json(os.path.join(service_dir(key), DESCRIPTION_STATE_FILE), data)


def list_services():
    """Every service with generated titles, as (key, date, time), in start order."""
    found = []
    try:
        names = os.listdir(SERVICES_DIR)
    except FileNotFoundError:
        return []
    for name in names:
        parsed = parse_service_key(name)
        if parsed and read_state(name):
            found.append((name, *parsed))
    found.sort(key=lambda s: service_start(s[1], s[2]))
    return found


def services_on(date):
    return [s for s in list_services() if s[1] == date]


def resolve_key(date, time=None):
    """The service a stream or upload for (date, time) belongs to, or None when
    no OW for it has been processed yet.

    Exact time first. An untimed OW is the 10:30 service, so a 10:30 request
    (the dashboard's default) finds it. And when the date has only one service
    at all, that's the one -- a Sunday stream moved to 10:00 still gets that
    Sunday's text. With several services on the date and no time match, it's
    None rather than a guess."""
    date = parse_date(date)
    time = normalize_time(time)
    if not date:
        return None
    on_date = services_on(date)
    if time:
        for key, _, t in on_date:
            if t == time:
                return key
    if (time in (None, DEFAULT_TIME)):
        for key, _, t in on_date:
            if t is None:
                return key
    if len(on_date) == 1:
        return on_date[0][0]
    return None


def current_service(now=None):
    """The service the live set should hold, or None when nothing upcoming has
    been processed.

    Each service holds it until LIVE_WINDOW after it starts -- or, when the
    next service follows closer than that, until NEXT_SERVICE_LEAD before the
    next one starts, so the hourly run has the 8pm titles in ProPresenter
    before 8pm rather than at 8:30. (A stream that runs over still keeps its
    titles: update_service_titles.py doesn't swap during a live stream.)"""
    now = now or datetime.datetime.now(TZ)
    services = list_services()
    for i, (key, date, time) in enumerate(services):
        until = service_start(date, time) + LIVE_WINDOW
        if i + 1 < len(services):
            _, next_date, next_time = services[i + 1]
            until = min(until, service_start(next_date, next_time) - NEXT_SERVICE_LEAD)
        if until > now:
            return key
    return None


def live_key():
    """The service service-titles/ holds right now (service_titles_state.json's
    key), or None. Description.txt and chapters.txt follow it, so the live set
    never mixes two services."""
    state = _read_json(os.path.join(REPO_ROOT, "Worship Scripts", "service_titles_state.json")) or {}
    key = state.get("key")
    return key if key and read_state(key) else None


def content_ready(key):
    """(ok, reason): this service's title and description were both generated,
    from the same OW, and aren't empty. The per-service replacement for the
    old "generated this week for this Sunday" checks, which can't tell
    Christmas Eve's three services apart and refuse anything uploaded early."""
    state = read_state(key)
    if not state:
        return False, f"no OW has been processed for {key}"
    title = read_text(os.path.join(titles_dir(key), "title.txt"))
    if not title:
        return False, f"title.txt for {key} is empty"
    desc_state = read_description_state(key) or {}
    if desc_state.get("ow_sha") != state.get("ow_sha"):
        return False, (f"Description.txt for {key} isn't generated from its current OW yet "
                       f"(description from {desc_state.get('ow_sha')!r}, titles from {state.get('ow_sha')!r})")
    if not read_text(os.path.join(service_dir(key), "Description.txt")):
        return False, f"Description.txt for {key} is empty"
    return True, ""


def read_text(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except Exception:
        return ""


def title_for(key):
    return read_text(os.path.join(titles_dir(key), "title.txt"))


def description_for(key):
    """Description.txt plus this service's own chapter timings, if pushed yet."""
    desc = read_text(os.path.join(service_dir(key), "Description.txt"))
    timings = read_text(os.path.join(service_dir(key), "timings.txt"))
    return desc + ("\n\n" + timings if desc and timings else "")


def copy_titles(src, dest):
    """Make dest's .txt files exactly src's (blanking ones src doesn't have, as
    update_service_titles.py always has, so a section dropped from this OW
    doesn't linger from the last one)."""
    os.makedirs(dest, exist_ok=True)
    for name in os.listdir(dest):
        if name.endswith(".txt") and not os.path.exists(os.path.join(src, name)):
            with open(os.path.join(dest, name), "w") as f:
                f.write("")
    for name in os.listdir(src):
        if name.endswith(".txt"):
            shutil.copyfile(os.path.join(src, name), os.path.join(dest, name))
