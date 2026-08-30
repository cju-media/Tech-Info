#!/usr/bin/env python3
"""
Poll the local media servers, publish their status to a GitHub Gist (read by the
dashboard at Utilities/dashboard/index.html), and email on up<->down transitions.

Runs on the self-hosted Studio Mini runner via .github/workflows/server_health.yml.
Standard library only.

Env:
  HEARTBEAT_GIST_TOKEN  classic PAT with the 'gist' scope (required to publish)
  HEARTBEAT_GIST_ID     id of the secret Gist holding 'server-status.json'
  HEALTH_ALERT_EMAIL    recipient for down/recovery alerts (falls back to
                        Cameron in ../Scheduling/Team Data/team_emails.json)
  SMTP_EMAIL / SMTP_PASSWORD / SMTP_SERVER / SMTP_PORT   outgoing mail (shared
                        with the other Tech-Info notification workflows)
  DRY_RUN=1             don't send mail or write the Gist, just print
"""

import json
import os
import socket
import smtplib
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from email.message import EmailMessage

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(SCRIPT_DIR, "servers.json")
STATE_FILE = os.path.join(SCRIPT_DIR, "server_status_state.json")
TEAM_EMAILS_FILE = os.path.join(
    SCRIPT_DIR, os.pardir, "Scheduling", "Team Data", "team_emails.json"
)
GIST_FILENAME = "server-status.json"
DRY_RUN = os.environ.get("DRY_RUN") == "1"


def env(name, default=None):
    """os.environ.get, but treat an empty string (unset Actions var) as missing."""
    v = os.environ.get(name)
    return v if v not in (None, "") else default


def now_utc():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.replace(microsecond=0).isoformat()


def load_json(path, default):
    try:
        with open(path, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except Exception as e:
        print(f"WARN: could not read {path}: {e}")
        return default


# --------------------------------------------------------------------------- #
# polling
# --------------------------------------------------------------------------- #
def poll_server(server, timeout, retries):
    """Return a status dict for one server config entry."""
    url = server["url"]
    expect_status = server.get("expect_status", 200)
    body_contains = server.get("body_contains")
    last_err = None

    for attempt in range(retries + 1):
        start = time.monotonic()
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "tech-info-health-check"}
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read(4096).decode("utf-8", "replace").strip()
                latency_ms = round((time.monotonic() - start) * 1000)
                code = resp.getcode()
                if code != expect_status:
                    last_err = f"HTTP {code} (expected {expect_status})"
                elif body_contains and body_contains not in body:
                    last_err = f"HTTP {code} but body missing '{body_contains}'"
                else:
                    return {
                        "ok": True,
                        "status_code": code,
                        "latency_ms": latency_ms,
                        "checked_at": iso(now_utc()),
                        "detail": f"HTTP {code} in {latency_ms} ms",
                    }
        except urllib.error.HTTPError as e:
            last_err = f"HTTP {e.code}"
        except (urllib.error.URLError, socket.timeout, TimeoutError) as e:
            reason = getattr(e, "reason", e)
            last_err = f"unreachable ({reason})"
        except Exception as e:  # noqa: BLE001 - report anything else verbatim
            last_err = f"error ({e})"

        if attempt < retries:
            time.sleep(1)

    return {
        "ok": False,
        "status_code": None,
        "latency_ms": None,
        "checked_at": iso(now_utc()),
        "detail": last_err or "unknown failure",
    }


