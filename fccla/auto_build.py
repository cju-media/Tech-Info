#!/usr/bin/env python3
"""Build the week's title graphics with nobody at the keyboard (title_graphics.yml, on Studio Mini).

After an Order of Worship is uploaded on the upload dashboard (ow_uploaded):
  1. find the OW in cju-media/OW: the uploaded file, else the coming Sunday's;
  2. read the text fields from the PDF and have Gemini check them;
  3. build the graphics once per suggested panel color, in a staging folder on this Mac (not in
     iCloud), and text a link to the picker page (Utilities/title-graphics/).
When one is picked there (title_graphics_pick):
  4. copy it into the iCloud week folder. Graphics made or edited by hand are never replaced
     unless asked;
  5. queue both JPGs for Drive the way a dashboard upload does (Utilities/uploads_queue/). The
     Worship Service one creates the livestream. A changed pick replaces them in place. Drive only
     takes the coming Sunday's thumbnails, so an earlier pick waits and the schedule sends it
     when its week comes up.

  python3 fccla/auto_build.py [--ow 10.4.26_OW_Draft.pdf]          # build the options
  python3 fccla/auto_build.py --pick 2    |   --pick '#325673'       # use one (a new hex is built first)
  python3 fccla/auto_build.py --probe                                # is this Mac set up?
"""

import argparse
import datetime
import glob
import json
import os
import re
import shutil
import sys
import tempfile
import time
import zoneinfo

import requests
from PIL import Image
from pypdf import PdfReader

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import prepare_week as pw  # noqa: E402
from illustrator import IllustratorError, do_javascript, run_updateweek  # noqa: E402

OW_API = "https://api.github.com/repos/cju-media/OW/contents/OWs"
PICKER_URL = "https://cju-media.github.io/Tech-Info/Utilities/title-graphics/"
GEMINI_MODEL = "gemini-3.5-flash"          # same model as update_service_titles.py
FIELDS = ["series", "seriesName", "title", "dateText", "preacher"]
SCRIPT_FILES = ["UpdateWeek.jsx", "prepare_week.py", "GEMINI.md", "README.md"]
TZ = zoneinfo.ZoneInfo("America/Los_Angeles")
EDIT_GRACE = 120                           # seconds between saving the .ai files and writing log.txt
# The upload dashboard's Worship Service / Sermon Series thumbnail folders (upload_queue_to_drive.py).
GRAPHICS = [("service", "Worship Service", "Service Title", "1KI_KifGRzRnafb5Z0IuXmdrgIEyB5_3f"),
            ("sermon", "Sermon Series", "Sermon Title", "1Ji2Bbe7vWTcaRCpdQOjzwQgxsIoOWdy4")]


class Stop(Exception):
    """Ends a run: status for the dashboard, message for the text, exit code."""

    def __init__(self, status, message, code=0, notify=True):
        super().__init__(message)
        self.status, self.message, self.code, self.notify = status, message, code, notify


def root_folder():
    return os.path.expanduser(os.environ.get("TITLE_GRAPHICS_ROOT") or pw.ICLOUD_ROOT)


def staging_folder():
    return os.path.expanduser(os.environ.get("TITLE_GRAPHICS_STAGING")
                              or "~/Library/Application Support/FCCLA Title Graphics")


# ---------------------------------------------------------------- dates

def ow_date(name):
    m = re.match(r"(\d{1,2})\.(\d{1,2})\.(\d{2})[ _]OW", name, re.I)
    try:
        return datetime.date(2000 + int(m.group(3)), int(m.group(1)), int(m.group(2))) if m else None
    except ValueError:
        return None


def coming_sunday(today):
    """The Sunday an OW is being prepared for (on a Sunday, that day), as update_service_titles.py."""
    return today + datetime.timedelta(days=6 - today.weekday())


def drive_sunday(today):
    """The Sunday Drive files a Sermon Series thumbnail under: always strictly after today, as
    get_upcoming_sunday() in upload_queue_to_drive.py."""
    return today + datetime.timedelta(days=(6 - today.weekday()) or 7)


def long_date(d):
    return d.strftime("%A, %B ") + str(d.day)


