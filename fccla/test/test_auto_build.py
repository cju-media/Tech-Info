#!/usr/bin/env python3
"""Unit tests for auto_build.py's decisions (no Illustrator, no network).

  python3 -m unittest fccla/test/test_auto_build.py
"""

import datetime
import json
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import auto_build as ab  # noqa: E402

OWS = [{"name": n, "sha": n, "download_url": ""} for n in [
    "9.20.26_OW_Draft.pdf", "9.27.26_OW_Draft.pdf", "9.27.26_OW.pdf", "10.4.26 OW Draft.pdf", "index.json"]]


class ChooseOW(unittest.TestCase):
    def test_named_file_wins(self):
        self.assertEqual(ab.choose_ow(OWS, "9.20.26_OW_Draft.pdf")["name"], "9.20.26_OW_Draft.pdf")

    def test_named_as_the_dashboard_renames_it(self):
        self.assertEqual(ab.choose_ow(OWS, "10.4.26_OW_Draft.pdf"), None)          # not there under that name
        ows = OWS + [{"name": "10.11.26_OW_Draft.pdf", "sha": "x", "download_url": ""}]
        self.assertEqual(ab.choose_ow(ows, "10.11.26 OW Draft.pdf")["name"], "10.11.26_OW_Draft.pdf")

    def test_coming_sunday_prefers_final_over_draft(self):
        wed = datetime.date(2026, 9, 23)
        self.assertEqual(ab.choose_ow(OWS, today=wed)["name"], "9.27.26_OW.pdf")

    def test_space_separated_name_and_sunday_itself(self):
        self.assertEqual(ab.choose_ow(OWS, today=datetime.date(2026, 10, 4))["name"], "10.4.26 OW Draft.pdf")

    def test_nothing_for_that_sunday(self):
        self.assertIsNone(ab.choose_ow(OWS, today=datetime.date(2026, 10, 6)))


class WhoMade(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.dir, "Worship Service 10-4-26"))
        os.makedirs(os.path.join(self.dir, "Sermon Series 10-4-26"))
        self.ai = os.path.join(self.dir, "Worship Service 10-4-26", "Service Title_10-4-26.ai")

    def touch(self, path, text="", age=0):
        with open(path, "w") as fh:
            fh.write(text)
        t = time.time() - age
        os.utime(path, (t, t))

    def test_empty_week(self):
        self.assertEqual(ab.who_made(self.dir), "none")

    def test_no_log_means_hand_made(self):
        self.touch(self.ai)
        self.assertEqual(ab.who_made(self.dir), "hand-made")

    def test_someone_elses_log_means_hand_made(self):
        self.touch(self.ai)
        self.touch(os.path.join(self.dir, "log.txt"), "notes\n")
        self.assertEqual(ab.who_made(self.dir), "hand-made")

    def test_built_by_script(self):
        self.touch(self.ai, age=30)
        self.touch(os.path.join(self.dir, "log.txt"), "UpdateWeek: Fall Series 4 / ...\n", age=20)
        self.assertEqual(ab.who_made(self.dir), "script")

    def test_edited_after_build(self):
        self.touch(os.path.join(self.dir, "log.txt"), "UpdateWeek: Fall Series 4 / ...\n", age=3600)
        self.touch(self.ai)
        self.assertEqual(ab.who_made(self.dir), "edited")


PARSED = {"heading": "Fall Series 4 | Painting the Stars | An Anticipatory Universe",
          "dateText": "September 27, 2026", "preacher": "Rev. Laura Vail Fregin"}
PDF_TEXT = "Fall Series 4\nPainting the Stars ~ An Anticipatory Universe\n27 September 2026\n" \
           "Sermon   An Anticipatory Universe   Rev. Laura Vail Fregin"