# --------------------------------------------------------------------------- #
# gist publish
# --------------------------------------------------------------------------- #
def publish_to_gist(payload, gist_id):
    token = env("HEARTBEAT_GIST_TOKEN")
    if not token or not gist_id:
        print("WARN: HEARTBEAT_GIST_TOKEN secret or gist id not set - "
              "skipping Gist publish.")
        return

    content = json.dumps(payload, indent=2)
    if DRY_RUN:
        print(f"[DRY RUN] Would publish to gist {gist_id}:\n{content}")
        return

    api = f"https://api.github.com/gists/{gist_id}"
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "tech-info-health-check",
    }

    # Keep exactly one file named GIST_FILENAME. A brand-new gist starts life
    # with a file called "gistfile1.txt"; rename it on the first publish.
    files_field = {GIST_FILENAME: {"content": content}}
    try:
        with urllib.request.urlopen(
            urllib.request.Request(api, headers=headers), timeout=15
        ) as r:
            existing = list(json.loads(r.read().decode()).get("files", {}).keys())
        if GIST_FILENAME not in existing and existing:
            files_field = {existing[0]: {"filename": GIST_FILENAME, "content": content}}
    except Exception as e:
        print(f"WARN: could not inspect gist before publish ({e}); "
              f"writing {GIST_FILENAME} directly.")

    req = urllib.request.Request(
        api,
        data=json.dumps({"files": files_field}).encode(),
        method="PATCH",
        headers=headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            print(f"Published status to Gist (HTTP {resp.getcode()}).")
    except urllib.error.HTTPError as e:
        print(f"ERROR: Gist publish failed: HTTP {e.code} {e.read().decode()[:200]}")
    except Exception as e:
        print(f"ERROR: Gist publish failed: {e}")


# --------------------------------------------------------------------------- #
# email
# --------------------------------------------------------------------------- #
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
        print(f"Sent alert email to {to_email}: {subject}")
    except Exception as e:
        print(f"ERROR: failed to send alert email: {e}")


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main():
    config = load_json(CONFIG_FILE, None)
    if not config or "servers" not in config:
        print(f"ERROR: {CONFIG_FILE} missing or has no 'servers'.")
        sys.exit(1)

    timeout = config.get("poll_timeout_seconds", 5)
    retries = config.get("poll_retries", 1)
    down_after = config.get("down_after_consecutive_fails", 2)
    dashboard_url = config.get("dashboard_url", "")
    gist_id = env("HEARTBEAT_GIST_ID") or config.get("gist_id")

    state = load_json(STATE_FILE, {})
    if not isinstance(state, dict):
        state = {}

    results = {}
    new_state = {}
    transitions = []  # (server_cfg, kind) kind in {"down", "recovered"}

    for server in config["servers"]:
        key = server["key"]
        res = poll_server(server, timeout, retries)
        results[key] = res

        prev = state.get(key, {})
        prev_down = bool(prev.get("down", False))
        prev_fails = int(prev.get("consecutive_fail", 0))

        if res["ok"]:
            fails = 0
            is_down = False
        else:
            fails = prev_fails + 1
            is_down = prev_down or fails >= down_after

        entry = {
            "down": is_down,
            "consecutive_fail": fails,
            "last_detail": res["detail"],
            "last_checked": res["checked_at"],
        }
        if is_down and not prev_down:
            entry["since"] = res["checked_at"]
            if server.get("alert", True):
                transitions.append((server, "down"))
        elif prev_down and not is_down:
            if server.get("alert", True):
                transitions.append((server, "recovered"))
        elif is_down:
            entry["since"] = prev.get("since", res["checked_at"])

        new_state[key] = entry
        flag = "OK  " if res["ok"] else "DOWN"
        print(f"  [{flag}] {server['name']}: {res['detail']}"
              + (f"  (fail #{fails})" if fails else ""))

    payload = {
        "generated_at": iso(now_utc()),
        "poller_host": socket.gethostname(),
        "poller_ran_at": iso(now_utc()),
        "dashboard_url": dashboard_url,
        "servers": {
            s["key"]: {
                "name": s["name"],
                "alert": s.get("alert", True),
                **results[s["key"]],
                "down": new_state[s["key"]]["down"],
            }
            for s in config["servers"]
        },
    }
    publish_to_gist(payload, gist_id)

    for server, kind in transitions:
        name = server["name"]
        detail = results[server["key"]]["detail"]
        if kind == "down":
            subject = f"⚠️ {name} is DOWN"
            body = (
                f"{name} has failed {down_after} consecutive health checks.\n\n"
                f"Last check: {detail}\n"
                f"URL polled: {server['url']}\n"
                f"Time: {iso(now_utc())} UTC\n\n"
                f"Dashboard: {dashboard_url}\n"
            )
        else:
            subject = f"✅ {name} recovered"
            body = (
                f"{name} is responding again.\n\n"
                f"Check: {detail}\n"
                f"Time: {iso(now_utc())} UTC\n\n"
                f"Dashboard: {dashboard_url}\n"
            )
        send_email(subject, body)

    if not DRY_RUN:
        with open(STATE_FILE, "w") as f:
            json.dump(new_state, f, indent=2)
            f.write("\n")
        print(f"Wrote {STATE_FILE}")

    down_now = [k for k, v in new_state.items() if v["down"]]
    print(f"Done. {len(down_now)} server(s) down: {down_now or 'none'}")


if __name__ == "__main__":
    main()
