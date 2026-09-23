"""Unit tests for generate_newsletter_ad.py.

Run from this directory:  python -m unittest test_generate_newsletter_ad -v

The card used to be static, so the only things worth guarding were the
sign-up link and the card's registration with the rotation. Now that it
carries the week's actual newsletter items, the things that can go wrong are
the ones that would put something *wrong* on a screen: a stale item that's
already happened, a title cut mid-word, an archive read in the wrong order,
or a failure anywhere in the chain taking the card down instead of falling
back to the sign-up design.
"""

import datetime
import os
import sys
import unittest
from unittest import mock

import generate_newsletter_ad as g
from generate_newsletter_ad import (
    CARD_FRAGMENT,
    MAX_ITEMS,
    MIN_ITEMS,
    SIGNUP_URL,
    build_html,
    build_qr_data_uri,
    clean_item,
    clip,
    drop_past,
    issue_text,
    order_items,
    parse_archive,
    parse_title_date,
)

TODAY = datetime.date(2026, 9, 22)


def item(title='An Item', when='', date='', blurb='A thing happening.'):
    return clean_item({'title': title, 'when': when, 'date': date, 'blurb': blurb})


ARCHIVE_DOM = '''
<html><body><div class="archive">
  <a href="https://conta.cc/aaa">The Meetinghouse This Week - September 17th, 2026</a>
  <a href="https://conta.cc/bbb">The Meetinghouse This Week - September 10th, 2026</a>
  <a href="https://conta.cc/ccc">The Meetinghouse This Week - September 3rd, 2026</a>
  <a href="https://www.fccla.org/calendar">Calendar</a>
</div></body></html>
'''


class SignupLink(unittest.TestCase):
    def test_points_at_the_newsletter_page(self):
        self.assertEqual(SIGNUP_URL,
                         'https://www.fccla.org/meetinghouse-newsletter')

    def test_qr_encodes_the_signup_url(self):
        import segno
        expected = segno.make(SIGNUP_URL, error='m').svg_data_uri(
            scale=4, border=4, dark='#1A1A1A', light='#FFFFFF')
        self.assertEqual(build_qr_data_uri(SIGNUP_URL), expected)

    def test_qr_is_an_inline_svg_data_uri(self):
        # Inlined, so a render never depends on reaching a CDN -- a code that
        # failed to load would be a blank square on every screen.
        self.assertTrue(build_qr_data_uri(SIGNUP_URL).startswith('data:image/svg+xml'))

    def test_qr_uri_is_attribute_safe(self):
        self.assertNotIn('"', build_qr_data_uri(SIGNUP_URL))

    def test_missing_segno_degrades_instead_of_crashing(self):
        with mock.patch.dict(sys.modules, {'segno': None}):
            self.assertEqual(build_qr_data_uri(SIGNUP_URL), '')

    def test_the_archive_and_the_signup_are_the_same_page(self):
        # They're scraped and encoded separately; if one moves the other
        # almost certainly has to as well.
        self.assertEqual(g.ARCHIVE_URL, SIGNUP_URL)


class Archive(unittest.TestCase):
    def test_finds_every_issue_and_ignores_other_links(self):
        issues = parse_archive(ARCHIVE_DOM)
        self.assertEqual([i['url'] for i in issues],
                         ['https://conta.cc/aaa', 'https://conta.cc/bbb',
                          'https://conta.cc/ccc'])

    def test_newest_issue_comes_first(self):
        self.assertEqual(parse_archive(ARCHIVE_DOM)[0]['date'],
                         datetime.date(2026, 9, 17))

    def test_the_dates_decide_the_order_not_the_page(self):
        """The page lists newest-first today. If that ever slipped, taking
        [0] on trust would advertise a month-old issue for a week."""
        shuffled = '''
          <a href="https://conta.cc/old">The Meetinghouse This Week - August 6th, 2026</a>
          <a href="https://conta.cc/new">The Meetinghouse This Week - September 17th, 2026</a>
        '''
        self.assertEqual(parse_archive(shuffled)[0]['url'], 'https://conta.cc/new')

    def test_undated_links_sort_behind_dated_ones(self):
        dom = ('<a href="https://conta.cc/odd">Special Edition</a>'
               '<a href="https://conta.cc/new">The Meetinghouse This Week - '
               'September 17th, 2026</a>')
        self.assertEqual(parse_archive(dom)[0]['url'], 'https://conta.cc/new')

    def test_an_empty_archive_is_not_a_crash(self):
        self.assertEqual(parse_archive('<html><body>nothing</body></html>'), [])

    def test_parses_every_ordinal_suffix(self):
        for text, expected in [
                ('- September 1st, 2026', datetime.date(2026, 9, 1)),
                ('- September 2nd, 2026', datetime.date(2026, 9, 2)),
                ('- September 3rd, 2026', datetime.date(2026, 9, 3)),
                ('- September 17th, 2026', datetime.date(2026, 9, 17)),
                ('- September 17, 2026', datetime.date(2026, 9, 17))]:
            with self.subTest(text=text):
                self.assertEqual(parse_title_date(text), expected)

    def test_a_nonsense_date_is_none_not_an_exception(self):
        for text in ('', 'The Meetinghouse', '- Septembruary 40th, 2026',
                     '- February 30th, 2026'):
            with self.subTest(text=text):
                self.assertIsNone(parse_title_date(text))


