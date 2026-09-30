#!/usr/bin/env python3
"""The heading through a year of page-1 layouts, chained week to week from test_9_27.py's output:

  Fall Series 4 (3 lines) -> 6/7 name + title (2) -> 1/18 title only (1) -> 12/24 Christmas Eve (1)
  -> 3/15 Lent 4 + a long name + title (3) -> 8/16 Pentecost 11 (3) -> 8/10/25 four lines (4)
  -> Fall Series 5 (3)

Each week's text comes from its real OW via prepare_week.py (cover photo included), so this covers
lines being added and removed, the block re-centering, the rules following the first line, and a
one-line heading sitting halfway between the header bar and the date (measured in Illustrator).
Writes fccla/test/out/formats/ and a review sheet, formats.jpg. Run test_9_27.py first; needs
Illustrator and the OW PDFs in ~/Documents/Programming/OW/OWs.
"""

import os
import sys

from PIL import Image, ImageDraw, ImageFont

from test_9_27 import FCCLA, OUT, ROOT, ai_text, run, run_updateweek

sys.path.insert(0, FCCLA)
import prepare_week as pw  # noqa: E402
from illustrator import do_javascript  # noqa: E402

# Read-only: how far the heading's visible text sits from halfway between the header bar and the date.
MIDDLE_JS = r"""
(function () {
    function glyphs(tf) {
        var d = tf.duplicate(), g = d.createOutline(), top = -1e9, bottom = 1e9;
        (function walk(c) {
            for (var i = 0; i < c.pageItems.length; i++)
                if (c.pageItems[i].typename == "GroupItem") walk(c.pageItems[i]);
                else { var b = c.pageItems[i].geometricBounds; top = Math.max(top, b[1]); bottom = Math.min(bottom, b[3]); }
        })(g);
        g.remove();
        return (top + bottom) / 2;
    }
    var prev = app.userInteractionLevel;
    app.userInteractionLevel = UserInteractionLevel.DONTDISPLAYALERTS;
    var doc = app.open(new File("%s")), header = null, date = null, head = null, most = 0, out;
    for (var i = 0; i < doc.textFrames.length; i++) {
        var tf = doc.textFrames[i], t = tf.contents.replace(/^\s+|\s+$/g, ""), b = tf.geometricBounds;
        if (tf.hidden || t == "") continue;
        if (/^(Sunday|Sermon)$/.test(t)) header = tf;
        else if (/^[A-Z][a-z]+ \d{1,2}, \d{4}$/.test(t)) date = tf;
        else if (b[0] > 900 && b[1] > 600 && t.length > most) { most = t.length; head = tf; }
    }
    out = (header.geometricBounds[3] + date.geometricBounds[1]) / 2 - glyphs(head);
    doc.close(SaveOptions.DONOTSAVECHANGES);
    app.userInteractionLevel = prev;
    return out;
})();
"""


def middle_offset(ai):
    return float(do_javascript(MIDDLE_JS % ai.replace('"', '\\"'), timeout=300))

OW = os.path.expanduser("~/Documents/Programming/OW/OWs")
DIR = os.path.join(OUT, "formats")
WEEKS = [   # (name, OW, heading lines expected, change in line count for the log)
    ("june", "6.7.26 OW.pdf", 2, "heading: 3 lines -> 2"),
    ("mlk", "1.18.26_OW.pdf", 1, "heading: 2 lines -> 1"),
    ("christmas", "12.24.25 8pm.pdf", 1, None),
    ("lent", "3.15.26 OW.pdf", 3, "heading: 1 line -> 3"),
    ("pentecost", "8.16.26_OW_Draft.pdf", 3, None),
    ("four", "8.10.25_OW.pdf", 4, "heading: 3 lines -> 4"),
    ("fall", "9.27.26_OW_Draft.pdf", 3, "heading: 4 lines -> 3"),
]


