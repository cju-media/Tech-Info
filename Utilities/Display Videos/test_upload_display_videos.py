"""Unit tests for upload_display_videos.py.

Run from this directory:  python -m unittest test_upload_display_videos -v

What matters: a video only reaches Drive whole and byte-for-byte what was
dropped on the dashboard, a retry never uploads it twice, and the upload
branch is deleted only once everything on it is in Drive.
"""

import hashlib
import json
import unittest
from unittest import mock

import upload_display_videos as u

VIDEO = bytes(range(256)) * 1000          # 256,000 bytes
PARTS = [VIDEO[:100000], VIDEO[100000:200000], VIDEO[200000:]]


def manifest(**overrides):
    m = {'name': 'building.mov', 'size': len(VIDEO),
         'sha256': hashlib.sha256(VIDEO).hexdigest(), 'parts': 3}
    m.update(overrides)
    return m


class FakeGitHub:
    """One upload branch holding one video in three parts."""

    def __init__(self, parts=PARTS, man=None):
        self.blobs = {f'sha-{i}': p for i, p in enumerate(parts, start=1)}
        self.blobs['sha-man'] = json.dumps(man or manifest()).encode()
        self.deleted = []

    def listing(self, path, branch):
        if path == u.QUEUE_PATH:
            return [{'type': 'dir', 'path': f'{u.QUEUE_PATH}/video-1'},
                    {'type': 'file', 'path': f'{u.QUEUE_PATH}/stray.txt'}]
        files = [{'name': 'manifest.json', 'sha': 'sha-man'}]
        files += [{'name': f'part-{n:04d}', 'sha': f'sha-{n}'}
                  for n in range(1, len(self.blobs))]
        return files

    def blob(self, sha):
        return self.blobs[sha]

    def delete_branch(self, branch):
        self.deleted.append(branch)


class Assemble(unittest.TestCase):
    FILES = {f'part-{n:04d}': n for n in (1, 2, 3)}

    def fetch(self, n):
        return PARTS[n - 1]

    def test_whole_video(self):
        self.assertEqual(u.assemble(manifest(), self.FILES, self.fetch), VIDEO)

    def test_missing_part(self):
        files = dict(self.FILES)
        del files['part-0002']
        with self.assertRaisesRegex(ValueError, 'part-0002 is missing'):
            u.assemble(manifest(), files, self.fetch)

    def test_wrong_size(self):
        with self.assertRaisesRegex(ValueError, 'expected'):
            u.assemble(manifest(size=5), self.FILES, self.fetch)

    def test_corrupt_bytes(self):
        with self.assertRaisesRegex(ValueError, "don't match"):
            u.assemble(manifest(sha256='0' * 64), self.FILES, self.fetch)

    def test_parts_are_joined_in_number_order(self):
        # part-0010 must come after part-0009, not after part-0001.
        parts = [bytes([i]) for i in range(12)]
        data = b''.join(parts)
        m = {'name': 'x.mov', 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest(),
             'parts': 12}
        files = {f'part-{n:04d}': n for n in range(1, 13)}
        self.assertEqual(u.assemble(m, files, lambda n: parts[n - 1]), data)


class ProcessBranch(unittest.TestCase):
    def setUp(self):
        self.uploads = []
        patches = [
            mock.patch.object(u, 'upload', side_effect=lambda d, f, name, data: self.uploads.append((name, data))),
            mock.patch.object(u, 'shuffle_new'),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def run_branch(self, gh, existing=None, dry_run=False):
        with mock.patch.object(u, 'already_in_folder', return_value=existing):
            u.process_branch(gh, object(), 'folder', 'video-upload/1', dry_run)

    def test_uploads_whole_video_then_deletes_branch(self):
        gh = FakeGitHub()
        self.run_branch(gh)
        self.assertEqual(self.uploads, [('building.mov', VIDEO)])
        self.assertEqual(gh.deleted, ['video-upload/1'])
        u.shuffle_new.assert_called_once()

    def test_retry_does_not_upload_twice(self):
        gh = FakeGitHub()
        self.run_branch(gh, existing={'name': '3F building.mov'})
        self.assertEqual(self.uploads, [])
        self.assertEqual(gh.deleted, ['video-upload/1'])

    def test_bad_video_keeps_branch(self):
        gh = FakeGitHub(man=manifest(sha256='0' * 64))
        with self.assertRaises(ValueError):
            self.run_branch(gh)
        self.assertEqual(self.uploads, [])
        self.assertEqual(gh.deleted, [])

    def test_dry_run_uploads_and_deletes_nothing(self):
        gh = FakeGitHub()
        self.run_branch(gh, dry_run=True)
        self.assertEqual(self.uploads, [])
        self.assertEqual(gh.deleted, [])


class Main(unittest.TestCase):
    def test_only_upload_branches_are_touched(self):
        gh = mock.Mock(upload_branches=mock.Mock(return_value=[]))
        with mock.patch.object(u, 'GitHub', return_value=gh), \
             mock.patch.dict(u.os.environ, {'GITHUB_TOKEN': 't', 'GITHUB_REPOSITORY': 'o/r',
                                            'BRANCH': 'main'}), \
             mock.patch.object(u, 'process_branch') as process:
            u.main()
        process.assert_not_called()


if __name__ == '__main__':
    unittest.main()
