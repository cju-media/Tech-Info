"""Tests for sync_propresenter_hymns.py: no ProPresenter, Drive, or real files needed.

Decks and the playlist file are built here in ProPresenter's shape (the fields the script reads,
plus some it must leave alone); the words are made up.

  cd "Worship Scripts/worship workflows" && python3 -m unittest test_sync_propresenter_hymns -v
"""
import os
import shutil
import tempfile
import unittest
from unittest import mock

import propresenter_pb as pb
import sync_propresenter_hymns as sh
from propresenter_pb import field

RTF_HEAD = (b"{\\rtf1\\ansi\\ansicpg1252\\cocoartf2822\n\\cocoatextscaling0\\cocoaplatform0"
            b"{\\fonttbl\\f0\\fnil\\fcharset0 Cinzel-Regular;}\n{\\colortbl;\\red255\\green255\\blue255;}\n"
            b"{\\*\\expandedcolortbl;;\\cssrgb\\c100000\\c100000\\c100000;}\n"
            b"\\pard\\sl192\\slmult1\\pardirnatural\\qc\\partightenfactor0\n\n"
            b"\\f0\\fs58 \\cf2 \\kerning1\\expnd8\\expndtw40\n")
EMPTY_RTF = (b"{\\rtf1\\ansi\\ansicpg1252\\cocoartf2822\n\\cocoatextscaling0\\cocoaplatform0{\\fonttbl}\n"
             b"{\\colortbl;\\red255\\green255\\blue255;}\n{\\*\\expandedcolortbl;;}\n}")
THEME = "11111111-2222-3333-4444-555555555555"     # shared by every slide: an outside reference


def uid(n):
    return "%08X-0000-4000-8000-%012X" % (n, n)


def rtf(text):
    return RTF_HEAD + sh.rtf_escape(text).encode() + b"}"


def element(u, name, rtf_bytes):
    return field(1, field(1, field(1, u.encode())) + field(2, name.encode()) + field(13, field(5, rtf_bytes)))


def cue(u, text, n):
    """A slide: uuid, name, the slide action (a text element and an empty shadow element), enabled."""
    elements = element(uid(1000 + n), "Text", rtf(text) if text else EMPTY_RTF) + \
        element(uid(2000 + n), "Shadow", EMPTY_RTF) + field(9, field(1, THEME.encode()))
    action = field(1, field(1, uid(3000 + n).encode())) + field(23, field(2, field(1, field(1, elements))))
    return field(1, field(1, u.encode())) + field(2, b"Slide") + field(10, action) + b"\x60\x01"


def deck(texts, name="Hymn", pres=uid(9), group=uid(8)):
    """A deck whose slides show `texts` (None: a slide with no words)."""
    ids = [uid(100 + i) for i in range(len(texts))]
    header = field(1, field(1, group.encode())) + field(2, b"")
    body = field(1, b"\x08\x01app") + field(2, field(1, pres.encode())) + field(3, name.encode())
    body += field(12, header + b"".join(field(2, field(1, u.encode())) for u in ids))
    for i, (u, t) in enumerate(zip(ids, texts)):
        body += field(13, cue(u, t, i))
    return body + field(14, b"") + field(17, b")\x00\x00\x00\x00\x00\xc0r@")


def item(u, rel, root="/Users/x/Documents/ProPresenter"):
    url = sh.file_url(os.path.join(root, rel)).encode()
    target = field(1, field(1, url) + b"\x18\x01" + field(4, b"\x08\x0a" + field(2, rel.encode())))
    name = os.path.splitext(os.path.basename(rel))[0].encode()
    return field(1, field(1, u.encode())) + field(2, name) + field(4, target)


def playlist(name, rels, u):
    items = b"".join(field(1, item(uid(500 + i + u), r)) for i, r in enumerate(rels))
    return field(1, field(1, uid(u).encode())) + field(2, name.encode()) + field(13, items)


SUNDAY = ["Libraries/Service Elements/Title Card.pro", "Libraries/Service Elements/Prelude.pro",
          "Libraries/Hymns/Old Gathering.pro", "Libraries/Service Elements/Sermon.pro",
          "Libraries/Hymns/Old Journey.pro", "Libraries/Service Elements/Postlude.pro"]


