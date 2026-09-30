#!/usr/bin/env python3
"""Prepare this week's title graphics from the Order of Worship PDF.

Reads the OW PDF (e.g. 9.27.26_OW_Draft.pdf) and:
  * pulls the text fields (series, series name, title, date, preacher),
  * finds last week's Illustrator files to use as templates,
  * extracts the cover photo from page 1, upscales it, and saves it as
    <week>/Cover_<week>.png,
  * suggests three right-panel colors from the photo and draws them in
    panel-colors.png (next to week-data.txt) so one can be picked by eye,
  * prints the draft week-data.txt, or writes it with --write.

UpdateWeek.jsx (run in Illustrator) reads week-data.txt and does the rest.

  python3 prepare_week.py 9.27.26_OW_Draft.pdf
  python3 prepare_week.py 9.27.26_OW_Draft.pdf --color "#325673" --write
  python3 prepare_week.py --check

Needs pypdf and Pillow (pip3 install pypdf pillow).
"""

import argparse
import colorsys
import datetime
import os
import re
import subprocess
import sys
import time
import zoneinfo

try:
    from pypdf import PdfReader
    from PIL import Image, ImageDraw, ImageFilter, ImageFont
except ImportError:
    sys.exit("prepare_week.py needs pypdf and Pillow:  pip3 install pypdf pillow")

HERE = os.path.dirname(os.path.abspath(__file__))
ICLOUD_ROOT = os.path.expanduser(
    "~/Library/Mobile Documents/com~apple~CloudDocs/FCCLA/Worship and Sermon Series")
# The installed copy sits in a folder inside the root (next to "Past Weeks") with UpdateWeek.jsx,
# which reads week-data.txt from its own folder. The repo copy writes to that installed folder.
INSTALLED = os.path.isdir(os.path.join(os.path.dirname(HERE), "Past Weeks"))
DEFAULT_ROOT = os.path.dirname(HERE) if INSTALLED else ICLOUD_ROOT
DEFAULT_DATA = os.path.join(HERE if INSTALLED else os.path.join(ICLOUD_ROOT, "Scripts"), "week-data.txt")

