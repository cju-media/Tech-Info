"""Unit tests for audit_newsletter_card.py.

Run from this directory:  python -m unittest test_audit_newsletter_card -v

Two things matter here and they pull in opposite directions. The audit has
to catch a card still advertising last Saturday, and it has to not text
about a card that's fine -- an alert that cries wolf gets ignored, and then
it may as well not exist. Most of these are the second kind.
"""

import datetime
import unittest
from unittest import mock

import audit_newsletter_card as a
from audit_newsletter_card import (
    STALE_AFTER_DAYS,
    audit_items,
    audit_publication,
    describe,
    nearest_year,
    parse_when_date,
)

TODAY = datetime.date(2026, 9, 26)


def item(title='An Item', when='', date=''):
    return {'title': title, 'when': when, 'date': date}


class WhenParsing(unittest.TestCase):
    """The card never prints a year, and Gemini is told to leave the date
    field empty rather than guess -- so the visible text is sometimes the
    only evidence of when a thing happens."""

    def test_reads_the_formats_the_card_actually_uses(self):
        for when, expected in [
                ('Wednesday, Sept 23 | 7pm', '2026-09-23'),
                ('Saturday, Sept 26 | 11am', '2026-09-26'),
                ('Thursday, Sept 24 | 6-7pm', '2026-09-24'),
                ('Sat, Oct 4 | 8:30am', '2026-10-04'),
                ('September 3, 2026', '2026-09-03')]:
            with self.subTest(when=when):
                self.assertEqual(parse_when_date(when, TODAY).isoformat(), expected)

    def test_reads_ordinals(self):
        # "December 12th" matched nothing before the suffix was consumed:
        # the word boundary after the digits never came.
        for when, expected in [('December 12th | 9am', '2026-12-12'),
                               ('Oct 1st | 7pm', '2026-10-01'),
                               ('Nov 3rd | 6pm', '2026-11-03'),
                               ('Aug 22nd', '2026-08-22')]:
            with self.subTest(when=when):
                self.assertEqual(parse_when_date(when, TODAY).isoformat(), expected)

    def test_a_recurring_item_names_no_day_and_never_expires(self):
        for when in ('Tuesdays | 7-8:15pm | Zoom', 'Saturdays | 9am',
                     'Every third Sunday', 'First and third Sundays', ''):
            with self.subTest(when=when):
                self.assertIsNone(parse_when_date(when, TODAY))

    def test_a_year_end_date_resolves_forward_not_backward(self):
        # "Jan 3" seen in late December is next week, not eleven months ago.
        december = datetime.date(2026, 12, 29)
        self.assertEqual(parse_when_date('Jan 3 | 10am', december).isoformat(),
                         '2027-01-03')

    def test_a_year_start_date_resolves_backward_not_forward(self):
        january = datetime.date(2027, 1, 2)
        self.assertEqual(parse_when_date('Dec 29 | 7pm', january).isoformat(),
                         '2026-12-29')

    def test_an_unresolvable_date_says_nothing(self):
        # Feb 29 with no leap year within a year either way. A guess here
        # would text about an event that is perfectly fine.
        self.assertIsNone(nearest_year(2, 29, datetime.date(2026, 9, 26)))
        self.assertIsNone(parse_when_date('Feb 29 | noon', TODAY))

    def test_a_bare_time_is_not_read_as_a_date(self):
        self.assertIsNone(parse_when_date('7pm', TODAY))
        self.assertIsNone(parse_when_date('6-7:15pm | Zoom', TODAY))


class StaleItems(unittest.TestCase):
    def test_an_item_whose_text_has_passed_is_caught(self):
        stale, _ = audit_items([item('Workday', 'Saturday, Sept 19 | 9am')], TODAY)
        self.assertEqual([i['title'] for i, _ in stale], ['Workday'])

    def test_the_gap_this_exists_for(self):
        """An item with a past date in its visible text and no date field
        behind it. drop_past() can't see this one -- there's nothing for it
        to compare -- so it would sit on the screens indefinitely."""
        stale, _ = audit_items(
            [item('Workday', 'Saturday, Sept 19 | 9am', date='')], TODAY)
        self.assertEqual(len(stale), 1)

    def test_an_item_happening_today_is_not_stale(self):
        stale, _ = audit_items([item('Agapia', 'Saturday, Sept 26 | 11am')], TODAY)
        self.assertEqual(stale, [])

    def test_an_upcoming_item_is_not_stale(self):
        stale, _ = audit_items([item('Kickoff', 'Sunday, Sept 27 | 1pm')], TODAY)
        self.assertEqual(stale, [])

    def test_a_recurring_item_is_never_stale(self):
        stale, _ = audit_items([item('Book Group', 'Tuesdays | 7pm | Zoom')], TODAY)
        self.assertEqual(stale, [])

    def test_the_static_card_has_nothing_to_audit(self):
        self.assertEqual(audit_items([], TODAY), ([], []))

    def test_text_and_field_disagreeing_is_noted_not_alerted(self):
        """Not past yet, so nobody needs a text -- but one of the two dates
        will be wrong when it matters, so it goes in the log."""
        stale, odd = audit_items(
            [item('Talk', 'Sunday, Sept 27 | 1pm', date='2026-10-03')], TODAY)
        self.assertEqual(stale, [])
        self.assertEqual(len(odd), 1)

    def test_agreeing_dates_are_not_noted(self):
        _, odd = audit_items(
            [item('Talk', 'Sunday, Sept 27 | 1pm', date='2026-09-27')], TODAY)
        self.assertEqual(odd, [])