def playlist_file(sunday=SUNDAY, other=("Libraries/Hymns/Old Gathering.pro",)):
    children = field(1, playlist("Sunday Service", sunday, 10)) + \
        field(1, playlist("Archive", list(other), 20))
    nested = field(1, field(1, uid(30).encode()) + field(2, b"Folder") +
                   field(12, field(1, playlist("Sunday Service", sunday, 40))))
    root = field(1, field(1, uid(1).encode())) + field(2, b"PLAYLIST") + field(12, children + nested)
    return field(1, b"\x08\x01app") + b"\x10\x01" + field(3, root)


def playlist_items(data, name="Sunday Service"):
    """[(item uuid, item name, rel path)] of every playlist called `name`, in order."""
    found = []

    def walk(pl):
        q = pb.fields(pl)
        pname = next((p.decode() for n, _, p in q if n == 2), "")
        for n, _, p in q:
            if n == 12:
                for k, _, c in pb.fields(p):
                    if k == 1:
                        walk(c)
            if n == 13 and pname == name:
                for k, _, it in pb.fields(p):
                    f = pb.fields(it)
                    found.append((pb.identifier(f[0][2]), f[1][2].decode(), sh.item_path(it)))
    for n, _, p in pb.fields(data):
        if n == 3:
            walk(p)
    return found


def slide_texts(data):
    return [sh.slide_text(c) for _, c in sh.cues_in_order(data)]


VERSES = [["Morning light is calling,", "Over hill and over sea,", "Wake the town and valley,",
           "Sing for you and me."],
          ["Evening comes so gently,", "Stars above the shore,", "Rest the town and valley,",
           "Peace for evermore."]]
SLIDES = ["Morning light is calling, over hill and over sea,\nWake the town and valley, sing for you and me.",
          "Evening comes so gently, stars above the shore,\nRest the town and valley, peace for evermore."]


class Words(unittest.TestCase):
    def test_normalize_ignores_case_punctuation_and_quotes(self):
        self.assertEqual(sh.normalize("Wake, Now My Senses"), sh.normalize("wake now my senses"))
        self.assertEqual(sh.normalize("When in Awe of God’s Creation "),
                         sh.normalize("When in awe of God's creation"))
        self.assertEqual(sh.normalize("Café — “Noël”"), "cafe noel")

    def test_similarity(self):
        self.assertEqual(sh.similarity("a b c", "A, b. C!"), 1.0)
        self.assertLess(sh.similarity("a b c d", "a b x y"), 1.0)


class JoinLines(unittest.TestCase):
    def test_continuing_line_loses_its_capital(self):
        self.assertEqual(sh.join_lines(["Creator God, you gave us life,", "Your image formed"]),
                         "Creator God, you gave us life, your image formed")

    def test_names_and_i_keep_capitals(self):
        self.assertEqual(sh.join_lines(["We sing,", "God is near"]), "We sing, God is near")
        self.assertEqual(sh.join_lines(["We sing,", "I’m here"]), "We sing, I’m here")
        self.assertEqual(sh.join_lines(["We sing,", "O hear us"]), "We sing, O hear us")

    def test_new_sentence_keeps_its_capital(self):
        self.assertEqual(sh.join_lines(["with each dawn.", "Taste the wine"]), "with each dawn. Taste the wine")
        self.assertEqual(sh.join_lines(["Who is near?", "Who is far"]), "Who is near? Who is far")
        self.assertEqual(sh.join_lines(["‘Go and serve!’", "Take its love"]), "‘Go and serve!’ Take its love")

    def test_single_line(self):
        self.assertEqual(sh.join_lines(["Alone."]), "Alone.")