# ---------------------------------------------------------------- which OW

def choose_ow(ows, name=None, today=None):
    """The OW to build from: `name` if given (as uploaded, or as the dashboard renames it), else
    the coming Sunday's, preferring a final over a draft."""
    pdfs = [o for o in ows if o["name"].lower().endswith(".pdf")]
    if name:
        safe = re.sub(r"[^a-zA-Z0-9.-]", "_", name)
        return next((o for o in pdfs if o["name"] in (name, safe)), None)
    sunday = coming_sunday(today or datetime.datetime.now(TZ).date())
    hits = [o for o in pdfs if ow_date(o["name"]) == sunday]
    hits.sort(key=lambda o: ("draft" in o["name"].lower(), o["name"]))
    return hits[0] if hits else None


def list_ows():
    headers = {"Accept": "application/vnd.github.v3+json"}
    if os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = "Bearer " + os.environ["GITHUB_TOKEN"]
    r = requests.get(OW_API, headers=headers, timeout=60)
    r.raise_for_status()
    return r.json()


# ---------------------------------------------------------------- hand-made graphics

def graphics_in(week_dir):
    return sorted(glob.glob(os.path.join(week_dir, "Worship Service*", "*.ai")) +
                  glob.glob(os.path.join(week_dir, "Sermon Series*", "*.ai")))


def who_made(week_dir):
    """'none' (nothing there yet), 'script' (put there by UpdateWeek.jsx and untouched since),
    'hand-made' (no UpdateWeek log), or 'edited' (an .ai changed after the script's log)."""
    ais = graphics_in(week_dir)
    if not ais:
        return "none"
    log = os.path.join(week_dir, "log.txt")
    try:
        with open(log, encoding="utf-8") as fh:
            if not fh.readline().startswith("UpdateWeek:"):
                return "hand-made"
    except OSError:
        return "hand-made"
    if any(os.path.getmtime(a) > os.path.getmtime(log) + EDIT_GRACE for a in ais):
        return "edited"
    return "script"


# ---------------------------------------------------------------- text fields + Gemini check

GEMINI_PROMPT = """You are checking the text for a church's weekly title graphic, taken from its
Order of Worship. Reply with JSON only:
{"series": "", "seriesName": "", "title": "", "dateText": "", "preacher": ""}

- series: the page-1 line "<Season> Series <N>", e.g. "Fall Series 4".
- seriesName and title: the page-1 line "<Series Name> ~ <Sermon Title>", split on the "~".
- dateText: the service date on page 1, written like "September 27, 2026".
- preacher: the name at the end of the "Sermon" row of the order of service, with its title
  (Rev., Dr., ...), e.g. "Rev. Laura Vail Fregin".
Copy words exactly as the PDF has them (spelling, capitalization, punctuation). Ignore the photo
caption and the church address. Use "" for anything that isn't there.

PAGE 1:
%s

WHOLE DOCUMENT:
%s
"""


def pdf_text(pdf):
    reader = PdfReader(pdf)
    return (reader.pages[0].extract_text(extraction_mode="layout"),
            "\n".join(p.extract_text() or "" for p in reader.pages))


def gemini_fields(page1, full):
    """Gemini's reading of the fields, or (None, why not)."""
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        return None, "Gemini check skipped (no GEMINI_API_KEY)"
    try:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=key, http_options=types.HttpOptions(timeout=120000))
        resp = client.models.generate_content(
            model=GEMINI_MODEL, contents=GEMINI_PROMPT % (page1, full[:60000]),
            config=types.GenerateContentConfig(response_mime_type="application/json"))
        text = re.sub(r"^```(?:json)?|```$", "", (resp.text or "").strip()).strip()
        data = json.loads(text)
        return {k: str(data.get(k) or "").strip() for k in FIELDS}, None
    except Exception as e:                       # the build goes ahead on the PDF parse alone
        return None, "Gemini check failed (%s)" % str(e)[:200]


