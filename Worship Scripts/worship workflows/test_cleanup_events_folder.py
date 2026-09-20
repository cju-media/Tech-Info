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
    PROTECTED_NAME_FRAGMENTS,
    SUSPECT_MISREAD_DAYS,
    build_date_prompt,
    is_protected_flyer,
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


class BuildDatePrompt(unittest.TestCase):
    def test_anchors_todays_date_and_asks_for_nearest_year(self):
        prompt = build_date_prompt(datetime.date(2026, 9, 6))
        self.assertIn("Today's date is 2026-09-06.", prompt)
        self.assertIn("closest to today", prompt)
        # JSON schema braces must survive the f-string.
        self.assertIn('{"has_date": true or false, "last_date": "YYYY-MM-DD" or null}', prompt)


class IsProtectedFlyer(unittest.TestCase):
    """The "at a glance" roundup card lists many dates at once, so Gemini would
    read one of them and trash the card while later events on it are still
    upcoming. It's skipped by name instead."""

    def test_the_at_a_glance_card_is_protected(self):
        self.assertTrue(is_protected_flyer("Events-At-A-Glance.png"))

    def test_the_weather_card_is_protected(self):
        # It prints eleven dates and none of them expires it; it's replaced
        # every few hours instead.
        self.assertTrue(is_protected_flyer("LA-Weather-Forecast.png"))

    def test_the_service_card_is_protected(self):
        # Built from the stream thumbnail, which prints the service date.
        self.assertTrue(is_protected_flyer("Upcoming-Service.png"))

    def test_match_is_case_insensitive(self):
        self.assertTrue(is_protected_flyer("events-at-a-glance.PNG"))

    def test_drive_collision_suffix_still_protected(self):
        # Drive renames a same-named upload rather than replacing it.
        self.assertTrue(is_protected_flyer("Events-At-A-Glance (1).png"))

    def test_future_revision_suffix_still_protected(self):
        self.assertTrue(is_protected_flyer("Events-At-A-Glance v2.png"))

    def test_extension_is_irrelevant(self):
        self.assertTrue(is_protected_flyer("Events-At-A-Glance.jpg"))

    def test_ordinary_flyers_are_not_protected(self):
        for name in (
            "Yamandu Costa Brazilian Guitar.png",
            "Morricone Candlelit.jpg",
            "upcoming-events.png",
            "Book Soup Susan Orlean.jpeg",
        ):
            with self.subTest(name=name):
                self.assertFalse(is_protected_flyer(name))

    def test_missing_or_empty_name_is_not_protected(self):
        self.assertFalse(is_protected_flyer(None))
        self.assertFalse(is_protected_flyer(""))

    def test_fragments_are_stored_lowercased(self):
        # is_protected_flyer lowercases the stem, so a non-lowercase entry here
        # would silently never match.
        for fragment in PROTECTED_NAME_FRAGMENTS:
            with self.subTest(fragment=fragment):
                self.assertEqual(fragment, fragment.lower())

    def test_sort_key_prefixed_copies_are_protected(self):
        # Each card is uploaded several times with a sort-key prefix so it
        # interleaves with the flyers; every copy must be spared.
        for name in ('1-Upcoming-Service.png', 'E-Upcoming-Service.png',
                     'A-LA-Weather-Forecast.png', 'W-LA-Weather-Forecast.png',
                     'C-Events-At-A-Glance.png', 'N-Events-At-A-Glance.png'):
            with self.subTest(name=name):
                self.assertTrue(is_protected_flyer(name))


if __name__ == "__main__":
    unittest.main()
