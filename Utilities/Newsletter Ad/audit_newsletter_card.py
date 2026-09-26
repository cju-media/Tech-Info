"""
Daily audit of the Meetinghouse card that's actually on the screens.

The generator already drops items whose date has passed, so re-running that
filter here would prove nothing -- it would be the same code over the same
data, agreeing with itself. This checks the things the generator can't check
about itself:

  1. The date as the card *says* it. Gemini is told to leave the date field
     empty rather than guess, so an item can carry "Saturday, Sept 26 | 11am"
     in its visible text with no machine-readable date behind it. Nothing
     expires that item, and it sits on the screens saying Sept 26 in October.
     This parses the displayed text instead of trusting the field the filter
     uses.

  2. Whether the card up there is today's card at all. GitHub's scheduler
     drops most runs, so a whole day of them going missing leaves yesterday's
     card in Drive with nothing to correct it. The generator records the md5
     and the items of what it published; this checks Drive still matches that
     record, and that the record isn't older than STALE_AFTER_DAYS.

Anything it finds is printed and sent as one text. It deliberately doesn't
fix anything: the fix is a generator run, which already happens hourly, so a
card that's still wrong at audit time means those runs aren't working and
re-triggering them would just hide that.

Env:
    GDRIVE_OAUTH_JSON / GDRIVE_SERVICE_ACCOUNT_JSON   to read the folder
    PAT        the token that dispatches the notification
    DRY_RUN    "1" reports but sends nothing
"""

import datetime
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
UPLOADER_DIR = os.path.join(REPO_ROOT, 'Worship Scripts', 'worship workflows')
ROTATION_DIR = os.path.join(REPO_ROOT, 'Utilities', 'Rotation')

if HERE not in sys.path:
    sys.path.insert(0, HERE)

from generate_newsletter_ad import (  # noqa: E402
    CARD_FRAGMENT,
    load_state,
    local_today,
)

# A published record older than this means the generator has stopped
# landing runs. Two days rather than one: the card is rendered hourly and
# only re-published when its bytes change, so a quiet day is normal and a
# quiet two days is not.
STALE_AFTER_DAYS = 2

MONTHS = {
    'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6, 'jul': 7,
    'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12,
}
# "Sept 26", "September 26", "Sat, Oct 4", "Dec 12th". The ordinal suffix is
# optional and consumed: without it "December 12th" matched nothing, because
# the word boundary after the digits never came.
WHEN_DATE = re.compile(
    r'\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+'
    r'(\d{1,2})(?:st|nd|rd|th)?\b',
    re.IGNORECASE)


def plural(count, singular, plural_form=None):
    return f'{count} {singular if count == 1 else (plural_form or singular + "s")}'


def nearest_year(month, day, today):
    """The occurrence of month/day closest to today.

    The card never prints a year -- "Saturday, Sept 26" -- so one has to be
    inferred. Nearest rather than current handles the turn of the year in
    both directions: a card showing "Jan 3" on December 29th is next week,
    not eleven months ago.
    """
    options = []
    for year in (today.year - 1, today.year, today.year + 1):
        try:
            options.append(datetime.date(year, month, day))
        except ValueError:      # Feb 29 in a non-leap year
            continue
    if not options:
        # Feb 29 with no leap year within a year either way. Unresolvable,
        # so say nothing rather than guess -- a wrong guess here is a text
        # about an event that is perfectly fine.
        return None
    return min(options, key=lambda d: abs((d - today).days))


def parse_when_date(when, today):
    """The date an item's visible text claims, or None if it names no day.

    Recurring items ("Tuesdays | 7pm | Zoom") name no day on purpose and
    come back None, which is right -- they don't expire.
    """
    match = WHEN_DATE.search(when or '')
    if not match:
        return None
    month = MONTHS.get(match.group(1)[:3].lower())
    if not month:
        return None
    return nearest_year(month, int(match.group(2)), today)


def audit_items(items, today):
    """Items on the card whose own text puts them in the past."""
    stale, inconsistent = [], []
    for item in items:
        shown = parse_when_date(item.get('when'), today)
        if shown is None:
            continue
        if shown < today:
            stale.append((item, shown))
            continue
        # Not past yet, but the two dates disagree, so one of them will be
        # wrong when it matters. Worth printing; not worth a text.
        stated = item.get('date')
        if stated and stated != shown.isoformat():
            inconsistent.append((item, shown, stated))
    return stale, inconsistent


