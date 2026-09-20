"""One-off: trash the single-copy cards superseded by the 2026-09-19 change.

Each info card is now published several times under sort-key prefixes so it
interleaves with the event flyers. The old un-prefixed uploads are not
overwritten by that -- they're different filenames -- and they can't be
reaped by cleanup_events_folder.py either, since its fragment matcher still
recognises them as protected cards. Without this they'd sit in the rotation
forever as a fourth, un-interleaved copy.

Delete this file and its workflow once it has run.

Env: GDRIVE_OAUTH_JSON, DRY_RUN=1 to preview.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(HERE, os.pardir))
sys.path.insert(0, os.path.join(REPO_ROOT, 'Worship Scripts', 'worship workflows'))

EVENTS_FOLDER_ID = '17-0kiqBKa0k5ofW6gOPrVbHl7nqanuQz'
SUPERSEDED = (
    'Events-At-A-Glance.png',
    'LA-Weather-Forecast.png',
    'Upcoming-Service.png',
)


def main():
    dry_run = os.environ.get('DRY_RUN') == '1'
    from upload_queue_to_drive import get_drive_service

    service = get_drive_service()
    if not service:
        raise RuntimeError('No Drive credentials.')

    trashed = 0
    for name in SUPERSEDED:
        safe = name.replace('\\', '\\\\').replace("'", "\\'")
        found = service.files().list(
            q=f"name='{safe}' and '{EVENTS_FOLDER_ID}' in parents and trashed=false",
            spaces='drive', fields='files(id, name)',
            supportsAllDrives=True, includeItemsFromAllDrives=True,
        ).execute().get('files', [])
        if not found:
            print(f'  {name}: not present (already gone)')
            continue
        for f in found:
            if dry_run:
                print(f"  DRY RUN: would trash {f['name']} ({f['id']})")
                continue
            service.files().update(fileId=f['id'], body={'trashed': True},
                                   supportsAllDrives=True).execute()
            print(f"  Trashed {f['name']} ({f['id']})")
            trashed += 1
    print(f'Done. Trashed {trashed}.' if not dry_run else 'Done (dry run).')


if __name__ == '__main__':
    main()
