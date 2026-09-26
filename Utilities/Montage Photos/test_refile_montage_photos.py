"""Unit tests for refile_montage_photos.py.

Run from this directory:  python -m unittest test_refile_montage_photos -v

The things to guard: a photo goes where the list says (in the folder's
existing spelling), a re-run moves nothing, a folder is trashed only when
this run emptied it, and the state file follows the photos.
"""

import json
import os
import tempfile
import unittest
from unittest import mock

import collect_montage_photos as collector
import refile_montage_photos as r


class FakeDrive:
    """Just enough of the Drive v3 client: files have one parent each."""

    def __init__(self, parents):
        self.parents = dict(parents)          # file id -> parent id
        self.trashed = set()
        self.created = []

    def files(self):
        return self

    def get(self, fileId, **_):
        return self._run({'parents': [self.parents[fileId]],
                          'trashed': fileId in self.trashed})

    def update(self, fileId, addParents=None, removeParents=None, body=None, **_):
        if addParents:
            self.parents[fileId] = addParents
        if (body or {}).get('trashed'):
            self.trashed.add(fileId)
        return self._run({'id': fileId})

    def list(self, q, **_):
        folder = q.split("'")[1]
        kids = [f for f, p in self.parents.items() if p == folder and f not in self.trashed]
        return self._run({'files': [{'id': k} for k in kids]})

    @staticmethod
    def _run(value):
        return mock.Mock(execute=mock.Mock(return_value=value))


def tree():
    return {
        'Other': {'id': 'other', 'subfolders': {'Youth Bake Sale': 'o-bake', 'Staff Farewell': 'o-staff'}},
        'First Kids Firsts': {'id': 'kids', 'subfolders': {'Sensory Play': 'k-sensory'}},
        'Food at First': {'id': 'food', 'subfolders': {'Summer Clothing Drive': 'f-clothes'}},
    }


class Targets(unittest.TestCase):
    def test_split(self):
        self.assertEqual(r.split_target('Events/Pride'), ('Events', 'Pride'))
        self.assertEqual(r.split_target('Food at First'), ('Food at First', ''))

    def test_existing_spelling_wins(self):
        self.assertEqual(r.resolve_target('first kids first/sensory play', tree()),
                         ('First Kids Firsts', 'Sensory Play'))

    def test_new_names_kept(self):
        self.assertEqual(r.resolve_target('Events/Pride', tree()), ('Events', 'Pride'))


class Main(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.drive = FakeDrive({'bake1': 'o-bake', 'bake2': 'o-bake', 'staff': 'o-staff',
                                'cans': 'f-clothes', 'racks': 'f-clothes'})
        self.state = {'issues': {}, 'images': {
            'sha-bake1': {'keep': True, 'drive_id': 'bake1', 'category': 'Other', 'subcategory': 'Youth Bake Sale'},
            'sha-cans': {'keep': True, 'drive_id': 'cans', 'category': 'Food at First', 'subcategory': 'Summer Clothing Drive'},
        }}
        self.moves = [
            {'drive_id': 'bake1', 'to': 'First Kids Firsts/Youth Bake Sale'},
            {'drive_id': 'bake2', 'to': 'First Kids Firsts/Youth Bake Sale'},
            {'drive_id': 'cans', 'to': 'Food at First'},
        ]
        self.tree = tree()
        made = []

        def make_folder(drive, parent, name):
            made.append((parent, name))
            return f'new-{name}'
        self.made = made
        with open(os.path.join(self.dir, 'moves.json'), 'w') as f:
            json.dump({'moves': self.moves}, f)
        collector_state = os.path.join(self.dir, 'state.json')
        with open(collector_state, 'w') as f:
            json.dump(self.state, f)
        patches = [
            mock.patch.object(r, 'MOVES_PATH', os.path.join(self.dir, 'moves.json')),
            mock.patch.object(collector, 'STATE_PATH', collector_state),
            mock.patch.object(collector, 'get_drive', return_value=self.drive),
            mock.patch.object(collector, 'check_access', return_value='2027 Assets'),
            mock.patch.object(collector, 'load_tree', side_effect=lambda d, root: self.tree),
            mock.patch.object(collector, 'make_folder', side_effect=make_folder),
            mock.patch.dict(os.environ, {'DRY_RUN': '0'}),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def saved_state(self):
        with open(collector.STATE_PATH) as f:
            return json.load(f)

    def test_moves_files_and_trashes_only_emptied_folders(self):
        r.main()
        self.assertEqual(self.drive.parents['bake1'], 'new-Youth Bake Sale')
        self.assertEqual(self.drive.parents['bake2'], 'new-Youth Bake Sale')
        self.assertEqual(self.drive.parents['cans'], 'food')
        self.assertEqual(self.made, [('kids', 'Youth Bake Sale')])
        self.assertIn('o-bake', self.drive.trashed)       # emptied by this run
        self.assertNotIn('f-clothes', self.drive.trashed)  # still holds "racks"
        self.assertNotIn('o-staff', self.drive.trashed)    # never touched
        images = self.saved_state()['images']
        self.assertEqual((images['sha-bake1']['category'], images['sha-bake1']['subcategory']),
                         ('First Kids Firsts', 'Youth Bake Sale'))
        self.assertEqual(images['sha-cans']['subcategory'], '')

    def test_second_run_moves_nothing(self):
        r.main()
        before = dict(self.drive.parents)
        self.made.clear()
        r.main()
        self.assertEqual(self.drive.parents, before)
        self.assertEqual(self.made, [])

    def test_dry_run_changes_nothing(self):
        before = dict(self.drive.parents)
        with mock.patch.dict(os.environ, {'DRY_RUN': '1'}):
            r.main()
        self.assertEqual(self.drive.parents, before)
        self.assertEqual(self.drive.trashed, set())
        self.assertEqual(self.saved_state(), self.state)


if __name__ == '__main__':
    unittest.main()