class SplitEven(unittest.TestCase):
    def test_keeps_order_and_every_line(self):
        lines = ["a" * n for n in (10, 20, 30, 40, 50)]
        for k in range(1, 6):
            groups = sh.split_even(lines, k)
            self.assertEqual(sum(groups, []), lines)
            self.assertEqual(len(groups), k)
            self.assertTrue(all(groups))

    def test_balances_by_length(self):
        self.assertEqual(sh.split_even(["aaaa", "aaaa", "aaaa", "aaaa"], 2), [["aaaa", "aaaa"], ["aaaa", "aaaa"]])
        self.assertEqual(len(sh.split_even(["a" * 60, "b", "c", "d"], 2)[0]), 1)

    def test_more_groups_than_lines(self):
        self.assertEqual(sh.split_even(["a", "b"], 5), [["a"], ["b"]])


class SlidesFromVerses(unittest.TestCase):
    def test_four_short_lines_make_one_two_line_slide(self):
        self.assertEqual(sh.slides_from_verses(VERSES), SLIDES)

    def test_five_line_verse_stays_on_one_slide(self):
        verse = ["When with our hearts,", "Our hands our minds,", "We share our gifts with all the world,",
                 "Our spirits soar beyond the veil,", "To touch the very face of God."]
        slides = sh.slides_from_verses([verse])
        self.assertEqual(len(slides), 1)
        self.assertEqual(slides[0].count("\n"), 1)

    def test_long_verse_is_spread_over_slides(self):
        verse = ["This line is about forty characters long."] * 8
        slides = sh.slides_from_verses([verse])
        self.assertEqual(len(slides), 2)
        self.assertTrue(all(s.count("\n") == 1 for s in slides))

    def test_long_lines_get_a_slide_line_each(self):
        verse = ["A long line that already holds two hymn lines, as some OWs print them."] * 4
        slides = sh.slides_from_verses([verse])
        self.assertEqual(len(slides), 2)
        self.assertNotIn("them. a long", slides[0])

    def test_each_verse_starts_a_slide(self):
        self.assertEqual(len(sh.slides_from_verses([["One."], ["Two."], ["Three."]])), 3)


class Rtf(unittest.TestCase):
    def test_text_round_trip(self):
        for text in ("Plain words", "Two\nlines", "Trav’ller’s “song” — naïve",
                     "Braces {and} back\\slash"):
            self.assertEqual(sh.rtf_text(rtf(text)), text)

    def test_set_text_keeps_formatting(self):
        new = sh.set_rtf_text(rtf("Old words"), "New words")
        self.assertTrue(new.startswith(RTF_HEAD))
        self.assertEqual(sh.rtf_text(new), "New words")

    def test_reads_cocoa_escapes(self):
        cocoa = RTF_HEAD + b"trav\\'92ller \\uc0\\u8220 hi\\uc0\\u8221 }"
        self.assertEqual(sh.rtf_text(cocoa), "trav’ller “hi”")

    def test_text_on_the_formatting_line(self):
        r = RTF_HEAD.rstrip(b"\n") + b" \\outl0\\strokewidth-40 \\strokec3 Welcome}"
        self.assertEqual(sh.rtf_text(sh.set_rtf_text(r, "Hello")), "Hello")

    def test_empty_rtf_has_no_text(self):
        self.assertEqual(sh.rtf_text(EMPTY_RTF), "")


