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
from unittest import mock

import sermon_audio
from sermon_audio import (
    apply_pads,
    check_span,
    cut_sermon,
    find_sermon_span,
    speech_end_before_music,
    parse_speech_segments,
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


class SpeechSegmentTests(unittest.TestCase):
    def test_parses_plain_json(self):
        reply = '{"speech": [{"start": 0.0, "end": 120.5}, {"start": 130, "end": 200}]}'
        self.assertEqual(parse_speech_segments(reply), [(0.0, 120.5), (130.0, 200.0)])

    def test_strips_markdown_fences(self):
        reply = '```json\n{"speech": [{"start": 1, "end": 2}]}\n```'
        self.assertEqual(parse_speech_segments(reply), [(1.0, 2.0)])

    def test_accepts_minute_second_strings(self):
        reply = '{"speech": [{"start": "0:05", "end": "3:20.5"}]}'
        self.assertEqual(parse_speech_segments(reply), [(5.0, 200.5)])

    def test_empty_list_is_valid(self):
        self.assertEqual(parse_speech_segments('{"speech": []}'), [])

    def test_drops_backwards_segments_and_sorts(self):
        reply = '{"speech": [{"start": 50, "end": 60}, {"start": 9, "end": 3}, {"start": 1, "end": 2}]}'
        self.assertEqual(parse_speech_segments(reply), [(1.0, 2.0), (50.0, 60.0)])

    def test_rejects_garbage(self):
        for reply in ("no idea", '{"other": 1}', '{"speech": [{"begin": 1}]}'):
            with self.assertRaises(ValueError):
                parse_speech_segments(reply)


class SpeechEndBeforeMusicTests(unittest.TestCase):
    def test_music_after_the_last_word(self):
        self.assertEqual(speech_end_before_music([(0, 100), (110, 215)], 300), 215)

    def test_next_speaker_starting_at_the_timing_does_not_block_the_trim(self):
        # The real 10-18 reply: speech to 251s, music, then the next item's first words.
        self.assertEqual(speech_end_before_music([(0, 251), (292, 300)], 300), 251)

    def test_a_long_run_of_speech_after_the_gap_is_still_sermon(self):
        self.assertIsNone(speech_end_before_music([(0, 100), (115, 300)], 300))

    def test_pause_in_the_sermon_before_the_music_is_not_the_cut(self):
        self.assertEqual(speech_end_before_music([(0, 100), (115, 215)], 300), 215)

    def test_last_qualifying_gap_wins(self):
        # "Amen" (5s of speech) between two gaps stays in the sermon.
        self.assertEqual(speech_end_before_music([(0, 100), (112, 117), (292, 300)], 300), 117)

    def test_less_than_ten_seconds_of_quiet_is_not_a_gap(self):
        self.assertIsNone(speech_end_before_music([(0, 295)], 300))

    def test_exactly_ten_seconds_counts(self):
        self.assertEqual(speech_end_before_music([(0, 290)], 300), 290)

    def test_no_speech_found_means_no_trim(self):
        self.assertIsNone(speech_end_before_music([], 300))

    def test_end_beyond_the_clip_is_clamped(self):
        self.assertIsNone(speech_end_before_music([(0, 400)], 300))


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

    def _make_audio(self, segments):
        """A stream built from ('tone'|'quiet', seconds) pieces; 'quiet' is -60 dB
        noise, like an open mic in an empty room."""
        audio = os.path.join(self.dir, "stream.m4a")
        sources = []
        for kind, seconds in segments:
            sources += ["-f", "lavfi", "-i",
                        f"sine=frequency=440:duration={seconds}" if kind == "tone"
                        else f"anoisesrc=duration={seconds}:amplitude=0.001:color=white"]
        inputs = "".join(f"[{i}]" for i in range(len(segments)))
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *sources, "-filter_complex",
                        f"{inputs}concat=n={len(segments)}:v=0:a=1", "-c:a", "aac", audio],
                       check=True)
        image = os.path.join(self.dir, "thumb.png")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
                        "-i", "color=c=blue:s=640x480", "-frames:v", "1", image], check=True)
        return audio, image

    def _cut_whole_stream(self, segments):
        audio, image = self._make_audio(segments)
        total = int(sum(seconds for _, seconds in segments))
        out = os.path.join(self.dir, "sermon.mp4")
        cut_sermon(f"00:00 Sermon - X\n{total // 60}:{total % 60:02d} Offering", audio, image, out)
        return probe_duration(out)

    def _cut_with_speech(self, segments, env_key="test-key"):
        audio, image = self._make_audio([("tone", 600)])
        out = os.path.join(self.dir, "sermon.mp4")
        env = {"GEMINI_API_KEY": env_key} if env_key else {}
        with mock.patch.dict(os.environ, env, clear=False), \
                mock.patch.object(sermon_audio, "ask_gemini_for_speech", return_value=segments) as ask:
            if not env_key:
                os.environ.pop("GEMINI_API_KEY", None)
            cut_sermon("00:00 Sermon - X\n10:00 Offering", audio, image, out)
        return probe_duration(out), ask

    def test_times_past_the_end_of_the_clip_are_not_trusted(self):
        # The model once answered 0-410 / 493-500 for a 300s clip (it meant 4:10, 4:53-5:00).
        duration, _ = self._cut_with_speech([(0.0, 410.0), (493.0, 500.0)])
        self.assertAlmostEqual(duration, 600, delta=1.5)

    def test_trims_the_music_after_the_last_spoken_word(self):
        # The span is 600s; Gemini sees the last 300s (starting at 300s) and hears
        # speech to 200s into it, so the sermon ends at 300 + 200 + pad.
        duration, ask = self._cut_with_speech([(0.0, 200.0)])
        self.assertAlmostEqual(duration, 500 + sermon_audio.SPEECH_TAIL_PAD_SECONDS, delta=1.5)
        self.assertEqual(ask.call_args[0][1], "test-key")

    def test_speech_to_the_end_is_not_trimmed(self):
        duration, _ = self._cut_with_speech([(0.0, 296.0)])
        self.assertAlmostEqual(duration, 600, delta=1.5)

    def test_next_speakers_first_words_at_the_end_are_cut_too(self):
        duration, _ = self._cut_with_speech([(0.0, 251.0), (292.0, 300.0)])
        self.assertAlmostEqual(duration, 300 + 251 + sermon_audio.SPEECH_TAIL_PAD_SECONDS, delta=1.5)

    def test_no_api_key_keeps_the_timed_end(self):
        duration, ask = self._cut_with_speech([(0.0, 200.0)], env_key=None)
        self.assertAlmostEqual(duration, 600, delta=1.5)
        ask.assert_not_called()

    def test_gemini_failure_keeps_the_timed_end(self):
        audio, image = self._make_audio([("tone", 600)])
        out = os.path.join(self.dir, "sermon.mp4")
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "k"}), \
                mock.patch.object(sermon_audio, "ask_gemini_for_speech",
                                  side_effect=sermon_audio.requests.ConnectionError("down")):
            cut_sermon("00:00 Sermon - X\n10:00 Offering", audio, image, out)
        self.assertAlmostEqual(probe_duration(out), 600, delta=1.5)

    def test_trim_that_would_leave_too_short_a_sermon_is_ignored(self):
        audio, image = self._make_audio([("tone", 300)])
        out = os.path.join(self.dir, "sermon.mp4")
        # Window = whole 300s span; speech ends at 50s -> 52s left, under 3 minutes.
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "k"}), \
                mock.patch.object(sermon_audio, "ask_gemini_for_speech", return_value=[(0.0, 50.0)]):
            cut_sermon("00:00 Sermon - X\n5:00 Offering", audio, image, out)
        self.assertAlmostEqual(probe_duration(out), 300, delta=1.5)

    def test_trailing_dead_air_is_trimmed(self):
        duration = self._cut_whole_stream([("tone", 200), ("quiet", 25)])
        self.assertAlmostEqual(duration, 200 + sermon_audio.SILENCE_TAIL_PAD_SECONDS, delta=1.5)

    def test_short_trailing_pause_is_kept(self):
        self.assertAlmostEqual(self._cut_whole_stream([("tone", 200), ("quiet", 5)]), 205, delta=1.5)

    def test_silence_in_the_middle_is_kept(self):
        duration = self._cut_whole_stream([("tone", 100), ("quiet", 15), ("tone", 100)])
        self.assertAlmostEqual(duration, 215, delta=1.5)

    def test_no_trim_that_would_leave_too_short_a_sermon(self):
        self.assertAlmostEqual(self._cut_whole_stream([("tone", 60), ("quiet", 150)]), 210, delta=1.5)

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
