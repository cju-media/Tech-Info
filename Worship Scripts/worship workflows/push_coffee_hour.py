#!/usr/bin/env python3
"""
Push today's Coffee Hour room (extracted from the worship script by
update_worship_scripts.py) to the Content-Display remote control server, so
the on-screen announcement matches the printed script.

Runs on the self-hosted Studio-Mini runner (the same machine the Content
Display server runs on) via .github/workflows/push_coffee_hour.yml,
scheduled for Sunday mornings only.

Env:
  CONTENT_DISPLAY_URL   base URL of the server (default http://localhost:1031)
"""

import json
import os
import sys
import urllib.request
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
WORSHIP_SCRIPTS_FILE = os.path.join(SCRIPT_DIR, os.pardir, 'worship_scripts.json')
SERVER_URL = os.environ.get('CONTENT_DISPLAY_URL', 'http://localhost:1031')


def main():
    today = datetime.now().strftime('%Y-%m-%d')

    try:
        with open(WORSHIP_SCRIPTS_FILE, 'r') as f:
            worship_scripts = json.load(f)
    except Exception as e:
        print(f"ERROR: could not read {WORSHIP_SCRIPTS_FILE}: {e}")
        sys.exit(1)

    entry = worship_scripts.get(today)
    if not entry:
        print(f"No worship script entry for {today}; nothing to push.")
        return

    room = entry.get('coffeeHourRoom')
    if not room:
        print(f"No Coffee Hour room recorded for {today}; leaving display as-is.")
        return

    text = f"Join us for Coffee Hour in {room}!"
    print(f"Pushing Coffee Hour text for {today}: {text!r}")

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


if __name__ == '__main__':
    main()
