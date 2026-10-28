"""Tests for sync_propresenter_musical_gift.py: no ProPresenter, Drive, or real files needed.

  cd "Worship Scripts/worship workflows" && python3 -m unittest test_sync_propresenter_musical_gift -v
"""
import os
import re
import shutil
import tempfile
import unittest
from unittest import mock

import propresenter_pb as pb
import sync_propresenter_hymns as sh
import sync_propresenter_musical_gift as mg
from propresenter_pb import field
from test_sync_propresenter_hymns import SUNDAY, playlist_file, playlist_items, rtf, uid

TITLES = "file:///Users/x/Library/CloudStorage/GoogleDrive-a@b.c/My%20Drive/Worship%20Titles/"
MACRO = uid(4242)       # "Timing Forward": defined in Configuration, used by the gift slide only


def gift_cue(u, n=None, enabled=True):
    """A slide; with n, the Musical Gift slide for piece n (0: the unnumbered pair)."""
    body = field(1, field(1, u.encode())) + field(2, b"Slide")
    if n is not None:
        s = str(n) if n else ""
        links = field(9, (TITLES + "musical-gift-title-composer%s.txt" % s).encode()) + \
            field(9, (TITLES + "musical-gift-group%s.txt" % s).encode())
        element = field(1, field(1, uid(6000 + (n or 0)).encode())) + field(2, b"Text") + \
            field(13, field(5, rtf("Piece - Composer\nGroup")) + links)
        body += field(10, field(1, field(1, uid(7000).encode())) + field(23, element))
        body += field(10, field(1, field(1, uid(7001).encode())) + field(40, field(1, field(1, MACRO.encode()))))
    else:
        body += field(10, field(1, field(1, (uid(8000) + u[-4:]).encode()[:36])))
    return body + (b"\x60\x01" if enabled else b"")


def gift_deck(pieces=1):
    """Setup slide, the gift slides for pieces 0..pieces-1, then a clear slide (as in the real deck)."""
    cues = [(uid(300), None)] + [(uid(310 + n), n) for n in range(pieces)] + [(uid(399), None)]
    body = field(2, field(1, uid(9).encode())) + field(3, b"Musical Gift")
    groups = [[cues[0][0]], [u for u, n in cues[1:-1]], [cues[-1][0]]]
    for g in groups:
        body += field(12, field(1, field(1, uid(len(g)).encode())) +
                      b"".join(field(2, field(1, u.encode())) for u in g))
    for u, n in cues:
        body += field(13, gift_cue(u, n))
    return body


def slides(data):
    """[(slot or None, enabled)] in slide order."""
    return [(mg.slot(c), any(m == 12 for m, _, _ in pb.fields(c))) for _, c in sh.cues_in_order(data)]


class CountPieces(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)

    def write(self, name, text):
        with open(os.path.join(self.dir, name), "w", encoding="utf-8") as f:
            f.write(text)

    def test_unnumbered_pair_is_the_first_piece(self):
        self.write("musical-gift-title-composer.txt", "A - B")
        self.assertEqual(mg.count_pieces(self.dir), 1)
        self.write("musical-gift-title-composer1.txt", "C - D")
        self.write("musical-gift-title-composer2.txt", "E - F")
        self.assertEqual(mg.count_pieces(self.dir), 3)

    def test_no_musical_gift(self):
        self.write("musical-gift-title-composer.txt", "")
        self.write("musical-gift-title-composer1.txt", "  \n")
        self.write("musical-gift-title.txt", "")
        self.assertEqual(mg.count_pieces(self.dir), 0)

    def test_gap_does_not_hide_a_later_piece(self):
        self.write("musical-gift-title-composer.txt", "A")
        self.write("musical-gift-title-composer3.txt", "D")
        self.assertEqual(mg.count_pieces(self.dir), 4)

    def test_other_musical_gift_files_do_not_count(self):
        self.write("musical-gift-title.txt", "Becoming Earth")
        self.write("musical-gift-performers.txt", "Choir")
        self.write("musical-gift-group5.txt", "Someone")
        self.assertEqual(mg.count_pieces(self.dir), 0)


