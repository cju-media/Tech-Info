#!/usr/bin/env python3
"""Unit tests for auto_build.py's decisions (no Illustrator, no network).

  python3 -m unittest fccla/test/test_auto_build.py
"""

import datetime
import os
import sys
import tempfile
import time
import unittest

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


PARSED = {"series": "Fall Series 4", "seriesName": "Painting the Stars", "title": "An Anticipatory Universe",
          "dateText": "September 27, 2026", "preacher": "Rev. Laura Vail Fregin"}
PDF_TEXT = "Fall Series 4\nPainting the Stars ~ An Anticipatory Universe\n27 September 2026\n" \
           "Sermon   An Anticipatory Universe   Rev. Laura Vail Fregin"


class MergeFields(unittest.TestCase):
    def test_agreement_keeps_the_pdf_text(self):
        gem = dict(PARSED, title="An anticipatory universe", seriesName="Painting  the Stars")
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
        parsed = dict(PARSED, title="An Anticipatory Universe 27 September 2026")
        merged, notes = ab.merge_fields(parsed, PARSED, PDF_TEXT)
        self.assertEqual(merged["title"], "An Anticipatory Universe")
        self.assertIn("used Gemini's", notes[0])

    def test_invented_text_loses(self):
        merged, notes = ab.merge_fields(PARSED, dict(PARSED, title="An Expectant Cosmos"), PDF_TEXT)
        self.assertEqual(merged["title"], "An Anticipatory Universe")
        self.assertIn("used the PDF's", notes[0])

    def test_dates_compare_as_dates(self):
        merged, notes = ab.merge_fields(PARSED, dict(PARSED, dateText="September 27, 2026"), PDF_TEXT)
        self.assertEqual(notes, [])
        # Gemini copying the PDF's own form isn't a disagreement (it was, on Studio Mini's first run)
        merged, notes = ab.merge_fields(PARSED, dict(PARSED, dateText="27 September 2026"), PDF_TEXT)
        self.assertEqual(notes, [])
        merged, notes = ab.merge_fields(dict(PARSED, dateText=""), dict(PARSED, dateText="27 September 2026"), PDF_TEXT)
        self.assertEqual(merged["dateText"], "September 27, 2026")
        merged, notes = ab.merge_fields(PARSED, dict(PARSED, dateText="September 20, 2026"), PDF_TEXT)
        self.assertEqual(merged["dateText"], "September 27, 2026")
        self.assertEqual(len(notes), 1)


class DriveTiming(unittest.TestCase):
    """Drive files the Sermon Series thumbnail under the next Sunday strictly after today."""

    def ctx(self, today, root):
        return type("Ctx", (), {"today": today, "root": root, "queue_dir": os.path.join(root, "queue")})()

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
        drive, _ = ab.send_to_drive(self.ctx(datetime.date(2026, 9, 29), root), {"week": "10-4-26", "dateText": "October 4, 2026"}, False)
        self.assertEqual(drive, "sent")
        names = sorted(os.listdir(os.path.join(root, "queue")))
        self.assertEqual(len(names), 2)
        self.assertTrue(names[0].endswith("---1KI_KifGRzRnafb5Z0IuXmdrgIEyB5_3f---Service_Title_10-4-26.jpg"))
        self.assertTrue(names[1].endswith("---1Ji2Bbe7vWTcaRCpdQOjzwQgxsIoOWdy4---Sermon_Title_10-4-26.jpg"))

    def test_a_changed_pick_asks_drive_to_replace(self):
        root = self.week_with_jpgs("10-4-26")
        ab.send_to_drive(self.ctx(datetime.date(2026, 9, 29), root), {"week": "10-4-26", "dateText": "October 4, 2026"}, True)
        metas = [n for n in os.listdir(os.path.join(root, "queue")) if n.endswith(".meta.json")]
        self.assertEqual(len(metas), 2)

    def test_next_weeks_pick_waits(self):
        root = self.week_with_jpgs("10-11-26")
        drive, msg = ab.send_to_drive(self.ctx(datetime.date(2026, 9, 29), root), {"week": "10-11-26", "dateText": "October 11, 2026"}, False)
        self.assertEqual(drive, "waiting")
        self.assertIn("Monday, October 5", msg)
        self.assertFalse(os.path.exists(os.path.join(root, "queue")))

    def test_picking_on_the_day_is_too_late_for_drive(self):
        root = self.week_with_jpgs("10-4-26")
        drive, _ = ab.send_to_drive(self.ctx(datetime.date(2026, 10, 4), root), {"week": "10-4-26", "dateText": "October 4, 2026"}, False)
        self.assertEqual(drive, "not sent")

    def test_schedule_sends_a_waiting_pick_when_its_week_comes_up(self):
        root = self.week_with_jpgs("10-11-26")
        state = {"build": {"week": "10-11-26", "dateText": "October 11, 2026", "pick": {"hex": "#385261", "drive": "waiting"}}}
        self.assertIsNone(ab.send_waiting(self.ctx(datetime.date(2026, 10, 2), root), state))    # Friday before
        self.assertIn("Sent to Drive", ab.send_waiting(self.ctx(datetime.date(2026, 10, 5), root), state))
        self.assertEqual(state["build"]["pick"]["drive"], "sent")


if __name__ == "__main__":
    unittest.main()
