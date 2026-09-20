"""Renumbers the Events_Ads folder into an explicit info/event running order.

Content Display plays the folder in filename order, and the filenames are
whatever the uploader's file happened to be called -- "5.png", "V1.png",
"image (1).jpeg". Choosing sort-key prefixes for the info cards to land
between those was guesswork that broke every time an event came or went, and
four flyers named image.jpeg/image.png couldn't be separated at all.

So this stops guessing and assigns the order outright:

    01 - Upcoming-Service.png
    02 - Candlelight_Poster.png
    03 - LA-Weather-Forecast.png
    04 - Min-Jin-Lee-Social-post-.png
    ...

Odd slots are info cards, taking turns in drive_cards.ROTATION; even slots
are event flyers. The number of card copies is matched to the number of
flyers, by copying an existing card or trashing a surplus one, so the
alternation stays exact as events come and go.

Renaming a file needs only edit access, which the automation has on every
file in the folder -- including flyers someone dropped in from a personal
account, which it can rename but could never trash.

Idempotent: a second run with nothing changed renames nothing.

Env:
    GDRIVE_OAUTH_JSON   required (the account that owns the folder)
    DRY_RUN             "1" prints the plan and changes nothing
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
sys.path.insert(0, os.path.join(REPO_ROOT, 'Worship Scripts', 'worship workflows'))
sys.path.insert(0, HERE)

from drive_cards import (  # noqa: E402
    CARDS, EVENTS_FOLDER_ID, ROTATION, base_name, card_fragment, list_folder,
)

# Never let the rotation grow past this many info cards, however many flyers
# turn up. Twelve is already a long lap.
MAX_CARDS = 12


def plan_rotation(files):
    """(sequence, to_copy, to_trash) for the folder's current contents.

    `sequence` is the target running order: a list of (slot, file) with the
    odd slots holding info cards and the even slots event flyers. Files in
    `to_copy` are cards that need another copy made; `to_trash` are surplus
    copies. Both are returned rather than acted on so the caller can print a
    plan under DRY_RUN.
    """
    # The id breaks ties: two flyers really are both named "image.jpeg", and
    # sorting on name alone let them swap places between runs, so every run
    # renamed them back and forth forever.
    flyers = sorted((f for f in files if not card_fragment(f['name'])),
                    key=lambda f: (base_name(f['name']).lower(), f['id']))
    cards = {}
    for f in files:
        fragment = card_fragment(f['name'])
        if fragment:
            cards.setdefault(fragment, []).append(f)
    for copies in cards.values():
        copies.sort(key=lambda f: f['name'])

    # One card per flyer gives strict alternation; with no flyers at all,
    # still show each card once rather than emptying the folder.
    target = max(len(ROTATION), min(len(flyers), MAX_CARDS)) if flyers else len(ROTATION)
    wanted = [ROTATION[i % len(ROTATION)] for i in range(target)]

    to_copy, to_trash, assigned = [], [], []
    used = {fragment: 0 for fragment in ROTATION}
    for fragment in wanted:
        have = cards.get(fragment) or []
        i = used[fragment]
        if i < len(have):
            assigned.append(have[i])
        elif have:
            # Nothing to assign yet -- a copy of this card is made below and
            # picked up on the next run, once it exists in Drive.
            to_copy.append((fragment, have[0]))
            assigned.append(None)
        else:
            # No copy at all; the generator creates one on its next run.
            assigned.append(None)
        used[fragment] += 1

    for fragment, have in cards.items():
        surplus = have[used.get(fragment, 0):]
        to_trash.extend(surplus)

    sequence, slot = [], 1
    for i in range(max(len(assigned), len(flyers))):
        if i < len(assigned) and assigned[i] is not None:
            sequence.append((slot, assigned[i]))
        slot += 1
        if i < len(flyers):
            sequence.append((slot, flyers[i]))
        slot += 1
    return sequence, to_copy, to_trash


def main():
    dry_run = os.environ.get('DRY_RUN') == '1'
    from upload_queue_to_drive import get_drive_service

    service = get_drive_service()
    if not service:
        raise RuntimeError('No Drive credentials: set GDRIVE_OAUTH_JSON.')

    files = list_folder(service)
    print(f'{len(files)} file(s) in the folder.')
    sequence, to_copy, to_trash = plan_rotation(files)

    for f in to_trash:
        print(f"  Surplus copy: {f['name']}")
        if not dry_run:
            service.files().update(fileId=f['id'], body={'trashed': True},
                                   supportsAllDrives=True).execute()
    for fragment, source in to_copy:
        print(f"  Need another {fragment}; copying {source['name']}")
        if not dry_run:
            service.files().copy(
                fileId=source['id'],
                body={'name': CARDS[fragment], 'parents': [EVENTS_FOLDER_ID]},
                supportsAllDrives=True,
            ).execute()

    if to_copy and not dry_run:
        # Re-plan against the copies that now exist, so the run converges
        # instead of leaving the new ones unplaced until tomorrow.
        sequence, _, _ = plan_rotation(list_folder(service))

    renamed = 0
    print('\nRunning order:')
    for slot, f in sequence:
        fragment = card_fragment(f['name'])
        # Cards are renamed to their canonical name rather than keeping
        # whatever earlier scheme left on them ("A-LA-Weather-Forecast");
        # flyers keep the name their uploader gave them.
        stem = CARDS[fragment] if fragment else base_name(f['name'])
        target = f"{slot:02d} - {stem}"
        kind = 'INFO ' if fragment else 'event'
        if target == f['name']:
            print(f'  {target}   [{kind}] (unchanged)')
            continue
        print(f"  {target}   [{kind}] (was {f['name']})")
        if not dry_run:
            service.files().update(fileId=f['id'], body={'name': target},
                                   supportsAllDrives=True).execute()
        renamed += 1

    if dry_run:
        print(f'\nDone (dry run). Would rename {renamed}, copy {len(to_copy)}, '
              f'trash {len(to_trash)}.')
    else:
        print(f'\nDone. Renamed {renamed}, copied {len(to_copy)}, '
              f'trashed {len(to_trash)}.')


if __name__ == '__main__':
    main()
