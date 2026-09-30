#!/usr/bin/env python3
"""prepare_week.py's reading of real Orders of Worship, one per page-1 layout seen so far.

  python3 -m unittest fccla/test/test_prepare_week.py

Uses the PDFs in ~/Documents/Programming/OW/OWs (a clone of cju-media/OW); skipped without them.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import prepare_week as pw  # noqa: E402

OW = os.path.expanduser("~/Documents/Programming/OW/OWs")

CASES = {
    # label, name ~ title
    "9.27.26_OW_Draft.pdf": (["Fall Series 4", "Painting the Stars", "An Anticipatory Universe"],
                             "September 27, 2026", "Rev. Laura Vail Fregin"),
    "8.16.26_OW_Draft.pdf": (["Pentecost 11", "Another Kind of Freedom", "Healing Division"],
                             "August 16, 2026", "Rev. Laura Vail Fregin"),
    # label, name, title on separate lines
    "3.15.26 OW.pdf": (["Lent 4", "The Only Thing More Powerful Than Hate is Love", "Edge Walking"],
                       "March 15, 2026", "Rev. Michael Lehman"),
    # no label; a musical "Reflection" row comes before the Sermon row
    "6.7.26 OW.pdf": (["Another Kind of Freedom", "The God Who Sees Us"], "June 7, 2026", "Rev. Fregin"),
    # title only
    "1.18.26_OW.pdf": (["Fulfilling The Dream For Freedom"], "January 18, 2026", "Rev. Lehman"),
    # 2025: "September 28th, 2025", and "Name ~" / "Title" split over two lines
    "9.28.25_OW.pdf": (["The Gospel of Life", "The Gospel According to Antoni Gaudí"], "September 28, 2025", None),
    # 2025: the week label at the bottom
    "4.27.25_OW.pdf": (["Life from Death", "Two Ways", "Eastertide 1"], "April 27, 2025", "Rev. Laura Vail Fregin"),
    # a Reflection instead of a Sermon; a Sermon row with no title
    "12.14.25_OW.pdf": (None, None, "Rev. Lehman"),
    "5.17.26 OW.pdf": (None, None, "Rev. Michael Lehman"),
}


@unittest.skipUnless(os.path.isdir(OW), "no OW PDFs at " + OW)
class RealOWs(unittest.TestCase):
    def test_layouts(self):
        for name, (heading, date, preacher) in CASES.items():
            with self.subTest(name):
                f, _ = pw.parse_pdf(os.path.join(OW, name))
                if heading is not None:
                    self.assertEqual(f["heading"], heading)
                    self.assertEqual(f["title"], heading[-1])
                if date is not None:
                    self.assertEqual(f.get("dateText"), date)
                if preacher is not None:
                    self.assertEqual(f.get("preacher"), preacher)


class HeadingOf(unittest.TestCase):
    def test_heading_field_and_the_older_three(self):
        self.assertEqual(pw.heading_of({"heading": "A | B |  | C "}), ["A", "B", "C"])
        self.assertEqual(pw.heading_of({"heading": ["A", " ", "C"]}), ["A", "C"])
        self.assertEqual(pw.heading_of({"series": "", "seriesName": "Name", "title": "Title"}), ["Name", "Title"])

    def test_split_heading(self):
        self.assertEqual(pw.split_heading(["Fall Series 4", "Painting the Stars ~ An Anticipatory Universe"]),
                         ["Fall Series 4", "Painting the Stars", "An Anticipatory Universe"])
        self.assertEqual(pw.split_heading(["The Gospel of Life ~", "The Gospel According to Antoni Gaudí"]),
                         ["The Gospel of Life", "The Gospel According to Antoni Gaudí"])


if __name__ == "__main__":
    unittest.main()
