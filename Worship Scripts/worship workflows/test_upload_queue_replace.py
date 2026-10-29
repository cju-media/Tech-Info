"""Unit tests for upload_queue_to_drive.py's "replace" sidecar.

Run from this directory:  python -m unittest test_upload_queue_replace -v

A thumbnail dropped on the upload dashboard (Worship Service or Sermon Series) and a title-graphics
pick (fccla/auto_build.py) both carry {"replace": true} in a .meta.json sidecar: the new image
replaces whatever that Sunday's Drive folder holds (in place, extra images to the trash, since
migrate_videos.py gives the sermon video the first image it finds there), and the livestream's
thumbnail is re-uploaded (create_youtube_stream.py --reconcile). A pick also sends "chosen_at": an
image uploaded after it was picked stays. Without the sidecar, nothing changes. The Google client
libraries and requests are stubbed so these run without them (CI installs nothing for this job).
"""

import json
import os
import sys
import tempfile
import types
import unittest
from unittest import mock

for name in ("requests", "googleapiclient", "googleapiclient.discovery", "googleapiclient.http",
             "google", "google.oauth2", "google.oauth2.service_account", "google.oauth2.credentials"):
    sys.modules.setdefault(name, types.ModuleType(name))
sys.modules["googleapiclient.discovery"].build = None
sys.modules["googleapiclient.http"].MediaIoBaseUpload = lambda *a, **k: object()
sys.modules["google.oauth2"].service_account = sys.modules["google.oauth2.service_account"]
sys.modules["google.oauth2.credentials"].Credentials = None

import service_keys  # noqa: E402
import upload_queue_to_drive as uq  # noqa: E402


def make_service(root, key, title="Fall Series 5 || Painting the Stars",
                 description="Download this service's program: https://www.fccla.org/ows/10-04-26", sha="sha1"):
    """A generated service folder (what update_service_titles.py and
    generate_youtube_description.py leave behind) under root."""
    date, time = service_keys.parse_service_key(key)
    folder = os.path.join(root, key)
    os.makedirs(os.path.join(folder, "service-titles"), exist_ok=True)
    with open(os.path.join(folder, "service-titles", "title.txt"), "w") as fh:
        fh.write(title)
    with open(os.path.join(folder, "Description.txt"), "w") as fh:
        fh.write(description)
    with open(os.path.join(folder, "state.json"), "w") as fh:
        json.dump({"key": key, "date": date.isoformat(), "time": time, "ow_sha": sha}, fh)
    with open(os.path.join(folder, "description.json"), "w") as fh:
        json.dump({"ow_sha": sha}, fh)


def services_root(*keys):
    """A temporary Worship Scripts/services/ holding these generated services,
    patched in for the duration of a test."""
    root = tempfile.mkdtemp()
    for key in keys:
        make_service(root, key)
    return mock.patch.object(service_keys, "SERVICES_DIR", root)


class FakeDrive:
    """Just enough of the Drive v3 client: files().list/create/update(...).execute(). Every list
    returns the folder's images (with a stale checksum, so nothing looks already uploaded)."""

    def __init__(self, images=()):
        self.images = [dict(id=i, name=n, modifiedTime=t, md5Checksum="old") for i, n, t in images]
        self.calls = []

    def files(self):
        return self

    def _call(self, kind, result):
        self.calls.append(kind)
        return types.SimpleNamespace(execute=lambda: result)

    def list(self, **kw):
        return self._call("list", {"files": self.images})

    def create(self, **kw):
        return self._call("create", {"id": "new"})

    def update(self, **kw):
        if kw.get("body", {}).get("trashed"):
            return self._call("trash:" + kw["fileId"], {"id": kw["fileId"]})
        return self._call("update:" + kw["fileId"], {"id": kw["fileId"]})


OLD = "2026-09-29T20:00:00.000Z"
NEW = "2026-09-30T23:30:00.000Z"


