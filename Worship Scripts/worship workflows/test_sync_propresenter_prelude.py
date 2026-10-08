"""Tests for sync_propresenter_prelude.py: no ProPresenter, Drive, or real .pro file needed.

  cd "Worship Scripts/worship workflows" && python3 -m unittest test_sync_propresenter_prelude -v
"""
import os
import shutil
import tempfile
import unittest
from unittest import mock

import sync_propresenter_prelude as sp

LINK = "file:///Users/x/Library/CloudStorage/GoogleDrive-a@b.c/My%%20Drive/Worship%%20Titles/%s.txt"


def varint_field(num, value):
    return sp.write_varint(num << 3) + sp.write_varint(value)


def bytes_field(num, payload):
    return sp.write_varint(num << 3 | 2) + sp.write_varint(len(payload)) + payload


def cue(uuid, links=(), enabled=True):
    """A Cue shaped like ProPresenter's: uuid(1), name(2), a few fields, actions(10), enabled(12), 13."""
    body = bytes_field(1, bytes_field(1, uuid.encode())) + bytes_field(2, b"Slide") + varint_field(5, 1)
    for link in links:
        body += bytes_field(10, (LINK % link).encode())
    if enabled:
        body += varint_field(sp.ENABLED_FIELD, 1)
    return body + sp.write_varint(14 << 3 | 1) + b"\x00" * 8     # a later field after the flag


def deck(cues, order=None, groups=None):
    """`order` is the slide order (cue uuids) of one group, or `groups` a list of such orders.
    The cues themselves are stored apart from the order, as ProPresenter does."""
    out = bytes_field(2, b"Deck name") + bytes_field(3, b"Service Prelude")
    if groups is None:
        groups = [order if order is not None else [sp.cue_uuid(c) for c in cues]]
    for g in groups:
        out += bytes_field(sp.GROUP_FIELD, bytes_field(1, b"group header") +
                           b"".join(bytes_field(2, bytes_field(1, u.encode())) for u in g))
    for c in reversed(cues):                                   # stored in a different order
        out += bytes_field(sp.CUE_FIELD, c)
    return out + bytes_field(17, b"trailer")


def prelude_deck(n_slides=9, enabled=True):
    """welcome, then (prelude N, clear N) pairs, then hymn."""
    cues = [cue("welcome", ["welcome"])]
    for n in range(1, n_slides + 1):
        cues.append(cue("p%d" % n, ["prelude-performer%d" % n, "prelude%d" % n], enabled))
        cues.append(cue("c%d" % n, [], enabled))
    cues.append(cue("hymn", ["hymn-of-gathering"]))
    return deck(cues)


def uuids(data):
    return {sp.cue_uuid(p): any(f == sp.ENABLED_FIELD for f, _, _ in sp.fields(p))
            for n, _, p in sp.fields(data) if n == sp.CUE_FIELD}


def state(data):
    """{prelude number: enabled} read back from deck bytes."""
    out = {}
    for num, _, payload in sp.fields(data):
        if num == sp.CUE_FIELD:
            n = sp.prelude_number(payload)
            if n is not None:
                out[n] = any(f == sp.ENABLED_FIELD for f, _, _ in sp.fields(payload))
    return out


class Varint(unittest.TestCase):
    def test_round_trip(self):
        for n in (0, 1, 127, 128, 300, 16384, 2 ** 31):
            enc = sp.write_varint(n)
            self.assertEqual(sp.read_varint(enc, 0), (n, len(enc)))


class PreludeNumber(unittest.TestCase):
    def test_reads_n_from_either_link(self):
        self.assertEqual(sp.prelude_number(cue("a", ["prelude7"])), 7)
        self.assertEqual(sp.prelude_number(cue("a", ["prelude-performer7", "prelude7"])), 7)
        self.assertEqual(sp.prelude_number(cue("a", ["prelude12"])), 12)

    def test_performer_only_slide_still_counts(self):
        self.assertIsNone(sp.prelude_number(cue("a", ["prelude-performer3"])))

    def test_other_slides_are_ignored(self):
        self.assertIsNone(sp.prelude_number(cue("a", ["welcome"])))
        self.assertIsNone(sp.prelude_number(cue("a")))

    def test_slide_linking_two_different_pieces_is_ambiguous(self):
        self.assertIsNone(sp.prelude_number(cue("a", ["prelude1", "prelude2"])))