class Publication(unittest.TestCase):
    """The other half: whether the card up there is today's card at all.
    The scheduler drops most runs, so a day of them going missing leaves
    yesterday's card in Drive with nothing to correct it."""

    def published(self, at=TODAY, md5='abc'):
        return {'md5': md5, 'at': at.isoformat(), 'items': []}

    def copies(self, *md5s):
        return [{'name': f'0{n} - Meetinghouse-Newsletter.png', 'md5Checksum': m}
                for n, m in enumerate(md5s, start=1)]

    def test_a_current_card_raises_nothing(self):
        self.assertEqual(
            audit_publication(self.published(), self.copies('abc', 'abc'), TODAY),
            [])

    def test_a_card_published_yesterday_is_fine(self):
        yesterday = TODAY - datetime.timedelta(days=1)
        self.assertEqual(
            audit_publication(self.published(at=yesterday),
                              self.copies('abc'), TODAY), [])

    def test_a_card_nobody_has_republished_in_days_is_flagged(self):
        old = TODAY - datetime.timedelta(days=STALE_AFTER_DAYS)
        problems = audit_publication(self.published(at=old),
                                     self.copies('abc'), TODAY)
        self.assertTrue(any('stopped landing' in p for p in problems))

    def test_drive_holding_something_else_is_flagged(self):
        problems = audit_publication(self.published(),
                                     self.copies('abc', 'different'), TODAY)
        self.assertTrue(any('not the card' in p for p in problems))
        self.assertTrue(any('02 - Meetinghouse-Newsletter.png' in p
                            for p in problems))

    def test_the_card_missing_from_the_folder_is_flagged(self):
        problems = audit_publication(self.published(), [], TODAY)
        self.assertTrue(any('no copy' in p for p in problems))

    def test_drive_being_unreachable_is_not_an_alert(self):
        """An outage on Google's side is not a stale card, and texting about
        it would train the alert to be ignored."""
        self.assertEqual(
            audit_publication(self.published(), None, TODAY), [])

    def test_an_unparseable_timestamp_does_not_crash_the_audit(self):
        record = {'md5': 'abc', 'at': 'not a date', 'items': []}
        self.assertEqual(audit_publication(record, self.copies('abc'), TODAY), [])


class Message(unittest.TestCase):
    def test_it_names_the_items_and_the_problems(self):
        stale = [(item('Workday', 'Saturday, Sept 19 | 9am'),
                  datetime.date(2026, 9, 19))]
        summary = describe(stale, ['no copy of the card is in the folder'])
        self.assertIn('Workday', summary)
        self.assertIn('Saturday, Sept 19 | 9am', summary)
        self.assertIn('no copy', summary)

    def test_it_is_short_enough_for_a_text(self):
        stale = [(item(f'Item Number {n}', f'Saturday, Sept {n} | 9am'),
                  datetime.date(2026, 9, n)) for n in range(1, 5)]
        self.assertLess(len(describe(stale, [])), 320)


class EndToEnd(unittest.TestCase):
    def run_audit(self, state, copies):
        with mock.patch.object(a, 'load_state', return_value=state), \
             mock.patch.object(a, 'local_today', return_value=TODAY), \
             mock.patch.object(a, 'drive_copies', return_value=copies), \
             mock.patch.object(a, 'notify') as notify:
            return a.main(), notify

    def test_a_healthy_card_passes_quietly(self):
        state = {'published': {
            'md5': 'abc', 'at': TODAY.isoformat(),
            'items': [item('Agapia', 'Saturday, Sept 26 | 11am', '2026-09-26'),
                      item('Kickoff', 'Sunday, Sept 27 | 1pm', '2026-09-27')]}}
        code, notify = self.run_audit(
            state, [{'name': 'x', 'md5Checksum': 'abc'}])
        self.assertEqual(code, 0)
        notify.assert_not_called()

    def test_a_stale_card_fails_and_texts_once(self):
        state = {'published': {
            'md5': 'abc', 'at': TODAY.isoformat(),
            'items': [item('Workday', 'Saturday, Sept 19 | 9am'),
                      item('Old Talk', 'Tuesday, Sept 22 | 7pm')]}}
        code, notify = self.run_audit(
            state, [{'name': 'x', 'md5Checksum': 'abc'}])
        self.assertEqual(code, 1)
        notify.assert_called_once()
        self.assertIn('Workday', notify.call_args[0][0])
        self.assertIn('Old Talk', notify.call_args[0][0])

    def test_no_published_record_yet_is_not_a_failure(self):
        # The first audit after this was added, before the generator has
        # written a record.
        code, notify = self.run_audit({}, [])
        self.assertEqual(code, 0)
        notify.assert_not_called()

    def test_the_static_card_passes(self):
        state = {'published': {'md5': 'abc', 'at': TODAY.isoformat(), 'items': []}}
        code, notify = self.run_audit(
            state, [{'name': 'x', 'md5Checksum': 'abc'}])
        self.assertEqual(code, 0)
        notify.assert_not_called()


if __name__ == '__main__':
    unittest.main()