class UploadToDrive(unittest.TestCase):
    def setUp(self):
        fh, self.path = tempfile.mkstemp(suffix=".jpg")
        os.write(fh, b"jpeg")
        os.close(fh)

    def test_replace_overwrites_the_folders_image_and_trashes_the_rest(self):
        drive = FakeDrive(images=[("abc", "Service Title 10-4.jpg", NEW), ("def", "old.jpg", OLD)])
        self.assertTrue(uq.upload_to_drive(drive, self.path, "Service_Title_10-4-26.jpg", "folder",
                                           skip_if_exists=True, replace=True))
        self.assertIn("update:abc", drive.calls)                  # newest, renamed in place
        self.assertIn("trash:def", drive.calls)                   # so the sermon video can't pick it
        self.assertNotIn("create", drive.calls)

    def test_replace_prefers_the_same_named_image(self):
        drive = FakeDrive(images=[("abc", "other.jpg", NEW), ("def", "Sermon_Title_10-4-26.jpg", OLD)])
        uq.upload_to_drive(drive, self.path, "Sermon_Title_10-4-26.jpg", "folder", replace=True)
        self.assertIn("update:def", drive.calls)
        self.assertIn("trash:abc", drive.calls)

    def test_replace_with_nothing_there_creates(self):
        drive = FakeDrive()
        uq.upload_to_drive(drive, self.path, "Service_Title_10-4-26.jpg", "folder", replace=True)
        self.assertIn("create", drive.calls)

    def test_without_replace_a_second_copy_is_added_as_before(self):
        drive = FakeDrive(images=[("abc", "Service_Title_10-4-26.jpg", OLD)])
        uq.upload_to_drive(drive, self.path, "Service_Title_10-4-26.jpg", "folder", skip_if_exists=True)
        self.assertIn("create", drive.calls)
        self.assertFalse(any(c.startswith(("update", "trash")) for c in drive.calls))

    def test_title_and_description_overwrite_their_earlier_copies(self):
        # a second thumbnail for the same Sunday mustn't leave two title.txt / Description.txt
        drive = FakeDrive(images=[("txt", "title.txt", OLD)])
        with services_root("2026-10-04"), mock.patch.object(uq, "dispatch_event"):
            self.assertTrue(uq.upload_title_and_description(drive, "folder", "10-04-2026", key="2026-10-04"))
        self.assertNotIn("create", drive.calls)
        self.assertEqual(drive.calls.count("update:txt"), 2)    # FakeDrive finds "txt" for any name

    def test_newer_image_is_found(self):
        drive = FakeDrive(images=[("abc", "mine.jpg", NEW)])
        self.assertEqual(uq.newer_image_in_folder(drive, "folder", "2026-09-30T23:00:00+00:00"), "mine.jpg")
        self.assertIsNone(uq.newer_image_in_folder(drive, "folder", "2026-10-01T00:00:00+00:00"))


