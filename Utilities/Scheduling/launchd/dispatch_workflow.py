"""
Fires a workflow_dispatch at GitHub Actions from the self-hosted Mac.

Why this exists: GitHub's `schedule:` trigger is best-effort, and on this
repo it is not close to reliable. Measured 2026-09-20 over 46 hours, two
workflows asking for an hourly run each got about one in four -- average
gap 3.7 hours, worst 6.1. That is survivable for a weather card and not
survivable for the service card, which has to flip to "Today's Service"
within minutes of 10:30 on a Sunday. On 2026-09-20 every scheduled run
between 09:52 and 11:07 was dropped and the card sat on "Upcoming Service"
for 37 minutes of the service.

launchd does not drop timers, and it works in local time -- so it also
disposes of the UTC/DST juggling the cron entries needed to hit 10:27
Pacific in both halves of the year.

This only *triggers* the workflow; the job still runs on GitHub's runners
where the API keys live. Nothing secret is needed here beyond a token that
can dispatch.

Token, in order of preference:
    $GITHUB_DISPATCH_TOKEN
    ~/.config/fccla/dispatch-token      (chmod 600)

Usage:
    python3 dispatch_workflow.py generate_service_ad.yml [ref]
"""

import json
import os
import sys
import urllib.error
import urllib.request

REPO = 'cju-media/Tech-Info'
TOKEN_FILE = os.path.expanduser('~/.config/fccla/dispatch-token')
DEFAULT_REF = 'main'


def read_token():
    token = (os.environ.get('GITHUB_DISPATCH_TOKEN') or '').strip()
    if token:
        return token
    try:
        with open(TOKEN_FILE) as fh:
            return fh.read().strip()
    except OSError:
        return ''


def dispatch(workflow, ref=DEFAULT_REF, token=None):
    """POST a workflow_dispatch. Returns True when GitHub accepts it.

    A 204 is the success case; the API returns no body.
    """
    token = token or read_token()
    if not token:
        raise RuntimeError(
            f'No dispatch token. Set GITHUB_DISPATCH_TOKEN or write one to '
            f'{TOKEN_FILE} (chmod 600).')

    url = f'https://api.github.com/repos/{REPO}/actions/workflows/{workflow}/dispatches'
    body = json.dumps({'ref': ref}).encode('utf-8')
    req = urllib.request.Request(url, data=body, method='POST', headers={
        'Accept': 'application/vnd.github+json',
        'Authorization': f'Bearer {token}',
        'X-GitHub-Api-Version': '2022-11-28',
        'Content-Type': 'application/json',
        'User-Agent': 'fccla-tech-info-dispatcher/1.0',
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status in (200, 201, 204)
    except urllib.error.HTTPError as e:
        detail = e.read().decode('utf-8', 'replace')[:300]
        raise RuntimeError(f'GitHub refused the dispatch ({e.code}): {detail}') from None


def main():
    if len(sys.argv) < 2:
        print(__doc__.strip().splitlines()[-1])
        return 2
    workflow = sys.argv[1]
    ref = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_REF
    import datetime
    stamp = datetime.datetime.now().astimezone().strftime('%Y-%m-%d %H:%M:%S %Z')
    try:
        dispatch(workflow, ref)
    except Exception as e:
        # Printed, not raised: launchd would otherwise just log a traceback,
        # and a missed dispatch should be readable in the log file.
        print(f'{stamp}  FAILED {workflow}: {e}')
        return 1
    print(f'{stamp}  dispatched {workflow} ({ref})')
    return 0


if __name__ == '__main__':
    sys.exit(main())