class IssueText(unittest.TestCase):
    def test_strips_markup_and_keeps_block_boundaries(self):
        out = issue_text('<div>Men&#39;s Group</div><div>Wednesday | 7pm</div>')
        self.assertEqual(out, "Men's Group\nWednesday | 7pm")

    def test_drops_script_and_style_content(self):
        out = issue_text('<style>.a{color:red}</style><script>var x=1;</script>'
                         '<p>Real copy</p>')
        self.assertEqual(out, 'Real copy')

    def test_strips_the_zero_width_characters_constant_contact_inserts(self):
        self.assertEqual(issue_text('<p>﻿Food@First​</p>'), 'Food@First')


class ItemHygiene(unittest.TestCase):
    def test_clip_backs_up_to_a_word_boundary(self):
        out = clip('A Conversation with Mother Agapia and Friends of the Holy Land', 60)
        self.assertTrue(out.endswith('…'))
        self.assertNotIn('Holy L', out)
        self.assertLessEqual(len(out), 61)

    def test_clip_leaves_short_text_exactly_alone(self):
        self.assertEqual(clip('FCCLA Men’s Group', 60), 'FCCLA Men’s Group')

    def test_clip_still_cuts_a_single_unbroken_word(self):
        out = clip('x' * 200, 40)
        self.assertLessEqual(len(out), 41)

    def test_an_item_without_a_title_is_dropped(self):
        self.assertIsNone(clean_item({'title': '  ', 'blurb': 'x'}))
        self.assertIsNone(clean_item('not a dict'))

    def test_an_unparseable_date_is_discarded_not_kept(self):
        # A bad date that survived would be compared against today and blow
        # up the run; an empty one just means "never expires".
        self.assertEqual(item(date='next Tuesday')['date'], '')
        self.assertEqual(item(date='2026-13-45')['date'], '')

    def test_a_good_date_survives(self):
        self.assertEqual(item(date='2026-09-26')['date'], '2026-09-26')

    def test_trailing_punctuation_is_trimmed_from_titles(self):
        self.assertEqual(item(title='Join Us!')['title'], 'Join Us')


class Staleness(unittest.TestCase):
    """The newsletter is weekly and the card renders hourly, so without this
    the screens would spend half of every week advertising last Saturday."""

    def test_a_past_item_is_dropped(self):
        kept = drop_past([item(title='Gone', date='2026-09-20')], TODAY)
        self.assertEqual(kept, [])

    def test_an_item_happening_today_is_kept(self):
        kept = drop_past([item(title='Today', date=TODAY.isoformat())], TODAY)
        self.assertEqual([i['title'] for i in kept], ['Today'])

    def test_an_undated_item_never_expires(self):
        kept = drop_past([item(title='Volunteers Needed')], TODAY)
        self.assertEqual([i['title'] for i in kept], ['Volunteers Needed'])

    def test_dated_items_run_soonest_first(self):
        ordered = order_items([item(title='Later', date='2026-10-01'),
                               item(title='Sooner', date='2026-09-23')])
        self.assertEqual([i['title'] for i in ordered], ['Sooner', 'Later'])

    def test_undated_items_go_last_in_the_order_given(self):
        ordered = order_items([item(title='Open A'), item(title='Open B'),
                               item(title='Dated', date='2026-09-23')])
        self.assertEqual([i['title'] for i in ordered],
                         ['Dated', 'Open A', 'Open B'])