class SetCueEnabled(unittest.TestCase):
    def flags(self, c):
        return [raw for num, raw, _ in sp.fields(c) if num == sp.ENABLED_FIELD]

    def test_disable_removes_only_the_flag(self):
        c = cue("a", ["prelude1"], enabled=True)
        out = sp.set_cue_enabled(c, False)
        self.assertEqual(self.flags(out), [])
        self.assertEqual([r for n, r, _ in sp.fields(out)], [r for n, r, _ in sp.fields(c) if n != sp.ENABLED_FIELD])

    def test_enable_restores_original_bytes(self):
        on = cue("a", ["prelude1"], enabled=True)
        self.assertEqual(sp.set_cue_enabled(sp.set_cue_enabled(on, False), True), on)

    def test_enable_when_already_enabled_is_a_no_op(self):
        on = cue("a", ["prelude1"], enabled=True)
        self.assertEqual(sp.set_cue_enabled(on, True), on)

    def test_disable_when_already_disabled_is_a_no_op(self):
        off = cue("a", ["prelude1"], enabled=False)
        self.assertEqual(sp.set_cue_enabled(off, False), off)

    def test_enable_appends_when_no_later_field(self):
        c = bytes_field(1, b"u") + varint_field(5, 1)
        out = sp.set_cue_enabled(c, True)
        self.assertEqual([n for n, _, _ in sp.fields(out)], [1, 5, sp.ENABLED_FIELD])


class SyncDeck(unittest.TestCase):
    def test_disables_slides_past_the_count(self):
        new, seen, _ = sp.sync_deck(prelude_deck(), 4)
        self.assertEqual(seen, {n: n <= 4 for n in range(1, 10)})
        self.assertEqual(state(new), seen)

    def test_count_zero_disables_every_prelude_slide(self):
        new, _, _ = sp.sync_deck(prelude_deck(), 0)
        self.assertEqual(set(state(new).values()), {False})

    def test_count_above_slides_enables_all(self):
        new, _, _ = sp.sync_deck(prelude_deck(enabled=False), 12)
        self.assertEqual(set(state(new).values()), {True})

    def test_non_prelude_slides_and_other_fields_are_untouched(self):
        data = prelude_deck()
        new, _, _ = sp.sync_deck(data, 2)
        managed = lambda p: sp.prelude_number(p) or sp.cue_uuid(p).startswith("c")
        before = [raw for n, raw, p in sp.fields(data) if not (n == sp.CUE_FIELD and managed(p))]
        after = [raw for n, raw, p in sp.fields(new) if not (n == sp.CUE_FIELD and managed(p))]
        self.assertEqual(before, after)

    def test_slides_keep_their_order(self):
        new, _, _ = sp.sync_deck(prelude_deck(), 3)
        order = lambda d: [sp.cue_uuid(p) for n, _, p in sp.fields(d) if n == sp.CUE_FIELD]
        self.assertEqual(order(new), order(prelude_deck()))

    def test_idempotent(self):
        once, _, _ = sp.sync_deck(prelude_deck(), 5)
        twice, _, _ = sp.sync_deck(once, 5)
        self.assertEqual(once, twice)

    def test_round_trip_is_byte_identical(self):
        data = prelude_deck()
        down, _, _ = sp.sync_deck(data, 3)
        up, _, _ = sp.sync_deck(down, 9)
        self.assertEqual(up, data)

    def test_deck_without_prelude_slides_reports_nothing(self):
        data = deck([cue("a", ["welcome"]), cue("b")])
        new, seen, _ = sp.sync_deck(data, 3)
        self.assertEqual(seen, {})
        self.assertEqual(new, data)

    def test_truncated_data_raises(self):
        with self.assertRaises((ValueError, IndexError)):
            sp.fields(prelude_deck()[:-3])


