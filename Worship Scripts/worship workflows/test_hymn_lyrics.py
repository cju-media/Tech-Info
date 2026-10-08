"""Tests for hymn_lyrics.py, on OW text laid out the way pypdf extracts it (made-up words).

  cd "Worship Scripts/worship workflows" && python3 -m unittest test_hymn_lyrics -v
"""
import unittest

import hymn_lyrics as hl

OW = """\
Please stand now, in body or in spirit, for our Hymn of Gathering

*Hymn of Gathering                 Morning Light Is Calling                OLD HUNDRETH
Morning light is calling,
Over hill and over sea,
Wake the town and valley,
Sing for you and me.

Evening comes so gently,
Stars above the shore,
Rest the town and valley,
Peace for evermore.
*Prayer of Awareness                       Rev. Someone
Holy One, hear us.

*Hymn for the Journey                 Go Now Together                           SLANE
Go now together, the road is ahead,
Carry the lantern and share the bread.

Go now rejoicing, the song is begun,
Into the world as the many are one.
                                                         --lyrics A. Writer

Postlude            Someone, Organist
"""


class Extract(unittest.TestCase):
    def test_both_hymns(self):
        r = hl.extract(OW)
        self.assertEqual(r["hymn-of-gathering"], [
            ["Morning light is calling,", "Over hill and over sea,", "Wake the town and valley,",
             "Sing for you and me."],
            ["Evening comes so gently,", "Stars above the shore,", "Rest the town and valley,",
             "Peace for evermore."]])
        self.assertEqual([len(v) for v in r["hymn-for-journey"]], [2, 2])
        self.assertEqual(r["hymn-for-journey"][1][-1], "Into the world as the many are one.")

    def test_please_stand_line_is_not_the_header(self):
        self.assertEqual(hl.extract(OW)["hymn-of-gathering"][0][0], "Morning light is calling,")

    def test_missing_hymn_is_left_out(self):
        self.assertEqual(hl.extract("*Hymn of Gathering   A   TUNE\nOne line\n"),
                         {"hymn-of-gathering": [["One line"]]})
        self.assertEqual(hl.extract("Nothing here"), {})

    def test_header_with_a_season_word(self):
        r = hl.extract("*Easter Hymn for the Journey      New Day      HYMN TO JOY\nA new day,\n")
        self.assertEqual(r, {"hymn-for-journey": [["A new day,"]]})

    def test_page_break_blank_lines_are_one_verse_break(self):
        r = hl.extract("*Hymn of Gathering   A   T\nOne,\nTwo,\n\n\n\n\nThree,\nFour.\n*Next   X\n")
        self.assertEqual(r["hymn-of-gathering"], [["One,", "Two,"], ["Three,", "Four."]])

    def test_whitespace_is_tidied(self):
        r = hl.extract("*Hymn of Gathering   A   T\n  Wide   spaced line,   \nNext.\n")
        self.assertEqual(r["hymn-of-gathering"], [["Wide spaced line,", "Next."]])


class Ends(unittest.TestCase):
    def ends(self, line):
        return hl.extract("*Hymn of Gathering   A   T\nFirst line,\n%s\nAfter.\n" % line)[
            "hymn-of-gathering"] == [["First line,"]]

    def test_section_headers(self):
        self.assertTrue(self.ends("*Prayer of Awareness                       Rev. Someone"))
        self.assertTrue(self.ends("Postlude            Dr. Someone, Organist"))
        self.assertTrue(self.ends("Inside The Music"))
        self.assertTrue(self.ends("Benediction"))

    def test_credits(self):
        for credit in ("-lyrics by A. Writer", "--lyrics Writer", "– Words by A. Writer",
                       "[Lyrics by A. Writer]", "Words and Music by A. Writer (1985)",
                       "lyrics adapted from A. Writer"):
            self.assertTrue(self.ends(credit), credit)

    def test_lyric_lines_that_look_like_endings_do_not_end(self):
        for line in ("Words of Scripture tell our stories,", "Music fills the morning air,",
                     "Text and tune together,", "Postludes are for later"):
            self.assertFalse(self.ends(line), line)


class Notes(unittest.TestCase):
    def lyrics(self, body):
        return hl.extract("*Hymn of Gathering   A   T\n%s\n*End   X\n" % body)["hymn-of-gathering"]

    def test_copyright_and_instructions_are_dropped(self):
        self.assertEqual(self.lyrics("Someone of Song Inc., Conductor\n\nSing it,"), [["Sing it,"]])
        self.assertEqual(self.lyrics("© 2020 Someone. Used by permission.\nSing it,"), [["Sing it,"]])
        self.assertEqual(self.lyrics("We will sing the words in capitals in response to the leader\n"
                                     "I SING,"), [["I SING,"]])

    def test_word_conductor_in_a_lyric_stays(self):
        self.assertEqual(self.lyrics("God, composer and conductor of the song,"),
                         [["God, composer and conductor of the song,"]])

    def test_trailing_notes_are_stripped(self):
        self.assertEqual(self.lyrics("Echoes our song. [JWriter]\nFreedom! (repeat many times)"),
                         [["Echoes our song.", "Freedom!"]])


class Refrain(unittest.TestCase):
    def test_refrain_is_sung_again_where_marked(self):
        body = ("Verse one line,\nVerse one end.\n\nRefrain: Lean on, lean on,\nLean on me.\n\n"
                "Verse two line,\nVerse two end.\nRefrain\n")
        r = hl.extract("*Hymn for the Journey   A   T\n%s\nPostlude   X\n" % body)["hymn-for-journey"]
        self.assertEqual(r, [["Verse one line,", "Verse one end."], ["Lean on, lean on,", "Lean on me."],
                             ["Verse two line,", "Verse two end."], ["Lean on, lean on,", "Lean on me."]])

    def test_chorus_label_too(self):
        body = "Chorus I will go,\nI will go.\n\nVerse,\nChorus\n"
        r = hl.extract("*Hymn for the Journey   A   T\n%s" % body)["hymn-for-journey"]
        self.assertEqual(r, [["I will go,", "I will go."], ["Verse,"], ["I will go,", "I will go."]])

    def test_bare_refrain_before_any_refrain_is_ignored(self):
        r = hl.extract("*Hymn for the Journey   A   T\nVerse,\nRefrain\n")["hymn-for-journey"]
        self.assertEqual(r, [["Verse,"]])


class FileText(unittest.TestCase):
    def test_round_trip(self):
        verses = [["One,", "Two."], ["Three,", "Four."]]
        text = hl.to_file_text(verses)
        self.assertEqual(text, "One,\nTwo.\n\nThree,\nFour.\n")
        self.assertEqual(hl.from_file_text(text), verses)

    def test_from_empty(self):
        self.assertEqual(hl.from_file_text(""), [])
        self.assertEqual(hl.from_file_text("\n \n"), [])


if __name__ == "__main__":
    unittest.main()
