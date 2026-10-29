"""Unit tests for service_keys.py and the per-service pipeline built on it.

Run from this directory:  python -m unittest test_service_keys -v

Most weeks have one service; Christmas Eve 2026 (a Thursday) has three --
5:30pm, 8pm and 11pm, each with its own OW, stream and titles -- and every OW,
the Sunday after's included, may be uploaded weeks ahead. These walk that
week through: which OW becomes which service, which service the live
service-titles/ holds as the evening goes on, which stream each thumbnail and
pushed timings.txt belong to, and what each description links to. The Google
client libraries and requests are stubbed (python-dateutil and pytz are
needed).
"""

import datetime
import json
import os
import shutil
import sys
import tempfile
import types
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
YT = os.path.join(HERE, "..", "..", "Youtube Processing")

for name in ("requests", "googleapiclient", "googleapiclient.discovery", "googleapiclient.http",
             "googleapiclient.errors", "google", "google.oauth2", "google.oauth2.service_account",
             "google.oauth2.credentials", "google.auth", "google.auth.exceptions", "pypdf",
             "google.genai", "google.genai.types"):
    sys.modules.setdefault(name, types.ModuleType(name))
sys.modules["googleapiclient.discovery"].build = None
sys.modules["googleapiclient.http"].MediaIoBaseUpload = lambda *a, **k: object()
sys.modules["googleapiclient.http"].MediaFileUpload = lambda *a, **k: object()
sys.modules["googleapiclient.errors"].HttpError = type("HttpError", (Exception,), {})
sys.modules["google.oauth2"].service_account = sys.modules["google.oauth2.service_account"]
sys.modules["google.oauth2.credentials"].Credentials = None
sys.modules["google.auth.exceptions"].RefreshError = type("RefreshError", (Exception,), {})
sys.modules["google"].genai = sys.modules["google.genai"]
sys.modules["google.genai"].types = sys.modules["google.genai.types"]

sys.path.insert(0, HERE)
sys.path.insert(0, YT)
import service_keys as sk  # noqa: E402
import update_service_titles as ust  # noqa: E402
import create_youtube_stream as cys  # noqa: E402
import update_youtube_stream as uys  # noqa: E402
import generate_youtube_description as gyd  # noqa: E402

D = datetime.date
TZ = sk.TZ


def at(y, m, d, hh, mm=0):
    return datetime.datetime(y, m, d, hh, mm, tzinfo=TZ)


