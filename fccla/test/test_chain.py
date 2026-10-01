#!/usr/bin/env python3
"""Two made-up weeks chained onto the script's own 9-27 output, to cover what 9-27 alone doesn't:

  week A (10-4-26): template = test_9_27.py's output (clipped, embedded photo), a landscape photo,
                    a title long enough to be split over two lines, a new series name and number, a preacher without "Rev.",
                    PST, and a photo nudge the photo can't allow.
  week B (10-11-26): template = week A's output. A short title must come back at full size, the
                    preacher is found by position, and a large nudge gets limited.

Run test_9_27.py first. Outputs go to fccla/test/out/chain/. Needs Illustrator running.
"""

import os
import sys

from test_9_27 import FCCLA, OUT, ROOT, ai_text, run, run_updateweek

OW = os.path.expanduser("~/Documents/Programming/OW/OWs")
CHAIN = os.path.join(OUT, "chain")
BASE = os.path.join(OUT, "9-27-26")

WEEKS = [
    ("10-4-26", "October 4, 2026", {
        "series": "Advent Series 1", "seriesName": "Stories of Light",
        "title": "The Heavens Declare the Glory of God and the Firmament Proclaims",
        "preacher": "Sarah Fuhrmeister", "panelColor": "#853320", "photoShiftY": "40",
        "cover": "9.20.26_OW_Draft.pdf", "from": (BASE, "9-27-26")}, [
        "(kept last week's mask)", ("time zone: 2 times -> PST", 1), "heading line 2: Painting the Stars -> Stories of Light",
        "moved 2 rules beside the first heading line", ("preacher: Rev. Laura Vail Fregin -> Sarah Fuhrmeister", 1),
        "!! photo shift limited to 0,0", "split the title over two lines"]),
    ("10-11-26", "October 11, 2026", {
        "series": "Advent Series 2", "seriesName": "Stories of Light", "title": "Hope",
        "preacher": "Rev. Michael Lehman", "panelColor": "#05293D", "photoShiftY": "5000",
        "cover": "9.13.26_OW_Draft.pdf", "from": (os.path.join(CHAIN, "10-4-26"), "10-4-26")}, [
        ("preacher: Sarah Fuhrmeister -> Rev. Michael Lehman", 1), "!! photo shift limited to 0,15"]),
]


def main():
    if not os.path.exists(os.path.join(BASE, "Sermon Series 9-27-26", "Sermon Title_9-27-26.ai")):
        sys.exit("run test_9_27.py first")
    os.makedirs(CHAIN, exist_ok=True)
    failures = []
    for week, date, v, expect in WEEKS:
        # the cover photo, extracted and upscaled the usual way
        pdf = os.path.join(OW, v["cover"])
        run([sys.executable, os.path.join(FCCLA, "prepare_week.py"), pdf, "--output-root",
             os.path.join(CHAIN, "covers"), "--data", os.path.join(CHAIN, "covers", "week-data.txt")])
        src = v["cover"].split("_")[0].replace(".", "-")
        cover = os.path.join(CHAIN, "covers", src, "Cover_%s.png" % src)
        prev_dir, prev = v["from"]
        data = os.path.join(CHAIN, week + ".txt")
        with open(data, "w") as fh:
            fh.write("\n".join("%s = %s" % kv for kv in [
                ("root", ROOT), ("series", v["series"]), ("seriesName", v["seriesName"]), ("title", v["title"]),
                ("dateText", date), ("preacher", v["preacher"]), ("panelColor", v["panelColor"]),
                ("timeZone", "PST"), ("imagePath", cover), ("photoShiftY", v["photoShiftY"]),
                ("outputFolder", os.path.join(CHAIN, week)),
                ("serviceTemplate", os.path.join(prev_dir, "Worship Service " + prev, "Service Title_%s.ai" % prev)),
                ("serviceFolder", "Worship Service " + week), ("serviceName", "Service Title_" + week),
                ("sermonTemplate", os.path.join(prev_dir, "Sermon Series " + prev, "Sermon Title_%s.ai" % prev)),
                ("sermonFolder", "Sermon Series " + week), ("sermonName", "Sermon Title_" + week)]) + "\n")
        log = run_updateweek(data)
        print("######## " + week + "\n" + "\n".join(l for l in log.splitlines() if "image candidate" not in l))

        expect = [e if isinstance(e, tuple) else (e, 2) for e in expect]   # (text, times): 2 = both graphics
        for e, times in expect:
            if log.count(e) != times:
                failures.append("%s: log should say '%s' %d time(s), not %d" % (week, e, times, log.count(e)))
        if week == "10-11-26" and ("shrank" in log.replace("shrank the first heading line", "") or "split" in log):
            failures.append("10-11-26: the short title should be back on one line at full size")
        allowed = [e for e, _ in expect if e.startswith("!!")]
        for line in log.splitlines():
            if line.startswith("  !! ") and not any(a[3:] in line for a in allowed):
                failures.append("%s: unexpected problem: %s" % (week, line.strip()))
        sermon_ai = os.path.join(CHAIN, week, "Sermon Series " + week, "Sermon Title_%s.ai" % week)
        words = " ".join(ai_text(sermon_ai)).upper()               # small caps extract as upper case
        for want in (v["title"], v["preacher"], date, v["series"]):
            if want.upper() not in words:
                failures.append("%s: sermon .ai text is missing '%s'" % (week, want))
    print("\nPASS" if not failures else "\nFAIL:\n  " + "\n  ".join(failures))
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
