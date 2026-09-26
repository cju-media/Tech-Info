"""Unit tests for collect_montage_photos.py.

Run from this directory:  python -m unittest test_collect_montage_photos -v

What can go wrong here is a photo in the wrong place or in two places: an
issue outside the montage year, a kids' photo filed under "Other" because
the program and its folder are spelled differently, "Easter" and "easter"
becoming two folders, a banner that runs every week uploaded every week, or
an issue marked done when Gemini never actually answered for it.
"""

import datetime
import json
import unittest
from unittest import mock

import collect_montage_photos as c
from collect_montage_photos import (
    OTHER_CATEGORY,
    clean_name,
    file_name,
    issues_to_process,
    mark_images,
    match_name,
    parse_decisions,
    process_issue,
    resolve_folder,
)

SINCE = datetime.date(2026, 5, 1)


def tree(**subs):
    names = ['Events', 'First Kids Firsts', 'Food at First', 'Gardens', 'Worship']
    return {n: {'id': f'id-{n}', 'subfolders': {s: f'id-{s}' for s in subs.get(n.split()[0].lower(), [])}}
            for n in names}


def decision(category='Worship', sub='', description='Choir singing', keep=True):
    return {'keep': keep, 'category': category, 'subcategory': sub,
            'description': description}


class IssuesToProcess(unittest.TestCase):
    ARCHIVE = [
        {'subject': 'The Meetinghouse This Week - September 17th, 2026', 'campaignUrl': 'u-sep17'},
        {'subject': 'The Meetinghouse This Week - May 7, 2026', 'campaignUrl': 'u-may7'},
        {'subject': 'The Meetinghouse This Week - April 30th, 2026', 'campaignUrl': 'u-apr30'},
        {'subject': 'A special message from the Senior Minister', 'campaignUrl': 'u-nodate'},
        {'subject': 'The Meetinghouse This Week - June 4th, 2026', 'campaignUrl': 'u-jun4'},
    ]

    def test_oldest_first_from_since(self):
        urls = [i['url'] for i in issues_to_process(self.ARCHIVE, SINCE, {})]
        self.assertEqual(urls, ['u-may7', 'u-jun4', 'u-sep17'])

    def test_done_issues_are_skipped(self):
        urls = [i['url'] for i in issues_to_process(self.ARCHIVE, SINCE, {'u-jun4': {}})]
        self.assertEqual(urls, ['u-may7', 'u-sep17'])

    def test_bad_rows_are_ignored(self):
        self.assertEqual(issues_to_process([None, {}, {'subject': 'x'}], SINCE, {}), [])


class MarkImages(unittest.TestCase):
    HTML = '''<table><tr><td>Last Sunday, First Kids First planted tomatoes.</td></tr>
      <tr><td><img data-image-content width="580" src="https://files.constantcontact.com/a/one.jpg?rdr=true"></td></tr>
      <tr><td><img width="5" height="1" src="https://imgssl.constantcontact.com/letters/images/S.gif"></td></tr>
      <tr><td><img width="40" src="https://files.constantcontact.com/a/icon.png"></td></tr>
      <tr><td><img width="200" src="https://files.constantcontact.com/a/two.png?a=1&amp;b=2"></td></tr>
      <tr><td><img width="580" src="https://files.constantcontact.com/a/one.jpg?rdr=true"></td></tr>
      </table>'''

    def test_photos_only_numbered_in_order_and_deduped(self):
        marked, urls = mark_images(self.HTML)
        self.assertEqual(urls, ['https://files.constantcontact.com/a/one.jpg?rdr=true',
                                'https://files.constantcontact.com/a/two.png?a=1&b=2'])
        self.assertEqual(marked.count('[[IMAGE 1]]'), 2)
        self.assertIn('[[IMAGE 2]]', marked)
        self.assertIn('S.gif', marked)            # spacer left alone
        self.assertIn('icon.png', marked)         # too small to be a photo

    def test_marker_sits_beside_its_caption(self):
        marked, _ = mark_images(self.HTML)
        text = c.issue_text(marked)
        self.assertIn('planted tomatoes.\n[[IMAGE 1]]', text)