class Slots(unittest.TestCase):
    def test_slot_from_links(self):
        self.assertEqual(mg.slot(gift_cue(uid(1), 0)), 0)
        self.assertEqual(mg.slot(gift_cue(uid(1), 7)), 7)
        self.assertIsNone(mg.slot(gift_cue(uid(1))))

    def test_relink_moves_both_files(self):
        c = mg.relink(gift_cue(uid(1), 0), 12)
        self.assertEqual(mg.slot(c), 12)
        self.assertIn(b"musical-gift-group12.txt", c)
        self.assertNotIn(b"musical-gift-group.txt", c)
        self.assertEqual(mg.slot(mg.relink(c, 0)), 0)


class SyncDeck(unittest.TestCase):
    def test_adds_slides_for_new_pieces_after_the_last_one(self):
        new, added, states = mg.sync_deck(gift_deck(1), 3)
        self.assertEqual(added, 2)
        self.assertEqual(slides(new), [(None, True), (0, True), (1, True), (2, True), (None, True)])
        self.assertEqual(states, {0: True, 1: True, 2: True})

    def test_copies_get_fresh_ids_but_keep_outside_ones(self):
        new, _, _ = mg.sync_deck(gift_deck(1), 3, keep=frozenset({MACRO.encode()}))
        gift = [(u, c) for u, c in sh.cues_in_order(new) if mg.slot(c) is not None]
        self.assertEqual(len({u for u, _ in gift}), 3)
        for _, c in gift:
            self.assertIn(MACRO.encode(), c)
        local = [u for _, c in gift for u in pb.UUID_RE.findall(c) if u != MACRO.encode()]
        self.assertEqual(len(local), len(set(local)))

    def test_disables_slides_past_the_count_and_re_enables(self):
        down, added, _ = mg.sync_deck(gift_deck(5), 2)
        self.assertEqual(added, 0)
        self.assertEqual([e for s, e in slides(down) if s is not None], [True, True, False, False, False])
        up, _, _ = mg.sync_deck(down, 5)
        self.assertEqual(up, gift_deck(5))

    def test_idempotent(self):
        once, _, _ = mg.sync_deck(gift_deck(1), 4)
        twice, added, _ = mg.sync_deck(once, 4)
        self.assertEqual((twice, added), (once, 0))

    def test_other_slides_untouched(self):
        data = gift_deck(2)
        new, _, _ = mg.sync_deck(data, 4)
        other = lambda d: [c for _, c in sh.cues_in_order(d) if mg.slot(c) is None]
        self.assertEqual(other(new), other(data))

    def test_deck_without_a_first_gift_slide(self):
        with self.assertRaises(ValueError):
            mg.sync_deck(gift_deck(0), 2)


