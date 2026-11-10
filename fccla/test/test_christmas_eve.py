#!/usr/bin/env python3
"""Christmas Eve 2026: three timed services (5:30pm, 8pm, 11pm, a Thursday) and the Sunday after,
their four OWs uploaded one evening. No Illustrator, no network: prepare_week.py runs for real on
a scratch "iCloud" folder, and a stand-in for UpdateWeek.jsx checks each week-data.txt it's given
and saves placeholder graphics where the real script would.

  python3 -m unittest fccla/test/test_christmas_eve.py
"""

import datetime
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import auto_build as ab  # noqa: E402
import prepare_week as pw  # noqa: E402

XMAS = datetime.date(2026, 12, 24)
SUNDAY = datetime.date(2026, 12, 27)
ADVENT_4 = "12-20-26"
OW_530, OW_8, OW_11, OW_SUN = "12.24.26 5:30pm OW.pdf", "12.24.26 8pm OW.pdf", "12.24.26_11pm.pdf", "12.27.26 OW.pdf"
FIELDS = {
    OW_530: {"heading": "Christmas Eve", "dateText": "December 24, 2026", "preacher": ""},
    OW_8: {"heading": "Christmas Eve", "dateText": "December 24, 2026", "preacher": ""},
    OW_11: {"heading": "Christmas Eve", "dateText": "December 24, 2026", "preacher": ""},
    OW_SUN: {"heading": "Christmastide 1 | The Word Made Flesh", "dateText": "December 27, 2026",
             "preacher": "Rev. Laura Vail Fregin"},
}


def cover_pdf(color):
    """A one-page PDF whose only image is a cover photo, as an OW's page 1 has."""
    im = ab.Image.new("RGB", (600, 750), color)
    for x in range(0, 600, 40):                       # a second color, so the suggestions differ
        im.paste((240, 200, 60), (x, 0, x + 12, 750))
    out = io.BytesIO()
    im.save(out, "PDF")
    return out.getvalue()


def fake_updateweek(data_path):
    """What UpdateWeek.jsx saves, for the jobs week-data.txt asks for. Fails the test if
    prepare_week.py's own check (the one the script mirrors) finds a problem."""
    problems = pw.check(data_path)
    assert not problems, problems
    d = pw.read_data(data_path)
    root = os.path.expanduser(d["root"])
    out = pw.resolve(d["outputFolder"], root)
    jobs = [(d["serviceFolder"], d["serviceName"], d["serviceTemplate"])]
    if d.get("graphics") != "service":
        jobs.append((d["sermonFolder"], d["sermonName"], d["sermonTemplate"]))
    for folder, name, template in jobs:
        os.makedirs(os.path.join(out, folder), exist_ok=True)
        ab.Image.new("RGB", (192, 108), d["panelColor"]).save(os.path.join(out, folder, name + ".jpg"))
        with open(os.path.join(out, folder, name + ".ai"), "w") as fh:
            fh.write("built from " + template)
    log = "UpdateWeek: %s / %s / %s\n" % (d["heading"], d["dateText"], d.get("serviceTime", ""))
    with open(os.path.join(out, "log.txt"), "w") as fh:
        fh.write(log)
    shutil.copyfile(data_path, os.path.join(out, "week-data.txt"))      # as the script keeps a record
    return log