class ParseDecisions(unittest.TestCase):
    def test_reads_fenced_json(self):
        raw = '```json\n{"images": [{"image": 1, "keep": true, "category": "Worship", ' \
              '"subcategory": "Easter", "description": "Sunrise service"}, ' \
              '{"image": "2", "keep": false}]}\n```'
        d = parse_decisions(raw, [1, 2])
        self.assertTrue(d[1]['keep'])
        self.assertEqual(d[1]['subcategory'], 'Easter')
        self.assertFalse(d[2]['keep'])
        self.assertEqual(d[2]['category'], '')

    def test_missing_image_raises(self):
        # An issue with an unanswered photo must be retried, not marked done.
        with self.assertRaises(ValueError):
            parse_decisions('{"images": [{"image": 1, "keep": false}]}', [1, 2])

    def test_unasked_numbers_ignored(self):
        d = parse_decisions('{"images": [{"image": 1, "keep": false}, {"image": 9, "keep": true}]}', [1])
        self.assertEqual(list(d), [1])

    def test_garbage_raises(self):
        with self.assertRaises(ValueError):
            parse_decisions('not json', [1])


class Names(unittest.TestCase):
    def test_clean_name_strips_path_characters(self):
        self.assertEqual(clean_name('Easter / Sunrise: "Service".'), 'Easter Sunrise Service')

    def test_clean_name_clips_on_a_word(self):
        name = clean_name('word ' * 30, 40)
        self.assertLessEqual(len(name), 40)
        self.assertFalse(name.endswith('…'))

    def test_program_name_matches_plural_folder(self):
        self.assertEqual(match_name('First Kids First', tree()), 'First Kids Firsts')

    def test_case_and_punctuation_match(self):
        self.assertEqual(match_name('christmas-eve', ['Christmas Eve']), 'Christmas Eve')
        self.assertEqual(match_name('FOOD AT FIRST', tree()), 'Food at First')

    def test_different_names_do_not_match(self):
        self.assertIsNone(match_name('Easter', ['Christmas Eve']))
        self.assertIsNone(match_name('', ['Easter']))

    def test_file_name_leads_with_issue_date(self):
        issue = {'date': datetime.date(2026, 4, 9)}
        self.assertEqual(file_name(issue, decision(description='Egg hunt'), 'image/png', 3),
                         '2026-04-09 Egg hunt.png')
        self.assertEqual(file_name(issue, decision(description=''), 'image/jpeg', 3),
                         '2026-04-09 Photo 3.jpg')


class ResolveFolder(unittest.TestCase):
    def test_existing_subfolder_reused_whatever_the_case(self):
        t = tree(worship=['Easter'])
        self.assertEqual(resolve_folder(decision('worship', 'easter'), t), ('Worship', 'Easter'))

    def test_new_subfolder_kept_as_given(self):
        self.assertEqual(resolve_folder(decision('Gardens', 'Saturday Workday'), tree()),
                         ('Gardens', 'Saturday Workday'))

    def test_no_subcategory_files_in_category(self):
        self.assertEqual(resolve_folder(decision('Events', ''), tree()), ('Events', ''))

    def test_unknown_category_goes_to_other(self):
        self.assertEqual(resolve_folder(decision('Choir', 'Easter'), tree()),
                         (OTHER_CATEGORY, 'Easter'))

    def test_other_reuses_existing_folder_spelling(self):
        t = tree()
        t['other'] = {'id': 'id-other', 'subfolders': {}}
        self.assertEqual(resolve_folder(decision('Nope'), t)[0], 'other')


