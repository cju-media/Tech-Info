"""Email an alert if Monday's Video Migration run did not happen or did not succeed.

The migration job and the iMessage notifier both run on the self-hosted Mac, so
if that Mac is down nothing can text. This runs on a GitHub-hosted runner
(video_migration_watchdog.yml) and emails instead, reusing the SMTP settings and
recipient of the server health check.

Env: GITHUB_TOKEN, GITHUB_REPOSITORY (set by Actions); SMTP_* as in
check_server_health.py; DRY_RUN=1 prints instead of sending.
"""
import importlib.util
import json
import os
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

WORKFLOW_FILE = 'video_migration.yml'
# GitHub starts the 17:00 UTC Monday cron 5-6 hours late in practice (22:21-23:24 UTC
# the last three weeks), so look back far enough to see it from Monday evening.
LOOKBACK = timedelta(hours=14)
QUEUED_TOO_LONG = timedelta(minutes=30)
RUNNING_TOO_LONG = timedelta(minutes=90)


def parse_time(text):
    return datetime.fromisoformat(text.replace('Z', '+00:00'))


def evaluate(runs, now):
    """The alert text if the newest recent run shows a problem, else None.

    runs are workflow-run dicts from the GitHub API. Only runs from the last
    LOOKBACK count, so an old success can't hide today's missing run.
    """
    recent = [r for r in runs if now - parse_time(r['created_at']) <= LOOKBACK]
    if not recent:
        return ("No Video Migration run started in the last "
                f"{int(LOOKBACK.total_seconds() // 3600)} hours. The self-hosted Mac runner is "
                "probably offline, or GitHub dropped the scheduled run.")

    run = max(recent, key=lambda r: r['created_at'])
    age = now - parse_time(run['created_at'])
    status, conclusion = run['status'], run.get('conclusion')

    if status in ('queued', 'waiting', 'pending') and age > QUEUED_TOO_LONG:
        return (f"The Video Migration run has been waiting {int(age.total_seconds() // 60)} minutes "
                "for a runner. The self-hosted Mac runner is probably offline or busy.")
    if status == 'in_progress' and age > RUNNING_TOO_LONG:
        return f"The Video Migration run has been going {int(age.total_seconds() // 60)} minutes and may be hung."
    if status == 'completed' and conclusion != 'success':
        return f"The Video Migration run finished with result '{conclusion}'."
    return None


def fetch_runs(repo, token):
    url = (f"https://api.github.com/repos/{repo}/actions/workflows/{WORKFLOW_FILE}/runs"
           "?branch=main&per_page=20")
    request = urllib.request.Request(url, headers={
        'Authorization': f'Bearer {token}', 'Accept': 'application/vnd.github+json'})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)['workflow_runs']


def load_send_email():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, 'dashboard',
                        'check_server_health.py')
    spec = importlib.util.spec_from_file_location('check_server_health', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.send_email


def main():
    repo = os.environ['GITHUB_REPOSITORY']
    problem = evaluate(fetch_runs(repo, os.environ['GITHUB_TOKEN']), datetime.now(timezone.utc))
    if not problem:
        print("Video Migration ran and succeeded.")
        return

    print(problem)
    load_send_email()(
        "Video Migration needs attention",
        f"{problem}\n\nRuns: https://github.com/{repo}/actions/workflows/{WORKFLOW_FILE}\n\n"
        "To re-run: open that page, choose Run workflow, leave the inputs at their defaults "
        "(privacy public, no service date, test run off). Check YouTube Studio first for a "
        "partial upload so a second copy isn't published.\n")
    sys.exit(1)


if __name__ == '__main__':
    main()
