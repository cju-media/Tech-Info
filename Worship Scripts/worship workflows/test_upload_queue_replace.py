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

import upload_queue_to_drive as uq  # noqa: E402


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

    def test_newer_image_is_found(self):
        drive = FakeDrive(images=[("abc", "mine.jpg", NEW)])
        self.assertEqual(uq.newer_image_in_folder(drive, "folder", "2026-09-30T23:00:00+00:00"), "mine.jpg")
        self.assertIsNone(uq.newer_image_in_folder(drive, "folder", "2026-10-01T00:00:00+00:00"))


class QueueWithSidecar(unittest.TestCase):
    """main() on a queued worship-service thumbnail, with and without {"replace": true}."""

    def run_queue(self, sidecar, images=(("abc", "Service_Title_10-4-26.jpg", OLD),),
                  name="Service_Title_10-4-26.jpg", folder=None):
        work = tempfile.mkdtemp()
        cwd = os.getcwd()
        os.chdir(work)
        try:
            os.makedirs(uq.QUEUE_DIR)
            os.makedirs(os.path.join("Worship Scripts", "service-titles"))
            os.makedirs("Youtube Processing")
            for p in (os.path.join("Worship Scripts", "service-titles", "title.txt"),
                      os.path.join("Youtube Processing", "Description.txt"),
                      os.path.join("Youtube Processing", "create_youtube_stream.py")):
                open(p, "w").close()
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
                 mock.patch.object(uq, "content_matches_target_week", return_value=True), \
                 mock.patch.object(uq, "description_matches_target_week", return_value=True), \
                 mock.patch.object(uq, "dispatch_event"), \
                 mock.patch("subprocess.run", side_effect=lambda cmd, **k: runs.append(cmd) or
                            types.SimpleNamespace(returncode=0)):
                uq.main()
            left = sorted(os.listdir(uq.QUEUE_DIR))
            self.dated = dated
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

    def test_replace_never_touches_an_undated_parent_folder(self):
        # no date in the sidecar or the name, so the image stays in the parent folder: nothing
        # there may be overwritten or trashed
        drive, runs, left = self.run_queue({"replace": True}, name="thumbnail.jpg")
        self.assertEqual(self.dated, [])
        self.assertIn("create", drive.calls)
        self.assertFalse(any(c.startswith(("update", "trash")) for c in drive.calls))


if __name__ == "__main__":
    unittest.main()