class MergeFields(unittest.TestCase):
    def test_agreement_keeps_the_pdf_text(self):
        gem = dict(PARSED, heading="Fall Series 4 |  Painting  the Stars | An anticipatory universe")
        merged, notes = ab.merge_fields(PARSED, gem, PDF_TEXT)
        self.assertEqual(merged, PARSED)
        self.assertEqual(notes, [])

    def test_no_gemini(self):
        self.assertEqual(ab.merge_fields(PARSED, None, PDF_TEXT), (PARSED, []))

    def test_gemini_fills_a_gap(self):
        merged, notes = ab.merge_fields(dict(PARSED, preacher=""), PARSED, PDF_TEXT)
        self.assertEqual(merged["preacher"], "Rev. Laura Vail Fregin")
        self.assertEqual(len(notes), 1)

    def test_gemini_wins_with_real_pdf_text(self):
        parsed = dict(PARSED, heading="Fall Series 4 Painting the Stars | An Anticipatory Universe")
        merged, notes = ab.merge_fields(parsed, PARSED, PDF_TEXT)
        self.assertEqual(merged["heading"], PARSED["heading"])
        self.assertIn("used Gemini's", notes[0])

    def test_invented_text_loses(self):
        merged, notes = ab.merge_fields(PARSED, dict(PARSED, heading="Fall Series 4 | An Expectant Cosmos"), PDF_TEXT)
        self.assertEqual(merged["heading"], PARSED["heading"])
        self.assertIn("used the PDF's", notes[0])

    def test_dates_compare_as_dates(self):
        merged, notes = ab.merge_fields(PARSED, dict(PARSED, dateText="September 27, 2026"), PDF_TEXT)
        self.assertEqual(notes, [])
        # Gemini copying the PDF's own form isn't a disagreement (it was, on Studio Mini's first run)
        merged, notes = ab.merge_fields(PARSED, dict(PARSED, dateText="27 September 2026"), PDF_TEXT)
        self.assertEqual(notes, [])
        merged, notes = ab.merge_fields(dict(PARSED, dateText=""), dict(PARSED, dateText="27 September 2026"), PDF_TEXT)
        self.assertEqual(merged["dateText"], "September 27, 2026")
        merged, notes = ab.merge_fields(dict(PARSED, dateText=""), dict(PARSED, dateText="September 28th, 2025"), PDF_TEXT)
        self.assertEqual(merged["dateText"], "September 28, 2025")


class BuildMessage(unittest.TestCase):
    def test_link_is_not_in_the_body(self):
        # iMessage via osascript only makes a URL tappable when it's a message of its own
        build = {"dateText": "October 4, 2026", "options": [{"n": 1, "label": "gold, 79%", "hex": "#6E6127", "problems": []}],
                 "notes": []}
        body = ab.build_message(build)
        self.assertNotIn("http", body)
        self.assertIn("link below", body)