def any_date(s):
    """A date written 'September 27, 2026' or, as Gemini sometimes copies it from the PDF, '27 September 2026'."""
    d = pw.parse_date_text((s or "").strip())
    m = not d and re.fullmatch(r"(\d{1,2}) (%s),? (\d{4})" % pw.MONTH_RE, (s or "").strip(), re.I)
    if m:
        d = datetime.date(int(m.group(3)), pw.MONTHS.index(m.group(2).capitalize()) + 1, int(m.group(1)))
    return d


def norm(s):
    s = (s or "").replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    s = s.replace("–", "-").replace("—", "-")
    return re.sub(r"\s+", " ", s).strip().lower()


def merge_fields(parsed, gem, full_text):
    """Combine the PDF parse with Gemini's reading. Agreement: the PDF's own text. Only one side
    found it: that side. Disagreement: Gemini's version if it is really in the PDF (it picked a
    different span of real text), else the parse. Every disagreement is reported."""
    merged, notes = {}, []
    in_pdf = norm(full_text)
    for k in FIELDS:
        p, g = parsed.get(k, ""), (gem or {}).get(k, "")
        if k == "dateText":
            gd = any_date(g)
            g = "%s %d, %d" % (pw.MONTHS[gd.month - 1], gd.day, gd.year) if gd else g
            same = pw.parse_date_text(p) == gd
        else:
            same = norm(p) == norm(g)
        if not g or same:
            merged[k] = p
        elif not p:
            merged[k] = g
            notes.append("%s came from Gemini only: %s" % (k, g))
        elif k != "dateText" and norm(g) in in_pdf:
            merged[k] = g
            notes.append("%s: the PDF parse read %r, Gemini %r; used Gemini's" % (k, p, g))
        else:
            merged[k] = p
            notes.append("%s: Gemini read %r; used the PDF's %r" % (k, g, p))
    return merged, notes


def read_fields(pdf):
    parsed, warn = pw.parse_pdf(pdf)
    page1, full = pdf_text(pdf)
    gem, gem_note = gemini_fields(page1, full)
    fields, notes = merge_fields(parsed, gem, full)
    notes += ([gem_note] if gem_note else []) + warn
    missing = [k for k in FIELDS if not fields.get(k)]
    if missing or not pw.parse_date_text(fields.get("dateText")):
        raise Stop("failed", "Title graphics: couldn't read %s from the OW. Make this week by hand (see "
                   "fccla/GEMINI.md)." % (", ".join(missing) or "the date"), code=1)
    return fields, notes


# ---------------------------------------------------------------- building options

def save_previews(week_dir, week, dest, n):
    os.makedirs(dest, exist_ok=True)
    out = {}
    for key, sub, name, _ in GRAPHICS:
        jpg = os.path.join(week_dir, "%s %s" % (sub, week), "%s_%s.jpg" % (name, week))
        if os.path.exists(jpg):
            im = Image.open(jpg).convert("RGB")
            im.thumbnail((800, 450))
            im.save(os.path.join(dest, "%d-%s.jpg" % (n, key)), quality=72)
            out[key] = os.path.relpath(os.path.join(dest, "%d-%s.jpg" % (n, key)), REPO)
    return out


def build_option(ctx, build, n, color, label):
    """Build option n (UpdateWeek.jsx in Illustrator) into staging and add it to build["options"]."""
    wdir = os.path.join(ctx.staging, build["week"])
    out = os.path.join(wdir, "option-%d" % n, build["week"])
    if os.path.isdir(os.path.dirname(out)):
        shutil.rmtree(os.path.dirname(out))
    try:
        prep = pw.prepare(os.path.join(wdir, "ow.pdf"), ctx.root, out_root=wdir,
                          data_path=os.path.join(wdir, "option-%d.txt" % n), color=color,
                          fields=build["fields"], write=True, output_dir=out)
    except pw.PrepareError as e:
        raise Stop("failed", "Title graphics for %s: %s" % (build["week"], e), code=1)
    if prep["problems"]:
        raise Stop("failed", "Title graphics for %s weren't built; week-data.txt has problems:\n%s"
                   % (build["week"], "\n".join(prep["problems"])), code=1)
    try:
        log = run_updateweek(prep["data_path"])
    except IllustratorError as e:
        raise Stop("failed", "Title graphics for %s: Illustrator failed: %s" % (build["week"], e), code=1)
    print(log)
    if not log.startswith("UpdateWeek:"):
        raise Stop("failed", "Title graphics for %s: the script stopped early: %s" % (build["week"], log[:300]), code=1)
    option = {"n": n, "hex": prep["color"], "label": label,
              "problems": [l.strip()[3:] for l in log.splitlines() if l.startswith("  !! ")]}
    option.update(save_previews(out, build["week"], os.path.join(ctx.status_dir, build["id"]), n))
    build["options"].append(option)
    return prep


