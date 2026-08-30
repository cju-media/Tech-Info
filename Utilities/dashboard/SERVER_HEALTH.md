# Server Health Monitoring

Monitors the four Node servers that run on the **Studio Mini** Mac and shows
their status on the dashboard, with an email when one goes down.

| Key               | Server                          | Polled URL (on Studio Mini)      |
| ----------------- | ------------------------------- | -------------------------------- |
| `qr`              | QR / URL Server                 | `http://localhost:3000/status`   |
| `vertical-stream` | Vertical Stream (ATEM status)   | `http://localhost:4200/status`   |
| `content-display` | Content Display Control Server  | `http://localhost:1031/health`   |
| `osc`             | YouTube OSC Server              | `http://localhost:3671/`         |

## How it works

1. **`server_health.yml`** (self-hosted, every 10 min) runs
   `check_server_health.py`, which polls each URL in `servers.json`, writes the
   result to a secret **Gist** (`server-status.json`), and emails on an
   up&rarr;down or down&rarr;up transition (after 2 consecutive failed polls, to
   avoid flapping). State is kept in `server_status_state.json`.
2. **`server_health_watchdog.yml`** (GitHub-hosted, every 30 min) runs
   `check_health_freshness.py`, which reads the Gist and emails once if the
   poller hasn't reported for &gt;40 min &mdash; this is how a fully-down Studio
   Mini gets caught, since workflow #1 can't run then. State: `health_watchdog_state.json`.
3. **`index.html`** (the dashboard hub) fetches the Gist (URL from
   `server-health-config.json`) on each status refresh and uses it as the real
   status for the QR / OSC / Content Display / Vertical Stream cards &mdash;
   replacing the browser "ping" that GitHub Pages' HTTPS blocks. Cards are linked
   to Gist entries by the `key` field in the `SERVERS` list. A banner appears if
   the poller itself has gone stale.

Gist writes don't create commits or workflow runs, so history stays clean. The
only automated commits are the occasional state-file updates on a transition.

## Setup

Already wired up for gist `7f410b4a9b15c57104f85ce4f6068d81` (owner `cju-media`):

- `servers.json` &rarr; `gist_id` holds the gist id.
- `server-health-config.json` &rarr; `gistRawUrl` holds the raw URL the dashboard
  and watchdog read.

The only repo secret required is **`HEARTBEAT_GIST_TOKEN`** &mdash; a **classic**
PAT with only the **`gist`** scope (fine-grained tokens cannot edit gists). It is
already set.

`SMTP_EMAIL` / `SMTP_PASSWORD` / `SMTP_SERVER` / `SMTP_PORT` already exist and are
reused. Optional: set an Actions **variable** `HEALTH_ALERT_EMAIL` to change the
alert recipient (otherwise it's Cameron from `team_emails.json`).

To move to a different gist: update `gist_id` in `servers.json`, `gistRawUrl` in
`server-health-config.json`, and rotate `HEARTBEAT_GIST_TOKEN`. The first poll
renames the gist's default `gistfile1.txt` to `server-status.json` automatically.

Make sure the four servers are running and the ports in `servers.json` match
(Content-Display defaults to `1031` in its code).

## Editing the server list

Edit `servers.json`. Each entry:

```json
{
  "key": "short-id",
  "name": "Human readable name",
  "url": "http://localhost:PORT/path",
  "expect_status": 200,
  "body_contains": "ok",      // optional substring the response body must contain
  "alert": true               // false = show on dashboard but never email
}
```

`osc` is `"alert": false` by default because it's usually only up during a
stream. Flip it to `true` once it runs continuously.

Tunables (top of `servers.json`): `poll_timeout_seconds`, `poll_retries`,
`down_after_consecutive_fails`. Dashboard staleness / banner thresholds live in
`server-health-config.json`. Cron cadence lives in the two workflow files.

## Testing

Run the poller by hand on Studio Mini (won't send mail or write the Gist):

```bash
cd ~/Documents/Tech-Info
DRY_RUN=1 python3 "Utilities/dashboard/check_server_health.py"
```

Full run (writes the Gist, can send mail):

```bash
cd ~/Documents/Tech-Info
HEARTBEAT_GIST_TOKEN=xxx SMTP_EMAIL=xxx SMTP_PASSWORD=xxx \
python3 "Utilities/dashboard/check_server_health.py"
```

(`gist_id` is read from `servers.json`; recipient defaults to Cameron.)

Or trigger **Server Health Poll** / **Server Health Watchdog** from the Actions
tab (Run workflow).