class DriveTiming(unittest.TestCase):
    """Drive files the Sermon Series thumbnail under the next Sunday strictly after today."""

    def ctx(self, today, root):
        now = datetime.datetime.combine(today, datetime.time(12), ab.TZ)
        return type("Ctx", (), {"today": today, "now": now, "root": root, "queue_dir": os.path.join(root, "queue")})()

    def sidecars(self, root):
        q, metas = os.path.join(root, "queue"), []
        for n in sorted(os.listdir(q)):
            if n.endswith(".meta.json"):
                with open(os.path.join(q, n)) as fh:
                    metas.append(json.load(fh))
        return metas

    def week_with_jpgs(self, week):
        root = tempfile.mkdtemp()
        for _, sub, name, _ in ab.GRAPHICS:
            os.makedirs(os.path.join(root, week, "%s %s" % (sub, week)))
            open(os.path.join(root, week, "%s %s" % (sub, week), "%s_%s.jpg" % (name, week)), "w").close()
        return root

    def test_drive_sunday(self):
        self.assertEqual(ab.drive_sunday(datetime.date(2026, 9, 29)), datetime.date(2026, 10, 4))   # Tue
        self.assertEqual(ab.drive_sunday(datetime.date(2026, 10, 4)), datetime.date(2026, 10, 11))  # Sun

    def test_this_weeks_pick_is_queued_like_a_dashboard_upload(self):
        root = self.week_with_jpgs("10-4-26")
        ctx = self.ctx(datetime.date(2026, 9, 29), root)
        drive, _ = ab.send_to_drive(ctx, {"week": "10-4-26", "dateText": "October 4, 2026"}, ctx.now)
        self.assertEqual(drive, "sent")
        names = sorted(n for n in os.listdir(os.path.join(root, "queue")) if not n.endswith(".meta.json"))
        self.assertEqual(len(names), 2)
        self.assertTrue(names[0].endswith("---1KI_KifGRzRnafb5Z0IuXmdrgIEyB5_3f---Service_Title_10-4-26.jpg"))
        self.assertTrue(names[1].endswith("---1Ji2Bbe7vWTcaRCpdQOjzwQgxsIoOWdy4---Sermon_Title_10-4-26.jpg"))

    def test_every_pick_replaces_under_the_dashboards_date_folder(self):
        # The dashboard files a Sunday under MM-DD-YYYY; a pick must land in the same Drive
        # folder, or a later dashboard upload can't replace it.
        root = self.week_with_jpgs("10-4-26")
        ctx = self.ctx(datetime.date(2026, 9, 29), root)
        ab.send_to_drive(ctx, {"week": "10-4-26", "dateText": "October 4, 2026"}, ctx.now)
        metas = self.sidecars(root)
        self.assertEqual(len(metas), 2)
        for m in metas:
            self.assertEqual(m["date"], "10-04-2026")
            self.assertTrue(m["replace"])
            self.assertEqual(m["chosen_at"], "2026-09-29T19:00:00+00:00")     # noon Pacific, in UTC

    def test_next_weeks_pick_waits(self):
        root = self.week_with_jpgs("10-11-26")
        ctx = self.ctx(datetime.date(2026, 9, 29), root)
        drive, msg = ab.send_to_drive(ctx, {"week": "10-11-26", "dateText": "October 11, 2026"}, ctx.now)
        self.assertEqual(drive, "waiting")
        self.assertIn("Monday, October 5", msg)
        self.assertFalse(os.path.exists(os.path.join(root, "queue")))

    def test_picking_on_the_day_is_too_late_for_drive(self):
        root = self.week_with_jpgs("10-4-26")
        ctx = self.ctx(datetime.date(2026, 10, 4), root)
        drive, _ = ab.send_to_drive(ctx, {"week": "10-4-26", "dateText": "October 4, 2026"}, ctx.now)
        self.assertEqual(drive, "not sent")

    def test_schedule_sends_a_waiting_pick_when_its_week_comes_up(self):
        root = self.week_with_jpgs("10-11-26")
        state = {"build": {"week": "10-11-26", "dateText": "October 11, 2026",
                           "pick": {"hex": "#385261", "drive": "waiting", "at": "2026-09-29T15:30:00-07:00"}}}
        self.assertIsNone(ab.send_waiting(self.ctx(datetime.date(2026, 10, 2), root), state))    # Friday before
        self.assertIn("Sent to Drive", ab.send_waiting(self.ctx(datetime.date(2026, 10, 5), root), state))
        self.assertEqual(state["build"]["pick"]["drive"], "sent")
        # a dashboard upload made after the pick (and before this) must win, so the time is the pick's
        self.assertEqual({m["chosen_at"] for m in self.sidecars(root)}, {"2026-09-29T22:30:00+00:00"})

    def test_schedule_never_sends_a_pick_kept_out_of_drive(self):
        root = self.week_with_jpgs("10-11-26")
        state = {"build": {"week": "10-11-26", "dateText": "October 11, 2026",
                           "pick": {"hex": "#385261", "drive": "held", "at": "2026-09-29T15:30:00-07:00"}}}
        self.assertIsNone(ab.send_waiting(self.ctx(datetime.date(2026, 10, 5), root), state))
        self.assertFalse(os.path.exists(os.path.join(root, "queue")))
        self.assertEqual(state["build"]["pick"]["drive"], "held")