class FollowOnSlide(unittest.TestCase):
    """The slide immediately after a prelude slide (the one that clears the lower third)."""

    def test_follows_its_prelude_slide(self):
        new, _, followers = sp.sync_deck(prelude_deck(), 4)
        on = uuids(new)
        self.assertEqual(followers, 9)
        for n in range(1, 10):
            self.assertEqual(on["p%d" % n], n <= 4)
            self.assertEqual(on["c%d" % n], n <= 4)

    def test_slides_elsewhere_are_not_touched(self):
        new, _, _ = sp.sync_deck(prelude_deck(), 0)
        on = uuids(new)
        self.assertTrue(on["welcome"])
        self.assertTrue(on["hymn"])

    def test_slide_after_the_last_prelude_slide_is_its_follower(self):
        cues = [cue("p1", ["prelude1"]), cue("c1", []), cue("other", [])]
        new, _, _ = sp.sync_deck(deck(cues), 0)
        on = uuids(new)
        self.assertFalse(on["c1"])
        self.assertTrue(on["other"])        # two slides after is not "immediately after"

    def test_follow_on_slide_is_re_enabled(self):
        down, _, _ = sp.sync_deck(prelude_deck(), 2)
        up, _, _ = sp.sync_deck(down, 9)
        self.assertEqual(set(uuids(up).values()), {True})

    def test_uses_slide_order_not_storage_order(self):
        cues = [cue("p1", ["prelude1"]), cue("x", []), cue("c1", [])]
        new, _, _ = sp.sync_deck(deck(cues, order=["p1", "c1", "x"]), 0)
        on = uuids(new)
        self.assertFalse(on["c1"])
        self.assertTrue(on["x"])

    def test_back_to_back_prelude_slides_have_no_follower_between(self):
        cues = [cue("p1", ["prelude1"]), cue("p2", ["prelude2"]), cue("c2", [])]
        new, _, followers = sp.sync_deck(deck(cues), 1)
        on = uuids(new)
        self.assertEqual(followers, 1)
        self.assertEqual((on["p1"], on["p2"], on["c2"]), (True, False, False))

    def test_prelude_slide_at_the_end_of_a_group_has_no_follower(self):
        cues = [cue("p1", ["prelude1"]), cue("next-group", [])]
        new, _, followers = sp.sync_deck(deck(cues, groups=[["p1"], ["next-group"]]), 0)
        self.assertEqual(followers, 0)
        self.assertTrue(uuids(new)["next-group"])

    def test_idempotent(self):
        once, _, _ = sp.sync_deck(prelude_deck(), 3)
        twice, _, _ = sp.sync_deck(once, 3)
        self.assertEqual(once, twice)


class CountPieces(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)

    def write(self, name, text):
        with open(os.path.join(self.dir, name), "w", encoding="utf-8") as f:
            f.write(text)

    def test_counts_non_empty_files(self):
        for n in range(1, 7):
            self.write("prelude%d.txt" % n, "Piece %d - Composer" % n)
        for n in range(7, 10):
            self.write("prelude%d.txt" % n, "")
        self.assertEqual(sp.count_pieces(self.dir), 6)

    def test_whitespace_only_counts_as_empty(self):
        self.write("prelude1.txt", "Piece")
        self.write("prelude2.txt", " \n")
        self.assertEqual(sp.count_pieces(self.dir), 1)

    def test_gap_does_not_hide_a_later_piece(self):
        self.write("prelude1.txt", "A")
        self.write("prelude2.txt", "")
        self.write("prelude3.txt", "C")
        self.assertEqual(sp.count_pieces(self.dir), 3)

    def test_ignores_performer_and_unrelated_files(self):
        self.write("prelude-performer5.txt", "Dr. Someone")
        self.write("prelude.txt", "x")
        self.write("title.txt", "x")
        self.assertEqual(sp.count_pieces(self.dir), 0)

    def test_handles_ten_or_more(self):
        self.write("prelude10.txt", "Tenth")
        self.assertEqual(sp.count_pieces(self.dir), 10)

    def test_empty_folder(self):
        self.assertEqual(sp.count_pieces(self.dir), 0)