class ServicesDir(unittest.TestCase):
    """Each test gets an empty Worship Scripts/services/ of its own."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        patcher = mock.patch.object(sk, "SERVICES_DIR", self.root)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(shutil.rmtree, self.root, True)

    def add(self, key, title="A title", description="A description", sha="sha", desc_sha=None,
            files=None):
        date, time = sk.parse_service_key(key)
        sk.write_state(key, {"key": key, "date": date.isoformat(), "time": time, "ow_sha": sha,
                             "ow_key": sk.ow_index_key(date, time)})
        os.makedirs(sk.titles_dir(key), exist_ok=True)
        for name, text in dict({"title.txt": title}, **(files or {})).items():
            with open(os.path.join(sk.titles_dir(key), name), "w") as fh:
                fh.write(text)
        if description is not None:
            with open(os.path.join(sk.service_dir(key), "Description.txt"), "w") as fh:
                fh.write(description)
            sk.write_description_state(key, {"ow_sha": desc_sha or sha})

    def christmas(self):
        for key in ("2026-12-20", "2026-12-24_1730", "2026-12-24_2000", "2026-12-24_2300", "2026-12-27"):
            self.add(key, title="Title for " + key)


class Parsing(unittest.TestCase):
    def test_ow_filenames(self):
        cases = {
            "11.1.26 OW Draft.pdf": (D(2026, 11, 1), None),
            "12.27.26 OW.pdf": (D(2026, 12, 27), None),
            "12.24.26 5:30pm OW.pdf": (D(2026, 12, 24), "17:30"),
            "12.24.26_530pm.pdf": (D(2026, 12, 24), "17:30"),
            "12.24.26 5:30 PM OW FINAL.pdf": (D(2026, 12, 24), "17:30"),
            "12.24.26 8pm OW.pdf": (D(2026, 12, 24), "20:00"),
            "12.24.25_11pm.pdf": (D(2025, 12, 24), "23:00"),
            "12.24.2026 11pm OW.pdf": (D(2026, 12, 24), "23:00"),
            "8.16.26_OW_Draft.pdf": (D(2026, 8, 16), None),
        }
        for name, want in cases.items():
            self.assertEqual(sk.parse_ow_filename(name), want, name)
        self.assertIsNone(sk.parse_ow_filename("index.json"))
        self.assertIsNone(sk.parse_ow_filename("13.40.26 OW.pdf"))

    def test_keys_round_trip(self):
        self.assertEqual(sk.service_key(D(2026, 12, 27)), "2026-12-27")
        self.assertEqual(sk.service_key("12-24-2026", "17:30"), "2026-12-24_1730")
        self.assertEqual(sk.service_key("12-24-2026", "5:30pm"), "2026-12-24_1730")
        self.assertEqual(sk.parse_service_key("2026-12-24_2300"), (D(2026, 12, 24), "23:00"))
        self.assertEqual(sk.parse_service_key("2026-12-27"), (D(2026, 12, 27), None))
        self.assertIsNone(sk.parse_service_key("pending"))

    def test_ow_index_keys_match_the_ow_repo(self):
        # cju-media/OW's generate-ow-index.js: on-the-hour keys unchanged, minutes kept
        self.assertEqual(sk.ow_index_key(D(2026, 12, 27)), "12-27-26")
        self.assertEqual(sk.ow_index_key(D(2026, 11, 1)), "11-1-26")
        self.assertEqual(sk.ow_index_key(D(2026, 12, 24), "20:00"), "12-24-26-8pm")
        self.assertEqual(sk.ow_index_key(D(2026, 12, 24), "17:30"), "12-24-26-530pm")
        self.assertEqual(sk.ow_index_key(D(2026, 12, 24), "23:00"), "12-24-26-11pm")

    def test_program_link_slugs(self):
        self.assertEqual(gyd.ows_slug_for(D(2026, 11, 1)), "11-01-26")         # as it has always been
        self.assertEqual(gyd.ows_slug_for(D(2026, 12, 24), "17:30"), "12-24-26-530pm")
        self.assertEqual(gyd.ows_slug_for(D(2026, 12, 24), "23:00"), "12-24-26-11pm")


class WhichService(ServicesDir):
    def test_dashboard_times_find_their_service(self):
        self.christmas()
        self.assertEqual(sk.resolve_key("12-24-2026", "17:30"), "2026-12-24_1730")
        self.assertEqual(sk.resolve_key("12-24-2026", "20:00"), "2026-12-24_2000")
        self.assertEqual(sk.resolve_key("12-24-2026", "23:00"), "2026-12-24_2300")
        # several services and no time match: no guess
        self.assertIsNone(sk.resolve_key("12-24-2026", "10:30"))
        self.assertIsNone(sk.resolve_key("12-24-2026", "19:00"))
        # an ordinary Sunday: the dashboard's default 10:30, or a stream moved to 10:00
        self.assertEqual(sk.resolve_key("12-27-2026", "10:30"), "2026-12-27")
        self.assertEqual(sk.resolve_key("12-27-2026", "10:00"), "2026-12-27")
        self.assertEqual(sk.resolve_key(D(2026, 12, 27), None), "2026-12-27")
        self.assertIsNone(sk.resolve_key("01-03-2027", "10:30"))              # OW not posted yet

    def test_live_set_moves_through_christmas_eve(self):
        self.christmas()
        timeline = [
            (at(2026, 12, 10, 12), "2026-12-20"),       # everything uploaded early: 12/20 is next
            (at(2026, 12, 20, 13), "2026-12-20"),       # 12/20's service still on
            (at(2026, 12, 20, 14), "2026-12-24_1730"),  # 3h after 12/20 started
            (at(2026, 12, 24, 17, 30), "2026-12-24_1730"),
            (at(2026, 12, 24, 18, 59), "2026-12-24_1730"),
            (at(2026, 12, 24, 19, 0), "2026-12-24_2000"),   # an hour before 8pm, not 3h after 5:30
            (at(2026, 12, 24, 21, 59), "2026-12-24_2000"),
            (at(2026, 12, 24, 22, 0), "2026-12-24_2300"),
            (at(2026, 12, 25, 1, 59), "2026-12-24_2300"),   # past midnight: still 11pm's
            (at(2026, 12, 25, 2, 0), "2026-12-27"),
            (at(2026, 12, 27, 13, 29), "2026-12-27"),
            (at(2026, 12, 27, 13, 30), None),               # nothing after 12/27 posted yet
        ]
        for now, want in timeline:
            self.assertEqual(sk.current_service(now), want, now)

    def test_content_ready_needs_both_halves_from_the_same_ow(self):
        self.add("2026-12-24_1730", sha="new", desc_sha="old")
        ok, reason = sk.content_ready("2026-12-24_1730")
        self.assertFalse(ok)
        self.assertIn("isn't generated from its current OW", reason)
        self.add("2026-12-24_2000", description="")
        self.assertFalse(sk.content_ready("2026-12-24_2000")[0])
        self.add("2026-12-24_2300")
        self.assertEqual(sk.content_ready("2026-12-24_2300"), (True, ""))
        self.assertFalse(sk.content_ready("2027-01-03")[0])

    def test_stream_is_recorded_per_service(self):
        self.add("2026-12-24_2000")
        sk.write_stream("2026-12-24_2000", "abc123")
        self.assertEqual(sk.read_stream("2026-12-24_2000")["url"], "https://www.youtube.com/watch?v=abc123")
        self.assertIsNone(sk.read_stream("2026-12-24_1730"))

    def test_description_carries_its_own_timings(self):
        self.add("2026-12-24_1730", description="Program link")
        self.assertEqual(sk.description_for("2026-12-24_1730"), "Program link")
        with open(os.path.join(sk.service_dir("2026-12-24_1730"), "timings.txt"), "w") as fh:
            fh.write("00:00 Announcements\n")
        self.assertEqual(sk.description_for("2026-12-24_1730"), "Program link\n\n00:00 Announcements")


class OwPicking(ServicesDir):
    LISTING = [{"name": n, "sha": "sha-" + n, "download_url": "u"} for n in (
        "12.20.26 OW.pdf", "12.24.26 5:30pm OW Draft.pdf", "12.24.26 5:30pm OW FINAL.pdf",
        "12.24.26 8pm OW.pdf", "12.24.26_11pm.pdf", "12.27.26 OW.pdf", "1.31.27 OW.pdf",
        "11.29.26 OW.pdf", "index.json")]

    def test_every_upcoming_ow_is_a_service(self):
        picked = ust.pick_ows(self.LISTING, D(2026, 12, 10))
        self.assertEqual(sorted(picked), ["2026-12-20", "2026-12-24_1730", "2026-12-24_2000",
                                          "2026-12-24_2300", "2026-12-27"])
        self.assertEqual(picked["2026-12-24_1730"]["name"], "12.24.26 5:30pm OW FINAL.pdf")

    def test_ow_filename_pins_one(self):
        picked = ust.pick_ows(self.LISTING, D(2026, 12, 10), "12.24.26 8pm OW.pdf", D(2026, 12, 13))
        self.assertEqual(list(picked), ["2026-12-24_2000"])

    def test_a_renamed_ow_drops_its_old_service(self):
        self.add("2026-12-24_1700")     # uploaded as 5pm, then corrected to 5:30pm
        self.add("2026-12-24_1730")
        self.add("2026-12-13")          # past: kept for the archive
        picked = ust.pick_ows(self.LISTING, D(2026, 12, 15))
        ust.remove_withdrawn_services(picked, D(2026, 12, 15))
        self.assertEqual([k for k, _, _ in sk.list_services()], ["2026-12-13", "2026-12-24_1730"])


class LivePromotion(ServicesDir):
    """update_service_titles.py's promote step, in a scratch 'Worship Scripts' directory."""

    def setUp(self):
        super().setUp()
        self.work = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.work, True)
        cwd = os.getcwd()
        os.chdir(self.work)
        self.addCleanup(os.chdir, cwd)
        for p in ("get_drive_service", "drive_missing_live_files"):
            patcher = mock.patch.object(ust, p, return_value=None if p == "get_drive_service" else False)
            patcher.start()
            self.addCleanup(patcher.stop)

    def live(self):
        with open(ust.LIVE_STATE_FILE) as fh:
            state = json.load(fh)
        with open(os.path.join(ust.LIVE_TITLES_DIR, "title.txt")) as fh:
            return state["key"], state["target_date"], state["target_time"], fh.read()

    def test_live_set_follows_the_evening(self):
        self.add("2026-12-24_1730", title="Children's Service", files={"prelude2.txt": "Carol"})
        self.add("2026-12-24_2000", title="Candlelight")
        ust.promote_live_set(at(2026, 12, 24, 12), False)
        self.assertEqual(self.live(), ("2026-12-24_1730", "2026-12-24", "17:30", "Children's Service"))
        ust.promote_live_set(at(2026, 12, 24, 19, 5), False)
        self.assertEqual(self.live(), ("2026-12-24_2000", "2026-12-24", "20:00", "Candlelight"))
        # a section only the 5:30 OW had is blanked, not carried into 8pm
        with open(os.path.join(ust.LIVE_TITLES_DIR, "prelude2.txt")) as fh:
            self.assertEqual(fh.read(), "")


