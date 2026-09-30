"""Unit tests for upload_queue_to_drive.py's "replace" sidecar.

Run from this directory:  python -m unittest test_upload_queue_replace -v

The title-graphics picker (fccla/auto_build.py) queues the chosen Service/Sermon Title JPGs
like a dashboard upload. When the pick is changed it adds {"replace": true} in a .meta.json
sidecar. Then the Drive copy must be overwritten in place, not duplicated, and the
livestream's thumbnail re-uploaded (create_youtube_stream.py --reconcile). Without the
sidecar, nothing may change. The Google client libraries and requests are stubbed so these run
without them (CI installs nothing for this job).
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
    """Just enough of the Drive v3 client: files().list/create/update(...).execute()."""

    def __init__(self, existing=()):
        self.existing = [{"id": i, "md5Checksum": "old"} for i in existing]
        self.calls = []

    def files(self):
        return self

    def _call(self, kind, result):
        self.calls.append(kind)
        return types.SimpleNamespace(execute=lambda: result)

    def list(self, **kw):
        return self._call("list", {"files": self.existing})

    def create(self, **kw):
        return self._call("create", {"id": "new"})

    def update(self, **kw):
        return self._call("update:" + kw["fileId"], {"id": kw["fileId"]})


class UploadToDrive(unittest.TestCase):
    def setUp(self):
        fh, self.path = tempfile.mkstemp(suffix=".jpg")
        os.write(fh, b"jpeg")
        os.close(fh)

    def test_replace_updates_the_existing_copy(self):
        drive = FakeDrive(existing=["abc"])
        self.assertTrue(uq.upload_to_drive(drive, self.path, "Service_Title_10-4-26.jpg", "folder",
                                           skip_if_exists=True, replace=True))
        self.assertIn("update:abc", drive.calls)
        self.assertNotIn("create", drive.calls)

    def test_replace_with_nothing_there_creates(self):
        drive = FakeDrive()
        uq.upload_to_drive(drive, self.path, "Service_Title_10-4-26.jpg", "folder", replace=True)
        self.assertIn("create", drive.calls)

    def test_without_replace_a_second_copy_is_added_as_before(self):
        drive = FakeDrive(existing=["abc"])
        uq.upload_to_drive(drive, self.path, "Service_Title_10-4-26.jpg", "folder", skip_if_exists=True)
        self.assertIn("create", drive.calls)
        self.assertFalse(any(c.startswith("update") for c in drive.calls))


class QueueWithSidecar(unittest.TestCase):
    """main() on a queued worship-service thumbnail, with and without {"replace": true}."""

    def run_queue(self, sidecar):
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
            queued = os.path.join(uq.QUEUE_DIR, "1790000000000---%s---Service_Title_10-4-26.jpg"
                                  % uq.THUMBNAILS_DEST_PARENT_FOLDER_ID)
            open(queued, "wb").write(b"jpeg")
            if sidecar is not None:
                json.dump(sidecar, open(queued + ".meta.json", "w"))
            drive = FakeDrive(existing=["abc"])
            runs = []
            with mock.patch.object(uq, "get_drive_service", return_value=drive), \
                 mock.patch.object(uq, "get_or_create_date_folder", return_value="datefolder"), \
                 mock.patch.object(uq, "content_matches_target_week", return_value=True), \
                 mock.patch.object(uq, "description_matches_target_week", return_value=True), \
                 mock.patch.object(uq, "dispatch_event"), \
                 mock.patch("subprocess.run", side_effect=lambda cmd, **k: runs.append(cmd) or
                            types.SimpleNamespace(returncode=0)):
                uq.main()
            left = sorted(os.listdir(uq.QUEUE_DIR))
            return drive, runs, left
        finally:
            os.chdir(cwd)

    def test_replace_reconciles_the_stream_and_overwrites_in_drive(self):
        drive, runs, left = self.run_queue({"replace": True})
        stream = [c for c in runs if any("create_youtube_stream.py" in str(x) for x in c)]
        self.assertEqual(len(stream), 1)
        self.assertIn("--reconcile", stream[0])
        self.assertIn("10-4-26", stream[0])
        self.assertIn("update:abc", drive.calls)
        self.assertEqual(left, [])                       # image and sidecar both cleared

    def test_plain_upload_is_unchanged(self):
        drive, runs, left = self.run_queue(None)
        stream = [c for c in runs if any("create_youtube_stream.py" in str(x) for x in c)]
        self.assertEqual(len(stream), 1)
        self.assertNotIn("--reconcile", stream[0])
        self.assertEqual(left, [])


if __name__ == "__main__":
    unittest.main()
