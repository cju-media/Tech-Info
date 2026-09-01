"""Unit tests for create_sermon_series.py's "which Sunday is this run about"
logic.

Run from this directory:  python -m unittest test_create_sermon_series -v

Regression cover for the 2026-08-30 miss: an Order of Worship processed on
Sunday morning triggered sermon_series.yml while it was still Sunday, and the
old get_upcoming_sunday() rolled the reference date forward a full week, so
the freshness guard rejected content that had just been generated for that
day's service. No title/description files reached Drive and the Monday video
migration then skipped the YouTube upload.
"""

import datetime
import unittest

from create_sermon_series import get_acceptable_service_sundays, resolve_service_sunday

# 2026-08-30 is a Sunday; the surrounding days are used throughout.
WED = datetime.date(2026, 8, 26)
SAT = datetime.date(2026, 8, 29)
SUN = datetime.date(2026, 8, 30)
MON = datetime.date(2026, 8, 31)
TUE = datetime.date(2026, 9, 1)
THU = datetime.date(2026, 9, 3)
NEXT_SUN = datetime.date(2026, 9, 6)
PREV_SUN = datetime.date(2026, 8, 23)


class AcceptableServiceSundays(unittest.TestCase):
    def test_midweek_points_at_the_coming_sunday(self):
        self.assertEqual(get_acceptable_service_sundays(WED), {SUN})
        self.assertEqual(get_acceptable_service_sundays(THU), {NEXT_SUN})

    def test_saturday_points_at_the_coming_sunday(self):
        self.assertEqual(get_acceptable_service_sundays(SAT), {SUN})

    def test_sunday_is_today_not_next_week(self):
        # The regression: this used to be {NEXT_SUN}.
        self.assertEqual(get_acceptable_service_sundays(SUN), {SUN})

    def test_monday_and_tuesday_also_accept_the_sunday_just_passed(self):
        self.assertEqual(get_acceptable_service_sundays(MON), {SUN, NEXT_SUN})
        self.assertEqual(get_acceptable_service_sundays(TUE), {SUN, NEXT_SUN})

    def test_accepts_a_datetime_as_well_as_a_date(self):
        noon_sun = datetime.datetime(2026, 8, 30, 12, 0)
        self.assertEqual(get_acceptable_service_sundays(noon_sun), {SUN})

    def test_year_boundary(self):
        # Wednesday 2026-12-30 -> Sunday 2027-01-03.
        self.assertEqual(
            get_acceptable_service_sundays(datetime.date(2026, 12, 30)),
            {datetime.date(2027, 1, 3)},
        )


class ResolveServiceSunday(unittest.TestCase):
    def test_missing_stamp_skips(self):
        sunday, reason = resolve_service_sunday(None, now=WED)
        self.assertIsNone(sunday)
        self.assertIn("no recorded target_date", reason)

    def test_sunday_morning_run_for_todays_service_proceeds(self):
        # The exact 2026-08-30 scenario that broke.
        sunday, reason = resolve_service_sunday(SUN, now=SUN)
        self.assertEqual(sunday, SUN)
        self.assertIsNone(reason)

    def test_monday_catch_up_for_yesterdays_service_proceeds(self):
        sunday, reason = resolve_service_sunday(SUN, now=MON)
        self.assertEqual(sunday, SUN)
        self.assertIsNone(reason)

    def test_normal_midweek_run_proceeds(self):
        sunday, reason = resolve_service_sunday(NEXT_SUN, now=datetime.date(2026, 9, 2))
        self.assertEqual(sunday, NEXT_SUN)
        self.assertIsNone(reason)

    def test_stale_content_by_wednesday_skips(self):
        # OW for the coming Sunday never processed; stamp still on last week.
        sunday, reason = resolve_service_sunday(SUN, now=datetime.date(2026, 9, 2))
        self.assertIsNone(sunday)
        self.assertIn("stale", reason)

    def test_two_week_old_stamp_skips_even_on_monday(self):
        sunday, reason = resolve_service_sunday(PREV_SUN, now=MON)
        self.assertIsNone(sunday)
        self.assertIn("stale", reason)


if __name__ == "__main__":
    unittest.main()
