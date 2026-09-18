"""Unit tests for generate_events_ad.py's pure helpers.

Run from this directory:  python -m unittest test_generate_events_ad -v

These cover the parts that decide what lands on the campus screens without
touching Chrome, Gemini or Drive: reading the calendar's JSON-LD, filtering
to what's genuinely upcoming, and the two fingerprints that decide whether a
run calls Gemini and whether it re-renders at all.
"""

import datetime
import os
import sys
import unittest
from unittest import mock

from generate_events_ad import (
    CALENDAR_URL,
    CARD_FILENAME,
    EVENTS_FOLDER_ID,
    TZ,
    build_html,
    build_qr_data_uri,
    card_fingerprint,
    event_key,
    heuristic_recurring,
    is_cancelled,
    page_fingerprint,
    parse_ld_events,
    parse_start,
    smart_quotes,
    upcoming_events,
)


def ld(body):
    return f'<script type="application/ld+json">{body}</script>'


CONCERT = """{"@context":"https://schema.org","@type":"Event",
 "name":"Yamandu Costa: The Brazilian Guitar",
 "startDate":"2026-12-04T19:30:00-08:00","endDate":"2026-12-04T19:30:00-08:00",
 "eventStatus":"https://schema.org/EventScheduled",
 "location":{"@type":"Place","name":"The Sanctuary"}}"""

QUOTED = """{"@type":"Event","name":" Min Jin Lee discusses &quot;American Hagwon&quot;",
 "startDate":"2026-10-13T19:00:00-07:00",
 "eventStatus":"https://schema.org/EventScheduled",
 "location":{"@type":"Place","name":"The Sanctuary"}}"""

NOT_AN_EVENT = """{"@type":"Organization","name":"First Congregational Church"}"""


class ParseLdEvents(unittest.TestCase):
    def test_extracts_an_event(self):
        events = parse_ld_events(ld(CONCERT))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['name'], 'Yamandu Costa: The Brazilian Guitar')
        self.assertEqual(events[0]['venue'], 'The Sanctuary')

    def test_decodes_entities_and_strips_stray_whitespace(self):
        # The widget escapes quotes and sometimes leaves a leading space.
        events = parse_ld_events(ld(QUOTED))
        self.assertEqual(events[0]['name'], 'Min Jin Lee discusses "American Hagwon"')

    def test_ignores_non_event_ld_blocks(self):
        self.assertEqual(parse_ld_events(ld(NOT_AN_EVENT)), [])

    def test_survives_a_malformed_block(self):
        # One bad block must not lose the good ones on the same page.
        events = parse_ld_events(ld('{not json at all') + ld(CONCERT))
        self.assertEqual(len(events), 1)

    def test_skips_an_event_with_no_start(self):
        self.assertEqual(parse_ld_events(ld('{"@type":"Event","name":"No date"}')), [])

    def test_empty_page_is_empty(self):
        self.assertEqual(parse_ld_events('<html><body>nothing</body></html>'), [])


class UpcomingEvents(unittest.TestCase):
    def setUp(self):
        self.now = datetime.datetime(2026, 9, 18, 9, 0, tzinfo=TZ)

    def ev(self, name, start, status='https://schema.org/EventScheduled'):
        return {'name': name, 'start': start, 'venue': 'The Sanctuary', 'status': status}

    def test_past_events_are_dropped(self):
        got = upcoming_events([self.ev('Old', '2026-09-01T19:00:00-07:00')], self.now)
        self.assertEqual(got, [])

    def test_events_beyond_the_window_are_dropped(self):
        got = upcoming_events([self.ev('Far', '2027-06-01T19:00:00-07:00')], self.now)
        self.assertEqual(got, [])

    def test_cancelled_events_are_dropped(self):
        # The widget keeps cancelled events listed rather than removing them.
        got = upcoming_events(
            [self.ev('Off', '2026-10-01T19:00:00-07:00',
                     'https://schema.org/EventCancelled')], self.now)
        self.assertEqual(got, [])

    def test_upcoming_events_are_sorted_soonest_first(self):
        got = upcoming_events([
            self.ev('Later', '2026-11-01T19:00:00-07:00'),
            self.ev('Sooner', '2026-10-01T19:00:00-07:00'),
        ], self.now)
        self.assertEqual([e['name'] for e in got], ['Sooner', 'Later'])

    def test_junk_start_is_skipped_not_fatal(self):
        got = upcoming_events([
            self.ev('Junk', 'not-a-date'),
            self.ev('Good', '2026-10-01T19:00:00-07:00'),
        ], self.now)
        self.assertEqual([e['name'] for e in got], ['Good'])


