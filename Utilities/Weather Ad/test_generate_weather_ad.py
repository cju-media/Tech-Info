"""Unit tests for generate_weather_ad.py's pure helpers.

Run from this directory:  python -m unittest test_generate_weather_ad -v

Covers the shaping between Open-Meteo's response and what the card prints,
without hitting the network or Chrome.
"""

import datetime
import os
import sys
import unittest
from unittest import mock

from generate_weather_ad import (
    CARD_FRAGMENT,
    EVENTS_FOLDER_ID,
    ICONS,
    TZ,
    build_html,
    build_model,
    describe,
    icon_svg,
    parse_local,
    pop_text,
    round_temp,
)

SAMPLE = {
    'current': {
        'time': '2026-09-18T08:30', 'temperature_2m': 69.0,
        'apparent_temperature': 71.5, 'relative_humidity_2m': 72,
        'weather_code': 1, 'wind_speed_10m': 1.6,
    },
    'daily': {
        'time': ['2026-09-18', '2026-09-19', '2026-09-20', '2026-09-21',
                 '2026-09-22', '2026-09-23', '2026-09-24', '2026-09-25',
                 '2026-09-26', '2026-09-27', '2026-09-28'],
        'weather_code': [1, 3, 3, 3, 0, 0, 3, 2, 0, 0, 95],
        'temperature_2m_max': [80.3, 82.7, 82.9, 80.2, 80.8, 80.6, 83.8,
                               83.8, 86.7, 89.1, 92.8],
        'temperature_2m_min': [62.2, 60.1, 63.0, 68.7, 68.4, 69.7, 69.7,
                               70.4, 70.5, 71.4, 78.2],
        'precipitation_probability_max': [0, 0, 0, 1, 0, 0, 2, 1, 1, 2, 60],
        'uv_index_max': [7.4, 7.1, 7.0, 6.8, 6.9, 6.7, 6.5, 6.4, 6.2, 6.0, 5.8],
        'sunrise': ['2026-09-18T06:38'] * 11,
        'sunset': ['2026-09-18T18:55'] * 11,
    },
}


class Describe(unittest.TestCase):
    def test_known_codes(self):
        self.assertEqual(describe(0), ('Clear', 'sun'))
        self.assertEqual(describe(3), ('Overcast', 'cloud'))
        self.assertEqual(describe(95), ('Thunderstorm', 'storm'))

    def test_unknown_code_falls_back_rather_than_raising(self):
        # An unmapped code must not blank out a tile on the screens.
        self.assertEqual(describe(404), ('Cloudy', 'cloud'))

    def test_every_mapped_icon_actually_exists(self):
        # A typo in the table would render an empty tile, which is the kind
        # of thing nobody notices until it's on a wall.
        from generate_weather_ad import WMO
        for code, (_, key) in WMO.items():
            with self.subTest(code=code):
                self.assertIn(key, ICONS)

    def test_icon_svg_falls_back_for_an_unknown_key(self):
        self.assertIn('<path', icon_svg('nonsense', 40))


class Numbers(unittest.TestCase):
    def test_temps_round_to_whole_degrees(self):
        # "80.3°" on a forecast card just looks unsure of itself.
        self.assertEqual(round_temp(80.3), 80)
        self.assertEqual(round_temp(62.6), 63)

    def test_round_temp_passes_none_through(self):
        self.assertIsNone(round_temp(None))

    def test_pop_hidden_when_zero_or_missing(self):
        # A column of "0%" ten days running is noise in Los Angeles.
        self.assertEqual(pop_text(0), '')
        self.assertEqual(pop_text(None), '')

    def test_pop_shown_when_meaningful(self):
        self.assertEqual(pop_text(60), '60%')


class ParseLocal(unittest.TestCase):
    def test_attaches_church_time_to_a_naive_stamp(self):
        # Open-Meteo returns local wall time with no offset because we ask it
        # for America/Los_Angeles; it must not be read as UTC.
        dt = parse_local('2026-09-18T18:55')
        self.assertEqual(dt.hour, 18)
        self.assertEqual(dt.tzinfo, TZ)

    def test_junk_is_none(self):
        self.assertIsNone(parse_local('not a time'))
        self.assertIsNone(parse_local(None))