class ProcessIssue(unittest.TestCase):
    """The whole of one issue, with the network and Drive stubbed out."""

    ISSUE = {'url': 'u-1', 'title': 'The Meetinghouse This Week - May 7, 2026',
             'date': datetime.date(2026, 5, 7)}
    HTML = ''.join(f'<p>caption {n}</p><img width="500" src="https://files.constantcontact.com/{n}.jpg">'
                   for n in (1, 2, 3))

    def setUp(self):
        self.tree = tree(worship=['Easter'])
        self.bytes = {f'https://files.constantcontact.com/{n}.jpg': f'photo-{n}'.encode()
                      for n in (1, 2, 3)}
        patches = [
            mock.patch.object(c, 'fetch_issue', return_value=self.HTML),
            mock.patch.object(c, 'download', side_effect=lambda u: (self.bytes[u], 'image/jpeg')),
            mock.patch.object(c, 'preview', side_effect=lambda d, m: (d, m)),
            mock.patch.object(c, 'find_uploaded', return_value=None),
            mock.patch.object(c, 'make_folder', side_effect=lambda d, p, n: f'new-{n}'),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.uploads = []
        up = mock.patch.object(c, 'upload', side_effect=self.fake_upload)
        up.start()
        self.addCleanup(up.stop)

    def fake_upload(self, drive, folder_id, name, data, mime, digest, description):
        self.uploads.append((folder_id, name))
        return f'file-{len(self.uploads)}'

    def run_issue(self, decisions, state=None, dry_run=False):
        state = state or {'issues': {}, 'images': {}}
        with mock.patch.object(c, 'ask_gemini', return_value=decisions) as ask:
            ok = process_issue(self.ISSUE, state, self.tree, object(), 'root', 'key', dry_run)
        return ok, state, ask

    def test_files_kept_photos_and_records_everything(self):
        ok, state, _ = self.run_issue({
            1: decision('Worship', 'easter', 'Sunrise service'),
            2: decision(keep=False),
            3: decision('First Kids First', 'Summer Camp', 'Kids painting')})
        self.assertTrue(ok)
        self.assertEqual(self.uploads, [
            ('id-Easter', '2026-05-07 Sunrise service.jpg'),
            ('new-Summer Camp', '2026-05-07 Kids painting.jpg')])
        self.assertEqual(self.tree['First Kids Firsts']['subfolders']['Summer Camp'], 'new-Summer Camp')
        self.assertEqual(len(state['images']), 3)
        self.assertEqual(state['issues']['u-1']['kept'], 2)

    def test_seen_photos_are_not_asked_about_again(self):
        seen = c.sha256(b'photo-1')
        state = {'issues': {}, 'images': {seen: {'keep': False}}}
        _, _, ask = self.run_issue({2: decision(keep=False), 3: decision(keep=False)}, state)
        self.assertEqual(sorted(ask.call_args.args[2]), [2, 3])

    def test_nothing_new_means_no_gemini_call(self):
        state = {'issues': {}, 'images': {c.sha256(v): {'keep': False} for v in self.bytes.values()}}
        ok, state, ask = self.run_issue({}, state)
        self.assertTrue(ok)
        ask.assert_not_called()
        self.assertIn('u-1', state['issues'])

    def test_failed_upload_leaves_issue_open_but_keeps_the_rest(self):
        calls = []

        def flaky(*args):
            calls.append(args)
            if len(calls) == 1:
                raise OSError('drive hiccup')
            return 'file-ok'
        with mock.patch.object(c, 'upload', side_effect=flaky):
            ok, state, _ = self.run_issue({n: decision() for n in (1, 2, 3)})
        self.assertFalse(ok)
        self.assertNotIn('u-1', state['issues'])
        self.assertEqual(len(state['images']), 2)     # retry only redoes photo 1

    def test_already_in_drive_is_not_uploaded_again(self):
        with mock.patch.object(c, 'find_uploaded', return_value='existing'):
            ok, state, _ = self.run_issue({n: decision() for n in (1, 2, 3)})
        self.assertTrue(ok)
        self.assertEqual(self.uploads, [])
        self.assertEqual({r['drive_id'] for r in state['images'].values()}, {'existing'})

    def test_dry_run_touches_no_drive(self):
        with mock.patch.object(c, 'find_uploaded', side_effect=AssertionError('drive')):
            ok, _, _ = self.run_issue({n: decision('Gardens', 'Workday') for n in (1, 2, 3)},
                                      dry_run=True)
        self.assertTrue(ok)
        self.assertEqual(self.uploads, [])
        self.assertIn('Workday', self.tree['Gardens']['subfolders'])


class Prompt(unittest.TestCase):
    def test_lists_existing_subcategories_and_other(self):
        issue = {'date': datetime.date(2026, 5, 7)}
        prompt = c.build_prompt(issue, 'text [[IMAGE 1]]', [1, 4], tree(worship=['Easter']))
        self.assertIn('"Easter"', prompt)
        self.assertIn(f'"{OTHER_CATEGORY}"', prompt)
        self.assertIn('IMAGE 1, IMAGE 4', prompt)
        self.assertIn('May 7, 2026', prompt)
        json.loads(prompt[prompt.rindex('{"images"'):].replace('{{', '{').replace('}}', '}'))


if __name__ == '__main__':
    unittest.main()