class UsePick(unittest.TestCase):
    """The picker's two buttons: "Use this one" and "Use, don't send to Drive"."""

    WEEK = "10-4-26"

    def setUp(self):
        self.base = tempfile.mkdtemp()
        root, staging = os.path.join(self.base, "icloud"), os.path.join(self.base, "staging")
        staged = os.path.join(staging, self.WEEK, "option-1", self.WEEK)
        for _, sub, name, _ in ab.GRAPHICS:
            os.makedirs(os.path.join(staged, "%s %s" % (sub, self.WEEK)))
            for ext in ("ai", "jpg"):
                open(os.path.join(staged, "%s %s" % (sub, self.WEEK), "%s_%s.%s" % (name, self.WEEK, ext)), "w").close()
        with open(os.path.join(staged, "log.txt"), "w") as fh:
            fh.write("UpdateWeek: test\n")
        open(os.path.join(staging, self.WEEK, "ow.pdf"), "w").close()
        os.makedirs(root)
        self.data = os.path.join(self.base, "week-data.txt")
        open(self.data, "w").close()
        now = datetime.datetime(2026, 9, 29, 12, 0, tzinfo=ab.TZ)
        self.ctx = type("Ctx", (), {"root": root, "staging": staging, "queue_dir": os.path.join(self.base, "queue"),
                                    "now": now, "today": now.date()})()

    def state(self, earlier=None):
        build = {"week": self.WEEK, "dateText": "October 4, 2026", "fields": {},
                 "options": [{"n": 1, "hex": "#1C304B", "label": "blue, 65%", "problems": []},
                             {"n": 2, "hex": "#6E6127", "label": "gold, 79%", "problems": []}]}
        if earlier:
            build["pick"] = earlier
        return {"build": build}

    def pick(self, state, send):
        with mock.patch.object(ab.pw, "prepare", return_value={"data_path": self.data}):
            return ab.use_pick(self.ctx, state, "1", False, send=send)

    def test_use_this_one_sends_to_drive(self):
        state = self.state()
        msg, link = self.pick(state, True)
        self.assertEqual(state["build"]["pick"]["drive"], "sent")
        self.assertEqual(len(os.listdir(self.ctx.queue_dir)), 4)         # two JPGs and their sidecars
        self.assertIsNone(link)
        self.assertIn("Sent to Drive", msg)

    def test_dont_send_keeps_it_in_icloud_only(self):
        state = self.state()
        msg, link = self.pick(state, False)
        self.assertEqual(state["build"]["pick"]["drive"], "held")
        self.assertFalse(os.path.exists(self.ctx.queue_dir))
        self.assertTrue(os.path.exists(os.path.join(self.ctx.root, self.WEEK, "Worship Service 10-4-26",
                                                    "Service Title_10-4-26.jpg")))
        self.assertIn("upload dashboard", msg)
        self.assertEqual(link, ab.DASHBOARD_URL)                        # texted on its own, to tap

    def test_dont_send_says_drive_keeps_the_earlier_pick(self):
        state = self.state({"n": 2, "hex": "#6E6127", "label": "gold", "drive": "sent", "at": "2026-09-28T10:00:00-07:00"})
        msg, _ = self.pick(state, False)
        self.assertIn("keep #6E6127", msg)


