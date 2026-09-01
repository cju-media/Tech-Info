"""Unit tests for cleanup_events_folder.py's date-sanity helpers.

Run from this directory:  python -m unittest test_cleanup_events_folder -v

Regression cover for 2026-08-31: after ~12 days of silent Gemini quota
exhaustion (every read failed, run still exited green), the first working
run mistook three upcoming fall concert flyers for 2020/2023/2024 events --
Gemini guessed the wrong year on flyers that print the date without one --
and fired four bogus "delete this from Events_Ads" iMessages.
"""

import datetime
import unittest

from cleanup_events_folder import (
    SUSPECT_MISREAD_DAYS,
    is_suspected_misread,
    parse_drive_created_date,
)


class ParseDriveCreatedDate(unittest.TestCase):
    def test_full_rfc3339_timestamp(self):
        self.assertEqual(
            parse_drive_created_date("2026-08-29T12:34:56.789Z"),
            datetime.date(2026, 8, 29),
        )

    def test_date_only(self):
        self.assertEqual(
            parse_drive_created_date("2026-08-29"), datetime.date(2026, 8, 29)
        )

    def test_missing_or_junk_is_none(self):
        self.assertIsNone(parse_drive_created_date(None))
        self.assertIsNone(parse_drive_created_date(""))
        self.assertIsNone(parse_drive_created_date("not-a-date"))
        self.assertIsNone(parse_drive_created_date(20260829))


class IsSuspectedMisread(unittest.TestCase):
    UPLOADED = datetime.date(2026, 8, 29)  # the Aug 2026 batch upload

    def test_wrong_year_read_is_flagged(self):
        # The three flyers that triggered bogus alerts.
        for event_date in (
            datetime.date(2023, 9, 27),
            datetime.date(2024, 9, 5),
            datetime.date(2020, 10, 13),
        ):
            self.assertTrue(is_suspected_misread(event_date, self.UPLOADED))

    def test_genuinely_upcoming_event_is_not_flagged(self):
        self.assertFalse(
            is_suspected_misread(datetime.date(2026, 9, 10), self.UPLOADED)
        )

    def test_recently_passed_event_is_not_flagged(self):
        # Uploaded a while back, event just happened -> real cleanup target.
        event_date = self.UPLOADED - datetime.timedelta(days=14)
        self.assertFalse(is_suspected_misread(event_date, self.UPLOADED))

    def test_threshold_boundary(self):
        just_within = self.UPLOADED - datetime.timedelta(days=SUSPECT_MISREAD_DAYS)
        just_over = self.UPLOADED - datetime.timedelta(days=SUSPECT_MISREAD_DAYS + 1)
        self.assertFalse(is_suspected_misread(just_within, self.UPLOADED))
        self.assertTrue(is_suspected_misread(just_over, self.UPLOADED))

    def test_unknown_upload_date_never_flags(self):
        self.assertFalse(is_suspected_misread(datetime.date(2020, 1, 1), None))
        self.assertFalse(is_suspected_misread(None, self.UPLOADED))


if __name__ == "__main__":
    unittest.main()