class Fingerprints(unittest.TestCase):
    A = {'name': 'Concert', 'start': '2026-12-04T19:30:00-08:00',
         'venue': 'The Sanctuary', 'status': 'scheduled'}
    B = {'name': 'Talk', 'start': '2026-10-13T19:00:00-07:00',
         'venue': 'Shatto Chapel', 'status': 'scheduled'}

    def test_page_fingerprint_ignores_ordering(self):
        # The widget's emission order isn't guaranteed; a reshuffle must not
        # look like a calendar change and burn a Gemini call.
        self.assertEqual(page_fingerprint([self.A, self.B]),
                         page_fingerprint([self.B, self.A]))

    def test_page_fingerprint_changes_when_an_event_changes(self):
        moved = dict(self.A, start='2026-12-05T19:30:00-08:00')
        self.assertNotEqual(page_fingerprint([self.A]), page_fingerprint([moved]))

    def test_page_fingerprint_changes_when_an_event_is_added(self):
        self.assertNotEqual(page_fingerprint([self.A]),
                            page_fingerprint([self.A, self.B]))

    def test_card_fingerprint_tracks_what_is_drawn(self):
        card = dict(self.A, category='CONCERT')
        self.assertNotEqual(card_fingerprint([card]),
                            card_fingerprint([dict(card, category='ORGAN')]))

    def test_card_fingerprint_is_order_sensitive(self):
        # Card order is the display order, so a reorder is a real change.
        x = dict(self.A, category='CONCERT')
        y = dict(self.B, category='TALK')
        self.assertNotEqual(card_fingerprint([x, y]), card_fingerprint([y, x]))


class Helpers(unittest.TestCase):
    def test_event_key_distinguishes_repeats_of_a_series(self):
        a = {'name': 'Sunday Worship Service', 'start': '2026-09-20T10:30:00-07:00'}
        b = {'name': 'Sunday Worship Service', 'start': '2026-09-27T10:30:00-07:00'}
        self.assertNotEqual(event_key(a), event_key(b))

    def test_heuristic_catches_the_weekly_service(self):
        self.assertTrue(heuristic_recurring('Sunday Worship Service'))
        self.assertTrue(heuristic_recurring('sunday worship service'))

    def test_heuristic_leaves_real_events_alone(self):
        # Erring toward "not recurring" keeps concerts on the ad.
        for name in ('Yamandu Costa: The Brazilian Guitar',
                     'Book Soup: Susan Orlean',
                     'The Music of Ennio Morricone'):
            with self.subTest(name=name):
                self.assertFalse(heuristic_recurring(name))

    def test_is_cancelled(self):
        self.assertTrue(is_cancelled('https://schema.org/EventCancelled'))
        self.assertFalse(is_cancelled('https://schema.org/EventScheduled'))
        self.assertFalse(is_cancelled(''))

    def test_parse_start_keeps_offset(self):
        dt = parse_start('2026-12-04T19:30:00-08:00')
        self.assertEqual(dt.hour, 19)
        self.assertIsNotNone(dt.tzinfo)

    def test_utc_feed_is_normalised_to_church_time(self):
        """Regression, 2026-09-18: the widget localises to the browser's
        timezone, so the UTC CI runner scraped a 7pm concert as 02:00 the
        next day. Printed verbatim, every event on the live card was a day
        late at 2 in the morning."""
        from_utc = parse_start('2026-09-28T02:00:00+00:00')
        from_la = parse_start('2026-09-27T19:00:00-07:00')
        self.assertEqual(from_utc, from_la, 'same instant')
        self.assertEqual(from_utc.strftime('%b %-d %-I:%M %p'), 'Sep 27 7:00 PM')
        self.assertEqual(from_utc.day, 27)

    def test_winter_feed_uses_standard_time(self):
        # December is PST (-08:00), so the offset isn't a constant.
        self.assertEqual(
            parse_start('2026-12-05T03:30:00+00:00').strftime('%b %-d %-I:%M %p'),
            'Dec 4 7:30 PM')

    def test_naive_timestamp_is_assumed_church_local(self):
        self.assertEqual(parse_start('2026-09-27T19:00:00').hour, 19)

    def test_parse_start_rejects_junk(self):
        self.assertIsNone(parse_start('nope'))
        self.assertIsNone(parse_start(None))

    def test_smart_quotes_pairs_straight_quotes(self):
        self.assertEqual(smart_quotes('discusses "American Hagwon"'),
                         'discusses “American Hagwon”')

    def test_smart_quotes_leaves_clean_titles_alone(self):
        self.assertEqual(smart_quotes('Yamandu Costa'), 'Yamandu Costa')


