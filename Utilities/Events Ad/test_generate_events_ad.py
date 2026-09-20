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
    CARD_FRAGMENT,
    EVENTS_FOLDER_ID,
    TZ,
    announce_new_events,
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

    def test_footer_carries_no_through_date(self):
        """The footer used to print "Events through <last date>", which only
        existed so cleanup_events_folder.py's vision read would expire the
        card correctly. The card is protected from that sweep now, so the
        line just aged the ad in the reader's eye."""
        card = {'name': 'Yamandu Costa', 'start': '2026-12-04T19:30:00-08:00',
                'venue': 'The Sanctuary', 'category': 'CONCERT',
                '_dt': datetime.datetime(2026, 12, 4, 19, 30, tzinfo=TZ)}
        out = build_html([card])
        self.assertNotIn('Events through', out)
        self.assertIn('Weekly Sunday Worship', out)

    def test_overflow_note_survives_and_only_shows_when_needed(self):
        # Dropping the date line must not take the "+N more" note with it --
        # that one does real work when the calendar outgrows the 3x3 grid.
        card = {'name': 'Yamandu Costa', 'start': '2026-12-04T19:30:00-08:00',
                'venue': 'The Sanctuary', 'category': 'CONCERT',
                '_dt': datetime.datetime(2026, 12, 4, 19, 30, tzinfo=TZ)}
        self.assertIn('+4 more at fccla.org/calendar', build_html([card], 4))
        self.assertNotIn('more at fccla.org/calendar', build_html([card], 0))

    def test_empty_calendar_publishes_a_check_the_calendar_card(self):
        """With nothing to list, the card used to be left untouched -- and
        since these cards are exempt from the cleanup sweep, nothing would
        ever expire it. A quiet stretch meant the screens advertised events
        that had all already happened.

        The wording points people at the calendar rather than announcing an
        empty one: "coming soon" told passers-by there was nothing on."""
        out = build_html([])
        self.assertIn('Check Out Upcoming Events at The Cathedral', out)
        self.assertNotIn('Coming Soon', out)
        self.assertNotIn('being planned', out)
        self.assertIn('fccla.org/calendar', out)
        self.assertIn('QR code linking to fccla.org/calendar', out)
        self.assertIn('class="empty"', out)
        self.assertNotIn('class="card', out)

    def test_populated_card_is_not_the_empty_one(self):
        card = {'name': 'Yamandu Costa', 'start': '2026-12-04T19:30:00-08:00',
                'venue': 'The Sanctuary', 'category': 'CONCERT',
                '_dt': datetime.datetime(2026, 12, 4, 19, 30, tzinfo=TZ)}
        out = build_html([card])
        self.assertIn('class="grid"', out)
        self.assertNotIn('Check Out Upcoming Events', out)

    def test_empty_card_has_a_stable_fingerprint(self):
        # It has to publish once and then stop, like any other card state.
        self.assertEqual(card_fingerprint([]), card_fingerprint([]))
        card = {'name': 'X', 'start': '2026-12-04T19:30:00-08:00',
                'venue': 'V', 'category': 'CONCERT'}
        self.assertNotEqual(card_fingerprint([]), card_fingerprint([card]))

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


class AnnounceNewEvents(unittest.TestCase):
    """A text when something new appears on fccla.org/calendar."""

    def card(self, name, day=4):
        return {'name': name, 'start': f'2026-12-{day:02d}T19:30:00-08:00',
                'venue': 'The Sanctuary', 'category': 'CONCERT',
                '_dt': datetime.datetime(2026, 12, day, 19, 30, tzinfo=TZ)}

    def test_first_run_seeds_silently(self):
        """Otherwise switching this on would text about every event already
        on the calendar."""
        state = {}
        fresh = announce_new_events([self.card('A'), self.card('B', 5)],
                                    state, dry_run=True)
        self.assertEqual(fresh, [])
        self.assertEqual(len(state['announced']), 2)

    def test_nothing_new_sends_nothing(self):
        cards = [self.card('A')]
        state = {'announced': [event_key(c) for c in cards]}
        self.assertEqual(announce_new_events(cards, state, dry_run=True), [])

    def test_a_new_event_is_picked_up(self):
        old = self.card('Old')
        state = {'announced': [event_key(old)]}
        fresh = announce_new_events([old, self.card('New', 5)], state, dry_run=True)
        self.assertEqual([c['name'] for c in fresh], ['New'])

    def test_a_renamed_or_rescheduled_event_counts_as_new(self):
        # The key is name + start, so a moved date is worth knowing about.
        original = self.card('Concert', 4)
        state = {'announced': [event_key(original)]}
        fresh = announce_new_events([self.card('Concert', 11)], state, dry_run=True)
        self.assertEqual(len(fresh), 1)

    def test_a_dry_run_does_not_mark_events_announced(self):
        # Otherwise a dry run would silence the real one that follows.
        old = self.card('Old')
        state = {'announced': [event_key(old)]}
        announce_new_events([old, self.card('New', 5)], state, dry_run=True)
        self.assertEqual(state['announced'], [event_key(old)])


if __name__ == '__main__':
    unittest.main()