def main():
    prev_dir, prev = os.path.join(OUT, "9-27-26"), "9-27-26"
    if not os.path.exists(os.path.join(prev_dir, "Sermon Series 9-27-26", "Sermon Title_9-27-26.ai")):
        sys.exit("run test_9_27.py first")
    os.makedirs(DIR, exist_ok=True)
    failures, shots = [], [("9/27 (start)", prev_dir, prev)]
    for name, ow, n_lines, change in WEEKS:
        pdf = os.path.join(OW, ow)
        f, _ = pw.parse_pdf(pdf)
        if name == "fall":
            f["heading"][0] = "Fall Series 5"              # not the same text as the starting week
        if len(f["heading"]) != n_lines:
            failures.append("%s: expected %d heading lines, read %r" % (name, n_lines, f["heading"]))
        cover_dir = os.path.join(DIR, "covers", name)
        os.makedirs(cover_dir, exist_ok=True)
        cover = os.path.join(cover_dir, "cover.png")
        pw.upscale(pw.extract_cover(pdf))[0].save(cover)
        data = os.path.join(DIR, name + ".txt")
        with open(data, "w") as fh:
            fh.write("\n".join("%s = %s" % kv for kv in [
                ("root", ROOT), ("heading", " | ".join(f["heading"])), ("dateText", f["dateText"]),
                ("preacher", f.get("preacher") or "Rev. Michael Lehman"), ("panelColor", "#385261"),
                ("timeZone", "PDT"), ("imagePath", cover), ("outputFolder", os.path.join(DIR, name)),
                ("serviceTemplate", os.path.join(prev_dir, "Worship Service " + prev, "Service Title_%s.ai" % prev)),
                ("serviceFolder", "Worship Service " + name), ("serviceName", "Service Title_" + name),
                ("sermonTemplate", os.path.join(prev_dir, "Sermon Series " + prev, "Sermon Title_%s.ai" % prev)),
                ("sermonFolder", "Sermon Series " + name), ("sermonName", "Sermon Title_" + name)]) + "\n")
        log = run_updateweek(data)
        print("######## %s: %s\n%s" % (name, " / ".join(f["heading"]),
                                      "\n".join(l for l in log.splitlines() if "image candidate" not in l and "template:" not in l)))
        if change and log.count(change) != 2:
            failures.append("%s: log should say '%s' for both graphics" % (name, change))
        if change and log.count("to keep it centered") + log.count("to center it between") != 2:
            failures.append("%s: the heading should have been re-centered" % name)
        if n_lines == 1:
            off = middle_offset(os.path.join(DIR, name, "Sermon Series " + name, "Sermon Title_%s.ai" % name))
            print("  one-line heading: %.1fpt from the middle between the header bar and the date" % off)
            if abs(off) > 3:
                failures.append("%s: the one-line heading is %.1fpt off the middle" % (name, off))
        for line in log.splitlines():
            if line.startswith("  !! ") and "had to shrink" not in line:
                failures.append("%s: %s" % (name, line.strip()))
        words = " ".join(ai_text(os.path.join(DIR, name, "Sermon Series " + name, "Sermon Title_%s.ai" % name))).upper()
        for h in f["heading"]:
            if " ".join(h.replace("…", " ").split()).upper() not in words.replace("…", " "):
                failures.append("%s: sermon .ai text is missing %r" % (name, h))
        shots.append((name + ": " + " / ".join(f["heading"]), os.path.join(DIR, name), name))
        prev_dir, prev = os.path.join(DIR, name), name

    # a sheet to look at: Service and Sermon Title for every week
    W, H, pad, cap = 960, 540, 20, 50
    sheet = Image.new("RGB", (2 * W + 3 * pad, len(shots) * (H + cap + pad) + pad), "white")
    d = ImageDraw.Draw(sheet)
    font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial Bold.ttf", 26)
    y = pad
    for label, folder, week in shots:
        d.text((pad, y + 8), label, fill="#1d2b36", font=font)
        for i, (sub, nm) in enumerate((("Worship Service", "Service Title"), ("Sermon Series", "Sermon Title"))):
            jpg = os.path.join(folder, "%s %s" % (sub, week), "%s_%s.jpg" % (nm, week))
            sheet.paste(Image.open(jpg).convert("RGB").resize((W, H), Image.LANCZOS), (pad + i * (W + pad), y + cap))
        y += H + cap + pad
    sheet.save(os.path.join(DIR, "formats.jpg"), quality=85)
    print("\nreview sheet: " + os.path.join(DIR, "formats.jpg"))
    print("\nPASS" if not failures else "\nFAIL:\n  " + "\n  ".join(failures))
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
