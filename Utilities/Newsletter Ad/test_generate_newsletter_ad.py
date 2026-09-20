"""Unit tests for generate_newsletter_ad.py.

Run from this directory:  python -m unittest test_generate_newsletter_ad -v

The card is static, so the things worth guarding are the sign-up link it
encodes and its registration with the rotation and the cleanup sweep.
"""

import os
import sys
import unittest
from unittest import mock

from generate_newsletter_ad import (
    CARD_FRAGMENT,
    SIGNUP_URL,
    build_html,
    build_qr_data_uri,
)


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


class Markup(unittest.TestCase):
    def test_card_carries_the_code_and_the_ask(self):
        out = build_html()
        self.assertIn('data:image/svg+xml', out)
        self.assertIn('Subscribe for Weekly News', out)
        self.assertIn('Meetinghouse Newsletter', out)
        self.assertIn('fccla.org/meetinghouse-newsletter', out)

    def test_card_renders_without_segno(self):
        # No QR is a worse card, not a failed run.
        with mock.patch.dict(sys.modules, {'segno': None}):
            out = build_html()
        self.assertIn('Subscribe for Weekly News', out)


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
        for name in (f'{CARD_FRAGMENT}.png', f'09 - Meetinghouse-Newsletter.png'):
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


if __name__ == '__main__':
    unittest.main()