class NewOwThatChangesNothing(unittest.TestCase):
    """A new OW that reads the same (and has the same photo) as what's already there isn't rebuilt,
    unless the dashboard's "Rebuild title graphics" box (--force-rebuild) is ticked."""

    WEEK = "10-4-26"
    FIELDS = {"heading": ["Fall Series 5", "Painting the Stars", "We are Stories & Stardust"],
              "dateText": "October 4, 2026", "preacher": "Rev. Laura Vail Fregin"}

    def setUp(self):
        self.base = tempfile.mkdtemp()
        self.root, self.staging = os.path.join(self.base, "icloud"), os.path.join(self.base, "staging")
        self.ctx = type("Ctx", (), {"root": self.root, "staging": self.staging,
                                    "today": datetime.date(2026, 9, 30),
                                    "now": datetime.datetime(2026, 9, 30, 12, tzinfo=ab.TZ)})()
        self.photo = ab.Image.new("RGB", (64, 80), (20, 40, 90))
        self.other = ab.Image.new("RGB", (64, 80), (200, 160, 40))

    def in_use(self, heading="Fall Series 5 | Painting the Stars | We are Stories & Stardust"):
        week = os.path.join(self.root, self.WEEK)
        os.makedirs(week)
        with open(os.path.join(week, "week-data.txt"), "w") as fh:
            fh.write("heading = %s\ndateText = October 4, 2026\npreacher = Rev. Laura Vail Fregin\n"
                     "panelColor = #1C304B\n" % heading)
        self.photo.save(os.path.join(week, "Cover_%s.png" % self.WEEK))

    def check(self, fields, photo, build=None):
        with mock.patch.object(ab, "cover_of", return_value=photo):
            return ab.unchanged(self.ctx, build or {}, fields, "new-ow.pdf")

    def test_same_text_and_photo_as_the_graphics_in_use(self):
        self.in_use()
        msg, link = self.check(self.FIELDS, self.photo)
        self.assertIn("#1C304B", msg)
        self.assertIn("Rebuild title graphics", msg)                # says how to force it
        self.assertIsNone(link)

    def test_spacing_and_curly_quotes_dont_count(self):
        self.in_use()
        fields = dict(self.FIELDS, preacher="Rev.  Laura Vail Fregin ")
        self.assertIsNotNone(self.check(fields, self.photo))

    def test_a_changed_title_rebuilds(self):
        self.in_use()
        fields = dict(self.FIELDS, heading=["Fall Series 5", "Painting the Stars", "An Anticipatory Universe"])
        self.assertIsNone(self.check(fields, self.photo))

    def test_capitals_count_since_they_show_on_the_graphic(self):
        self.in_use()
        fields = dict(self.FIELDS, heading=["Fall Series 5", "Painting the Stars", "We Are Stories & Stardust"])
        self.assertIsNone(self.check(fields, self.photo))

    def test_a_new_photo_rebuilds(self):
        self.in_use()
        self.assertIsNone(self.check(self.FIELDS, self.other))

    def test_nothing_picked_yet_compares_with_the_options_built(self):
        os.makedirs(os.path.join(self.staging, self.WEEK))
        open(os.path.join(self.staging, self.WEEK, "ow.pdf"), "w").close()
        build = {"week": self.WEEK, "fields": dict(self.FIELDS, heading=" | ".join(self.FIELDS["heading"])),
                 "options": [{"n": 1}]}
        msg, link = self.check(self.FIELDS, self.photo, build)
        self.assertIn("options already built", msg)
        self.assertEqual(link, ab.PICKER_URL)

    def run_upload(self, force):
        self.in_use()
        state = {"build": {"week": self.WEEK, "ow": {"name": "10.4.26_OW_Draft.pdf", "sha": "old"},
                           "pick": {"hex": "#1C304B", "drive": "sent"}}}
        a = type("A", (), {"pick": None, "trigger": "upload", "ow": "10.4.26_OW.pdf", "force_rebuild": force})()
        new_ow = {"name": "10.4.26_OW.pdf", "sha": "new", "download_url": "https://example.invalid/ow.pdf"}
        built = []
        with mock.patch.object(ab, "list_ows", return_value=[new_ow]), \
             mock.patch.object(ab, "choose_ow", return_value=dict(new_ow)), \
             mock.patch.object(ab.requests, "get", return_value=mock.Mock(content=b"%PDF")), \
             mock.patch.object(ab, "read_fields", return_value=(dict(self.FIELDS), [])), \
             mock.patch.object(ab, "cover_of", return_value=self.photo), \
             mock.patch.object(ab, "build_options", side_effect=lambda *x: built.append(1) or {"id": "b"}), \
             mock.patch.object(ab, "build_message", return_value="ready"), \
             mock.patch.object(ab, "clean_up"):
            try:
                result = ab.run(a, self.ctx, state)
            except ab.Stop as stop:
                result = stop
        return result, built, state

    def test_upload_that_changes_nothing_isnt_rebuilt(self):
        stop, built, state = self.run_upload(force=False)
        self.assertEqual(stop.status, "unchanged")
        self.assertTrue(stop.notify)                                # you're told
        self.assertEqual(built, [])
        self.assertEqual(state["build"]["ow"]["sha"], "new")        # the schedule won't read it again
        self.assertEqual(state["build"]["pick"]["hex"], "#1C304B")  # still in use

    def test_rebuild_box_forces_it(self):
        result, built, state = self.run_upload(force=True)
        self.assertEqual(result[0], "built")
        self.assertEqual(built, [1])


if __name__ == "__main__":
    unittest.main()