def build_options(ctx, ow, pdf, fields, notes, previous):
    """Every suggested color (plus last pick for this week, if the OW changed), in staging."""
    date = pw.parse_date_text(fields["dateText"])
    week = pw.week_name(date)
    wdir = os.path.join(ctx.staging, week)
    if os.path.isdir(wdir):
        shutil.rmtree(wdir)
    os.makedirs(wdir)
    shutil.copyfile(pdf, os.path.join(wdir, "ow.pdf"))
    build = {"id": "%s_%s" % (week, ctx.now.strftime("%m%d-%H%M")), "week": week, "dateText": fields["dateText"],
             "fields": fields, "ow": ow, "built_at": ctx.now.isoformat(timespec="seconds"),
             "notes": notes, "options": [], "pick": None}
    for k in ("series", "seriesName", "title", "preacher"):
        build[k] = fields[k]
    if previous and previous.get("week") == week and (previous.get("pick") or previous.get("previous_pick")):
        build["previous_pick"] = previous.get("pick") or previous.get("previous_pick")

    first = build_option(ctx, build, 1, "1", None)
    picks = first["picks"]
    build["options"][0]["label"] = picks[0][0]
    build["notes"] = notes + first["warnings"]
    for n, (label, hexc) in enumerate(picks[1:], 2):
        build_option(ctx, build, n, hexc, label)
    earlier = (build.get("previous_pick") or {}).get("hex")
    if earlier and earlier not in [o["hex"] for o in build["options"]]:
        build_option(ctx, build, len(build["options"]) + 1, earlier, "your earlier pick")
    cover = first["cover"].copy()
    cover.thumbnail((360, 420))
    cover.save(os.path.join(ctx.status_dir, build["id"], "cover.jpg"), quality=75)
    build["cover"] = os.path.relpath(os.path.join(ctx.status_dir, build["id"], "cover.jpg"), REPO)
    return build


def clean_up(ctx, keep_id):
    """Only the current build's previews stay in the repo; staged weeks go after three weeks."""
    for d in glob.glob(os.path.join(ctx.status_dir, "*")):
        if os.path.basename(d) != keep_id:
            shutil.rmtree(d) if os.path.isdir(d) else os.remove(d)
    for d in glob.glob(os.path.join(ctx.staging, "*")):
        wd = pw.parse_week_name(os.path.basename(d))
        if wd and (ctx.today - wd).days > 21:
            shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------- using a pick

def queue_for_drive(week_dir, week, replace, queue_dir):
    """Queue both JPGs like the upload dashboard's thumbnail drop zones (TIMESTAMP---FOLDER---NAME)."""
    os.makedirs(queue_dir, exist_ok=True)
    stamp, queued = int(time.time() * 1000), []
    for i, (_, sub, name, folder) in enumerate(GRAPHICS):
        src = os.path.join(week_dir, "%s %s" % (sub, week), "%s_%s.jpg" % (name, week))
        safe = re.sub(r"[^a-zA-Z0-9.-]", "_", os.path.basename(src))
        dst = os.path.join(queue_dir, "%d---%s---%s" % (stamp + i, folder, safe))
        shutil.copyfile(src, dst)
        if replace:
            with open(dst + ".meta.json", "w") as fh:
                json.dump({"replace": True}, fh)
        queued.append(dst)
    return queued