class Evening(unittest.TestCase):
    def setUp(self):
        self.base = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.base, True)
        self.root = os.path.join(self.base, "icloud")
        for p in ("Past Weeks", "Scripts"):
            os.makedirs(os.path.join(self.root, p))
        # Advent 4, the Sunday before: the template for all four
        for sub, name in (("Worship Service", "Service Title"), ("Sermon Series", "Sermon Title")):
            os.makedirs(os.path.join(self.root, ADVENT_4, "%s %s" % (sub, ADVENT_4)))
            open(os.path.join(self.root, ADVENT_4, "%s %s" % (sub, ADVENT_4), "%s_%s.ai" % (name, ADVENT_4)), "w").close()
        # last year's hand-made Christmas Eve folder, which must never be a template
        os.makedirs(os.path.join(self.root, "Past Weeks", "12-24-25 8pm"))
        open(os.path.join(self.root, "Past Weeks", "12-24-25 8pm", "Service Title_2025-12-25-25 8pm.ai"), "w").close()
        self.pdfs = {OW_530: cover_pdf((150, 30, 40)), OW_8: cover_pdf((20, 50, 140)),
                     OW_11: cover_pdf((30, 110, 60)), OW_SUN: cover_pdf((120, 40, 130))}
        self.ows = [{"name": n, "sha": "sha-" + n, "download_url": "https://example.invalid/" + n} for n in self.pdfs]
        self.ows.append({"name": "12.20.26 OW.pdf", "sha": "old", "download_url": "x"})       # passed
        self.ows.append({"name": "index.json", "sha": "i", "download_url": "x"})
        self.state = {}
        self.read = []

    def ctx(self, when):
        return type("Ctx", (), {"root": self.root, "staging": os.path.join(self.base, "staging"),
                                "status_dir": os.path.join(self.base, "status"),
                                "queue_dir": os.path.join(self.base, "queue"), "now": when, "today": when.date()})()

    def args(self, trigger, ow=None, pick=None, service=None, drive=True, force=False):
        return type("A", (), {"trigger": trigger, "ow": ow, "pick": pick, "service": service, "no_drive": not drive,
                              "replace_hand_edits": False, "force_rebuild": force})()

    def download(self, url, timeout=None):
        return mock.Mock(content=self.pdfs[url.rsplit("/", 1)[1]], raise_for_status=lambda: None)

    def read_fields(self, pdf, name=""):
        self.read.append(name)
        heading = pw.heading_of(FIELDS[name])
        return dict(FIELDS[name], heading=heading, title=heading[-1]), []

    def run_ab(self, a, when, ows):
        with mock.patch.object(ab, "list_ows", return_value=ows), \
             mock.patch.object(ab.requests, "get", side_effect=self.download), \
             mock.patch.object(ab, "read_fields", side_effect=self.read_fields), \
             mock.patch.object(ab, "run_updateweek", side_effect=fake_updateweek), \
             mock.patch.object(ab.pw, "color_preview"), \
             mock.patch("builtins.print"):
            try:
                return ab.run(a, self.ctx(when), self.state)
            except ab.Stop as stop:
                return stop

    def at(self, day, hour, minute=0):
        return datetime.datetime(2026, 12, day, hour, minute, tzinfo=ab.TZ)

    def upload_all(self):
        """Monday 12/21: the four OWs uploaded within a few minutes. The first upload's run starts
        while it's the only one in cju-media/OW; GitHub cancels the runs for the second and third
        (a newer one replaced each while it waited), so the fourth's run is the only other."""
        first = self.run_ab(self.args("upload", OW_530), self.at(21, 19, 5), [self.ows[0]])
        last = self.run_ab(self.args("upload", OW_SUN), self.at(21, 19, 20), self.ows)
        return first, last

    def sidecars(self):
        q = os.path.join(self.base, "queue")
        out = {}
        for n in sorted(os.listdir(q)) if os.path.isdir(q) else []:
            if n.endswith(".meta.json"):
                with open(os.path.join(q, n)) as fh:
                    out[n[:-len(".meta.json")].split("---", 2)[2]] = (n.split("---")[1], json.load(fh))
        return out

    # ---------------------------------------------------------------- building

    def test_each_service_gets_its_own_options(self):
        first, last = self.upload_all()
        self.assertEqual(first[0], "built")
        self.assertEqual(last[0], "built")
        builds = self.state["builds"]
        self.assertEqual(sorted(builds), ["12-24-26 11pm", "12-24-26 530pm", "12-24-26 8pm", "12-27-26"])
        for week, b in builds.items():
            self.assertGreaterEqual(len(b["options"]), 2, week)             # one per suggested color
            self.assertIsNone(b["pick"], week)
        self.assertEqual(builds["12-24-26 530pm"]["time"], "17:30")
        self.assertEqual(builds["12-24-26 11pm"]["time"], "23:00")
        self.assertIsNone(builds["12-27-26"]["time"])
        # all four options' previews stay for the picker
        self.assertEqual(sorted(os.listdir(os.path.join(self.base, "status"))), sorted(b["id"] for b in builds.values()))
        self.assertNotIn(" ", "".join(b["id"] for b in builds.values()))
        # the second run read the dropped runs' OWs, not the first one's again
        self.assertEqual(self.read, [OW_530, OW_SUN, OW_8, OW_11])
        for when in ("Thursday, December 24 at 8pm", "Thursday, December 24 at 11pm", "Sunday, December 27"):
            self.assertIn(when, last[1])

    def test_christmas_eve_gets_the_worship_service_graphic_only(self):
        self.upload_all()
        staged = os.path.join(self.base, "staging", "12-24-26 8pm")
        self.assertTrue(os.path.exists(os.path.join(staged, "12-24-26 8pm", "Cover_12-24-26 8pm.png")))
        self.assertEqual(sorted(os.listdir(os.path.join(staged, "option-1", "12-24-26 8pm"))),
                         ["Worship Service 12-24-26 8pm", "log.txt", "week-data.txt"])
        d = pw.read_data(os.path.join(self.base, "staging", "12-24-26 8pm", "option-1.txt"))
        self.assertEqual((d["dayName"], d["serviceTime"], d["graphics"]), ("Thursday", "8:00 PM", "service"))
        self.assertEqual(d["timeZone"], "PST")
        self.assertEqual(d["serviceTemplate"], "12-20-26/Worship Service 12-20-26/Service Title_12-20-26.ai")
        for k in pw.SERMON_KEYS:
            self.assertNotIn(k, d)
        sunday = pw.read_data(os.path.join(self.base, "staging", "12-27-26", "option-1.txt"))
        self.assertNotIn("dayName", sunday)
        self.assertNotIn("serviceTime", sunday)
        self.assertEqual(sunday["sermonName"], "Sermon Title_12-27-26")
        self.assertIn("Worship Service only", self.state["last_run"]["message"] if "last_run" in self.state else
                      ab.build_message(self.state["builds"]["12-24-26 11pm"]))

    def test_a_schedule_run_afterwards_reads_nothing_again(self):
        self.upload_all()
        self.read.clear()
        stop = self.run_ab(self.args("schedule"), self.at(22, 2, 20), self.ows)
        self.assertEqual(stop.status, "idle")
        self.assertFalse(stop.notify)
        self.assertEqual(self.read, [])

    def test_reuploading_one_service_rebuilds_only_that_one(self):
        self.upload_all()
        before = {w: b["id"] for w, b in self.state["builds"].items()}
        self.ows[1] = dict(self.ows[1], sha="sha-8pm-v2")
        FIELDS_8 = dict(FIELDS[OW_8])
        with mock.patch.dict(FIELDS, {OW_8: dict(FIELDS_8, heading="Christmas Eve | Candlelight")}):
            result = self.run_ab(self.args("upload", OW_8), self.at(22, 9), self.ows)
        self.assertEqual(result[0], "built")
        after = {w: b["id"] for w, b in self.state["builds"].items()}
        self.assertNotEqual(after.pop("12-24-26 8pm"), before.pop("12-24-26 8pm"))
        self.assertEqual(after, before)

    def test_one_unreadable_ow_doesnt_stop_the_others(self):
        # last year's 5pm OW (the pageant) had no text on page 1 to read the heading from
        readable = self.read_fields

        def read(pdf, name=""):
            if name == OW_530:
                self.read.append(name)
                raise ab.Stop("failed", "Title graphics: couldn't read heading from the OW.", code=1)
            return readable(pdf, name)
        with mock.patch.object(self, "read_fields", side_effect=read):
            stop = self.run_ab(self.args("upload", OW_SUN), self.at(21, 19, 20), self.ows)
        self.assertEqual(stop.status, "built")
        self.assertEqual(stop.code, 1)
        self.assertIn("couldn't read heading", stop.message)
        self.assertEqual(sorted(self.state["builds"]), ["12-24-26 11pm", "12-24-26 8pm", "12-27-26"])
        # and the schedule doesn't retry it every three hours
        self.read.clear()
        self.run_ab(self.args("schedule"), self.at(22, 2, 20), self.ows)
        self.assertEqual(self.read, [])

    # ---------------------------------------------------------------- picking

    def pick(self, service, n, when, drive=True):
        with mock.patch.object(ab, "sync_scripts"):
            return self.run_ab(self.args("pick", pick=str(n), service=service, drive=drive), when, self.ows)

    def test_picks_go_to_each_services_folder_and_stream(self):
        self.upload_all()
        monday = self.at(21, 20)
        self.assertEqual(self.pick("12-24-26 530pm", 2, monday)[0], "picked")
        self.assertEqual(self.pick("12-27-26", 1, monday)[0], "picked")
        status, msg, _, link = self.pick("12-24-26 8pm", 3, monday, drive=False)
        self.assertIn("8pm set", msg)
        self.assertEqual(link, ab.DASHBOARD_URL)
        builds = self.state["builds"]
        self.assertEqual(builds["12-24-26 530pm"]["pick"]["n"], 2)       # a later pick for another
        self.assertEqual(builds["12-24-26 530pm"]["pick"]["drive"], "sent")  # service left it alone
        self.assertEqual(builds["12-27-26"]["pick"]["drive"], "sent")
        self.assertEqual(builds["12-24-26 8pm"]["pick"]["drive"], "held")
        self.assertIsNone(builds["12-24-26 11pm"]["pick"])

        # iCloud: a folder per service, no Sermon Series for Christmas Eve
        xmas = os.path.join(self.root, "12-24-26 530pm")
        self.assertEqual(sorted(os.listdir(xmas)), ["Cover_12-24-26 530pm.png", "Worship Service 12-24-26 530pm",
                                                    "log.txt", "week-data.txt"])
        self.assertTrue(os.path.exists(os.path.join(xmas, "Worship Service 12-24-26 530pm",
                                                    "Service Title_12-24-26 530pm.jpg")))
        self.assertTrue(os.path.isdir(os.path.join(self.root, "12-24-26 8pm")))
        self.assertTrue(os.path.isdir(os.path.join(self.root, "12-27-26", "Sermon Series 12-27-26")))

        # Drive: Christmas Eve's one JPG goes now (not waiting for the 12/27 week), with its time;
        # 12/27's two go as before
        sent = self.sidecars()
        self.assertEqual(sorted(sent), ["Sermon_Title_12-27-26.jpg", "Service_Title_12-24-26_530pm.jpg",
                                        "Service_Title_12-27-26.jpg"])
        folder, meta = sent["Service_Title_12-24-26_530pm.jpg"]
        self.assertEqual(folder, ab.GRAPHICS[0][3])
        self.assertEqual((meta["date"], meta["time"], meta["replace"]), ("12-24-2026", "17:30", True))
        self.assertNotIn("time", sent["Service_Title_12-27-26.jpg"][1])
        self.assertEqual(sent["Sermon_Title_12-27-26.jpg"][1]["date"], "12-27-2026")

    def test_christmas_eve_pick_doesnt_wait_for_its_week(self):
        # Picked two weeks ahead: a Sunday pick would wait; a timed one goes now
        early = self.at(10, 12)
        self.run_ab(self.args("upload", OW_530), early, [self.ows[0]])
        status, msg, _, _ = self.pick("12-24-26 530pm", 1, early)
        self.assertEqual(self.state["builds"]["12-24-26 530pm"]["pick"]["drive"], "sent")
        self.assertIn("no Sermon Series graphic", msg)
        self.assertEqual(list(self.sidecars()), ["Service_Title_12-24-26_530pm.jpg"])

    def test_a_pick_after_the_service_started_isnt_sent(self):
        self.upload_all()
        evening = self.at(24, 18)
        self.pick("12-24-26 530pm", 1, evening)
        self.assertEqual(self.state["builds"]["12-24-26 530pm"]["pick"]["drive"], "not sent")
        self.pick("12-24-26 8pm", 1, evening)
        self.assertEqual(self.state["builds"]["12-24-26 8pm"]["pick"]["drive"], "sent")

    def test_a_pick_without_a_service_uses_the_newest_build(self):
        # an old picker page (or the workflow_dispatch form left blank)
        self.upload_all()
        newest = max(self.state["builds"].values(), key=lambda b: b["built_at"])
        self.pick(None, 1, self.at(21, 21))
        self.assertIsNotNone(self.state["builds"][newest["week"]]["pick"])

    def test_a_pick_for_a_service_with_no_build(self):
        stop = self.pick("12-24-26 530pm", 1, self.at(21, 21))
        self.assertEqual(stop.status, "failed")
        self.assertIn("12-24-26 530pm", stop.message)

    def test_the_sunday_after_is_still_built_from_advent_4(self):
        self.upload_all()
        monday = self.at(21, 20)
        for service in ("12-24-26 530pm", "12-24-26 8pm", "12-24-26 11pm"):
            self.pick(service, 1, monday)
        # rebuilt after Christmas Eve's folders are in iCloud: still Advent 4's files
        self.run_ab(self.args("manual", OW_SUN, force=True), self.at(25, 10), self.ows)
        d = pw.read_data(os.path.join(self.base, "staging", "12-27-26", "option-1.txt"))
        self.assertTrue(d["serviceTemplate"].startswith("12-20-26/"), d["serviceTemplate"])
        self.assertTrue(d["sermonTemplate"].startswith("12-20-26/"), d["sermonTemplate"])

    def test_passed_services_leave_the_picker(self):
        self.upload_all()
        # the first build after Christmas Eve drops its three
        self.ows.append({"name": "1.3.27 OW.pdf", "sha": "jan", "download_url": "https://example.invalid/1.3.27 OW.pdf"})
        self.pdfs["1.3.27 OW.pdf"] = cover_pdf((10, 10, 120))
        with mock.patch.dict(FIELDS, {"1.3.27 OW.pdf": {"heading": "Epiphany", "dateText": "January 3, 2027",
                                                        "preacher": "Rev. Lehman"}}):
            self.run_ab(self.args("upload", "1.3.27 OW.pdf"), self.at(26, 10), self.ows)
        self.assertEqual(sorted(self.state["builds"]), ["1-3-27", "12-27-26"])
        self.assertEqual(len(os.listdir(os.path.join(self.base, "status"))), 2)


