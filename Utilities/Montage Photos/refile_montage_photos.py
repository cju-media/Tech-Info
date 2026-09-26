"""
Moves montage photos to the folders listed in montage_refile.json.

Gemini's filing is right most of the time, and this is for the rest. The
Drive credentials only exist in GitHub, so a fix to the folder goes through
here: list the moves, run refile_montage_photos.yml.

    {"moves": [{"drive_id": "...", "name": "2026-06-18 ...jpg",
                "to": "First Kids Firsts/Youth Bake Sale"}]}

"to" is <category> or <category>/<subcategory>, and either is made if it
doesn't exist yet. "name" is only for whoever reads the file. A move is
skipped when the file is already there, so the list can be run twice, or
left in place and added to later.

A subfolder that a move empties is moved to the Drive trash (recoverable for
30 days), so an old wrong folder doesn't hang around looking like a category.
Only folders this run took files out of are considered. The state file is
updated to match, so it keeps saying where each photo is.

Env:
    GDRIVE_OAUTH_JSON / GDRIVE_SERVICE_ACCOUNT_JSON   required
    MONTAGE_FOLDER_ID   overrides DEFAULT_FOLDER_ID
    DRY_RUN          "1" prints the moves and changes nothing
"""

import json
import os
import sys

import collect_montage_photos as collector

HERE = os.path.dirname(os.path.abspath(__file__))
MOVES_PATH = os.path.join(HERE, 'montage_refile.json')


def load_moves():
    with open(MOVES_PATH, encoding='utf-8') as f:
        return json.load(f).get('moves') or []


def split_target(to):
    """"Events/Pride" -> ("Events", "Pride"); "Food at First" -> ("Food at First", "")."""
    category, _, sub = (to or '').partition('/')
    return category.strip(), sub.strip()


def resolve_target(to, tree):
    """The target as spelled in Drive, when a folder by that name already exists."""
    category, sub = split_target(to)
    category = collector.match_name(category, tree) or category
    if sub:
        sub = collector.match_name(sub, tree.get(category, {}).get('subfolders', {})) or sub
    return category, sub


def folder_is_empty(drive, folder_id):
    resp = drive.files().list(
        q=f"'{folder_id}' in parents and trashed=false", fields='files(id)',
        pageSize=1, supportsAllDrives=True, includeItemsFromAllDrives=True).execute()
    return not resp.get('files')


def main():
    dry_run = os.environ.get('DRY_RUN') == '1'
    root_id = os.environ.get('MONTAGE_FOLDER_ID') or collector.DEFAULT_FOLDER_ID
    drive = collector.get_drive()
    if not drive:
        sys.exit('No Drive credentials: set GDRIVE_OAUTH_JSON or GDRIVE_SERVICE_ACCOUNT_JSON.')
    print(f'Montage folder: {collector.check_access(drive, root_id)}')
    tree = collector.load_tree(drive, root_id)
    subfolder_ids = {fid for node in tree.values() for fid in node['subfolders'].values()}

    state = collector.load_state()
    by_drive_id = {r.get('drive_id'): r for r in state['images'].values() if r.get('drive_id')}

    emptied, moved, failed = set(), 0, 0
    for move in load_moves():
        category, sub = resolve_target(move.get('to'), tree)
        label = f"{move.get('name') or move['drive_id']} -> {category}/{sub}".rstrip('/')
        if not category:
            print(f'  No "to" for {label}; skipping.')
            failed += 1
            continue
        try:
            meta = drive.files().get(fileId=move['drive_id'], fields='parents, trashed',
                                     supportsAllDrives=True).execute()
            if meta.get('trashed'):
                print(f'  In the trash, leaving it: {label}')
                continue
            parents = meta.get('parents') or []
            target = tree.get(category, {'subfolders': {}})
            target_id = target['subfolders'].get(sub) if sub else target.get('id')
            if target_id and target_id in parents:
                print(f'  Already there: {label}')
                continue
            print(f'  Moving: {label}')
            if dry_run:
                moved += 1
                continue
            target_id = collector.ensure_folder(drive, tree, root_id, category, sub)
            drive.files().update(fileId=move['drive_id'], addParents=target_id,
                                 removeParents=','.join(parents), fields='id',
                                 supportsAllDrives=True).execute()
        except Exception as exc:                  # noqa: BLE001
            print(f'    Failed ({exc}).')
            failed += 1
            continue
        moved += 1
        emptied.update(p for p in parents if p in subfolder_ids)
        record = by_drive_id.get(move['drive_id'])
        if record:
            record.update({'category': category, 'subcategory': sub})

    for folder_id in sorted(emptied):
        if folder_is_empty(drive, folder_id):
            name = next(f'{c}/{s}' for c, node in tree.items()
                        for s, fid in node['subfolders'].items() if fid == folder_id)
            print(f'  Trashing the now-empty folder {name}/')
            drive.files().update(fileId=folder_id, body={'trashed': True},
                                 supportsAllDrives=True).execute()

    if not dry_run:
        collector.save_state(state)
    print(f'\n{moved} moved, {failed} failed.' + (' (dry run)' if dry_run else ''))
    if failed:
        sys.exit(1)


if __name__ == '__main__':
    main()
