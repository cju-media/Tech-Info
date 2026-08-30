#!/usr/bin/env python3
"""
Cloud-side watchdog for the server health monitor.

check_server_health.py runs on the Studio Mini runner, so if that whole machine
is down it cannot tell anyone. This script runs on a GitHub-hosted runner
(.github/workflows/server_health_watchdog.yml), reads the published Gist, and
emails once if the poller has stopped reporting.

Standard library only.

Env:
  HEARTBEAT_GIST_RAW_URL  raw URL of the Gist's 'server-status.json'
  HEALTH_ALERT_EMAIL      recipient (falls back to Cameron in team_emails.json)
  SMTP_EMAIL / SMTP_PASSWORD / SMTP_SERVER / SMTP_PORT
  WATCHDOG_STALE_MINUTES  minutes without a poll before alerting (default 40)
  DRY_RUN=1               print instead of sending / writing
"""

import json
import os
import smtplib
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from email.message import EmailMessage

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(SCRIPT_DIR, "health_watchdog_state.json")
DASHBOARD_CONFIG_FILE = os.path.join(SCRIPT_DIR, "server-health-config.json")
TEAM_EMAILS_FILE = os.path.join(
    SCRIPT_DIR, os.pardir, "Scheduling", "Team Data", "team_emails.json"
)


def env(name, default=None):
    """os.environ.get, but treat an empty string (unset Actions var) as missing."""
    v = os.environ.get(name)
    return v if v not in (None, "") else default


STALE_MINUTES = int(env("WATCHDOG_STALE_MINUTES", 40))
DRY_RUN = os.environ.get("DRY_RUN") == "1"


def now_utc():
    return datetime.now(timezone.utc)


def load_json(path, default):
    try:
        with open(path, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except Exception as e:
        print(f"WARN: could not read {path}: {e}")
        return default


def parse_iso(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def resolve_recipient():
    explicit = env("HEALTH_ALERT_EMAIL")
    if explicit:
        return explicit
    team = load_json(TEAM_EMAILS_FILE, {})
    return team.get("Cameron") or "cjohnston@fccla.org"


def send_email(subject, body):
    to_email = resolve_recipient()
    creds = {
        "email": env("SMTP_EMAIL"),
        "password": env("SMTP_PASSWORD"),
        "server": env("SMTP_SERVER", "smtp.gmail.com"),
        "port": int(env("SMTP_PORT", 587)),
    }
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = creds["email"] or "dry-run@example.com"
    msg["To"] = to_email
    msg.set_content(body)

    if DRY_RUN or not creds["email"] or not creds["password"]:
        print(f"[DRY RUN / no SMTP creds] Would email {to_email}\n"
              f"Subject: {subject}\n{body}\n")
        return
    try:
        with smtplib.SMTP(creds["server"], creds["port"]) as s:
            s.starttls()
            s.login(creds["email"], creds["password"])
            s.send_message(msg)
        print(f"Sent watchdog email to {to_email}: {subject}")
    except Exception as e:
        print(f"ERROR: failed to send watchdog email: {e}")


def gist_raw_url():
    url = env("HEARTBEAT_GIST_RAW_URL")
    if url:
        return url.strip()
    cfg = load_json(DASHBOARD_CONFIG_FILE, {})
    return (cfg.get("gistRawUrl") or "").strip()


def fetch_gist():
    url = gist_raw_url()
    if not url:
        print("ERROR: no Gist raw URL (set HEARTBEAT_GIST_RAW_URL or "
              "gistRawUrl in server-health-config.json).")
        sys.exit(1)
    # cache-bust GitHub's raw CDN
    sep = "&" if "?" in url else "?"
    req = urllib.request.Request(
        f"{url}{sep}t={int(now_utc().timestamp())}",
        headers={"User-Agent": "tech-info-health-watchdog"},
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main():
    state = load_json(STATE_FILE, {})
    if not isinstance(state, dict):
        state = {}
    was_offline = bool(state.get("offline", False))
    ever_initialized = bool(state.get("last_ok"))

    problem = None
    try:
        gist = fetch_gist()
        ran_at = parse_iso(gist.get("poller_ran_at"))
        if ran_at is None:
            if not ever_initialized:
                # First deploy: the poll workflow hasn't populated the Gist yet.
                # Don't cry wolf - wait for it to run at least once.
                print("Gist has no 'poller_ran_at' yet and no poll has ever "
                      "succeeded; treating as not-yet-initialized (no alert).")
                if not DRY_RUN:
                    with open(STATE_FILE, "w") as f:
                        json.dump(state, f, indent=2)
                        f.write("\n")
                return
            problem = "The status Gist has no valid 'poller_ran_at' timestamp."
        else:
            age_min = (now_utc() - ran_at).total_seconds() / 60
            print(f"Last poll was {age_min:.1f} min ago (threshold {STALE_MINUTES}).")
            if age_min > STALE_MINUTES:
                problem = (
                    f"The health poller last reported {age_min:.0f} minutes ago "
                    f"(> {STALE_MINUTES} min threshold)."
                )
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError, TimeoutError) as e:
        if not ever_initialized:
            print(f"Could not read the status Gist ({e}) and no poll has ever "
                  f"succeeded; treating as not-yet-initialized (no alert).")
            if not DRY_RUN:
                with open(STATE_FILE, "w") as f:
                    json.dump(state, f, indent=2)
                    f.write("\n")
            return
        problem = f"Could not read the status Gist: {e}"

    if problem:
        if not was_offline:
            send_email(
                "⚠️ Server monitoring is OFFLINE",
                f"{problem}\n\n"
                f"The Studio Mini runner that polls the media servers is not "
                f"reporting, so QR / OSC / Vertical Stream / Content Display "
                f"status is currently unknown. This usually means the Studio "
                f"Mini itself is off, asleep, or offline.\n\n"
                f"Checked at {now_utc().replace(microsecond=0).isoformat()} UTC.\n",
            )
        else:
            print("Still offline; alert already sent.")
        new_state = {"offline": True, "since": state.get("since")
                     or now_utc().replace(microsecond=0).isoformat()}
    else:
        if was_offline:
            send_email(
                "✅ Server monitoring is back online",
                "The health poller on the Studio Mini is reporting again.\n\n"
                f"Recovered at {now_utc().replace(microsecond=0).isoformat()} UTC.\n",
            )
        new_state = {"offline": False, "last_ok":
                     now_utc().replace(microsecond=0).isoformat()}

    if not DRY_RUN:
        with open(STATE_FILE, "w") as f:
            json.dump(new_state, f, indent=2)
            f.write("\n")
        print(f"Wrote {STATE_FILE}")


if __name__ == "__main__":
    main()
