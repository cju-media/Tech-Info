"""One-off: trash the generated cards left behind by the 2026-09-18 rename.

The three generated cards used to share an "FCCLA-Upcoming-*" prefix, which
made Content Display play them back to back -- it runs the Events_Ads folder
in filename order. They were renamed to interleave with the event flyers
instead. Renaming the constant makes the generators upload under the *new*
name; it does nothing about the files already sitting in Drive under the old
one, which would keep playing forever and are no longer on
cleanup_events_folder.py's protected list (nor reliably reaped by it, since
two of them carry no date that ever passes).

Delete this file and its workflow once it has run.

Env: GDRIVE_OAUTH_JSON / GDRIVE_SERVICE_ACCOUNT_JSON, DRY_RUN=1 to preview.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(HERE, os.pardir))
sys.path.insert(0, os.path.join(REPO_ROOT, 'Worship Scripts', 'worship workflows'))

EVENTS_FOLDER_ID = '17-0kiqBKa0k5ofW6gOPrVbHl7nqanuQz'
LEGACY_NAMES = (
    'FCCLA-Upcoming-Events-At-A-Glance.png',
    'FCCLA-Upcoming-Forecast.png',
    'FCCLA-Upcoming-Service.png',
)


def main():
    dry_run = os.environ.get('DRY_RUN') == '1'
    from upload_queue_to_drive import get_drive_service

    service = get_drive_service()
    if not service:
        raise RuntimeError('No Drive credentials.')

    trashed = 0
    for name in LEGACY_NAMES:
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
            # Trashed, never hard-deleted: Drive keeps it ~30 days, so a
            # mistake here is recoverable.
            service.files().update(fileId=f['id'], body={'trashed': True},
                                   supportsAllDrives=True).execute()
            print(f"  Trashed {f['name']} ({f['id']})")
            trashed += 1

    print(f'Done. Trashed {trashed} legacy card(s).' if not dry_run else 'Done (dry run).')


if __name__ == '__main__':
    main()