def drive_copies(fragment=CARD_FRAGMENT):
    """Every copy of the card in the Events_Ads folder, or None if Drive
    can't be reached (which is not the same as there being none)."""
    for path in (UPLOADER_DIR, ROTATION_DIR):
        if path not in sys.path:
            sys.path.insert(0, path)
    from upload_queue_to_drive import get_drive_service
    from drive_cards import card_fragment, list_folder

    drive = get_drive_service()
    if not drive:
        return None
    return [f for f in list_folder(drive) if card_fragment(f['name']) == fragment]


def audit_publication(published, copies, today):
    """Whether what's in Drive is the card the generator last published."""
    problems = []

    published_at = published.get('at')
    if published_at:
        try:
            age = (today - datetime.date.fromisoformat(published_at)).days
        except ValueError:
            age = None
        if age is not None and age >= STALE_AFTER_DAYS:
            problems.append(
                f'It was last published {plural(age, "day")} ago '
                f'({published_at}), so the hourly runs have stopped landing')

    if copies is None:
        print('  Drive unreachable; skipping the published-bytes check.')
        return problems
    if not copies:
        problems.append('No copy of the card is in the Events_Ads folder')
        return problems

    expected = published.get('md5')
    drifted = [f['name'] for f in copies
               if expected and f.get('md5Checksum') != expected]
    if drifted:
        problems.append(
            f"{plural(len(drifted), 'copy', 'copies')} in Drive "
            f"{'is' if len(drifted) == 1 else 'are'} not what the generator "
            f"last published: {', '.join(sorted(drifted))}")
    return problems


def describe(stale, problems):
    """One text's worth of what's wrong.

    Read on a phone, so each finding is its own sentence and the items are
    named -- "something is stale" would just mean opening the Actions log to
    find out what.
    """
    parts = []
    if stale:
        listed = '; '.join(f"{i['title']} ({i['when']})" for i, _ in stale)
        parts.append(
            f"{plural(len(stale), 'item')} on the Meetinghouse card "
            f"{'has' if len(stale) == 1 else 'have'} already passed: {listed}")
    parts.extend(p[0].upper() + p[1:] if p else p for p in problems)
    return '. '.join(parts) + '.' 


def notify(summary, dry_run):
    if dry_run:
        print(f'  DRY RUN: would text - {summary}')
        return
    if UPLOADER_DIR not in sys.path:
        sys.path.insert(0, UPLOADER_DIR)
    try:
        from upload_queue_to_drive import dispatch_event
        dispatch_event('newsletter_card_stale', {'summary': summary})
        print('  Notification dispatched.')
    except Exception as exc:                      # noqa: BLE001
        # The audit still fails loudly in the run log; a missed text
        # shouldn't turn a real finding into a silent one.
        print(f'  Could not dispatch the notification ({exc}).')


def main():
    dry_run = os.environ.get('DRY_RUN') == '1'
    today = local_today()
    print(f'Auditing the Meetinghouse card for {today}...')

    state = load_state()
    published = state.get('published')
    if not published:
        print('  Nothing published recorded yet; nothing to audit.')
        return 0

    items = published.get('items') or []
    if not items:
        print('  The screens are showing the static sign-up card, which '
              'cannot go stale.')
    else:
        print(f'  {len(items)} item(s) on the card:')
        for item in items:
            print(f"    - {item['title']} [{item.get('when') or 'no date shown'}]")

    stale, inconsistent = audit_items(items, today)
    for item, shown in inconsistent:
        print(f"  Note: {item['title']} shows {shown} but expires on "
              f"{item['date']}; one of those is wrong.")

    problems = audit_publication(published, drive_copies(), today)

    if not stale and not problems:
        print('  Everything on the card is still upcoming. No action needed.')
        return 0

    summary = describe(stale, problems)
    print(f'  PROBLEM: {summary}')
    notify(summary, dry_run)
    return 1


if __name__ == '__main__':
    sys.exit(main())
