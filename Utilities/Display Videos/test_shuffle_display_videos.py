"""Unit tests for shuffle_display_videos.py.

Run from this directory:  python -m unittest test_shuffle_display_videos -v

The things to guard: every video gets a different code, codes never stack
("2B 3F building.mov"), a name that merely looks like a code ("3D Tour.mov")
keeps it, a hand rename sticks, and "new" mode leaves the existing order be.
"""

import random
import unittest

from shuffle_display_videos import (
    CODES, GIVEN_KEY, ORIGINAL_KEY, adjacent_repeats, base_name, current_code,
    group_key, plan)


def shuffled(name, original):
    return {'id': name, 'name': name,
            'appProperties': {ORIGINAL_KEY: original, GIVEN_KEY: name}}


def fresh(name):
    return {'id': name, 'name': name}


class BaseName(unittest.TestCase):
    def test_strips_our_code(self):
        self.assertEqual(base_name('3F building 2.mov',
                                   {ORIGINAL_KEY: 'building 2.mov', GIVEN_KEY: '3F building 2.mov'}),
                         'building 2.mov')

    def test_keeps_a_name_that_only_looks_like_a_code(self):
        self.assertEqual(base_name('3D Tour.mov', None), '3D Tour.mov')
        self.assertIsNone(current_code('3D Tour.mov', None))

    def test_hand_rename_is_the_new_own_name(self):
        props = {ORIGINAL_KEY: 'building 2.mov', GIVEN_KEY: '3F building 2.mov'}
        self.assertEqual(base_name('Sanctuary drone.mov', props), 'Sanctuary drone.mov')

    def test_hand_rename_keeping_our_code_drops_the_code(self):
        props = {ORIGINAL_KEY: 'building 2.mov', GIVEN_KEY: '3F building 2.mov'}
        self.assertEqual(base_name('3F Building exterior.mov', props), 'Building exterior.mov')


class Plan(unittest.TestCase):
    FILES = [fresh('building 1.mov'), fresh('Choir.mov'), fresh('3D Tour.mov'),
             shuffled('7C Garden.mov', 'Garden.mov')]

    def test_all_gives_every_file_a_unique_code_once(self):
        changes = plan(self.FILES, 'all', random.Random(1))
        names = [n for _, n in changes]
        codes = [n.split(' ', 1)[0] for n in names]
        self.assertEqual(len(set(codes)), len(codes))
        self.assertTrue(all(c in CODES for c in codes))
        self.assertIn('Garden.mov', {n.split(' ', 1)[1] for n in names})
        self.assertIn('3D Tour.mov', {n.split(' ', 1)[1] for n in names})
        garden = next(n for f, n in changes if f['id'] == '7C Garden.mov')
        self.assertRegex(garden, r'^[1-9][A-Z] Garden\.mov$')   # swapped, not stacked

    def test_all_actually_reorders(self):
        orders = set()
        for seed in range(20):
            renamed = dict((f['id'], n) for f, n in plan(self.FILES, 'all', random.Random(seed)))
            names = {f['id']: renamed.get(f['id'], f['name']) for f in self.FILES}
            orders.add(tuple(sorted(names, key=names.get)))
        self.assertGreater(len(orders), 5)

    def test_new_only_touches_uncoded_files_and_avoids_taken_codes(self):
        changes = plan(self.FILES, 'new', random.Random(2))
        self.assertEqual({f['id'] for f, _ in changes},
                         {'building 1.mov', 'Choir.mov', '3D Tour.mov'})
        self.assertNotIn('7C', {n.split(' ', 1)[0] for _, n in changes})

    def test_new_with_nothing_new_changes_nothing(self):
        self.assertEqual(plan([shuffled('7C Garden.mov', 'Garden.mov')], 'new'), [])

    def test_too_many_files(self):
        with self.assertRaises(ValueError):
            plan([fresh(f'{i}.mov') for i in range(len(CODES) + 1)], 'all')

    def test_unknown_mode(self):
        with self.assertRaises(ValueError):
            plan(self.FILES, 'shuffle')


# The folder as it was on 2026-09-28: lopsided on purpose (13 rose clips).
LOOP = (['rose%d.MP4' % i for i in range(1, 8)] + ['nightRose%d.MP4' % i for i in range(1, 7)]
        + ['building.mov'] + ['building %d.mov' % i for i in range(1, 6)]
        + ['shatto%d.MP4' % i for i in (1, 2, 3)] + ['nightShatto1.MP4', 'nightShatto2.MP4']
        + ['tower%d.MP4' % i for i in (1, 2, 3)] + ['nightTower1.MP4', 'nightTower2.MP4']
        + ['Mirror %d.mov' % i for i in range(1, 5)] + ['Garden.mov', 'Garden 1.mov', 'Garden 2.mov']
        + ['Organ.mov', 'organ2.mp4', 'organKeys.mov', 'Choir.mov', 'churchDrone.mp4'])


def played_order(files, changes):
    renamed = {f['id']: n for f, n in changes}
    return sorted((renamed.get(f['id'], f['name']) for f in files))


def keys_of(names):
    return [group_key(n.split(' ', 1)[1] if current_code(n, {GIVEN_KEY: n}) else n)
            for n in names]


class Similar(unittest.TestCase):
    def test_group_key(self):
        self.assertEqual({group_key(n) for n in ('rose3.MP4', 'nightRose2.MP4', 'Rose 7.mov')}, {'rose'})
        self.assertEqual(group_key('building 5.mov'), group_key('building.mov'))
        self.assertNotEqual(group_key('organKeys.mov'), group_key('organ2.mp4'))

    def test_adjacent_repeats_counts_the_wrap(self):
        self.assertEqual(adjacent_repeats(['a', 'b', 'a']), 1)   # last -> first
        self.assertEqual(adjacent_repeats(['a', 'b', 'c']), 0)

    def test_all_keeps_similar_clips_apart(self):
        files = [fresh(n) for n in LOOP]
        for seed in range(50):
            order = played_order(files, plan(files, 'all', random.Random(seed)))
            self.assertEqual(adjacent_repeats(keys_of(order)), 0, order)

    def test_all_still_uses_each_file_once(self):
        files = [fresh(n) for n in LOOP]
        changes = plan(files, 'all', random.Random(3))
        self.assertEqual(len(changes), len(files))
        self.assertEqual(sorted(n.split(' ', 1)[1] for _, n in changes), sorted(LOOP))
        self.assertEqual(len({n.split(' ', 1)[0] for _, n in changes}), len(files))

    def test_new_lands_away_from_its_group(self):
        existing = [shuffled('1A rose1.MP4', 'rose1.MP4'), shuffled('3A building.mov', 'building.mov'),
                    shuffled('5A Choir.mov', 'Choir.mov'), shuffled('7A tower1.MP4', 'tower1.MP4')]
        for seed in range(30):
            files = existing + [fresh('rose9.MP4')]
            order = played_order(files, plan(files, 'new', random.Random(seed)))
            self.assertEqual(adjacent_repeats(keys_of(order)), 0, order)


class Codes(unittest.TestCase):
    def test_codes_sort_as_they_would_in_any_player(self):
        self.assertEqual(len(CODES), 234)
        self.assertEqual(sorted(CODES), CODES)
        # Codes sort before any plain name, lower or upper case.
        self.assertLess('9Z zebra.mov', 'Aardvark.mov')
        self.assertLess('9Z zebra.mov', 'building.mov')


if __name__ == '__main__':
    unittest.main()