MONTHS = ["January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]
MONTH_RE = "|".join(MONTHS)
TITLE_PREFIX_RE = r"(?:The\s+)?(?:Rev\.|Reverend|Dr\.|Pastor|Rabbi|Minister|Elder|Chaplain)"

# Photo frame on the left of the 1920x1080 graphic, in points (= pixels at 100%).
FRAME_W, FRAME_H = 934, 1080

REQUIRED_KEYS = ["root", "heading", "dateText", "preacher", "panelColor",
                 "imagePath", "outputFolder", "serviceTemplate", "serviceFolder", "serviceName",
                 "sermonTemplate", "sermonFolder", "sermonName"]
OPTIONAL_KEYS = ["timeZone", "photoShiftX", "photoShiftY"]
LEGACY_KEYS = ["series", "seriesName", "title"]          # the heading as three fields, before "heading"


# ---------------------------------------------------------------- PDF text

def week_name(d):
    """9/27/2026 -> '9-27-26' (the folder/file naming convention)."""
    return "%d-%d-%02d" % (d.month, d.day, d.year % 100)


def parse_week_name(name):
    m = re.fullmatch(r"(\d{1,2})-(\d{1,2})-(\d{2})", name)
    if not m:
        return None
    try:
        return datetime.date(2000 + int(m.group(3)), int(m.group(1)), int(m.group(2)))
    except ValueError:
        return None


# "27 September 2026", "September 27, 2026", "September 28th, 2025" (2025's OWs)
DAY = r"\d{1,2}(?:st|nd|rd|th)?"
DATE_LINE_RE = re.compile(r"\b(%s\s+(%s)\s+\d{4}|(%s)\s+%s,?\s+\d{4})\b" % (DAY, MONTH_RE, MONTH_RE, DAY), re.I)
def split_heading(lines):
    """Page 1's lines above the date -> the heading lines, in order, with a "Name ~ Title" line
    counted as two. The layouts seen so far:
        Fall Series 4 / Painting the Stars ~ An Anticipatory Universe
        Pentecost 11 / Another Kind of Freedom ~ Healing Division
        Lent 4 / The Only Thing More Powerful Than Hate is Love / Edge Walking
        Another Kind of Freedom / The God Who Sees Us
        Fulfilling The Dream For Freedom
        Life from Death / Two Ways / Eastertide 1                          (2025)"""
    return [p.strip() for l in lines for p in l.split("~") if p.strip()]


def heading_of(fields):
    """The heading lines of a week: "heading", or the older series / seriesName / title fields."""
    h = fields.get("heading")
    if isinstance(h, str):
        h = [p.strip() for p in h.split("|")]
    if h is None:
        h = [fields.get(k, "") for k in ("series", "seriesName", "title")]
    return [p.strip() for p in h if p and p.strip()]


def parse_pdf(pdf):
    """Returns (fields, warnings). Fields hold the raw values the graphics need."""
    reader = PdfReader(pdf)
    page1 = reader.pages[0].extract_text(extraction_mode="layout")
    lines = [re.sub(r"\s+", " ", l).strip() for l in page1.splitlines()]
    lines = [l for l in lines if l]
    f, warn = {}, []

    date_at = next((i for i, l in enumerate(lines) if DATE_LINE_RE.search(l)), None)
    heading = lines[:date_at] if date_at is not None else []
    f["heading"] = split_heading(heading)
    f["title"] = f["heading"][-1] if f["heading"] else ""
    if not f["heading"]:
        warn.append("no heading lines above the date on page 1")
    elif len(f["heading"]) > 4:
        warn.append("page 1 has %d heading lines; the graphic has room for about four" % len(f["heading"]))

    text1 = " ".join(lines)
    m = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(%s)\s+(\d{4})\b" % MONTH_RE, text1, re.I)
    if m:
        day, mon, year = int(m.group(1)), m.group(2).capitalize(), int(m.group(3))
    else:
        m = re.search(r"\b(%s)\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b" % MONTH_RE, text1, re.I)
        if m:
            mon, day, year = m.group(1).capitalize(), int(m.group(2)), int(m.group(3))
    if m:
        f["date"] = datetime.date(year, MONTHS.index(mon) + 1, day)
        f["dateText"] = "%s %d, %d" % (mon, day, year)
    else:
        warn.append("no date like '27 September 2026' on page 1")

    # The sermon row of the order of service: "Sermon   <title>   Rev. Laura Vail Fregin". Some weeks it's
    # a Reflection or Homily instead, has no title ("Sermon   Rev. Michael Lehman"), or names two people.
    # Read with the page layout kept, so a row ends at its line; a real Sermon row wins over a Reflection
    # (and a "Musical Reflection" is music, not the sermon).
    pages = [p.extract_text(extraction_mode="layout") for p in reader.pages[1:]]
    row = None
    for kind in ("Sermon", "Reflection", "Homily"):
        for t in pages:
            row = re.search(r"(?m)^\s*(?<!Musical )%s\s{2,}(?:(.+?)\s{2,})?(%s[^\n]*?)\s*$" % (kind, TITLE_PREFIX_RE), t)
            if row:
                break
        if row:
            break
    if not row:                                   # the plain-text search this started with
        for page in reader.pages[1:]:
            row = re.search(r"\bSermon\s{2,}(.+?)\s{2,}(%s\s*[^\n]+?)(?=\s{2,}|\n|$)" % TITLE_PREFIX_RE, page.extract_text())
            if row:
                break
    if row:
        f["preacher"] = re.sub(r"\s+", " ", row.group(2)).strip()
        row_title = re.sub(r"\s+", " ", row.group(1) or "").strip()
        if row_title and f.get("title") and row_title.lower() != f["title"].lower():
            warn.append("sermon row title '%s' differs from page-1 title '%s'" % (row_title, f["title"]))
    else:
        warn.append("no 'Sermon  <title>  Rev. <name>' row found; set preacher by hand")
    return f, warn


# ---------------------------------------------------------------- cover image

def extract_cover(pdf):
    """The cover photo = the largest page-1 image that isn't a wide banner (the church logo)."""
    best = None
    for im in PdfReader(pdf).pages[0].images:
        w, h = im.image.size
        if not 0.4 < w / h < 2.5:
            continue
        if best is None or w * h > best.size[0] * best.size[1]:
            best = im.image
    if best is None:
        raise SystemExit("no cover photo found on page 1 of %s" % pdf)
    return best


def upscale(img):
    """Upscale small covers so they still look sharp at 1.5x the 934x1080 frame (512x640 -> 3x)."""
    img = img.convert("RGB")
    w, h = img.size
    need = max(1.5 * FRAME_W / w, 1.5 * FRAME_H / h)
    k = min(4, max(1, int(need + 0.999)))
    if k > 1:
        img = img.resize((w * k, h * k), Image.LANCZOS).filter(ImageFilter.UnsharpMask(2, 60, 2))
    return img, k


# ---------------------------------------------------------------- panel colors

def luminance(rgb):
    def lin(c):
        c /= 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_with_white(rgb):
    return 1.05 / (luminance(rgb) + 0.05)


def to_hex(rgb):
    return "#%02X%02X%02X" % tuple(int(round(c)) for c in rgb)


def hue_name(rgb):
    h, s, v = colorsys.rgb_to_hsv(*(c / 255.0 for c in rgb))
    if s < 0.15:
        return "gray"
    names = [(15, "red"), (40, "orange"), (65, "gold"), (160, "green"), (195, "teal"),
             (245, "blue"), (275, "indigo"), (320, "purple"), (345, "pink"), (360, "red")]
    return next(n for limit, n in names if h * 360 < limit)


def tone(rgb, lightness=1.0, sat=1.15, min_contrast=3.0):
    """A photo color as a panel color: a touch more saturated, and never so light that the
    white text stops reading. Tuned on the Aug-Sep 2026 picks (#508421 #F29B0C #2C3377
    #853320 #05293D #AB6854 #325673): the photo's own family color lands within about
    delta-E 11 of what was chosen."""
    h, l, s = colorsys.rgb_to_hls(*(c / 255.0 for c in rgb))
    l, s = min(1.0, l * lightness), min(1.0, s * sat)

    def at(light):
        return tuple(c * 255 for c in colorsys.hls_to_rgb(h, light, s))
    if contrast_with_white(at(l)) >= min_contrast:
        return at(l)
    a, b = 0.02, l                        # darken until white text has enough contrast
    for _ in range(40):
        mid = (a + b) / 2
        if contrast_with_white(at(mid)) >= min_contrast:
            a = mid
        else:
            b = mid
    return at(a)


def suggest_colors(img, n=3):
    """Group the photo's colorful pixels into hue families (black sky and white stars are
    ignored, vivid pixels count most) and return one panel color per family, most prominent
    first. A dominant-color pick alone misses choices like the blue for the Andromeda cover,
    so each distinct family gets a slot; one-color photos get a deeper and a lighter variant.
    Returns [(label, hex)]."""
    im = img.convert("RGB")
    im.thumbnail((240, 240))
    bins = [[0.0, 0.0, 0.0, 0.0] for _ in range(24)]      # weight, r, g, b per 15 degrees of hue
    pixels = im.get_flattened_data() if hasattr(im, "get_flattened_data") else im.getdata()   # Pillow 12.1+
    for r, g, b in pixels:
        h, s, v = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
        if v < 0.15 or s < 0.15:
            continue
        w = s * s * v
        acc = bins[int(h * 24) % 24]
        acc[0] += w
        acc[1] += w * r
        acc[2] += w * g
        acc[3] += w * b
    total = sum(b[0] for b in bins)
    used, fams = set(), []
    while total and len(fams) < n:
        def window(i):
            return [(i + k) % 24 for k in (-1, 0, 1) if (i + k) % 24 not in used]
        cands = [i for i in range(24) if i not in used]
        if not cands:
            break
        best = max(cands, key=lambda i: sum(bins[j][0] for j in window(i)))
        members = window(best)
        wsum = sum(bins[j][0] for j in members)
        if wsum < 0.03 * total:
            break
        fams.append((wsum / total, tuple(sum(bins[j][c] for j in members) / wsum for c in (1, 2, 3))))
        used |= {(best + k) % 24 for k in (-2, -1, 0, 1, 2)}   # next pick must be a different hue
    if not fams:                                                # black-and-white photo
        fams = [(1.0, im.resize((1, 1), Image.BOX).getpixel((0, 0)))]
    picks = [("%s, %d%% of the photo's color" % (hue_name(c), share * 100), to_hex(tone(c)))
             for share, c in fams]
    base = fams[0][1]
    for label, k in (("deeper %s", 0.7), ("lighter %s", 1.3)):
        if len(picks) >= n:
            break
        hexc = to_hex(tone(base, lightness=k))
        if hexc not in (p[1] for p in picks):
            picks.append((label % hue_name(base), hexc))
    return picks[:n]


def font(size):
    for p in ("/System/Library/Fonts/Supplemental/Georgia.ttf",
              "/System/Library/Fonts/Supplemental/Times New Roman.ttf"):
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default(size=size)


def cover_crop(img, w, h):
    k = max(w / img.width, h / img.height)
    im = img.resize((max(w, int(img.width * k + 0.5)), max(h, int(img.height * k + 0.5))), Image.LANCZOS)
    x, y = (im.width - w) // 2, (im.height - h) // 2
    return im.crop((x, y, x + w, y + h))


def color_preview(img, picks, fields, path):
    """A small mock of the graphic for each suggested color, side by side."""
    W, H, pad = 480, 270, 16
    sheet = Image.new("RGB", (len(picks) * (W + pad) + pad, H + 70), "white")
    photo = cover_crop(img, W * FRAME_W // 1920, H)
    d = ImageDraw.Draw(sheet)
    lines = [(t, 17) for t in heading_of(fields)] + [(fields.get("dateText", "").upper(), 11)]
    for i, (_, hexc) in enumerate(picks):
        x0 = pad + i * (W + pad)
        sheet.paste(photo, (x0, pad))
        d.rectangle([x0 + photo.width, pad, x0 + W - 1, pad + H - 1], fill=hexc)
        cx, y = x0 + photo.width + (W - photo.width) // 2, pad + 40
        for text, size in lines:
            f = font(size)
            tw = d.textlength(text, font=f)
            d.text((cx - tw / 2, y), text, font=f, fill="white")
            y += size + 16
        d.text((x0, pad + H + 10), "%d   %s" % (i + 1, hexc), font=font(22), fill="black")
    sheet.save(path)


# ---------------------------------------------------------------- templates (last week's files)

def ensure_local(path, timeout=180):
    """iCloud may keep old weeks only in the cloud; download before Illustrator opens them."""
    try:
        dataless = os.stat(path).st_flags & 0x40000000        # SF_DATALESS
    except (OSError, AttributeError):
        return
    if not dataless:
        return
    print("downloading from iCloud: %s" % os.path.basename(path), file=sys.stderr)
    subprocess.run(["brctl", "download", path], capture_output=True)
    end = time.time() + timeout
    while time.time() < end and os.stat(path).st_flags & 0x40000000:
        time.sleep(2)


def find_templates(root, date):
    """Newest week folder before `date` (in <root> or <root>/Past Weeks) with both .ai files.
    Returns (week, service_path, sermon_path), paths relative to root."""
    weeks = []
    for base in ("", "Past Weeks"):
        d = os.path.join(root, base)
        if not os.path.isdir(d):
            continue
        for name in os.listdir(d):
            wd = parse_week_name(name)
            if wd and wd < date and os.path.isdir(os.path.join(d, name)):
                weeks.append((wd, os.path.join(base, name) if base else name, name))
    weeks.sort(reverse=True)
    for wd, rel, name in weeks:
        svc = pick_ai(root, rel, "Worship Service", ["Service Title_" + name + ".ai"])
        ser = pick_ai(root, rel, "Sermon Series",
                      ["Sermon Title_" + name + ".ai", "SermonSeries_" + name + ".ai"])
        if svc and ser:
            return name, wd, svc, ser
    return None, None, None, None


def pick_ai(root, rel, sub_prefix, names):
    """The .ai in <week>/<sub_prefix ...>/. Older weeks vary: sermon files named 'Service Title_...',
    'SermonSeries_...', or a folder name with a trailing space."""
    week_dir = os.path.join(root, rel)
    subs = [s for s in sorted(os.listdir(week_dir))
            if s.startswith(sub_prefix) and os.path.isdir(os.path.join(week_dir, s))]
    for sub in subs:
        ais = sorted(n for n in os.listdir(os.path.join(week_dir, sub))
                     if n.lower().endswith(".ai") and not n.startswith("."))
        for n in names:
            if n in ais:
                return os.path.join(rel, sub, n)
        if ais:
            return os.path.join(rel, sub, ais[0])
    return None


# ---------------------------------------------------------------- week-data.txt

def tilde(path):
    home = os.path.expanduser("~")
    return "~" + path[len(home):] if path.startswith(home + "/") else path


def build_data(root, out_root, f, color, week, svc, ser, image_rel, pdf, output_dir=None):
    out_rel = tilde(output_dir) if output_dir else week if out_root == root else tilde(os.path.join(out_root, week))
    tz = datetime.datetime(f["date"].year, f["date"].month, f["date"].day, 11,
                           tzinfo=zoneinfo.ZoneInfo("America/Los_Angeles")).tzname()
    rows = [
        ("root", tilde(root)),
        ("heading", " | ".join(heading_of(f))),
        ("dateText", f.get("dateText", "")),
        ("preacher", f.get("preacher", "")),
        ("panelColor", color or "CHOOSE"),
        ("timeZone", tz),
        ("imagePath", image_rel),
        ("photoShiftX", "0"),
        ("photoShiftY", "0"),
        ("outputFolder", out_rel),
        ("serviceTemplate", svc or "NOT FOUND"),
        ("serviceFolder", "Worship Service " + week),
        ("serviceName", "Service Title_" + week),
        ("sermonTemplate", ser or "NOT FOUND"),
        ("sermonFolder", "Sermon Series " + week),
        ("sermonName", "Sermon Title_" + week),
    ]
    head = ["# week-data.txt for UpdateWeek.jsx: week of %s, from %s" % (week, os.path.basename(pdf)),
            "# key = value, one per line. Relative paths are relative to root (outputs: to outputFolder).",
            "# photoShiftX/Y nudge the photo in points (+X right, +Y up)."]
    return "\n".join(head + ["%-16s= %s" % (k, v) for k, v in rows]) + "\n"


def read_data(path):
    d = {}
    with open(path, encoding="utf-8-sig") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            d[k.strip()] = v.strip()
    return d


def resolve(p, root):
    p = os.path.expanduser(p)
    return p if os.path.isabs(p) else os.path.join(root, p)


def check(path):
    """Validate week-data.txt the same way UpdateWeek.jsx will. Returns a list of problems."""
    if not os.path.exists(path):
        return ["%s does not exist" % path]
    d, bad = read_data(path), []
    for k in REQUIRED_KEYS:
        if not d.get(k) and not (k == "heading" and heading_of(d)):
            bad.append("missing %s" % k)
    for k in d:
        if k not in REQUIRED_KEYS + OPTIONAL_KEYS + LEGACY_KEYS:
            bad.append("unknown key %s (typo?)" % k)
    if bad:
        return bad
    root = os.path.expanduser(d["root"])
    if not os.path.isdir(root):
        bad.append("root folder not found: %s" % root)
    if not re.fullmatch(r"#[0-9A-Fa-f]{6}", d["panelColor"]):
        bad.append("panelColor must look like #325673, got %r" % d["panelColor"])
    m = re.fullmatch(r"(%s) (\d{1,2}), (\d{4})" % MONTH_RE, d["dateText"])
    if not m:
        bad.append("dateText must look like 'September 27, 2026', got %r" % d["dateText"])
    else:
        date = datetime.date(int(m.group(3)), MONTHS.index(m.group(1)) + 1, int(m.group(2)))
        week = week_name(date)
        if date.weekday() != 6:
            bad.append("%s is not a Sunday" % d["dateText"])
        for k in ("serviceFolder", "serviceName", "sermonFolder", "sermonName"):
            if not d[k].endswith(week):
                bad.append("%s %r should end with %s" % (k, d[k], week))
        if os.path.basename(d["outputFolder"].rstrip("/")) != week:
            bad.append("outputFolder %r should be the %s week folder" % (d["outputFolder"], week))
    if any("~" in h for h in heading_of(d)):
        bad.append("heading still contains '~' (a 'Name ~ Title' line is two heading lines)")
    if d.get("timeZone", "PDT") not in ("PDT", "PST"):
        bad.append("timeZone must be PDT or PST")
    for k in ("photoShiftX", "photoShiftY"):
        if not re.fullmatch(r"-?\d+(\.\d+)?", d.get(k, "0")):
            bad.append("%s must be a number of points" % k)
    for k in ("serviceTemplate", "sermonTemplate", "imagePath"):
        p = resolve(d[k], root)
        if not os.path.exists(p):
            bad.append("%s not found: %s" % (k, p))
    return bad


# ---------------------------------------------------------------- main

class PrepareError(Exception):
    pass


def parse_date_text(s):
    m = re.fullmatch(r"(%s) (\d{1,2}), (\d{4})" % MONTH_RE, s or "")
    return datetime.date(int(m.group(3)), MONTHS.index(m.group(1)) + 1, int(m.group(2))) if m else None


def prepare(pdf, root=DEFAULT_ROOT, out_root=None, data_path=DEFAULT_DATA, color=None, fields=None,
            sets=(), write=False, output_dir=None):
    """Everything the command line does, as a function (auto_build.py calls it).
    fields: text fields already read from the PDF (default: parse it here).
    output_dir: where UpdateWeek.jsx saves the graphics (default: the week folder).
    color: '#RRGGBB' or '1'-'3' for a suggestion. Returns a dict describing the week;
    raises PrepareError if it can't be prepared."""
    root = os.path.abspath(os.path.expanduser(root))
    out_root = os.path.abspath(os.path.expanduser(out_root)) if out_root else root
    f, warn = (dict(fields), []) if fields is not None else parse_pdf(pdf)
    for kv in sets:
        k, _, v = kv.partition("=")
        f[k.strip()] = v.strip()
    f["heading"] = heading_of(f)
    f["date"] = parse_date_text(f.get("dateText"))
    if not f["date"]:
        raise PrepareError("couldn't read the service date; pass --set dateText='September 27, 2026'")
    week = week_name(f["date"])
    if f["date"].weekday() != 6:
        warn.append("%s is not a Sunday; check the date" % f["dateText"])

    prev, prev_date, svc, ser = find_templates(root, f["date"])
    if not prev:
        warn.append("no earlier week folder has both a Service Title and a Sermon .ai")
    else:
        if (f["date"] - prev_date).days != 7:
            warn.append("newest earlier week is %s (%d days before), not last Sunday; "
                        "check it's the right template" % (prev, (f["date"] - prev_date).days))
        for p in (svc, ser):
            ensure_local(os.path.join(root, p))

    week_dir = os.path.join(out_root, week)
    for sub in ("", "Worship Service " + week, "Sermon Series " + week):
        os.makedirs(os.path.join(week_dir, sub), exist_ok=True)
    raw = extract_cover(pdf)
    cover, k = upscale(raw)
    cover_path = os.path.join(week_dir, "Cover_%s.png" % week)
    cover.save(cover_path)
    image_rel = os.path.relpath(cover_path, root) if out_root == root else cover_path

    picks = suggest_colors(raw)
    data_path = os.path.abspath(os.path.expanduser(data_path))
    os.makedirs(os.path.dirname(data_path), exist_ok=True)
    preview = os.path.join(os.path.dirname(data_path), "panel-colors.png")
    color_preview(cover, picks, f, preview)

    if color and re.fullmatch(r"[1-9]", color):
        if int(color) > len(picks):
            raise PrepareError("there are only %d suggestions" % len(picks))
        color = picks[int(color) - 1][1]
    if color and not re.fullmatch(r"#[0-9A-Fa-f]{6}", color):
        raise PrepareError("the color must look like #325673, not %r" % color)
    color = color and color.upper()
    data = build_data(root, out_root, f, color, week, svc, ser, tilde(image_rel), pdf, output_dir)

    result = {"week": week, "week_dir": week_dir, "fields": f, "warnings": warn, "prev": prev,
              "cover": cover, "cover_path": cover_path, "raw_size": raw.size, "factor": k,
              "picks": picks, "preview": preview, "color": color, "data": data,
              "data_path": data_path, "problems": None}
    if write:
        if not color:
            raise PrepareError("writing week-data.txt needs a color (pick a suggestion, or any hex)")
        with open(data_path, "w", encoding="utf-8") as fh:
            fh.write(data)
        result["problems"] = check(data_path)
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("pdf", nargs="?", help="Order of Worship PDF")
    ap.add_argument("--color", help="chosen panel color, e.g. '#325673' (or 1/2/3 = a suggestion)")
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                    help="override a field the PDF parse got wrong (repeatable)")
    ap.add_argument("--write", action="store_true", help="write week-data.txt (needs --color)")
    ap.add_argument("--data", default=DEFAULT_DATA, help="week-data.txt path (default: %(default)s)")
    ap.add_argument("--root", default=DEFAULT_ROOT, help="Worship and Sermon Series folder")
    ap.add_argument("--output-root", help="where the week folder is made (default: root; tests use a scratch dir)")
    ap.add_argument("--check", action="store_true", help="validate week-data.txt and exit")
    a = ap.parse_args()

    if a.check:
        bad = check(a.data)
        print("\n".join("PROBLEM: " + b for b in bad) or "week-data.txt OK: %s" % a.data)
        sys.exit(1 if bad else 0)
    if not a.pdf:
        ap.error("give the Order of Worship PDF")
    try:
        r = prepare(a.pdf, a.root, a.output_root, a.data, a.color, sets=a.set, write=a.write)
    except PrepareError as e:
        sys.exit(str(e))

    cover, (rw, rh) = r["cover"], r["raw_size"]
    print("week:            %s (%s)" % (r["week"], r["fields"].get("dateText")))
    print("templates from:  %s" % (r["prev"] or "NOT FOUND"))
    print("cover:           %s  (%dx%d from %dx%d, %dx)" % (r["cover_path"], cover.width, cover.height,
                                                          rw, rh, r["factor"]))
    print("panel colors:    (preview: %s)" % r["preview"])
    for i, (label, hexc) in enumerate(r["picks"]):
        print("  %d  %s   %s" % (i + 1, hexc, label))
    for w in r["warnings"]:
        print("WARNING: " + w)
    print("\n----- week-data.txt -----\n" + r["data"] + "-------------------------")
    if a.write:
        print("wrote %s" % r["data_path"])
        for b in r["problems"]:
            print("PROBLEM: " + b)
        sys.exit(1 if r["problems"] else 0)


if __name__ == "__main__":
    main()