def send_to_drive(ctx, build, replace):
    """Queue the picked graphics for Drive if their week is the one Drive files under now."""
    date = pw.parse_date_text(build["dateText"])
    if date == drive_sunday(ctx.today):
        queue_for_drive(os.path.join(ctx.root, build["week"]), build["week"], replace, ctx.queue_dir)
        return "sent", "Sent to Drive for the livestream (Worship Service + Sermon Series)."
    if date <= ctx.today:
        return "not sent", "Not sent to Drive: that Sunday is already here, so upload them by hand if they're still needed."
    send_on = date - datetime.timedelta(days=6)
    return "waiting", "Drive gets them on %s (it only takes the coming Sunday's)." % long_date(send_on)


def use_pick(ctx, state, sel, replace_hand_edits):
    build = state.get("build")
    if not build:
        raise Stop("failed", "Title graphics: nothing has been built yet to pick from.", code=1)
    week = build["week"]
    if not os.path.exists(os.path.join(ctx.staging, week, "ow.pdf")):
        raise Stop("failed", "Title graphics for %s: the options aren't on Studio Mini any more; upload "
                   "the OW again to rebuild them." % week, code=1)
    sel = sel.strip()
    if re.fullmatch(r"\d+", sel):
        option = next((o for o in build["options"] if o["n"] == int(sel)), None)
        if not option:
            raise Stop("failed", "Title graphics for %s: there's no option %s." % (week, sel), code=1)
    elif re.fullmatch(r"#?[0-9A-Fa-f]{6}", sel):
        hexc = "#" + sel.lstrip("#").upper()
        option = next((o for o in build["options"] if o["hex"] == hexc), None)
        if not option:                                  # a color of your own: build it first
            build_option(ctx, build, len(build["options"]) + 1, hexc, "your color")
            option = build["options"][-1]
    else:
        raise Stop("failed", "Title graphics: %r isn't an option number or a color like #325673." % sel, code=1)

    target = os.path.join(ctx.root, week)
    made = who_made(target)
    if made in ("hand-made", "edited") and not replace_hand_edits:
        how = "were made by hand" if made == "hand-made" else "were edited by hand after they were built"
        raise Stop("skipped", "The %s graphics in iCloud %s, so %s wasn't copied over them. Tick \"Replace "
                   "hand edits\" on the picker to use it anyway." % (week, how, option["hex"]))

    # the week-data.txt record and the cover in iCloud, then the chosen graphics and their log
    try:
        prep = pw.prepare(os.path.join(ctx.staging, week, "ow.pdf"), ctx.root,
                          data_path=os.path.join(ctx.root, "Scripts", "week-data.txt"), color=option["hex"],
                          fields=build["fields"], write=True)
    except pw.PrepareError as e:
        raise Stop("failed", "Title graphics for %s: %s" % (week, e), code=1)
    staged = os.path.join(ctx.staging, week, "option-%d" % option["n"], week)
    for _, sub, _, _ in GRAPHICS:
        folder = "%s %s" % (sub, week)
        os.makedirs(os.path.join(target, folder), exist_ok=True)
        for f in sorted(os.listdir(os.path.join(staged, folder))):
            if f.lower().endswith((".ai", ".jpg")):
                shutil.copyfile(os.path.join(staged, folder, f), os.path.join(target, folder, f))
    shutil.copyfile(os.path.join(staged, "log.txt"), os.path.join(target, "log.txt"))
    shutil.copyfile(prep["data_path"], os.path.join(target, "week-data.txt"))
    sync_scripts(ctx.root)

    earlier = build.get("pick") or build.get("previous_pick") or {}
    replace = earlier.get("drive") == "sent"
    drive, drive_msg = send_to_drive(ctx, build, replace)
    build["pick"] = {"n": option["n"], "hex": option["hex"], "label": option["label"],
                     "at": ctx.now.isoformat(timespec="seconds"), "drive": drive}
    name = (option["label"] or "").split(",")[0]
    date = pw.parse_date_text(build["dateText"])
    return "Using %s%s for %s. Saved to iCloud (%s). %s" % (
        option["hex"], " (%s)" % name if name else "", long_date(date), week, drive_msg)


def send_waiting(ctx, state):
    """Schedule: a pick waiting for its week to come up goes to Drive now."""
    build = state.get("build") or {}
    pick = build.get("pick") or {}
    if pick.get("drive") != "waiting":
        return None
    drive, msg = send_to_drive(ctx, build, replace=False)
    if drive == "waiting":
        return None
    pick["drive"] = drive
    date = pw.parse_date_text(build["dateText"])
    return "Title graphics for %s (%s): %s" % (long_date(date), pick["hex"], msg)