class RebuildDeck(unittest.TestCase):
    def test_replaces_the_words(self):
        new = sh.rebuild_deck(deck(["old one", "old two", "old three"]), SLIDES)
        self.assertEqual(slide_texts(new), SLIDES)

    def test_slides_get_fresh_ids_and_keep_shared_ones(self):
        new = sh.rebuild_deck(deck(["old one", "old two"]), SLIDES + ["Third"])
        ids = [u for u, _ in sh.cues_in_order(new)]
        self.assertEqual(len(set(ids)), 3)
        self.assertFalse(set(ids) & {uid(100), uid(101)})
        for _, c in sh.cues_in_order(new):
            self.assertIn(THEME.encode(), c)          # the shared reference is untouched
        inner = [u for _, c in sh.cues_in_order(new) for u in pb.UUID_RE.findall(c)]
        per_slide = [u for u in inner if u != THEME.encode()]
        self.assertEqual(len(per_slide), len(set(per_slide)))

    def test_existing_deck_keeps_its_identity(self):
        new = sh.rebuild_deck(deck(["old"], name="Mine"), SLIDES)
        top = pb.fields(new)
        self.assertEqual([pb.identifier(p) for n, _, p in top if n == 2], [uid(9)])
        self.assertEqual([p for n, _, p in top if n == 3], [b"Mine"])
        self.assertIn(uid(8).encode(), new)

    def test_new_deck_gets_a_new_identity_and_name(self):
        new = sh.rebuild_deck(deck(["old"]), SLIDES, name="Fresh Hymn")
        top = pb.fields(new)
        self.assertEqual([p for n, _, p in top if n == 3], [b"Fresh Hymn"])
        self.assertNotIn(uid(9).encode(), new)
        self.assertNotIn(uid(8).encode(), new)
        self.assertEqual(slide_texts(new), SLIDES)

    def test_slides_without_words_stay_where_they_are(self):
        new = sh.rebuild_deck(deck([None, "old one", "old two", None]), SLIDES)
        self.assertEqual(slide_texts(new), [""] + SLIDES + [""])
        ids = [u for u, _ in sh.cues_in_order(new)]
        self.assertEqual((ids[0], ids[-1]), (uid(100), uid(103)))

    def test_other_fields_are_kept(self):
        data = deck(["old"])
        new = sh.rebuild_deck(data, SLIDES)
        keep = lambda d: [raw for n, raw, _ in pb.fields(d) if n not in (12, 13)]
        self.assertEqual(keep(new), keep(data))

    def test_deck_without_words_cannot_be_a_template(self):
        with self.assertRaises(ValueError):
            sh.rebuild_deck(deck([None]), SLIDES)

    def test_rebuilt_deck_matches_its_lyrics(self):
        new = sh.rebuild_deck(deck(["old"]), sh.slides_from_verses(VERSES))
        lyrics = "\n".join("\n".join(v) for v in VERSES)
        self.assertEqual(sh.normalize(sh.deck_words(new)), sh.normalize(lyrics))


class FindDecks(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)

    def put(self, name, texts, mtime=None):
        p = os.path.join(self.dir, name)
        with open(p, "wb") as f:
            f.write(deck(texts))
        if mtime:
            os.utime(p, (mtime, mtime))
        return p

    def test_matches_ignore_punctuation_case_and_spaces(self):
        a = self.put("Wake Now My Senses.pro", ["x"])
        b = self.put("Wake, Now My Senses .pro", ["x"])
        self.put("Wake Now My Senses Again.pro", ["x"])
        self.assertEqual(sorted(sh.find_decks(self.dir, "wake now, my senses")), sorted([a, b]))

    def test_picks_the_closest_words(self):
        close = self.put("A.pro", SLIDES, mtime=1000)
        far = self.put("A .pro", ["something else entirely"], mtime=2000)
        lyrics = "\n".join("\n".join(v) for v in VERSES)
        self.assertEqual(sh.pick_deck([close, far], lyrics), close)

    def test_newest_without_lyrics(self):
        old = self.put("A.pro", ["x"], mtime=1000)
        new = self.put("A .pro", ["x"], mtime=2000)
        self.assertEqual(sh.pick_deck([old, new], ""), new)

    def test_file_name_for_a_title(self):
        self.assertEqual(sh.deck_file_name("“We’ve Got A Job”"), "We’ve Got A Job.pro")
        self.assertEqual(sh.deck_file_name("Either/Or: A Hymn"), "Either-Or- A Hymn.pro")


