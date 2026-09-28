"""
Shuffles the play order of the campus display videos.

The display plays the Drive folder in alphanumeric order, so the only way to
change the order is to change the names. This puts a short random code at
the front of each one -- "3F building 2.mov", "1K Choir.mov" -- and since the
codes sort before any ordinary name, the codes decide the order.

A code is one digit 1-9 and one capital letter. One digit keeps it sorting
the same way everywhere: "10A" would land between "1A" and "2A" in some
players and after "9Z" in others. That allows 234 videos.

Each file's own name is kept in Drive appProperties, so a reshuffle swaps the
code rather than stacking a second one in front of the first. If someone
renames a file by hand, the new name is taken as its own name from then on.

Two modes:
    all   every file gets a fresh code: a new order for the whole loop
    new   only files without a code get one, at random free codes, so they
          land at random points in the loop and nothing else moves

Either way similar clips are kept apart: "rose3", "rose7" and "nightRose2"
are one group (see group_key), and the order spreads each group evenly
around the loop so two of them rarely play back to back.

Env:
    GDRIVE_OAUTH_JSON / GDRIVE_SERVICE_ACCOUNT_JSON   required
    DISPLAY_VIDEOS_FOLDER_ID   overrides FOLDER_ID
    MODE             "all" (default) or "new"
    DRY_RUN          "1" prints the new names and renames nothing
"""

import os
import random
import re
import string
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
UPLOADER_DIR = os.path.join(REPO_ROOT, 'Worship Scripts', 'worship workflows')
if UPLOADER_DIR not in sys.path:
    sys.path.insert(0, UPLOADER_DIR)

# The folder the lobby screens loop through. The upload dashboard's
# "Display Videos" drop zone sends files here.
FOLDER_ID = '1-0rdd-NWDB9Tfv59HqlWYd0O3Y-g117V'

CODES = [f'{digit}{letter}' for digit in '123456789' for letter in string.ascii_uppercase]
# A name this run (or an earlier one) gave a file: the code and one space.
CODE_PREFIX = re.compile(r'^([1-9][A-Z]) (.+)$')

ORIGINAL_KEY = 'shuffleOriginal'   # the file's own name, without a code
GIVEN_KEY = 'shuffleName'          # the name this script last gave it

FOLDER_MIME = 'application/vnd.google-apps.folder'


def base_name(current, props):
    """The file's own name, without any code this script put on it.

    Only a name this script gave the file is stripped, so a video that
    happens to be called "3D Tour.mov" keeps its "3D".
    """
    props = props or {}
    if props.get(GIVEN_KEY) == current and props.get(ORIGINAL_KEY):
        return props[ORIGINAL_KEY]
    # Renamed by hand since the last shuffle, or never shuffled: the name
    # it has now is its own. If the person kept a code on the front, it's
    # one of ours and goes.
    m = CODE_PREFIX.match(current)
    if m and props.get(GIVEN_KEY):
        return m.group(2)
    return current


def current_code(current, props):
    """The code this file has from a previous shuffle, or None."""
    props = props or {}
    m = CODE_PREFIX.match(current)
    if m and props.get(GIVEN_KEY) == current:
        return m.group(1)
    return None


def group_key(name):
    """Which clips count as similar: the name without its number, and
    without "night", so "nightRose3.MP4", "rose7.MP4" and "Rose 2.mov" are
    one group -- the same subject, which shouldn't play back to back.
    "organKeys" stays apart from "organ": different words, different shot.
    """
    stem = os.path.splitext(name)[0].lower()
    stem = re.sub(r'[\d\s_()\-]+$', '', stem)
    stem = re.sub(r'^night[\s_\-]*', '', stem)
    stem = re.sub(r'[^a-z0-9]+', '', stem)
    return stem or name.lower()


def adjacent_repeats(keys):
    """How many neighbouring pairs share a group, counting the last clip
    against the first -- the display loops, so that pair plays too."""
    if len(keys) < 2:
        return 0
    return sum(keys[i] == keys[(i + 1) % len(keys)] for i in range(len(keys)))


# More tries find better orders but it's already good at a few hundred, and
# each is a sort of a few dozen items.
ARRANGE_TRIES = 400


def arrange(files, rng=random):
    """The files in a random play order that spreads each group out.

    Each group's clips get evenly spaced slots around the loop -- seven rose
    clips in 43 land roughly every sixth -- from a random start, with some
    jitter so it isn't a fixed pattern. Of ARRANGE_TRIES such orders, the
    one with the fewest same-group neighbours wins.
    """
    n = len(files)
    groups = {}
    for f in files:
        groups.setdefault(group_key(base_name(f['name'], f.get('appProperties'))), []).append(f)

    best, best_score = None, None
    for _ in range(ARRANGE_TRIES):
        slots = []
        for members in groups.values():
            members = members[:]
            rng.shuffle(members)
            step = n / len(members)
            start = rng.random() * step
            for i, f in enumerate(members):
                slots.append((start + i * step + rng.uniform(-0.35, 0.35) * step, rng.random(), f))
        order = [f for *_, f in sorted(slots, key=lambda s: (s[0] % n, s[1]))]
        score = adjacent_repeats(
            [group_key(base_name(f['name'], f.get('appProperties'))) for f in order])
        if best is None or score < best_score:
            best, best_score = order, score
            if score == 0:
                break
    return best