class BuildModel(unittest.TestCase):
    def setUp(self):
        self.today, self.days = build_model(SAMPLE)

    def test_today_comes_from_current_conditions(self):
        self.assertEqual(self.today['temp'], 69)
        self.assertEqual(self.today['feels'], 72)
        self.assertEqual(self.today['label'], 'Mainly Clear')
        self.assertEqual(self.today['wind'], 2)

    def test_today_high_low_come_from_the_first_daily_entry(self):
        self.assertEqual(self.today['high'], 80)
        self.assertEqual(self.today['low'], 62)

    def test_exactly_ten_forecast_days(self):
        self.assertEqual(len(self.days), 10)

    def test_today_is_not_repeated_in_the_ten(self):
        self.assertNotIn(self.today['date'].date(),
                         [d['date'].date() for d in self.days])

    def test_days_are_in_order_starting_tomorrow(self):
        self.assertEqual(self.days[0]['date'].strftime('%m-%d'), '09-19')
        self.assertEqual(self.days[-1]['date'].strftime('%m-%d'), '09-28')

    def test_a_stormy_day_carries_its_icon_and_chance(self):
        last = self.days[-1]
        self.assertEqual(last['icon'], 'storm')
        self.assertEqual(last['pop'], 60)

    def test_empty_forecast_raises_rather_than_rendering_a_blank_card(self):
        with self.assertRaises(RuntimeError):
            build_model({'current': {}, 'daily': {'time': []}})


class Markup(unittest.TestCase):
    def test_card_shows_today_and_all_ten_days(self):
        today, days = build_model(SAMPLE)
        out = build_html(today, days, datetime.datetime(2026, 9, 18, 8, 40, tzinfo=TZ))
        self.assertIn('69&deg;', out)
        self.assertIn('Mainly Clear', out)
        self.assertEqual(out.count('class="day"'), 10)
        self.assertIn('Friday, September 18', out)

    def test_zero_chance_days_print_nothing(self):
        today, days = build_model(SAMPLE)
        out = build_html(today, days, datetime.datetime(2026, 9, 18, 8, 40, tzinfo=TZ))
        self.assertIn('60%', out)          # the stormy day
        self.assertNotIn('>0%<', out.replace('class="v">0%<', ''))  # not in tiles


class CanonicalCardName(unittest.TestCase):
    """The card is found in Drive by a fragment of its filename, because
    renumber_rotation.py renames every copy into the folder's running order.
    That same fragment is what cleanup_events_folder.py refuses to trash, so
    the two have to agree or the card either goes stale or gets deleted."""

    def setUp(self):
        base = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
        for extra in (os.path.join(base, os.pardir, 'Worship Scripts', 'worship workflows'),
                      os.path.join(base, 'Rotation')):
            extra = os.path.abspath(extra)
            if extra not in sys.path:
                sys.path.insert(0, extra)

    def test_cleanup_never_trashes_this_card(self):
        from cleanup_events_folder import is_protected_flyer
        for name in (f'{CARD_FRAGMENT}.png', f'07 - {CARD_FRAGMENT}.PNG'):
            with self.subTest(name=name):
                self.assertTrue(is_protected_flyer(name),
                                f'cleanup_events_folder.py would auto-trash {name}')

    def test_rotation_recognises_this_card(self):
        # If the rotation job didn't see it as an info card it would be
        # numbered as an event flyer and break the alternation.
        from drive_cards import card_fragment
        self.assertEqual(card_fragment(f'09 - {CARD_FRAGMENT}.png'), CARD_FRAGMENT)

    def test_fragment_is_in_the_shared_card_table(self):
        from drive_cards import CARDS, ROTATION
        self.assertIn(CARD_FRAGMENT, CARDS)
        self.assertIn(CARD_FRAGMENT, ROTATION)

class DriveUpload(unittest.TestCase):
    """The card goes straight to Drive rather than through the git-backed
    upload queue -- hourly, that queue would add ~1GB of unprunable git
    objects a year."""

    def setUp(self):
        from generate_weather_ad import upload_to_drive
        self.upload = upload_to_drive

    def test_dry_run_uploads_nothing(self):
        # Must not need credentials, or a dry run can't be run locally.
        self.assertTrue(self.upload('/nonexistent.png', dry_run=True))

    def test_missing_credentials_fails_loudly(self):
        # Silently "succeeding" would leave a stale forecast on the screens
        # with a green tick next to it.
        with mock.patch.dict(os.environ, {'GDRIVE_OAUTH_JSON': '',
                                          'GDRIVE_SERVICE_ACCOUNT_JSON': ''},
                             clear=False):
            with self.assertRaises(RuntimeError) as ctx:
                self.upload('/nonexistent.png', dry_run=False)
        self.assertIn('credentials', str(ctx.exception).lower())

    def test_nothing_is_written_to_the_upload_queue(self):
        # The whole point of the direct upload: this module must not know
        # about the queue directory any more.
        import generate_weather_ad as g
        self.assertFalse(hasattr(g, 'QUEUE_DIR'))
        self.assertFalse(hasattr(g, 'queue_card'))

    def test_targets_the_events_ads_folder(self):
        self.assertRegex(EVENTS_FOLDER_ID, r'^[A-Za-z0-9_-]{20,}$')


if __name__ == '__main__':
    unittest.main()