class QueueWithSidecar(unittest.TestCase):
    """main() on a queued worship-service thumbnail, with and without {"replace": true}."""

    def run_queue(self, sidecar, images=(("abc", "Service_Title_10-4-26.jpg", OLD),),
                  name="Service_Title_10-4-26.jpg", folder=None, services=("2026-10-04",)):
        work = tempfile.mkdtemp()
        cwd = os.getcwd()
        os.chdir(work)
        try:
            os.makedirs(uq.QUEUE_DIR)
            os.makedirs("Youtube Processing")
            open(os.path.join("Youtube Processing", "create_youtube_stream.py"), "w").close()
            queued = os.path.join(uq.QUEUE_DIR, "1790000000000---%s---%s"
                                  % (folder or uq.THUMBNAILS_DEST_PARENT_FOLDER_ID, name))
            with open(queued, "wb") as fh:
                fh.write(b"jpeg")
            if sidecar is not None:
                with open(queued + ".meta.json", "w") as fh:
                    json.dump(sidecar, fh)
            drive = FakeDrive(images=images)
            runs, dated = [], []
            with mock.patch.object(uq, "get_drive_service", return_value=drive), \
                 mock.patch.object(uq, "get_or_create_date_folder",
                                   side_effect=lambda s, parent, d: dated.append(d) or "datefolder"), \
                 services_root(*services), \
                 mock.patch.object(uq, "dispatch_event"), \
                 mock.patch("subprocess.run", side_effect=lambda cmd, **k: runs.append(cmd) or
                            types.SimpleNamespace(returncode=0)):
                uq.main()
            left = sorted(os.listdir(uq.QUEUE_DIR))
            self.dated = dated
            self.pending = sorted(os.listdir(uq.PENDING_STREAM_DIR)) if os.path.isdir(uq.PENDING_STREAM_DIR) else []
            return drive, runs, left
        finally:
            os.chdir(cwd)

    @staticmethod
    def stream_runs(runs):
        return [c for c in runs if any("create_youtube_stream.py" in str(x) for x in c)]

    def test_replace_reconciles_the_stream_and_overwrites_in_drive(self):
        drive, runs, left = self.run_queue({"replace": True})
        stream = self.stream_runs(runs)
        self.assertEqual(len(stream), 1)
        self.assertIn("--reconcile", stream[0])
        self.assertIn("10-4-26", stream[0])
        self.assertIn("update:abc", drive.calls)
        self.assertEqual(left, [])                       # image and sidecar both cleared

    def test_plain_upload_is_unchanged(self):
        drive, runs, left = self.run_queue(None)
        stream = self.stream_runs(runs)
        self.assertEqual(len(stream), 1)
        self.assertNotIn("--reconcile", stream[0])
        self.assertIn("create", drive.calls)
        self.assertEqual(left, [])

    def test_a_pick_files_under_the_dashboards_date(self):
        self.run_queue({"date": "10-04-2026", "replace": True, "chosen_at": "2026-09-30T23:01:13+00:00"})
        self.assertEqual(self.dated, ["10-04-2026"])

    def test_an_upload_after_the_pick_stays(self):
        drive, runs, left = self.run_queue(
            {"date": "10-04-2026", "replace": True, "chosen_at": "2026-09-30T23:01:13+00:00"},
            images=[("mine", "Service_Title_10-4-26_edited.jpg", NEW)])
        self.assertFalse(any(c.startswith(("update", "trash", "create")) for c in drive.calls))
        self.assertEqual(self.stream_runs(runs), [])     # the livestream keeps the newer one too
        self.assertEqual(left, [])

    def test_a_pick_replaces_an_upload_made_before_it(self):
        drive, runs, left = self.run_queue(
            {"date": "10-04-2026", "replace": True, "chosen_at": "2026-09-30T23:01:13+00:00"},
            images=[("mine", "Service_Title_10-4-26.jpg", OLD)])
        self.assertIn("update:mine", drive.calls)
        self.assertIn("--reconcile", self.stream_runs(runs)[0])

    def test_sermon_thumbnail_from_the_dashboard_replaces(self):
        drive, runs, left = self.run_queue({"replace": True}, name="Sermon_Title_10-4-26.jpg",
                                           folder=uq.SERMON_DEST_PARENT_FOLDER_ID,
                                           images=[("auto", "Sermon_Title_10-4-26.jpg", OLD),
                                                   ("dup", "Sermon Title copy.jpg", OLD)])
        self.assertIn("update:auto", drive.calls)
        self.assertIn("trash:dup", drive.calls)
        self.assertEqual(self.stream_runs(runs), [])     # sermon thumbnails don't touch the stream
        self.assertEqual(left, [])

    def test_christmas_eve_thumbnail_gets_its_own_folder_and_stream(self):
        drive, runs, left = self.run_queue({"date": "12-24-2026", "time": "17:30", "replace": True},
                                           name="Service_Title_12-24-26.jpg",
                                           services=("2026-12-24_1730", "2026-12-24_2000", "2026-12-24_2300"))
        self.assertEqual(self.dated, ["12-24-2026 5:30pm"])
        stream = self.stream_runs(runs)
        self.assertEqual(len(stream), 1)
        self.assertEqual((stream[0][2], stream[0][4]), ("12-24-2026", "17:30"))
        self.assertNotIn("--title", stream[0])          # generated text, not an override

    def test_a_service_without_its_ow_waits_in_its_own_slot(self):
        drive, runs, left = self.run_queue({"date": "12-24-2026", "time": "20:00", "replace": True},
                                           name="Service_Title_12-24-26.jpg",
                                           services=("2026-12-24_1730", "2026-12-24_2300"))
        self.assertEqual(self.stream_runs(runs), [])
        self.assertEqual(self.pending, ["2026-12-24_2000"])

    def test_typed_title_alone_is_kept_with_the_generated_description(self):
        drive, runs, left = self.run_queue({"date": "10-04-2026", "time": "10:30", "title": "My Title"})
        stream = self.stream_runs(runs)[0]
        self.assertEqual(stream[stream.index("--title") + 1], "My Title")
        self.assertIn("ows/10-04-26", stream[stream.index("--description") + 1])

    def test_replace_never_touches_an_undated_parent_folder(self):
        # no date in the sidecar or the name, so the image stays in the parent folder: nothing
        # there may be overwritten or trashed
        drive, runs, left = self.run_queue({"replace": True}, name="thumbnail.jpg")
        self.assertEqual(self.dated, [])
        self.assertIn("create", drive.calls)
        self.assertFalse(any(c.startswith(("update", "trash")) for c in drive.calls))