class StreamMatching(ServicesDir):
    """create_youtube_stream.find_stream_for_service with Christmas Eve's three broadcasts."""

    BROADCASTS = {"a530": "2026-12-25T01:30:00Z", "a800": "2026-12-25T04:00:00Z",
                  "a1100": "2026-12-25T07:00:00Z", "sun": "2026-12-27T18:30:00Z"}

    def youtube(self):
        items = [{"id": i, "liveStreamingDetails": {"scheduledStartTime": t}} for i, t in self.BROADCASTS.items()]
        call = lambda result: types.SimpleNamespace(execute=lambda: result)  # noqa: E731
        return types.SimpleNamespace(
            playlistItems=lambda: types.SimpleNamespace(list=lambda **k: call(
                {"items": [{"snippet": {"resourceId": {"videoId": i}}} for i in self.BROADCASTS]})),
            videos=lambda: types.SimpleNamespace(list=lambda **k: call({"items": items})),
            liveBroadcasts=lambda: types.SimpleNamespace(list=lambda **k: call({"items": []})))

    def find(self, date, time, date_only=False):
        import pytz
        return cys.find_stream_for_service(self.youtube(), date, time, pytz.timezone("America/Los_Angeles"),
                                           date_only)[0]

    def test_each_service_finds_only_its_own_stream(self):
        self.assertEqual(self.find(D(2026, 12, 24), "17:30"), "a530")
        self.assertEqual(self.find(D(2026, 12, 24), "20:00"), "a800")
        self.assertEqual(self.find(D(2026, 12, 24), "23:00"), "a1100")   # 07:00 UTC on the 25th
        self.assertIsNone(self.find(D(2026, 12, 24), "10:30"))

    def test_a_single_service_day_still_finds_a_moved_stream(self):
        self.assertIsNone(self.find(D(2026, 12, 27), "09:00"))
        self.assertEqual(self.find(D(2026, 12, 27), "09:00", date_only=True), "sun")

    def test_gate_reads_the_services_own_text(self):
        self.add("2026-12-24_2000", title="Candlelight")
        ok, _ = cys.verify_content_ready("2026-12-24_2000", D(2026, 12, 24), D(2026, 12, 3))
        self.assertTrue(ok)                                          # three weeks early is fine
        ok, reason = cys.verify_content_ready(None, D(2026, 12, 24), D(2026, 12, 3))
        self.assertFalse(ok)
        self.assertFalse(cys.verify_content_ready("2026-12-24_2000", D(2026, 12, 24), D(2026, 12, 27))[0])