def sync_scripts(root):
    """Keep the iCloud Scripts copy (used when running by hand) the same as the repo's."""
    dest = os.path.join(root, "Scripts")
    if not os.path.isdir(dest):
        return []
    changed = []
    for f in SCRIPT_FILES:
        src, dst = os.path.join(HERE, f), os.path.join(dest, f)
        if not os.path.exists(dst) or open(src, "rb").read() != open(dst, "rb").read():
            shutil.copyfile(src, dst)
            changed.append(f)
    return changed


# ---------------------------------------------------------------- probe

def probe(root, staging):
    """Checklist for the Mac running the workflow. Returns (ok, lines)."""
    lines, ok = [], True

    def item(good, text):
        nonlocal ok
        ok = ok and good
        lines.append(("OK   " if good else "FIX  ") + text)
    item(os.path.isdir(os.path.join(root, "Past Weeks")),
         "Worship and Sermon Series folder at %s (set the TITLE_GRAPHICS_ROOT variable if it's elsewhere)" % root)
    item(os.path.isdir(os.path.join(root, "Scripts")), "Scripts folder in it")
    try:
        os.makedirs(staging, exist_ok=True)
        item(os.access(staging, os.W_OK), "staging folder %s" % staging)
    except OSError as e:
        item(False, "staging folder %s: %s" % (staging, e))
    apps = glob.glob("/Applications/Adobe Illustrator*/Adobe Illustrator.app")
    item(bool(apps), "Illustrator installed" + (": " + apps[0] if apps else ""))
    if apps:
        try:
            v = do_javascript("app.version", timeout=180)
            item(True, "Illustrator answers scripts (version %s)" % v)
        except IllustratorError as e:
            item(False, "Illustrator answers scripts: %s. If macOS asked whether this runner may "
                        "control Adobe Illustrator, allow it (System Settings > Privacy & Security > "
                        "Automation)." % e)
    try:
        from google import genai  # noqa: F401
        item(True, "google-genai installed")
    except ImportError:
        item(False, "google-genai installed (pip3 install google-genai)")
    item(bool(os.environ.get("GEMINI_API_KEY")), "GEMINI_API_KEY available")
    item(shutil.which("brctl") is not None, "brctl available (downloads cloud-only iCloud files)")
    return ok, lines


# ---------------------------------------------------------------- main

class Context:
    def __init__(self, a):
        self.root = os.path.abspath(os.path.expanduser(a.root))
        self.staging = os.path.abspath(os.path.expanduser(a.staging))
        self.status_dir = os.path.abspath(a.status_dir)
        self.queue_dir = os.path.abspath(a.queue_dir)
        self.now = datetime.datetime.now(TZ) if not a.today else \
            datetime.datetime.combine(datetime.date.fromisoformat(a.today), datetime.time(12), TZ)
        self.today = self.now.date()


def build_message(build, notes_prefix=""):
    date = pw.parse_date_text(build["dateText"])
    opts = " · ".join("%d %s %s" % (o["n"], (o["label"] or "").split(",")[0], o["hex"]) for o in build["options"])
    lines = [notes_prefix + "Title graphics for %s are ready to pick: %s" % (long_date(date), PICKER_URL), opts]
    if build.get("previous_pick"):
        lines.append("The OW changed after you picked %s, so pick again to update iCloud and Drive."
                     % build["previous_pick"]["hex"])
    for o in build["options"]:
        lines += ["Check (option %d): %s" % (o["n"], p) for p in o["problems"]]
    lines += ["Note: " + n for n in build["notes"]]
    return "\n".join(lines)