class CanonicalCardName(unittest.TestCase):
    """The card's filename is load-bearing in three places: the generator
    writes it, cleanup_events_folder.py refuses to trash it, and
    upload_queue_to_drive.py replaces it in place instead of stacking copies.
    Renaming it in one place only would quietly break the other two -- the
    card would start getting auto-trashed, and duplicates would pile up on
    the screens. Tie them together here so that can't happen silently."""

    def setUp(self):
        workflows = os.path.abspath(os.path.join(
            os.path.dirname(__file__), os.pardir, os.pardir,
            'Worship Scripts', 'worship workflows'))
        if workflows not in sys.path:
            sys.path.insert(0, workflows)

    def test_cleanup_protects_the_name_the_generator_writes(self):
        from cleanup_events_folder import is_protected_flyer
        self.assertTrue(
            is_protected_flyer(CARD_FILENAME),
            f"cleanup_events_folder.py would auto-trash {CARD_FILENAME}",
        )

    def test_uploader_replaces_the_name_the_generator_writes(self):
        # The uploader reuses cleanup's predicate; assert the wiring holds
        # rather than trusting the import.
        from upload_queue_to_drive import is_protected_flyer as uploader_predicate
        self.assertTrue(
            uploader_predicate(CARD_FILENAME),
            f"upload_queue_to_drive.py would stack copies of {CARD_FILENAME}",
        )

    def test_generator_targets_the_events_ads_drop_zone(self):
        from upload_queue_to_drive import main  # noqa: F401  (import sanity)
        self.assertRegex(EVENTS_FOLDER_ID, r'^[A-Za-z0-9_-]{20,}$')


class QrCode(unittest.TestCase):
    """The card carries a "scan for tickets" QR. It is generated at render
    time and inlined, so the failure modes are: pointing somewhere other than
    the calendar, or silently vanishing when segno isn't installed."""

    def test_encodes_the_calendar_url(self):
        # Compare against segno's own encoding of the URL, so a refactor that
        # accidentally encoded, say, the Drive folder id would fail here.
        import segno
        expected = segno.make(CALENDAR_URL, error='m').svg_data_uri(
            scale=4, border=4, dark='#1A1A1A', light='#FFFFFF')
        self.assertEqual(build_qr_data_uri(CALENDAR_URL), expected)

    def test_is_an_inline_svg_data_uri(self):
        # Inline, because a CDN-hosted QR that fails to load would leave a
        # blank square on every screen and nothing would notice.
        uri = build_qr_data_uri(CALENDAR_URL)
        self.assertTrue(uri.startswith('data:image/svg+xml'), uri[:40])

    def test_uri_is_attribute_safe(self):
        # It goes straight into src="...", so an unescaped quote would break
        # the markup rather than just look wrong.
        self.assertNotIn('"', build_qr_data_uri(CALENDAR_URL))

    def test_calendar_url_still_points_at_the_calendar(self):
        self.assertEqual(CALENDAR_URL, 'https://fccla.org/calendar')

    def test_missing_segno_degrades_instead_of_crashing(self):
        # A missing dependency should cost the QR, not the whole card -- the
        # screens keep getting an up-to-date ad either way.
        with mock.patch.dict(sys.modules, {'segno': None}):
            self.assertEqual(build_qr_data_uri(CALENDAR_URL), '')

    def test_card_markup_includes_the_qr(self):
        now = datetime.datetime(2026, 9, 18, 9, 0, tzinfo=TZ)
        card = {'name': 'Yamandu Costa', 'start': '2026-12-04T19:30:00-08:00',
                'venue': 'The Sanctuary', 'category': 'CONCERT',
                '_dt': datetime.datetime(2026, 12, 4, 19, 30, tzinfo=TZ)}
        html_out = build_html([card])
        self.assertIn('data:image/svg+xml', html_out)
        self.assertIn('QR code linking to fccla.org/calendar', html_out)
        # The code stands on its own now -- no caption under it.
        self.assertNotIn('Scan for tickets', html_out)


if __name__ == '__main__':
    unittest.main()