class Playlists(unittest.TestCase):
    ROOT = "/Users/x/Documents/ProPresenter"
    NEW = ["Libraries/Hymns/New Gathering.pro", "Libraries/Hymns/New Journey.pro"]

    def test_swaps_the_two_hymn_slots(self):
        new, changes = sh.update_playlists(playlist_file(), self.NEW, self.ROOT)
        items = playlist_items(new)[:6]
        self.assertEqual([r for _, _, r in items], [SUNDAY[0], SUNDAY[1], self.NEW[0], SUNDAY[3],
                                                     self.NEW[1], SUNDAY[5]])
        self.assertEqual(items[2][1], "New Gathering")
        self.assertEqual(len(changes), 4)           # both Sunday Service playlists

    def test_items_keep_their_ids_and_url_follows(self):
        before = playlist_items(playlist_file())
        new, _ = sh.update_playlists(playlist_file(), self.NEW, self.ROOT)
        self.assertEqual([u for u, _, _ in playlist_items(new)], [u for u, _, _ in before])
        self.assertIn(sh.file_url(os.path.join(self.ROOT, self.NEW[0])).encode(), new)
        self.assertNotIn(b"Old%20Gathering", new.split(b"Archive")[0])

    def test_every_named_playlist_is_updated(self):
        new, changes = sh.update_playlists(playlist_file(), self.NEW, self.ROOT, names=("Sunday Service", "Archive"))
        self.assertEqual([r for _, _, r in playlist_items(new, "Archive")], [self.NEW[0]])
        self.assertEqual({pl for pl, _, _ in changes}, {"Sunday Service", "Archive"})

    def test_communion_playlist_is_in_the_defaults(self):
        self.assertIn("Communion Sunday Service", sh.PLAYLIST_NAMES)

    def test_other_playlists_are_untouched(self):
        new, _ = sh.update_playlists(playlist_file(), self.NEW, self.ROOT)
        self.assertEqual(playlist_items(new, "Archive"), playlist_items(playlist_file(), "Archive"))

    def test_already_in_place_changes_nothing(self):
        once, _ = sh.update_playlists(playlist_file(), self.NEW, self.ROOT)
        twice, changes = sh.update_playlists(once, self.NEW, self.ROOT)
        self.assertEqual(changes, [])
        self.assertEqual(twice, once)

    def test_missing_slot_is_left_alone(self):
        new, changes = sh.update_playlists(playlist_file(), [None, self.NEW[1]], self.ROOT)
        rels = [r for _, _, r in playlist_items(new)[:6]]
        self.assertEqual((rels[2], rels[4]), (SUNDAY[2], self.NEW[1]))
        self.assertEqual(len(changes), 2)

    def test_curly_apostrophe_is_url_encoded_like_propresenter(self):
        self.assertEqual(sh.file_url("/a/Libraries/Hymns/We’ve Got A Job.pro"),
                         "file:///a/Libraries/Hymns/We%E2%80%99ve%20Got%20A%20Job.pro")
        self.assertEqual(sh.file_url("/a/Joyful, Joyful.pro"), "file:///a/Joyful,%20Joyful.pro")