class Playlists(unittest.TestCase):
    ROOT = "/Users/x/Documents/ProPresenter"
    WITH = SUNDAY[:4] + [mg.DECK] + SUNDAY[4:]

    def rels(self, data, name="Sunday Service"):
        """The first playlist of that name (the test file also has a nested one)."""
        items = [r for _, _, r in playlist_items(data, name)]
        return items[:len(items) // 2]

    def test_added_after_the_sermon_deck(self):
        new, changes = mg.update_playlists(playlist_file(), True, self.ROOT)
        self.assertEqual(self.rels(new), self.WITH)
        self.assertEqual(changes, [("Sunday Service", "added")] * 2)       # both, incl. the nested one
        item = playlist_items(new)[4]
        self.assertEqual(item[1], "Musical Gift")
        self.assertNotIn(item[0], [u for u, _, _ in playlist_items(playlist_file())])   # its own id

    def test_removed_when_there_is_none(self):
        added, _ = mg.update_playlists(playlist_file(), True, self.ROOT)
        removed, changes = mg.update_playlists(added, False, self.ROOT)
        self.assertEqual(self.rels(removed), SUNDAY)
        self.assertEqual(changes, [("Sunday Service", "removed")] * 2)

    def test_no_change_when_already_right(self):
        self.assertEqual(mg.update_playlists(playlist_file(), False, self.ROOT), (playlist_file(), []))
        added, _ = mg.update_playlists(playlist_file(), True, self.ROOT)
        self.assertEqual(mg.update_playlists(added, True, self.ROOT), (added, []))

    def test_other_playlists_untouched(self):
        new, _ = mg.update_playlists(playlist_file(), True, self.ROOT)
        self.assertEqual(playlist_items(new, "Archive"), playlist_items(playlist_file(), "Archive"))

    def test_without_a_sermon_deck_it_goes_last(self):
        data = playlist_file(sunday=["Libraries/Service Elements/Title Card.pro"])
        new, _ = mg.update_playlists(data, True, self.ROOT)
        self.assertEqual(self.rels(new)[:2], ["Libraries/Service Elements/Title Card.pro", mg.DECK])


class Main(unittest.TestCase):
    def setUp(self):
        self.pp, self.titles, self.backups = (tempfile.mkdtemp() for _ in range(3))
        for d in (self.pp, self.titles, self.backups):
            self.addCleanup(shutil.rmtree, d)
        os.makedirs(os.path.join(self.pp, "Libraries/Service Elements"))
        os.makedirs(os.path.join(self.pp, "Playlists"))
        self.write(mg.DECK, gift_deck(1))
        self.write(sh.PLAYLISTS_FILE, playlist_file())
        p = mock.patch("builtins.print")
        p.start()
        self.addCleanup(p.stop)

    def write(self, rel, data):
        with open(os.path.join(self.pp, rel), "wb") as f:
            f.write(data)

    def read(self, rel):
        with open(os.path.join(self.pp, rel), "rb") as f:
            return f.read()

    def pieces(self, n):
        for name in os.listdir(self.titles):
            os.remove(os.path.join(self.titles, name))
        for i in range(n):
            s = str(i) if i else ""
            with open(os.path.join(self.titles, "musical-gift-title-composer%s.txt" % s), "w") as f:
                f.write("Piece %d - Composer" % i)

    def run_main(self, *args):
        mg.main(["--propresenter", self.pp, "--titles", self.titles, "--backup-dir", self.backups, *args])

    def test_week_with_a_musical_gift(self):
        self.pieces(3)
        self.run_main()
        self.assertEqual([s for s, e in slides(self.read(mg.DECK)) if e and s is not None], [0, 1, 2])
        self.assertIn(mg.DECK, [r for _, _, r in playlist_items(self.read(sh.PLAYLISTS_FILE))])
        self.assertEqual(len(os.listdir(self.backups)), 2)

    def test_week_without_leaves_the_deck_and_takes_it_out(self):
        self.pieces(3)
        self.run_main()
        deck = self.read(mg.DECK)
        self.pieces(0)
        self.run_main()
        self.assertEqual(self.read(mg.DECK), deck)
        self.assertNotIn(mg.DECK, [r for _, _, r in playlist_items(self.read(sh.PLAYLISTS_FILE))])

    def test_dry_run_writes_nothing(self):
        self.pieces(3)
        deck, playlist = self.read(mg.DECK), self.read(sh.PLAYLISTS_FILE)
        self.run_main("--dry-run")
        self.assertEqual((self.read(mg.DECK), self.read(sh.PLAYLISTS_FILE)), (deck, playlist))
        self.assertEqual(os.listdir(self.backups), [])

    def test_second_run_changes_nothing(self):
        self.pieces(4)
        self.run_main()
        deck, playlist = self.read(mg.DECK), self.read(sh.PLAYLISTS_FILE)
        self.run_main()
        self.assertEqual((self.read(mg.DECK), self.read(sh.PLAYLISTS_FILE)), (deck, playlist))
        self.assertEqual(len(os.listdir(self.backups)), 2)


if __name__ == "__main__":
    unittest.main()
