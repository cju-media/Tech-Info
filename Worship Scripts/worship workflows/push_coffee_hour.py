#!/usr/bin/env python3
"""
Push the upcoming Coffee Hour room (extracted from the worship script by
update_worship_scripts.py) to the Content-Display remote control server, so
the on-screen announcement matches the printed script.

Runs on the self-hosted Studio-Mini runner (the same machine the Content
Display server runs on) via .github/workflows/push_coffee_hour.yml, after
every worship-script check and again on Sunday mornings as a backstop.

Uses the soonest script dated today or later. If it names a Coffee Hour room
that room is used; if it doesn't mention Coffee Hour, DEFAULT_ROOM is used.
A normal run only pushes when that text differs from the last one it pushed,
so a manual edit made in the control app during the week isn't clobbered
every hour. --force (the Sunday backstop) always pushes.

Env:
  CONTENT_DISPLAY_URL   base URL of the server (default http://localhost:1031)
  COFFEE_HOUR_STATE     file recording the last pushed text
                        (default ~/.cache/content-display-coffee-hour.txt)
"""

import json
import os
import sys
import urllib.request
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
WORSHIP_SCRIPTS_FILE = os.path.join(SCRIPT_DIR, os.pardir, 'worship_scripts.json')
DEFAULT_ROOM = 'Mayflower Courtyard'
SERVER_URL = os.environ.get('CONTENT_DISPLAY_URL', 'http://localhost:1031')
STATE_FILE = os.environ.get(
    'COFFEE_HOUR_STATE',
    os.path.expanduser('~/.cache/content-display-coffee-hour.txt'),
)


def upcoming_room(worship_scripts, today):
    """Returns (date_str, room) for the soonest script on/after today: its
    named Coffee Hour room, or DEFAULT_ROOM when it doesn't mention one.
    Returns (None, None) when there is no upcoming script."""
    for date_str in sorted(worship_scripts):
        if date_str < today:
            continue
        room = (worship_scripts[date_str] or {}).get('coffeeHourRoom')
        return date_str, room or DEFAULT_ROOM
    return None, None


def read_last_pushed():
    try:
        with open(STATE_FILE, 'r') as f:
            return f.read()
    except OSError:
        return None


def write_last_pushed(text):
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        with open(STATE_FILE, 'w') as f:
            f.write(text)
    except OSError as e:
        print(f"WARNING: could not record last pushed text in {STATE_FILE}: {e}")


def main():
    force = '--force' in sys.argv[1:]
    today = datetime.now().strftime('%Y-%m-%d')

    try:
        with open(WORSHIP_SCRIPTS_FILE, 'r') as f:
            worship_scripts = json.load(f)
    except Exception as e:
        print(f"ERROR: could not read {WORSHIP_SCRIPTS_FILE}: {e}")
        sys.exit(1)

    date_str, room = upcoming_room(worship_scripts, today)
    if not room:
        print(f"No worship script on/after {today}; leaving display as-is.")
        return

    text = f"Join us for Coffee Hour in {room}!"
    if not force and read_last_pushed() == text:
        print(f"Coffee Hour text for {date_str} unchanged since last push ({text!r}); skipping.")
        return

    print(f"Pushing Coffee Hour text for {date_str}: {text!r}")

    body = json.dumps({'text': text}).encode('utf-8')
    req = urllib.request.Request(
        f"{SERVER_URL}/api/coffee-hour",
        data=body,
        headers={'Content-Type': 'application/json'},
        method='POST',
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            print(f"Content-Display server responded HTTP {resp.getcode()}")
    except Exception as e:
        print(f"ERROR: failed to push to Content-Display server: {e}")
        sys.exit(1)

    write_last_pushed(text)


if __name__ == '__main__':
    main()
