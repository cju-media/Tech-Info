"""
Reminds Cameron each July to point the montage collector at next year's folder.

The montage is shown at the annual meeting around the end of the fiscal year
in July. After that, collect_montage_photos.py would go on filing photos
into the year that just finished until DEFAULT_FOLDER_ID and DEFAULT_SINCE
are changed, and nothing else would say so.

Sends an email and an iMessage (via a repository_dispatch that
imessage_notifications.yml turns into a text on the Mac runner), once per
year. The workflow fires every day in July because GitHub drops most
scheduled runs on this repo; montage_reminder_state.json is what stops the
second through thirty-first. It also says nothing if the collector has
already been moved on -- DEFAULT_SINCE in this calendar year means someone
has done it.

Env:
    PAT              for the repository_dispatch (the text)
    SMTP_EMAIL / SMTP_PASSWORD / SMTP_SERVER / SMTP_PORT   for the email
    DRY_RUN          "1" prints what would be sent and records nothing
    FORCE            "1" sends even outside July or if already sent this year
"""

import datetime
import json
import os
import smtplib
import sys
import time
from email.message import EmailMessage

import requests

import collect_montage_photos as collector

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_PATH = os.path.join(HERE, 'montage_reminder_state.json')

REMINDER_MONTH = 7
REPO = 'cju-media/tech-info'
EVENT_TYPE = 'montage_folder_reminder'
# The same pair every other automation alert in this repo goes to.
TO_EMAIL = 'cameron@cju.media'
CC_EMAIL = 'cjohnston@fccla.org'
SCRIPT_PATH = 'Utilities/Montage Photos/collect_montage_photos.py'


def local_today():
    from zoneinfo import ZoneInfo
    return datetime.datetime.now(ZoneInfo('America/Los_Angeles')).date()


def load_state():
    try:
        with open(STATE_PATH, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(state):
    with open(STATE_PATH, 'w', encoding='utf-8') as f:
        json.dump(state, f, indent=2, sort_keys=True)
        f.write('\n')


def reminder_due(today, since, state, force=False):
    """Why not to send, or None to send.

    The collector counts as moved on once DEFAULT_SINCE falls in this
    calendar year: the folder for the montage just shown started the year
    before.
    """
    if force:
        return None
    if today.month != REMINDER_MONTH:
        return f'not {datetime.date(2000, REMINDER_MONTH, 1):%B}'
    if state.get('last_reminded_year') == today.year:
        return f'already reminded in {today.year}'
    if since.year >= today.year:
        return f'already moved on (DEFAULT_SINCE is {since})'
    return None


def folder_url(folder_id):
    return f'https://drive.google.com/drive/folders/{folder_id}'


def text_message(folder_id, since):
    return ("📸 Time to set up next year's montage folder. Photos from the newsletter "
            f"are still going into this year's folder ({folder_url(folder_id)}). "
            f"Make the new folder, then update DEFAULT_FOLDER_ID and DEFAULT_SINCE "
            f"(now {since}) in collect_montage_photos.py.")


def email_body(folder_id, since, today):
    return f"""Hi Cameron,

The annual meeting has come around, so the montage photo collector needs pointing at next year's folder. Until it is, photos from The Meetinghouse keep going into this year's folder:

  {folder_url(folder_id)}
  DEFAULT_FOLDER_ID = '{folder_id}'
  DEFAULT_SINCE = '{since}'

To move it on:

  1. In Drive, make the new folder (e.g. "{today.year + 1} Assets") with the category folders inside it: Events, First Kids Firsts, Food at First, Gardens, Worship. The collector files into whatever top-level folders are there, so add or rename them as you like.
  2. Make sure the Drive account in GDRIVE_OAUTH_JSON can edit the new folder.
  3. Copy the folder's ID from its URL (the part after /folders/).
  4. In {SCRIPT_PATH}, set DEFAULT_FOLDER_ID to that ID and DEFAULT_SINCE to the date of the first newsletter the new montage should cover.
  5. Merge it. The next run backfills every issue from DEFAULT_SINCE on.

You'll get this once a year, and not at all if it's already been done.

Best,
Cam-Bot"""


def send_email(subject, body, dry_run):
    email, password = os.environ.get('SMTP_EMAIL'), os.environ.get('SMTP_PASSWORD')
    if dry_run or not email or not password:
        print(f'DRY RUN: would email {TO_EMAIL} (CC: {CC_EMAIL})\nSubject: {subject}\n\n{body}\n')
        return dry_run
    msg = EmailMessage()
    msg['Subject'], msg['From'], msg['To'], msg['Cc'] = subject, email, TO_EMAIL, CC_EMAIL
    msg.set_content(body)
    try:
        with smtplib.SMTP(os.environ.get('SMTP_SERVER') or 'smtp.gmail.com',
                          int(os.environ.get('SMTP_PORT') or 587)) as server:
            server.starttls()
            server.login(email, password)
            server.send_message(msg, to_addrs=[TO_EMAIL, CC_EMAIL])
    except Exception as exc:                      # noqa: BLE001
        print(f'Email failed: {exc}')
        return False
    print(f'Emailed {TO_EMAIL} (CC: {CC_EMAIL}).')
    return True


def send_text(message, dry_run, attempts=4):
    """The iMessage, by way of imessage_notifications.yml on the Mac runner."""
    pat = os.environ.get('PAT')
    if dry_run or not pat:
        print(f'DRY RUN: would dispatch {EVENT_TYPE} for the text:\n{message}\n')
        return dry_run
    for attempt in range(1, attempts + 1):
        try:
            resp = requests.post(
                f'https://api.github.com/repos/{REPO}/dispatches', timeout=15,
                headers={'Accept': 'application/vnd.github.v3+json',
                         'Authorization': f'token {pat}'},
                json={'event_type': EVENT_TYPE, 'client_payload': {'message': message}})
            if resp.status_code == 204:
                print(f'Dispatched {EVENT_TYPE}.')
                return True
            print(f'Dispatch failed: {resp.status_code} {resp.text}')
        except requests.RequestException as exc:
            print(f'Dispatch failed: {exc}')
        if attempt < attempts:
            time.sleep(3 * 2 ** (attempt - 1))
    return False


def main():
    dry_run = os.environ.get('DRY_RUN') == '1'
    force = os.environ.get('FORCE') == '1'
    today = local_today()
    folder_id = collector.DEFAULT_FOLDER_ID
    since = datetime.date.fromisoformat(collector.DEFAULT_SINCE)

    state = load_state()
    skip = reminder_due(today, since, state, force)
    if skip:
        print(f'No reminder: {skip}.')
        return

    emailed = send_email("Montage photos: set up next year's folder",
                         email_body(folder_id, since, today), dry_run)
    texted = send_text(text_message(folder_id, since), dry_run)
    if dry_run:
        return
    if not (emailed or texted):
        # Leave the year unrecorded so tomorrow's run tries again.
        sys.exit('Neither the email nor the text went out.')
    state['last_reminded_year'] = today.year
    save_state(state)


if __name__ == '__main__':
    main()