class TimingsAttribution(ServicesDir):
    """update_youtube_stream.attribute_timings: one timings.txt, three streams ending in an evening."""

    def setUp(self):
        super().setUp()
        self.work = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.work, True)
        cwd = os.getcwd()
        os.chdir(self.work)
        self.addCleanup(os.chdir, cwd)
        for key in ("2026-12-24_1730", "2026-12-24_2000", "2026-12-24_2300"):
            self.add(key)

    def stream(self, vid, start, end=None):
        return {"id": vid, "title": vid, "scheduledStartTime": start, "actualEndTime": end}

    def push(self, text, streams, now):
        with open("timings.txt", "w") as fh:
            fh.write(text)
        state = {}
        if os.path.exists(uys.PUSH_STATE_FILE):
            with open(uys.PUSH_STATE_FILE) as fh:
                state = json.load(fh)
        fake_now = mock.patch.object(uys, "datetime", wraps=datetime.datetime)
        with fake_now as dt:
            dt.now = lambda tz=None: now
            return uys.attribute_timings(streams, state)

    def timings(self, key):
        return sk.read_text(os.path.join(sk.service_dir(key), "timings.txt"))

    def test_each_push_goes_to_the_stream_that_just_ended(self):
        s530 = self.stream("a530", "2026-12-25T01:30:00Z", "2026-12-25T02:45:00Z")
        s800 = self.stream("a800", "2026-12-25T04:00:00Z")
        s1100 = self.stream("a1100", "2026-12-25T07:00:00Z")
        self.assertEqual(self.push("00:00 Kids", [s530, s800, s1100], at(2026, 12, 24, 18, 50)), "2026-12-24_1730")
        s800["actualEndTime"] = "2026-12-25T05:20:00Z"
        self.assertEqual(self.push("00:00 Candles", [s530, s800, s1100], at(2026, 12, 24, 21, 25)), "2026-12-24_2000")
        # the same file again (a re-run) is filed only once
        self.assertIsNone(self.push("00:00 Candles", [s530, s800, s1100], at(2026, 12, 24, 21, 40)))
        s1100["actualEndTime"] = "2026-12-25T08:15:00Z"
        self.assertEqual(self.push("00:00 Midnight", [s530, s800, s1100], at(2026, 12, 25, 0, 20)), "2026-12-24_2300")
        self.assertEqual([self.timings(k) for k in ("2026-12-24_1730", "2026-12-24_2000", "2026-12-24_2300")],
                         ["00:00 Kids", "00:00 Candles", "00:00 Midnight"])