class Markup(unittest.TestCase):
    def test_the_items_card_shows_the_items_and_the_code(self):
        out = build_html({'date': datetime.date(2026, 9, 17)},
                         [item(title='Men’s Group', when='Wed | 7pm',
                               blurb='In the Amanda Scott Room.'),
                          item(title='Garden Workday', when='Sat | 9am')])
        self.assertIn('Inside This Week', out)
        self.assertIn('September 17, 2026', out)
        self.assertIn('Garden Workday', out)
        self.assertIn('In the Amanda Scott Room.', out)
        self.assertIn('data:image/svg+xml', out)
        self.assertIn('fccla.org/', out)

    def test_no_items_falls_back_to_the_static_signup_card(self):
        """Every failure path in the chain lands here. A plain card is a
        worse card; a card advertising a talk that already happened is a
        wrong one, and this is what makes falling back safe."""
        out = build_html(None, [])
        self.assertIn('Subscribe for Weekly News', out)
        self.assertIn('data:image/svg+xml', out)
        self.assertNotIn('Inside This Week', out)

    def test_the_old_no_argument_call_still_renders(self):
        self.assertIn('Subscribe for Weekly News', build_html())

    def test_item_text_is_html_escaped(self):
        out = build_html(None, [item(title='Tea & Toast',
                                     blurb='<script>alert(1)</script>')])
        self.assertIn('Tea &amp; Toast', out)
        self.assertNotIn('<script>alert(1)</script>', out)

    def test_an_issue_without_a_date_just_omits_the_stamp(self):
        out = build_html({'date': None}, [item()])
        self.assertIn('Inside This Week', out)
        self.assertNotIn('class="issue"', out)

    def test_the_card_renders_without_segno(self):
        # No QR is a worse card, not a failed run.
        with mock.patch.dict(sys.modules, {'segno': None}):
            out = build_html(None, [item()])
        self.assertIn('Inside This Week', out)

    def test_both_layouts_are_the_screen_size(self):
        for label, out in (('items', build_html(None, [item()])),
                           ('static', build_html())):
            with self.subTest(layout=label):
                self.assertIn('width:1920px;height:1080px', out)

    def test_no_unsubstituted_format_placeholders_survive(self):
        # The shells are one .format() over CSS full of braces; a stray
        # single brace silently eats a rule or raises at render time.
        for label, out in (('items', build_html({'date': TODAY}, [item()])),
                           ('static', build_html())):
            with self.subTest(layout=label):
                self.assertNotIn('{items}', out)
                self.assertNotIn('{qr}', out)
                self.assertNotIn('{issue}', out)


class Thresholds(unittest.TestCase):
    def test_a_nearly_empty_column_is_not_worth_showing(self):
        self.assertGreaterEqual(MIN_ITEMS, 2)
        self.assertLessEqual(MIN_ITEMS, MAX_ITEMS)

    def test_the_card_holds_what_the_layout_was_drawn_for(self):
        # Four items at the drawn type sizes fill the column; more would
        # overflow it, and the overflow wouldn't be visible in a PNG.
        self.assertEqual(MAX_ITEMS, 4)


class Registration(unittest.TestCase):
    """A card nobody knows about is invisible: the rotation wouldn't place
    it, and the cleanup sweep would eventually trash it."""

    def setUp(self):
        base = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
        for extra in (os.path.join(base, os.pardir, 'Worship Scripts', 'worship workflows'),
                      os.path.join(base, 'Rotation')):
            extra = os.path.abspath(extra)
            if extra not in sys.path:
                sys.path.insert(0, extra)

    def test_cleanup_never_trashes_this_card(self):
        from cleanup_events_folder import is_protected_flyer
        for name in (f'{CARD_FRAGMENT}.png', '09 - Meetinghouse-Newsletter.png'):
            with self.subTest(name=name):
                self.assertTrue(is_protected_flyer(name),
                                f'cleanup_events_folder.py would auto-trash {name}')

    def test_rotation_recognises_and_schedules_this_card(self):
        from drive_cards import CARDS, ROTATION, card_fragment
        self.assertIn(CARD_FRAGMENT, CARDS)
        self.assertIn(CARD_FRAGMENT, ROTATION)
        self.assertEqual(card_fragment('09 - Meetinghouse-Newsletter.png'),
                         CARD_FRAGMENT)

    def test_it_does_not_collide_with_another_card(self):
        from drive_cards import CARDS, card_fragment
        for fragment, default in CARDS.items():
            if fragment == CARD_FRAGMENT:
                continue
            with self.subTest(other=default):
                self.assertNotEqual(card_fragment(default), CARD_FRAGMENT)


