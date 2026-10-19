"""Unit tests for sermon_audio.py.

Run from this directory:  python -m unittest test_sermon_audio -v

The ffmpeg test renders a synthetic tone, so it needs ffmpeg/ffprobe but no
network, YouTube or Drive; it is skipped when they are missing.
"""

import json
import os
import shutil
import subprocess
import tempfile
import unittest

import sermon_audio
from sermon_audio import (
    apply_pads,
    check_span,
    cut_sermon,
    find_sermon_span,
    parse_timestamp,
    probe_duration,
)

TIMINGS = """00:00 Announcements
38:36 Welcome - Rev. Michael Lehman
1:01:09 Meditation Anthem - Fly Free By Day, Swim Deep At Night - David Montoya
1:09:25 Sermon - We Are Stories & Stardust - Rev. Laura Vail Fregin
1:25:20 Offering Prayers - Lance Skiles
1:43:40 Postroll Chat"""


class ParseTimestampTests(unittest.TestCase):
    def test_minutes_and_seconds(self):
        self.assertEqual(parse_timestamp("56:01"), 56 * 60 + 1)

    def test_hours(self):
        self.assertEqual(parse_timestamp("1:09:25"), 3600 + 9 * 60 + 25)

    def test_zero(self):
        self.assertEqual(parse_timestamp("00:00"), 0)


class FindSermonSpanTests(unittest.TestCase):
    def test_span_runs_to_the_next_timing(self):
        start, end, item = find_sermon_span(TIMINGS)
        self.assertEqual(start, 4165)   # 1:09:25
        self.assertEqual(end, 5120)     # 1:25:20
        self.assertTrue(item.startswith("Sermon - We Are Stories"))

    def test_sermon_as_last_line_has_no_end(self):
        start, end, _ = find_sermon_span("00:00 Announcements\n50:00 Sermon - X")
        self.assertEqual((start, end), (3000, None))

    def test_no_sermon_line(self):
        self.assertIsNone(find_sermon_span("00:00 Announcements\n10:00 Welcome"))

    def test_word_sermon_inside_another_item_is_not_the_sermon(self):
        self.assertIsNone(find_sermon_span("00:00 Announcements\n10:00 Hymn - Sermon On The Mount"))

    def test_blank_and_untimed_lines_are_ignored(self):
        text = "\n00:00 Announcements\n\nnot a timing\n20:00 Sermon - X\n40:00 Offering\n"
        self.assertEqual(find_sermon_span(text)[:2], (1200, 2400))

    def test_every_archived_week_has_a_plausible_sermon(self):
        archives = os.path.join(os.path.dirname(__file__), "..", "Archives")
        checked = 0
        for week in sorted(os.listdir(archives)):
            path = os.path.join(archives, week, "timings.txt")
            if not os.path.exists(path):
                continue
            with open(path, encoding="utf-8") as f:
                span = find_sermon_span(f.read())
            self.assertIsNotNone(span, week)
            check_span(*apply_pads(span[0], span[1]))
            checked += 1
        self.assertGreater(checked, 0)


class CheckSpanTests(unittest.TestCase):
    def test_accepts_a_typical_sermon(self):
        check_span(4165, 5120)

    def test_rejects_open_ended_span(self):
        with self.assertRaises(ValueError):
            check_span(4165, None)

    def test_rejects_too_short(self):
        with self.assertRaises(ValueError):
            check_span(100, 200)

    def test_rejects_too_long(self):
        with self.assertRaises(ValueError):
            check_span(0, 2 * 3600)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg not installed")
class RenderTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)

    def _make_inputs(self, audio_seconds):
        audio = os.path.join(self.dir, "stream.m4a")
        image = os.path.join(self.dir, "thumb.png")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
                        "-i", f"sine=frequency=440:duration={audio_seconds}",
                        "-c:a", "aac", audio], check=True)
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
                        "-i", "color=c=blue:s=640x480", "-frames:v", "1", image], check=True)
        return audio, image

    def _streams(self, path):
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type",
                              "-of", "json", path], check=True, capture_output=True, text=True)
        return [s["codec_type"] for s in json.loads(out.stdout)["streams"]]

    def test_cuts_the_sermon_span_over_the_image(self):
        audio, image = self._make_inputs(540)
        out = os.path.join(self.dir, "sermon.mp4")
        cut_sermon("00:00 Announcements\n02:00 Sermon - X\n07:00 Offering", audio, image, out)
        self.assertAlmostEqual(probe_duration(out), 300, delta=1.5)
        self.assertCountEqual(self._streams(out), ["video", "audio"])

    def test_sermon_as_last_line_runs_to_end_of_audio(self):
        audio, image = self._make_inputs(540)
        out = os.path.join(self.dir, "sermon.mp4")
        cut_sermon("00:00 Announcements\n04:00 Sermon - X", audio, image, out)
        self.assertAlmostEqual(probe_duration(out), 300, delta=1.5)

    def test_implausible_span_is_not_rendered(self):
        audio, image = self._make_inputs(540)
        out = os.path.join(self.dir, "sermon.mp4")
        with self.assertRaises(ValueError):
            cut_sermon("00:00 Announcements\n02:00 Sermon - X\n02:30 Offering", audio, image, out)
        self.assertFalse(os.path.exists(out))


if __name__ == "__main__":
    unittest.main()