class Main(unittest.TestCase):
    def setUp(self):
        self.pp = tempfile.mkdtemp()
        self.titles = tempfile.mkdtemp()
        self.backups = tempfile.mkdtemp()
        for d in (self.pp, self.titles, self.backups):
            self.addCleanup(shutil.rmtree, d)
        os.makedirs(os.path.join(self.pp, "Libraries/Hymns"))
        os.makedirs(os.path.join(self.pp, "Playlists"))
        self.write(os.path.join(self.pp, "Libraries/Hymns", sh.TEMPLATE), deck(["Template words"]))
        self.write(os.path.join(self.pp, "Libraries/Hymns/Old Gathering.pro"), deck(["x"]))
        self.write(os.path.join(self.pp, "Libraries/Hymns/Old Journey.pro"), deck(["x"]))
        self.write(os.path.join(self.pp, "Libraries/Hymns/Go Now Together.pro"), deck(["Wrong words"]))
        self.write(os.path.join(self.pp, sh.PLAYLISTS_FILE), playlist_file())
        self.title("hymn-of-gathering", "Morning Light Is Calling", VERSES)
        self.title("hymn-for-journey", "Go Now, Together", [["Go now together,", "The road is ahead."]])
        p = mock.patch("builtins.print")
        p.start()
        self.addCleanup(p.stop)

    def write(self, path, data):
        with open(path, "wb") as f:
            f.write(data)

    def read(self, rel):
        with open(os.path.join(self.pp, rel), "rb") as f:
            return f.read()

    def title(self, key, title, verses):
        with open(os.path.join(self.titles, key + ".txt"), "w") as f:
            f.write(title)
        with open(os.path.join(self.titles, key + "-lyrics.txt"), "w") as f:
            f.write(sh.hymn_lyrics.to_file_text(verses))

    def run_main(self, *args):
        sh.main(["--propresenter", self.pp, "--titles", self.titles, "--backup-dir", self.backups, *args])

    def test_makes_rewrites_and_swaps(self):
        self.run_main()
        made = self.read("Libraries/Hymns/Morning Light Is Calling.pro")
        self.assertEqual(slide_texts(made), SLIDES)
        rewritten = self.read("Libraries/Hymns/Go Now Together.pro")
        self.assertEqual(slide_texts(rewritten), ["Go now together,\nThe road is ahead."])
        rels = [r for _, _, r in playlist_items(self.read(sh.PLAYLISTS_FILE))[:6]]
        self.assertEqual((rels[2], rels[4]), ("Libraries/Hymns/Morning Light Is Calling.pro",
                                              "Libraries/Hymns/Go Now Together.pro"))
        self.assertEqual(sorted(n.split(".bak-")[0] for n in os.listdir(self.backups)),
                         ["Go Now Together.pro", "Library"])

    def test_second_run_changes_nothing(self):
        self.run_main()
        before = {n: self.read("Libraries/Hymns/" + n) for n in os.listdir(os.path.join(self.pp, "Libraries/Hymns"))}
        playlist = self.read(sh.PLAYLISTS_FILE)
        backups = len(os.listdir(self.backups))
        self.run_main()
        after = {n: self.read("Libraries/Hymns/" + n) for n in os.listdir(os.path.join(self.pp, "Libraries/Hymns"))}
        self.assertEqual(after, before)
        self.assertEqual(self.read(sh.PLAYLISTS_FILE), playlist)
        self.assertEqual(len(os.listdir(self.backups)), backups)

    def test_dry_run_writes_nothing(self):
        playlist = self.read(sh.PLAYLISTS_FILE)
        self.run_main("--dry-run")
        self.assertFalse(os.path.exists(os.path.join(self.pp, "Libraries/Hymns/Morning Light Is Calling.pro")))
        self.assertEqual(slide_texts(self.read("Libraries/Hymns/Go Now Together.pro")), ["Wrong words"])
        self.assertEqual(self.read(sh.PLAYLISTS_FILE), playlist)
        self.assertEqual(os.listdir(self.backups), [])

    def test_skip_if_running(self):
        playlist = self.read(sh.PLAYLISTS_FILE)
        with mock.patch.object(sh, "propresenter_running", return_value=True):
            self.run_main("--skip-if-running")
        self.assertEqual(self.read(sh.PLAYLISTS_FILE), playlist)
        self.assertFalse(os.path.exists(os.path.join(self.pp, "Libraries/Hymns/Morning Light Is Calling.pro")))

    def test_no_lyrics_uses_existing_deck_as_is(self):
        os.remove(os.path.join(self.titles, "hymn-for-journey-lyrics.txt"))
        self.run_main()
        self.assertEqual(slide_texts(self.read("Libraries/Hymns/Go Now Together.pro")), ["Wrong words"])
        rels = [r for _, _, r in playlist_items(self.read(sh.PLAYLISTS_FILE))[:6]]
        self.assertEqual(rels[4], "Libraries/Hymns/Go Now Together.pro")

    def test_no_lyrics_and_no_deck_leaves_the_slot(self):
        os.remove(os.path.join(self.titles, "hymn-of-gathering-lyrics.txt"))
        self.run_main()
        rels = [r for _, _, r in playlist_items(self.read(sh.PLAYLISTS_FILE))[:6]]
        self.assertEqual(rels[2], SUNDAY[2])

    def test_missing_titles_folder_exits(self):
        with mock.patch.object(sh, "titles_dir", return_value=None):
            with self.assertRaises(SystemExit):
                sh.main(["--propresenter", self.pp, "--backup-dir", self.backups])


if __name__ == "__main__":
    unittest.main()