class SermonSeries(ServicesDir):
    def test_the_sunday_after_christmas_eve_uses_its_own_folder(self):
        import create_sermon_series as css
        self.christmas()
        self.add("2026-12-27", files={"sermon-title.txt": "Sermon - Light", "sermon-minister.txt": "Rev. A"})
        # Monday of Christmas week: the live set is about to be Christmas Eve's, but the
        # sermon series is the 12/27 one, made now rather than after 11pm on the 24th.
        folder, sunday = css.sunday_titles_source(D(2026, 12, 21))
        self.assertEqual((folder, sunday), (sk.titles_dir("2026-12-27"), D(2026, 12, 27)))

    def test_without_a_folder_it_falls_back_to_the_live_set(self):
        import create_sermon_series as css
        with mock.patch.object(css, "get_sermon_title_target_date", return_value=D(2026, 11, 1)):
            self.assertEqual(css.sunday_titles_source(D(2026, 10, 29)), ("service-titles", D(2026, 11, 1)))


class DescriptionRendering(unittest.TestCase):
    def test_timed_service_links_to_its_own_program(self):
        titles = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, titles, True)
        with open(os.path.join(titles, "welcome.txt"), "w") as fh:
            fh.write("Rev. Someone")
        cwd = os.getcwd()
        os.chdir(YT)
        try:
            boiler, chapters = gyd.render(["DescriptionBoiler.txt", "Welcome, welcome.txt", "Postroll Chat"],
                                          titles, gyd.ows_slug_for(D(2026, 12, 24), "17:30"))
        finally:
            os.chdir(cwd)
        self.assertIn("https://www.fccla.org/ows/12-24-26-530pm", boiler[0])
        self.assertEqual(chapters, ["Welcome - Rev. Someone", "Postroll Chat"])


if __name__ == "__main__":
    unittest.main()
