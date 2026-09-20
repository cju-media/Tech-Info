"""Shared helpers for the generated info cards in the Events_Ads folder.

The cards are published several times over so they interleave with the event
flyers, and renumber_rotation.py renames every file in the folder into an
explicit "NN - " running order. That means a generator can no longer find its
own uploads by exact filename -- the rotation job renames them out from under
it. It finds them by a stable fragment of the name instead
("la-weather-forecast"), which survives renumbering, and refreshes every copy
it finds.

The same fragments drive cleanup_events_folder.py's protected list, so the
three ideas -- "this is an info card", "never auto-trash it" and "refresh all
of its copies" -- stay one idea.
"""

import os

EVENTS_FOLDER_ID = '17-0kiqBKa0k5ofW6gOPrVbHl7nqanuQz'

# Fragment -> the name a first-ever upload gets. The rotation job renames it
# almost immediately; these only have to be unique and recognisable.
CARDS = {
    'upcoming-service':       'Upcoming-Service.png',
    'la-weather-forecast':    'LA-Weather-Forecast.png',
    'events-at-a-glance':     'Events-At-A-Glance.png',
    'meetinghouse-newsletter': 'Meetinghouse-Newsletter.png',
}

# The order info cards take their turns in the rotation.
ROTATION = ('upcoming-service', 'la-weather-forecast', 'events-at-a-glance',
            'meetinghouse-newsletter')


def base_name(name):
    """A filename with any "NN - " rotation prefix stripped.

    Renumbering has to be idempotent: without this, every run would stack
    another prefix and produce "03 - 07 - 01 - Poster.png".
    """
    name = (name or '').strip()
    head, sep, tail = name.partition(' - ')
    if sep and head.isdigit():
        return tail.strip()
    return name


def card_fragment(name):
    """Which card a filename is a copy of, or None if it's an event flyer."""
    stem = os.path.splitext(base_name(name))[0].strip().lower()
    for fragment in CARDS:
        if fragment in stem:
            return fragment
    return None


def list_folder(service, folder_id=EVENTS_FOLDER_ID):
    """Every non-trashed image in the folder, as returned by the API."""
    files, page = [], None
    while True:
        resp = service.files().list(
            q=f"'{folder_id}' in parents and trashed=false and mimeType contains 'image/'",
            spaces='drive', fields='nextPageToken, files(id, name, md5Checksum)',
            pageToken=page, supportsAllDrives=True, includeItemsFromAllDrives=True,
        ).execute()
        files.extend(resp.get('files', []))
        page = resp.get('nextPageToken')
        if not page:
            return files


def publish_card(service, png_path, fragment, folder_id=EVENTS_FOLDER_ID,
                 dry_run=False):
    """Refresh every copy of one card in Drive with the rendered PNG.

    Updates in place rather than creating, so the rotation job's numbering
    survives and copies don't stack. A copy whose bytes already match is left
    alone. If no copy exists yet -- a first run, or someone deleted them all
    -- one is created under the default name for the rotation job to place.
    """
    import hashlib
    import io
    from googleapiclient.http import MediaIoBaseUpload

    with open(png_path, 'rb') as fh:
        blob = fh.read()
    digest = hashlib.md5(blob).hexdigest()

    copies = [f for f in list_folder(service, folder_id)
              if card_fragment(f['name']) == fragment]

    if not copies:
        name = CARDS[fragment]
        if dry_run:
            print(f'  DRY RUN: would create {name} (no copy exists yet)')
            return 1
        service.files().create(
            body={'name': name, 'parents': [folder_id]},
            media_body=MediaIoBaseUpload(io.BytesIO(blob), mimetype='image/png',
                                         resumable=True),
            supportsAllDrives=True,
        ).execute()
        print(f'  Created {name}')
        return 1

    touched = 0
    for f in copies:
        if f.get('md5Checksum') == digest:
            print(f"  {f['name']}: unchanged")
            continue
        if dry_run:
            print(f"  DRY RUN: would refresh {f['name']}")
            touched += 1
            continue
        service.files().update(
            fileId=f['id'],
            media_body=MediaIoBaseUpload(io.BytesIO(blob), mimetype='image/png',
                                         resumable=True),
            supportsAllDrives=True,
        ).execute()
        print(f"  Refreshed {f['name']}")
        touched += 1
    return touched
