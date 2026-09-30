#!/usr/bin/env python3
"""End-to-end check of the weekly title-graphics workflow against the 9-27-26 graphics made by hand.

  prepare_week.py (9.27.26 OW PDF, panel #325673) -> week-data.txt -> UpdateWeek.jsx in Illustrator
  (templates: last week's 9-20-26 files) -> compare with the hand-made 9-27-26 .ai/.jpg.

Outputs go to fccla/test/out/, never into the iCloud week folders. Needs Illustrator running on this Mac.

  python3 fccla/test/test_9_27.py [path/to/9.27.26_OW_Draft.pdf]

Known, intentional differences in the hand-made version: the photo sits a little higher and the
preacher line was nudged up, so those areas are reported but not held to the tolerance.
"""

import os
import re
import shutil
import subprocess
import sys

from PIL import Image, ImageChops, ImageFilter, ImageStat
from pypdf import PdfReader

HERE = os.path.dirname(os.path.abspath(__file__))
FCCLA = os.path.dirname(HERE)
sys.path.insert(0, FCCLA)
from illustrator import run_updateweek  # noqa: E402
OUT = os.path.join(HERE, "out")
ROOT = os.path.expanduser("~/Library/Mobile Documents/com~apple~CloudDocs/FCCLA/Worship and Sermon Series")
PDF = os.path.expanduser("~/Documents/Programming/OW/OWs/9.27.26_OW_Draft.pdf")
WEEK, COLOR = "9-27-26", "#325673"
JOBS = [("Worship Service", "Service Title"), ("Sermon Series", "Sermon Title")]
# Right-panel crop (pixels) and the preacher rows (from the top) left out of the strict comparison.
PANEL = (940, 0, 1920, 1080)
PREACHER_ROWS = (640, 740)
# The panel is compared in 60px bands after a light blur, so JPEG noise (the hand-made JPGs were
# exported at a lower quality) stays near 1-2 while a line of text moved by 4px scores about 10.
BAND = 60
MAX_BAND_DIFF = 4.0


def run(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if r.returncode:
        sys.exit("FAILED: %s\n%s%s" % (" ".join(cmd), r.stdout, r.stderr))
    return r.stdout


def expected_dir():
    for base in (ROOT, os.path.join(ROOT, "Past Weeks")):
        if os.path.isdir(os.path.join(base, WEEK)):
            return os.path.join(base, WEEK)
    sys.exit("can't find the hand-made %s folder" % WEEK)


def ai_text(path):
    """Text of an .ai saved with PDF compatibility, as a list of words."""
    text = " ".join(p.extract_text() or "" for p in PdfReader(path).pages)
    return re.findall(r"[A-Za-z0-9.,]+", text)


def diff(a, b, box):
    """Mean absolute difference per channel (0-255) inside box."""
    a, b = Image.open(a).convert("RGB").crop(box), Image.open(b).convert("RGB").crop(box)
    return sum(ImageStat.Stat(ImageChops.difference(a, b)).mean) / 3


def band_diffs(a, b, box):
    """{top row: difference} for each BAND-high strip of box, after a 2px blur."""
    a, b = (Image.open(p).convert("RGB").crop(box).filter(ImageFilter.GaussianBlur(2)) for p in (a, b))
    d = ImageChops.difference(a, b).convert("L")
    return {box[1] + y: ImageStat.Stat(d.crop((0, y, d.width, y + BAND))).mean[0]
            for y in range(0, d.height, BAND)}


def mean_color(path, box):
    return tuple(round(c) for c in ImageStat.Stat(Image.open(path).convert("RGB").crop(box)).mean)


def main():
    pdf = sys.argv[1] if len(sys.argv) > 1 else PDF
    if os.path.isdir(OUT):
        shutil.rmtree(OUT)
    os.makedirs(OUT)
    data = os.path.join(OUT, "week-data.txt")

    print("1. prepare_week.py")
    print(run([sys.executable, os.path.join(FCCLA, "prepare_week.py"), pdf, "--output-root", OUT,
               "--data", data, "--color", COLOR, "--write"]))

    print("2. UpdateWeek.jsx in Illustrator")
    log = run_updateweek(data)
    print(log)

    print("3. compare with the hand-made files")
    exp_root, failures = expected_dir(), []
    if "!!" in log:
        failures.append("the log reports problems")
    for folder, prefix in JOBS:
        name = "%s_%s" % (prefix, WEEK)
        got_ai = os.path.join(OUT, WEEK, "%s %s" % (folder, WEEK), name + ".ai")
        got_jpg = got_ai[:-3] + ".jpg"
        exp_ai = os.path.join(exp_root, "%s %s" % (folder, WEEK), name + ".ai")
        exp_jpg = exp_ai[:-3] + ".jpg"
        for p in (got_ai, got_jpg):
            if not os.path.exists(p):
                failures.append("missing " + p)
        if failures and not os.path.exists(got_jpg):
            continue

        words_got, words_exp = ai_text(got_ai), ai_text(exp_ai)
        same_text = words_got == words_exp
        size = Image.open(got_jpg).size
        bands = band_diffs(got_jpg, exp_jpg, PANEL)
        in_preacher = [y for y in bands if prefix == "Sermon Title" and PREACHER_ROWS[0] <= y + BAND / 2 < PREACHER_ROWS[1]]
        strict = {y: v for y, v in bands.items() if y not in in_preacher}
        worst = max(strict, key=strict.get)
        photo = diff(got_jpg, exp_jpg, (0, 0, 930, 1080))
        swatch = (960, 620, 1010, 700)               # plain panel between the date and the lower bar
        c_got, c_exp = mean_color(got_jpg, swatch), mean_color(exp_jpg, swatch)
        print("%s\n  text identical to hand-made: %s\n  jpg size %dx%d"
              "\n  right panel: worst band (rows %d-%d) %.1f, limit %.1f"
              "\n  panel swatch %s vs %s" % (name, same_text, size[0], size[1], worst, worst + BAND,
                                            strict[worst], MAX_BAND_DIFF, c_got, c_exp))
        print("  not held to the limit (moved by hand): photo %.1f%s" % (
            photo, "".join(", preacher rows %d-%d %.1f" % (y, y + BAND, bands[y]) for y in in_preacher)))
        if not same_text:
            print("  got:      %s\n  expected: %s" % (" ".join(words_got), " ".join(words_exp)))
            failures.append(name + ": text differs")
        if size != (1920, 1080):
            failures.append(name + ": jpg is %dx%d" % size)
        if strict[worst] > MAX_BAND_DIFF:
            failures.append(name + ": right panel differs around rows %d-%d (%.1f)" % (worst, worst + BAND, strict[worst]))
        if max(abs(a - b) for a, b in zip(c_got, c_exp)) > 4:
            failures.append(name + ": panel color differs")

    print("\nPASS" if not failures else "\nFAIL:\n  " + "\n  ".join(failures))
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