class Folders(unittest.TestCase):
    def test_week_names(self):
        self.assertEqual(pw.week_name(XMAS, "17:30"), "12-24-26 530pm")
        self.assertEqual(pw.week_name(XMAS, "20:00"), "12-24-26 8pm")
        self.assertEqual(pw.week_name(XMAS, "23:00"), "12-24-26 11pm")
        self.assertEqual(pw.week_name(SUNDAY), "12-27-26")
        for name in ("12-24-26 530pm", "12-24-26 8pm", "12-24-26 11pm", "12-27-26"):
            self.assertEqual(pw.week_name(*pw.parse_folder_name(name)), name)
        self.assertIsNone(pw.parse_week_name("12-24-26 530pm"))

    def test_service_of(self):
        self.assertEqual(ab.service_of(OW_530), (XMAS, "17:30"))
        self.assertEqual(ab.service_of(OW_11), (XMAS, "23:00"))
        self.assertEqual(ab.service_of("12.27.26 10:30am OW.pdf"), (SUNDAY, None))
        self.assertEqual(ab.service_of(OW_SUN), (SUNDAY, None))

    def test_only_sunday_folders_are_templates(self):
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, True)
        for week in (ADVENT_4, "12-24-26 530pm", "12-24-26", "12-25-26"):
            for sub, name in (("Worship Service", "Service Title"), ("Sermon Series", "Sermon Title")):
                os.makedirs(os.path.join(root, week, "%s %s" % (sub, week)))
                open(os.path.join(root, week, "%s %s" % (sub, week), "%s_%s.ai" % (name, week)), "w").close()
        self.assertEqual(pw.find_templates(root, SUNDAY)[0], ADVENT_4)
        self.assertEqual(pw.find_templates(root, XMAS)[0], ADVENT_4)