class Main(unittest.TestCase):
    def setUp(self):
        self.lib = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.lib)
        self.path = os.path.join(self.lib, "Service Prelude-HoG.pro")
        self.original = prelude_deck()
        with open(self.path, "wb") as f:
            f.write(self.original)
        patcher = mock.patch("builtins.print")
        patcher.start()
        self.addCleanup(patcher.stop)

    def read(self):
        with open(self.path, "rb") as f:
            return f.read()

    def backups(self):
        return [n for n in os.listdir(self.lib) if ".bak-" in n]

    def run_main(self, *args):
        sp.main(["--library", self.lib, *args])

    def test_updates_the_deck_and_keeps_a_backup(self):
        self.run_main("--count", "4")
        self.assertEqual(state(self.read()), {n: n <= 4 for n in range(1, 10)})
        self.assertEqual(len(self.backups()), 1)
        with open(os.path.join(self.lib, self.backups()[0]), "rb") as f:
            self.assertEqual(f.read(), self.original)

    def test_dry_run_changes_nothing(self):
        self.run_main("--count", "4", "--dry-run")
        self.assertEqual(self.read(), self.original)
        self.assertEqual(self.backups(), [])

    def test_unchanged_deck_is_not_rewritten_or_backed_up(self):
        self.run_main("--count", "9")
        self.assertEqual(self.read(), self.original)
        self.assertEqual(self.backups(), [])

    def test_only_prelude_named_decks_are_picked_up_by_default(self):
        other = os.path.join(self.lib, "Communion Sequence.pro")
        with open(other, "wb") as f:
            f.write(prelude_deck())
        self.run_main("--count", "2")
        with open(other, "rb") as f:
            self.assertEqual(f.read(), prelude_deck())

    def test_named_deck_only(self):
        second = os.path.join(self.lib, "AUX1-Service Prelude-HoG.pro")
        with open(second, "wb") as f:
            f.write(prelude_deck())
        self.run_main("--count", "2", "Service Prelude-HoG.pro")
        with open(second, "rb") as f:
            self.assertEqual(f.read(), prelude_deck())
        self.assertEqual(state(self.read()), {n: n <= 2 for n in range(1, 10)})

    def test_deck_without_prelude_slides_is_left_alone(self):
        plain = deck([cue("a", ["welcome"])])
        with open(self.path, "wb") as f:
            f.write(plain)
        self.run_main("--count", "2")
        self.assertEqual(self.read(), plain)
        self.assertEqual(self.backups(), [])

    def test_reads_count_from_the_titles_folder(self):
        titles = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, titles)
        for n in (1, 2, 3):
            with open(os.path.join(titles, "prelude%d.txt" % n), "w") as f:
                f.write("Piece")
        with mock.patch.object(sp, "titles_dir", return_value=titles):
            self.run_main()
        self.assertEqual(state(self.read()), {n: n <= 3 for n in range(1, 10)})

    def test_missing_titles_folder_exits_without_touching_decks(self):
        with mock.patch.object(sp, "titles_dir", return_value=None):
            with self.assertRaises(SystemExit):
                self.run_main()
        self.assertEqual(self.read(), self.original)

    def test_skip_if_running_leaves_decks_alone(self):
        with mock.patch.object(sp, "propresenter_running", return_value=True):
            self.run_main("--count", "2", "--skip-if-running")
        self.assertEqual(self.read(), self.original)

    def test_skip_if_running_proceeds_when_propresenter_is_closed(self):
        with mock.patch.object(sp, "propresenter_running", return_value=False):
            self.run_main("--count", "2", "--skip-if-running")
        self.assertEqual(state(self.read()), {n: n <= 2 for n in range(1, 10)})


if __name__ == "__main__":
    unittest.main()
