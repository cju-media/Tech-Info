"""Unit tests for the Events_Ads rotation planner.

Run from this directory:  python -m unittest test_renumber_rotation -v

plan_rotation() decides the running order the campus screens play, so these
cover the properties that matter: strict alternation, idempotence, and not
losing or duplicating anything.
"""

import unittest

from drive_cards import CARDS, ROTATION, base_name, card_fragment
from renumber_rotation import MAX_CARDS, plan_rotation

FLYERS = ['5.png', 'book-meets-world-600-x-600.png', 'Candlelight_Poster.png',
          'image (1).jpeg', 'image.jpeg', 'image.jpeg', 'image.png',
          'Min-Jin-Lee-Social-post-.png', 'susan-orlean-600x600.png', 'V1.png']


def f(name, fid=None):
    return {'id': fid or name, 'name': name}


def folder(flyers=None, cards=()):
    return [f(n) for n in (FLYERS if flyers is None else flyers)] + [f(n) for n in cards]


def pattern(sequence):
    return ''.join('I' if card_fragment(x['name']) else 'e' for _, x in sequence)


def enough_cards(n):
    """n card copies, cycling through the rotation like the job does."""
    return [f'{ROTATION[i % len(ROTATION)]}-{i}.png' for i in range(n)]


class Alternation(unittest.TestCase):
    def test_strict_alternation_when_copies_match_flyers(self):
        seq, to_copy, to_trash = plan_rotation(folder(cards=enough_cards(len(FLYERS))))
        self.assertEqual(pattern(seq), 'Ie' * len(FLYERS))
        self.assertEqual((to_copy, to_trash), ([], []))

    def test_identically_named_flyers_are_still_separated(self):
        # Two files both named image.jpeg: no filename sorts between them,
        # which is exactly why numbering replaced sort-key prefixes.
        seq, _, _ = plan_rotation(folder(cards=enough_cards(len(FLYERS))))
        names = [x['name'] for _, x in seq]
        first, second = [i for i, n in enumerate(names) if n == 'image.jpeg']
        self.assertGreater(second - first, 1, 'the two image.jpeg files are adjacent')

    def test_too_few_copies_asks_for_more(self):
        seq, to_copy, to_trash = plan_rotation(folder(cards=enough_cards(4)))
        self.assertTrue(to_copy)
        self.assertEqual(to_trash, [])

    def test_surplus_copies_are_trashed(self):
        _, to_copy, to_trash = plan_rotation(folder(flyers=['a.png'],
                                                    cards=enough_cards(9)))
        self.assertTrue(to_trash)
        self.assertEqual(to_copy, [])

    def test_card_count_is_capped(self):
        many = [f'flyer-{i:02d}.png' for i in range(40)]
        seq, _, _ = plan_rotation(folder(flyers=many, cards=enough_cards(MAX_CARDS)))
        self.assertLessEqual(sum(1 for _, x in seq if card_fragment(x['name'])),
                             MAX_CARDS)

    def test_an_empty_folder_is_not_an_error(self):
        seq, to_copy, to_trash = plan_rotation([])
        self.assertEqual(seq, [])


class Numbering(unittest.TestCase):
    def test_slots_are_strictly_increasing(self):
        seq, _, _ = plan_rotation(folder(cards=enough_cards(len(FLYERS))))
        slots = [slot for slot, _ in seq]
        self.assertEqual(slots, sorted(slots))
        self.assertEqual(len(slots), len(set(slots)), 'two files share a slot')

    def test_cards_take_odd_slots_and_flyers_even(self):
        seq, _, _ = plan_rotation(folder(cards=enough_cards(len(FLYERS))))
        for slot, x in seq:
            with self.subTest(name=x['name']):
                self.assertEqual(slot % 2, 1 if card_fragment(x['name']) else 0)

    def test_every_file_is_placed_exactly_once(self):
        contents = folder(cards=enough_cards(len(FLYERS)))
        seq, _, _ = plan_rotation(contents)
        placed = [x['id'] for _, x in seq]
        self.assertEqual(sorted(placed), sorted(c['id'] for c in contents))


class BaseName(unittest.TestCase):
    def test_strips_a_rotation_prefix(self):
        self.assertEqual(base_name('07 - Candlelight_Poster.png'),
                         'Candlelight_Poster.png')

    def test_leaves_an_unnumbered_name_alone(self):
        self.assertEqual(base_name('Candlelight_Poster.png'),
                         'Candlelight_Poster.png')

    def test_renumbering_is_idempotent(self):
        # Without stripping, a second run would produce "03 - 07 - Poster.png".
        once = f"07 - {base_name('Candlelight_Poster.png')}"
        twice = f"03 - {base_name(once)}"
        self.assertEqual(twice, '03 - Candlelight_Poster.png')

    def test_a_flyer_named_like_a_number_is_not_mangled(self):
        self.assertEqual(base_name('5.png'), '5.png')
        self.assertEqual(base_name('V1.png'), 'V1.png')


class Classification(unittest.TestCase):
    def test_cards_are_recognised_however_they_are_numbered(self):
        for name in ('Upcoming-Service.png', '01 - Upcoming-Service.png',
                     'T-Upcoming-Service.png', '11 - upcoming-service (1).PNG'):
            with self.subTest(name=name):
                self.assertEqual(card_fragment(name), 'upcoming-service')

    def test_event_flyers_are_not_cards(self):
        for name in FLYERS:
            with self.subTest(name=name):
                self.assertIsNone(card_fragment(name))

    def test_every_rotation_entry_has_a_default_name(self):
        for fragment in ROTATION:
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, CARDS)


if __name__ == '__main__':
    unittest.main()