def pick_new_codes(files, rng=random):
    """{file id: code} for files without a code, each at a free code whose
    neighbours in the current loop aren't from its group, where there is
    one."""
    coded = sorted(((current_code(f['name'], f.get('appProperties')), f) for f in files
                    if current_code(f['name'], f.get('appProperties'))), key=lambda c: c[0])
    fresh = [f for f in files if not current_code(f['name'], f.get('appProperties'))]
    rng.shuffle(fresh)
    order = [(code, group_key(base_name(f['name'], f.get('appProperties')))) for code, f in coded]

    picked = {}
    for f in fresh:
        key = group_key(base_name(f['name'], f.get('appProperties')))
        taken = {code for code, _ in order}
        free = [c for c in CODES if c not in taken]
        good = []
        for c in free:
            before = [k for code, k in order if code < c]
            after = [k for code, k in order if code > c]
            # The loop wraps: before the first clip is the last, and after
            # the last is the first.
            prev = (before or after or [None])[-1]
            nxt = (after or before or [None])[0]
            if key not in (prev, nxt):
                good.append(c)
        code = rng.choice(good or free)
        picked[f['id']] = code
        order = sorted(order + [(code, key)])
    return picked


def plan(files, mode, rng=random):
    """[(file, new name)] for every file whose name should change.

    `files` are Drive file dicts with id, name and appProperties.
    """
    if len(files) > len(CODES):
        raise ValueError(f'{len(files)} videos but only {len(CODES)} codes.')

    if mode == 'all':
        # Codes sorted in the order arrange() chose, so the codes -- which are
        # what the display sorts by -- play the files in that order.
        codes = sorted(rng.sample(CODES, len(files)))
        assignments = list(zip(arrange(files, rng), codes))
    elif mode == 'new':
        picked = pick_new_codes(files, rng)
        assignments = [(f, picked[f['id']]) for f in files if f['id'] in picked]
    else:
        raise ValueError(f'Unknown mode {mode!r}; use "all" or "new".')

    changes = []
    for f, code in assignments:
        new_name = f"{code} {base_name(f['name'], f.get('appProperties'))}"
        if new_name != f['name']:
            changes.append((f, new_name))
    return changes


# --------------------------------------------------------------------------
# Drive
# --------------------------------------------------------------------------

def list_files(drive, folder_id):
    files, token = [], None
    while True:
        resp = drive.files().list(
            q=f"'{folder_id}' in parents and trashed=false and mimeType!='{FOLDER_MIME}'",
            fields='nextPageToken, files(id, name, appProperties)', pageToken=token,
            supportsAllDrives=True, includeItemsFromAllDrives=True).execute()
        files.extend(resp.get('files', []))
        token = resp.get('nextPageToken')
        if not token:
            return files


def rename(drive, f, new_name):
    base = base_name(f['name'], f.get('appProperties'))
    drive.files().update(
        fileId=f['id'], supportsAllDrives=True, fields='id',
        body={'name': new_name,
              'appProperties': {ORIGINAL_KEY: base, GIVEN_KEY: new_name}}).execute()


def main():
    dry_run = os.environ.get('DRY_RUN') == '1'
    mode = (os.environ.get('MODE') or 'all').strip().lower()
    folder_id = os.environ.get('DISPLAY_VIDEOS_FOLDER_ID') or FOLDER_ID

    from upload_queue_to_drive import get_drive_service
    drive = get_drive_service()
    if not drive:
        sys.exit('No Drive credentials: set GDRIVE_OAUTH_JSON or GDRIVE_SERVICE_ACCOUNT_JSON.')
    meta = drive.files().get(fileId=folder_id, supportsAllDrives=True,
                             fields='name, capabilities(canAddChildren)').execute()
    if not (meta.get('capabilities') or {}).get('canAddChildren'):
        who = drive.about().get(fields='user(emailAddress)').execute()
        sys.exit(f"{(who.get('user') or {}).get('emailAddress')} can't edit "
                 f"{meta.get('name')!r}. Share the folder with that account as an editor.")
    print(f"Folder: {meta.get('name')}")

    files = list_files(drive, folder_id)
    changes = plan(files, mode)
    print(f'{len(files)} file(s); mode {mode}; {len(changes)} to rename.')

    failed = 0
    for f, new_name in sorted(changes, key=lambda c: c[1]):
        print(f"  {f['name']}  ->  {new_name}")
        if dry_run:
            continue
        try:
            rename(drive, f, new_name)
        except Exception as exc:                  # noqa: BLE001
            print(f'    Failed ({exc}).')
            failed += 1

    if not dry_run:
        print('\nNew play order:')
        for f in sorted(list_files(drive, folder_id), key=lambda f: f['name']):
            print(f"  {f['name']}")
    if failed:
        sys.exit(f'{failed} rename(s) failed.')


if __name__ == '__main__':
    main()