class Resilience(unittest.TestCase):
    """gather_items() is the whole network-facing chain. Nothing in it should
    be able to take the card down -- every failure returns no items, which
    renders the static sign-up card."""

    def setUp(self):
        self.state = {'issue_url': None, 'issue_fingerprint': None, 'items': []}

    def test_an_unreachable_archive_falls_back(self):
        with mock.patch.object(g, 'dump_dom', side_effect=RuntimeError('no chrome')):
            issue, items = g.gather_items(self.state, TODAY, force=False)
        self.assertEqual(items, [])

    def test_an_archive_with_no_issues_falls_back(self):
        with mock.patch.object(g, 'dump_dom', return_value='<html></html>'):
            issue, items = g.gather_items(self.state, TODAY, force=False)
        self.assertEqual(items, [])

    def test_an_unreachable_issue_falls_back(self):
        with mock.patch.object(g, 'dump_dom', return_value=ARCHIVE_DOM), \
             mock.patch.object(g, 'fetch_issue', side_effect=OSError('timeout')):
            issue, items = g.gather_items(self.state, TODAY, force=False)
        self.assertEqual(items, [])
        self.assertEqual(issue['url'], 'https://conta.cc/aaa')

    def test_a_suspiciously_short_issue_falls_back(self):
        # A redirect to an error page would parse fine and read as an issue
        # with nothing in it, which is worse than no items at all.
        with mock.patch.object(g, 'dump_dom', return_value=ARCHIVE_DOM), \
             mock.patch.object(g, 'fetch_issue', return_value='<p>Not found</p>'):
            _, items = g.gather_items(self.state, TODAY, force=False)
        self.assertEqual(items, [])

    def test_no_api_key_falls_back_without_calling_gemini(self):
        with mock.patch.object(g, 'dump_dom', return_value=ARCHIVE_DOM), \
             mock.patch.object(g, 'fetch_issue', return_value='<p>' + 'copy ' * 300 + '</p>'), \
             mock.patch.object(g, 'gemini_highlights') as gem, \
             mock.patch.dict(os.environ, {'GEMINI_API_KEY': ''}, clear=False):
            _, items = g.gather_items(self.state, TODAY, force=False)
        self.assertEqual(items, [])
        gem.assert_not_called()

    def test_a_failed_model_call_leaves_the_cache_untouched(self):
        """A bad run must not overwrite good cached items with nothing --
        that would turn one flaky call into a week of static cards."""
        self.state.update({'issue_url': 'https://conta.cc/aaa',
                           'issue_fingerprint': 'stale',
                           'items': [item(title='Cached Item')]})
        with mock.patch.object(g, 'dump_dom', return_value=ARCHIVE_DOM), \
             mock.patch.object(g, 'fetch_issue', return_value='<p>' + 'new ' * 300 + '</p>'), \
             mock.patch.object(g, 'gemini_highlights', return_value=[]), \
             mock.patch.dict(os.environ, {'GEMINI_API_KEY': 'k'}, clear=False):
            _, items = g.gather_items(self.state, TODAY, force=False)
        self.assertEqual(items, [])
        self.assertEqual(self.state['items'][0]['title'], 'Cached Item')
        self.assertEqual(self.state['issue_fingerprint'], 'stale')


class Caching(unittest.TestCase):
    """Gemini costs a call; the newsletter changes once a week. The cache is
    what keeps those two facts apart."""

    def setUp(self):
        self.body = '<p>' + 'copy ' * 300 + '</p>'
        self.fingerprint = g.text_fingerprint(g.issue_text(self.body))
        self.state = {'issue_url': 'https://conta.cc/aaa',
                      'issue_fingerprint': self.fingerprint,
                      'items': [item(title='Cached Item')]}

    def _run(self, force=False, gem=None):
        with mock.patch.object(g, 'dump_dom', return_value=ARCHIVE_DOM), \
             mock.patch.object(g, 'fetch_issue', return_value=self.body), \
             mock.patch.object(g, 'gemini_highlights',
                               return_value=gem if gem is not None else []) as spy, \
             mock.patch.dict(os.environ, {'GEMINI_API_KEY': 'k'}, clear=False):
            issue, items = g.gather_items(self.state, TODAY, force=force)
        return issue, items, spy

    def test_an_unchanged_issue_reuses_the_cache(self):
        _, items, spy = self._run()
        spy.assert_not_called()
        self.assertEqual([i['title'] for i in items], ['Cached Item'])

    def test_force_refresh_asks_again_anyway(self):
        fresh = [item(title='Fresh Item')]
        _, items, spy = self._run(force=True, gem=fresh)
        spy.assert_called_once()
        self.assertEqual([i['title'] for i in items], ['Fresh Item'])

    def test_a_changed_issue_asks_again_and_updates_the_cache(self):
        self.state['issue_fingerprint'] = 'something else'
        fresh = [item(title='Fresh Item')]
        _, items, spy = self._run(gem=fresh)
        spy.assert_called_once()
        self.assertEqual([i['title'] for i in items], ['Fresh Item'])
        self.assertEqual(self.state['issue_fingerprint'], self.fingerprint)

    def test_a_new_issue_url_asks_again_even_at_the_same_fingerprint(self):
        self.state['issue_url'] = 'https://conta.cc/older'
        _, _, spy = self._run(gem=[item(title='Fresh Item')])
        spy.assert_called_once()

    def test_a_long_model_answer_is_capped_at_what_fits(self):
        self.state['issue_fingerprint'] = 'something else'
        fresh = [item(title=f'Item {n}') for n in range(10)]
        _, items, _ = self._run(gem=fresh)
        self.assertEqual(len(items), MAX_ITEMS)


if __name__ == '__main__':
    unittest.main()