class WeekDataCheck(unittest.TestCase):
    """prepare_week.check(), which UpdateWeek.jsx's validate() mirrors."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)
        for p in ("t.ai", "s.ai", "c.png"):
            open(os.path.join(self.root, p), "w").close()
        self.data = os.path.join(self.root, "week-data.txt")

    def write(self, **rows):
        base = {"root": self.root, "heading": "Christmas Eve", "dateText": "December 24, 2026",
                "panelColor": "#1C304B", "imagePath": "c.png", "outputFolder": "12-24-26 530pm",
                "serviceTemplate": "t.ai", "serviceFolder": "Worship Service 12-24-26 530pm",
                "serviceName": "Service Title_12-24-26 530pm", "dayName": "Thursday", "serviceTime": "5:30 PM",
                "graphics": "service"}
        base.update(rows)
        with open(self.data, "w") as fh:
            fh.write("".join("%s = %s\n" % kv for kv in base.items() if kv[1] is not None))
        return pw.check(self.data)

    def test_a_christmas_eve_service(self):
        self.assertEqual(self.write(), [])

    def test_its_day_and_time_must_agree(self):
        self.assertIn("December 24, 2026 is a Thursday; set dayName = Thursday", self.write(dayName=None))
        self.assertIn("serviceTime must look like 5:30 PM, got '17:30'", self.write(serviceTime="17:30"))
        self.assertTrue(any("should end with 12-24-26 8pm" in p for p in self.write(serviceTime="8:00 PM")))

    def test_it_has_no_sermon_series_graphic(self):
        self.assertIn("December 24, 2026 5:30 PM gets only the Service Title: set graphics = service",
                      self.write(graphics=None, preacher="Rev. X", sermonTemplate="s.ai",
                                 sermonFolder="Sermon Series 12-24-26 530pm", sermonName="Sermon Title_12-24-26 530pm"))

    def test_a_sunday_still_needs_both(self):
        problems = self.write(dateText="December 27, 2026", dayName=None, serviceTime=None, graphics=None,
                              outputFolder="12-27-26", serviceFolder="Worship Service 12-27-26",
                              serviceName="Service Title_12-27-26")
        self.assertIn("missing sermonTemplate", problems)
        self.assertIn("missing preacher", problems)


@unittest.skipUnless(shutil.which("node"), "needs node to run UpdateWeek.jsx's plain-JavaScript parts")
class UpdateWeekScript(unittest.TestCase):
    """UpdateWeek.jsx's header and times, with stand-ins for Illustrator's text frames. (The real
    thing runs on Studio Mini: see fccla/CHRISTMAS_EVE_CHECKLIST.md.)"""

    HARNESS = r"""
    const fs = require('fs');
    let src = fs.readFileSync(process.argv[1], 'utf8').replace('#target illustrator', '').replace(/\nUpdateWeek\(\);\s*$/, '\n');
    eval(src + `
    function unlock() {}
    function frame(s) {
        var tf = { contents: s };
        tf.characters = new Proxy({}, { get: function (o, i) { return {
            set contents(v) { tf.contents = tf.contents.substr(0, +i) + v + tf.contents.substr(+i + 1); },
            remove: function () { tf.contents = tf.contents.substr(0, +i) + tf.contents.substr(+i + 1); } }; } });
        return tf;
    }
    var out = {};
    var times = frame("Organ Prelude-Concert\\r10:30 AM pst\\rVirtual Service\\r11:00 AM pst");
    setServiceTimes([frame("December 24, 2026"), times], process.argv[2], true);
    out.times = times.contents;
    var a = frame("10:30 AM PST"), b = frame("11:00 AM PST");
    setServiceTimes([b, a], process.argv[2], true);
    out.split = [a.contents, b.contents];
    var base = { root: "r", heading: "Christmas Eve", dateText: "December 24, 2026", panelColor: "#123456",
                 imagePath: "c", outputFolder: "o", serviceTemplate: "t", serviceFolder: "f", serviceName: "n" };
    out.serviceOnly = validate(Object.assign({ graphics: "service", serviceTime: "5:30 PM", dayName: "Thursday" }, base));
    out.both = validate(Object.assign({ serviceTime: "17:30" }, base));
    out.log = log;
    console.log(JSON.stringify(out));
    `);
    """

    def run_js(self, service_time):
        r = subprocess.run(["node", "-e", self.HARNESS, os.path.join(os.path.dirname(HERE), "UpdateWeek.jsx"),
                            service_time], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_times_follow_the_service(self):
        out = self.run_js("8:00 PM")
        self.assertEqual(out["times"], "Organ Prelude-Concert\r7:30 PM pst\rVirtual Service\r8:00 PM pst")
        self.assertEqual(out["split"], ["7:30 PM PST", "8:00 PM PST"])
        self.assertIn("  times: 10:30 AM, 11:00 AM -> 7:30 PM, 8:00 PM", out["log"])
        self.assertEqual(self.run_js("5:30 PM")["split"], ["5:00 PM PST", "5:30 PM PST"])
        self.assertEqual(self.run_js("11:00 PM")["split"], ["10:30 PM PST", "11:00 PM PST"])

    def test_service_only_needs_no_sermon_keys(self):
        out = self.run_js("5:30 PM")
        self.assertEqual(out["serviceOnly"], [])
        self.assertEqual(out["both"], ["missing preacher", "missing sermonTemplate", "missing sermonFolder",
                                       "missing sermonName", "serviceTime must look like 5:30 PM, not 17:30"])


if __name__ == "__main__":
    unittest.main()