class DelayedStream(unittest.TestCase):
    """A stream that waited for its title/description (create_pending_stream.py) must still get
    title.txt and Description.txt next to its thumbnail in the week's Drive folder."""

    def run_pending(self, returncode, meta=None, slots=(("10-04-2026", "10:30"),), services=("2026-10-04",)):
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "Youtube Processing"))
        import create_pending_stream as cps
        work = tempfile.mkdtemp()
        cwd = os.getcwd()
        os.chdir(work)
        try:
            self.metas = []
            for date, time in slots:
                slot = uq.pending_slot(date, time)
                os.makedirs(slot)
                thumb = os.path.join(slot, "thumbnail.jpg")
                with open(thumb, "wb") as fh:
                    fh.write(b"jpeg")
                pending = dict({"date": date, "time": time, "thumbnail_path": thumb}, **(meta or {}))
                self.metas.append(os.path.join(work, slot, "meta.json"))
                with open(self.metas[-1], "w") as fh:
                    json.dump(pending, fh)
            drive, events, dated = FakeDrive(), [], []
            self.runs = []
            with services_root(*services), \
                 mock.patch.object(uq, "get_drive_service", return_value=drive), \
                 mock.patch.object(uq, "get_or_create_date_folder",
                                   side_effect=lambda s, parent, d: dated.append((parent, d)) or "datefolder"), \
                 mock.patch.object(uq, "dispatch_event", side_effect=lambda kind, payload: events.append(kind)), \
                 mock.patch.object(cps, "note_thumbnail_attempt"), \
                 mock.patch("subprocess.run", side_effect=lambda cmd, **k: self.runs.append(cmd) or
                            types.SimpleNamespace(returncode=returncode)):
                cps.main()
            left = any(os.path.exists(m) for m in self.metas)
            return drive, events, dated, left
        finally:
            os.chdir(cwd)

    def test_created_stream_gets_its_text_in_drive(self):
        drive, events, dated, left = self.run_pending(0)
        self.assertEqual(dated, [(uq.THUMBNAILS_DEST_PARENT_FOLDER_ID, "10-04-2026")])
        self.assertEqual(drive.calls.count("create"), 2)          # title.txt and Description.txt, new
        self.assertEqual(events, ["worship_title_description_uploaded"])
        self.assertFalse(left)

    def test_not_ready_puts_nothing_in_drive(self):
        drive, events, dated, left = self.run_pending(3)
        self.assertEqual(drive.calls, [])
        self.assertTrue(left)

    def test_a_thumbnail_retry_doesnt_upload_the_text_again(self):
        drive, events, dated, left = self.run_pending(0, {"thumbnail_attempts": 1, "text_in_drive": True})
        self.assertEqual(drive.calls, [])

    def test_no_ow_yet_waits_without_running_anything(self):
        drive, events, dated, left = self.run_pending(0, services=())
        self.assertEqual(self.runs, [])
        self.assertTrue(left)

    def test_christmas_eve_services_wait_and_go_independently(self):
        # 5:30 and 11pm have OWs, 8pm doesn't yet: two streams, each with its own time and Drive
        # folder; 8pm keeps waiting in its own slot.
        drive, events, dated, left = self.run_pending(
            0, slots=(("12-24-2026", "17:30"), ("12-24-2026", "20:00"), ("12-24-2026", "23:00")),
            services=("2026-12-24_1730", "2026-12-24_2300"))
        made = [(c[2], c[4]) for c in self.runs if "create_youtube_stream.py" in c[1]]
        self.assertEqual(sorted(made), [("12-24-2026", "17:30"), ("12-24-2026", "23:00")])
        self.assertEqual(sorted(d for _, d in dated), ["12-24-2026 11pm", "12-24-2026 5:30pm"])
        self.assertEqual([os.path.exists(m) for m in self.metas], [False, True, False])


if __name__ == "__main__":
    unittest.main()