def run(a, ctx, state):
    """Does the work for one trigger; returns (status, message, notify) or raises Stop."""
    if a.pick:
        return "picked", use_pick(ctx, state, a.pick, a.replace_hand_edits), True

    waiting = send_waiting(ctx, state) if a.trigger == "schedule" else None
    if a.trigger == "schedule" and ctx.today.weekday() == 6:
        if waiting:
            return "sent", waiting, True
        raise Stop("idle", "Sunday: the schedule doesn't build on service day.", notify=False)

    try:
        ow = choose_ow(list_ows(), a.ow, ctx.today)
    except requests.RequestException as e:
        raise Stop("failed", "Title graphics: couldn't list the OWs in cju-media/OW (%s)" % e, code=1)
    build = state.get("build") or {}
    if not ow:
        if waiting:
            return "sent", waiting, True
        msg = "Title graphics: no OW %s in cju-media/OW." % ("named %s" % a.ow if a.ow else "for the coming Sunday")
        raise Stop("idle" if a.trigger == "schedule" else "skipped", msg, notify=a.trigger != "schedule")
    ow = {"name": ow["name"], "sha": ow["sha"], "download_url": ow["download_url"]}
    if build.get("ow", {}).get("sha") == ow["sha"] and a.trigger in ("upload", "schedule"):
        if waiting:
            return "sent", waiting, True
        raise Stop("idle" if a.trigger == "schedule" else "skipped",
                   "Title graphics for %s are already built from %s: %s" % (build["week"], ow["name"], PICKER_URL),
                   notify=a.trigger != "schedule")

    work = tempfile.mkdtemp(prefix="title-graphics-")
    try:
        pdf = os.path.join(work, ow["name"])
        r = requests.get(ow.pop("download_url"), timeout=120)
        r.raise_for_status()
        with open(pdf, "wb") as fh:
            fh.write(r.content)
        fields, notes = read_fields(pdf)
        new = build_options(ctx, ow, pdf, fields, notes, build)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    state["build"] = new
    clean_up(ctx, new["id"])
    return "built", build_message(new, (waiting + "\n\n") if waiting else ""), True


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--ow", default=os.environ.get("OW_FILENAME") or None, help="OW file name in cju-media/OW/OWs")
    ap.add_argument("--pick", default=os.environ.get("PICK") or None, help="option number, or '#RRGGBB'")
    ap.add_argument("--replace-hand-edits", action="store_true",
                    default=os.environ.get("REPLACE_HAND_EDITS", "").lower() == "true")
    ap.add_argument("--trigger", default=os.environ.get("TRIGGER", "manual"),
                    choices=["upload", "pick", "manual", "schedule"])
    ap.add_argument("--root", default=root_folder())
    ap.add_argument("--staging", default=staging_folder())
    ap.add_argument("--state", default=os.path.join(HERE, "title_graphics_state.json"))
    ap.add_argument("--status-dir", default=os.path.join(HERE, "status"))
    ap.add_argument("--queue-dir", default=os.path.join(REPO, "Utilities", "uploads_queue"))
    ap.add_argument("--message", default=os.path.join(HERE, ".message.txt"), help="iMessage text is written here")
    ap.add_argument("--today", help=argparse.SUPPRESS)          # tests: pretend it's this date
    ap.add_argument("--probe", action="store_true")
    a = ap.parse_args()
    ctx = Context(a)
    if os.path.exists(a.message):
        os.remove(a.message)

    def say(msg):
        print(msg)
        with open(a.message, "w", encoding="utf-8") as fh:
            fh.write(msg)

    if a.probe:
        ok, lines = probe(ctx.root, ctx.staging)
        say(("Title graphics: Studio Mini is ready." if ok else "Title graphics: Studio Mini needs setup:") +
            "\n" + "\n".join(lines))
        sys.exit(0 if ok else 1)

    try:
        state = json.load(open(a.state))
    except (OSError, ValueError):
        state = {}
    try:
        status, message, notify, code = run(a, ctx, state) + (0,)
    except Stop as s:
        status, message, notify, code = s.status, s.message, s.notify, s.code
    if status != "idle":                         # quiet schedule runs leave no trace (and no commit)
        state["last_run"] = {"at": ctx.now.isoformat(timespec="seconds"), "trigger": a.trigger,
                             "status": status, "message": message}
        with open(a.state, "w") as fh:
            json.dump(state, fh, indent=2)
            fh.write("\n")
    say(message) if notify else print(message)
    sys.exit(code)


if __name__ == "__main__":
    main()
