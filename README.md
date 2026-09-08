# Tech-Info

Automation and dashboards for the production / A-V team at First Congregational
Church of Los Angeles (540 S Commonwealth Ave). The repo is one part public
web app (served from GitHub Pages), one part a fleet of GitHub Actions
workflows that run the weekly Sunday-content pipeline, crew scheduling and
notifications, and one part scripts that live on physical hardware at the
church (a self-hosted macOS runner and a fleet of Raspberry Pi displays).

Repo: `cju-media/Tech-Info` · Pages site: <https://cju-media.github.io/Tech-Info/>

---

## Table of contents

- [The web app](#the-web-app)
- [Sunday content pipeline](#sunday-content-pipeline)
- [YouTube livestream & description automation](#youtube-livestream--description-automation)
- [YouTube OSC timestamp server](#youtube-osc-timestamp-server)
- [Crew scheduling & notifications](#crew-scheduling--notifications)
- [RF coordination](#rf-coordination)
- [Server health monitoring](#server-health-monitoring)
- [RTMP display watchdog (Raspberry Pi fleet)](#rtmp-display-watchdog-raspberry-pi-fleet)
- [Google Drive housekeeping](#google-drive-housekeeping)
- [Video migration](#video-migration)
- [GitHub Actions workflows](#github-actions-workflows)
- [Repository layout](#repository-layout)
- [Infrastructure, secrets & external services](#infrastructure-secrets--external-services)
- [Tests](#tests)

---

## The web app

Static HTML/JS pages deployed to GitHub Pages by `pages_deployment.yml`. They
pull live data at runtime from public Google Sheets (`gviz` JSONP), a secret
GitHub Gist, and the GitHub API — there is no backend.

| Page | Purpose |
| ---- | ------- |
| `index.html` | **Tech Info** — the crew-facing home page. Renders the master Tech Schedule Google Sheet as an upcoming-events grid / card view, with per-event detail modals (call time, speaker info, RF warnings, custom notes). Has an inline "edit mode" that commits per-event overrides (RF warnings, speaker info, custom notes) back to the repo via the GitHub contents API using a PAT the user supplies, plus deep links into the underlying Google Sheet rows. |
| `Utilities/dashboard/index.html` | **Tech Info Dashboard** — hub page: this week's Sunday info, RF coordination conflicts, the Weekly Content Pipeline freshness panel, a "Draft Upcoming Events" button, and a link to the workflows dashboard. |
| `Utilities/dashboard/workflows.html` | **Automated Workflows Dashboard** — live view of every GitHub Actions workflow (last run, next scheduled run, trigger type) drawn as a Mermaid dependency flowchart. Optionally authenticated with a GitHub PAT stored in `localStorage`. |
| `Utilities/uploads/index.html` | **FCCLA Upload Dashboard** — drag-and-drop upload zones for weekly assets: Order of Worship PDF, worship-service and sermon-series thumbnails, event ad flyers, sermon recordings, a `timings.txt` fallback, and livestream settings. Files are committed to an uploads queue (or pushed to the `cju-media/OW` repo) and picked up by workflows. |
| `event-draft.html` | Renders `event_draft.json` — a copy-paste draft of upcoming events for the Tech Availability sheet. |
| `event-notes.html` | Renders calendar notes pulled from the published Outlook calendar. |
| `Worship Scripts/upcoming_script.html` | Renders the parsed run-of-show / worship script for the upcoming service. |
| `Youtube Processing/public/index.html` | Web UI for the OSC timestamp server (served by `osc_server.js`, not Pages). |
| `Utilities/Tech-Info-Block.html` | Embeddable snippet that fetches and inlines `index.html` from the GitHub API, for embedding the schedule on the church's main site (`fccla.org/tech-info`). |

## Sunday content pipeline

Turns each week's source documents into the parsed worship script, the
individual "service title" text files, the sermon-series description, and the
YouTube livestream — all committed back into the repo and mirrored to Google
Drive. `Utilities/dashboard/CONTENT_PIPELINE.md` documents how the dashboard
reports freshness for each stage.

| Stage | Script | Trigger | Output |
| ----- | ------ | ------- | ------ |
| **Worship script** | `Worship Scripts/worship workflows/update_worship_scripts.py` | `worship_scripts_checker.yml` (hourly) | Pulls the run-of-show PDF from Drive, parses it (pypdf + Gemini), writes `worship_scripts.json` |
| **Order of Worship → service titles** | `update_service_titles.py` | `service_titles_checker.yml` (hourly, or `ow_uploaded` repository_dispatch from the upload dashboard) | Ingests the OW PDF from the `cju-media/OW` repo, sends it to Gemini with `worship-prompt.txt`, writes the per-attribute files under `Worship Scripts/service-titles/` and `service_titles_state.json` |
| **Service titles → Drive** | `sync_service_titles_to_drive.py` | `sync_service_titles.yml` (on push to `service-titles/**`) | Mirrors the title files into the shared Drive folder |
| **Sermon series description** | `create_sermon_series.py` | `sermon_series.yml` (on push to `sermon-title.txt` / `sermon-minister.txt`) | Generates the Sermon Series title + description files, uploads to Drive, adds the video to the sermon-series playlist |
| **Backfill stream link** | `backfill_sermon_series_link.py` | `backfill_sermon_series_link.yml` (`youtube_stream_created` repository_dispatch) | Replaces the `YOUTUBE SERVICE LINK` placeholder in the sermon-series description once the livestream URL is known |
| **Archive** | `archive_service_files.yml` | Mondays 05:00 UTC | Snapshots `chapters.txt` / `description.txt` / `timings.txt` / `title.txt` into `Utilities/Archives/<date>/` |

Gemini reads are cached (per Drive file id / per content hash) so unchanged
inputs don't cost repeated API calls.

## YouTube livestream & description automation

Under `Youtube Processing/`.

- **`create_youtube_stream.py`** — creates the Sunday-service live broadcast on
  YouTube from that week's title/description, adds it to the playlist, writes
  the `last_stream.json` breadcrumb, and fires a `youtube_stream_created`
  `repository_dispatch`. Exits with a distinct code (`EXIT_NOT_READY`) when the
  week's title/description aren't confirmed yet.
- **`create_pending_stream.py`** (`create_pending_youtube_stream.yml`) — finishes
  a *deferred* stream: when a worship-service thumbnail is uploaded before the
  week's OW exists, `upload_queue_to_drive.py` stashes it in
  `Youtube Processing/pending_stream/` and this script creates the stream once
  title + description are ready. Chained off the hourly description / titles
  workflows.
- **`generate_youtube_description.py`** (`youtube_description.yml`) — assembles
  `Description.txt` and `chapters.txt` from `DescriptionBoiler.txt` /
  `DescriptionTemplate.txt` and the service-title files.
- **`update_youtube_stream.py`** (`update_youtube_stream.yml`) — pushes the
  generated description + timestamped chapters onto the live broadcast. Records
  the hash of each description it applies in `description_push_state.json` so
  the hourly poll never clobbers a manual fix made directly on YouTube until
  the underlying content actually changes again.
- **`get_youtube_credentials.py`** — one-time local helper to mint the OAuth
  refresh token stored in the `YOUTUBE_CREDENTIALS_JSON` secret. See
  `Youtube Processing/YOUTUBE_API_SETUP.md`.

## YouTube OSC timestamp server

`Youtube Processing/osc_server.js` — a Node.js (Express + socket.io + `osc`)
server with a web UI at `http://localhost:3671`, run at the church during the
live service. See `Youtube Processing/README.md`.

- Listens for OSC messages `/timings/forward` and `/timings/back` (from the
  lighting / show-control desk) to timestamp each section of the service as it
  happens; the web UI shows elapsed time, the remaining `chapters.txt`
  sections, and manual next/prev controls.
- Configurable timing offset (default 7s) to compensate for encoder / ingest /
  transcode lag; config saved to `osc_config.json`.
- **OBS WebSocket integration** — auto-starts / stops OBS recording when the
  service enters / leaves the "Sermon" segment.
- Reads the live stream's title and start time from the YouTube Data API;
  when it detects the stream has ended it pushes `timings.txt` back to the repo
  (only on Sundays, as a safeguard).
- Weekly auto-reset every Sunday 00:00 Pacific: drops sample/override data and
  fetches the fresh `chapters.txt` for the coming service.

## Crew scheduling & notifications

Under `Utilities/Scheduling/`. Full detail in
`Utilities/Scheduling/SCHEDULING_WORKFLOWS.md`.

`send_weekly_schedule.py` is the unified driver. It runs hourly from two
workflows and branches on the current UTC hour:

- **`schedule_notifications.yml`** (`ubuntu-latest`, `RUN_MODE=auto`) — email
  notifications:
  - **Availability check** (hourly) — emails when new crew availability appears
    (state: `avail_state.json`).
  - **Schedule updates** (daily 21:00 UTC) — diffs the whole schedule against
    `state.json`: new events, new assignments, cancellations, time changes;
    broadcasts a master PDF for new events, emails targeted per-member update
    PDFs.
  - **Daily reminder** (10:00 UTC) — emails each assigned member a PDF of that
    day's shifts.
  - **Weekly schedule** (Fridays 11:00 UTC) — emails every member a
    personalized PDF of the next 14 days.
- **`imessage_notifications.yml`** (self-hosted macOS, `RUN_MODE=auto_imessage`)
  — native iMessage sends via `osascript` (`send_imessage.py`):
  - iMessage shift reminders at 03:00 / 12:00 / 17:00 UTC (early-morning,
    same-day, day-before).
  - iMessage cancellations for removed events.
  - Also the sink for many `repository_dispatch` events across the repo
    (drive-upload complete, stream created, video-migration upload skipped,
    events-folder cleanup needs a human, RTMP watchdog restart / failure, …).

Supporting scripts:

- **`mark_past_events.py`** (`mark_past_sheet_events.yml`, daily) — strikes
  through + reddens past-dated rows on the master Tech Schedule sheet,
  including month-divider rows once their block has passed.
- **`sync_calendar_notes.py`** (`sync_calendar_notes.yml`, daily) — copies each
  published Outlook calendar event's description into column L ("Event Notes")
  of the sheet; Gemini matches calendar events to sheet rows, cached in
  `calendar_notes_state.json`.
- **`draft_upcoming_events.py`** (`draft_upcoming_events.yml`, daily) — builds a
  copy-paste draft of upcoming events (Outlook `.ics` feed via Gemini, plus
  synthesized weekly "Worship Service" rows) into `event_draft.json`; nothing
  touches the live sheet.
- **`check_disabled_workflows.py`** — iMessages Cameron if any workflow in the
  repo has been auto-disabled by GitHub (state: `disabled_workflows_state.json`).

Team contact info: `Utilities/Scheduling/Team Data/team_emails.json` /
`team_phones.json`. Crew: Cameron, Danny, Jaffe, Kaspar, Marc, Saad.

## RF coordination

`Utilities/Scheduling/RF Coordination/send_rf_coordination.py`
(`rf_coordination.yml`, Wednesdays 17:00 UTC).

Weekly scan for events near the church (within ~1 mi = coordinate frequencies,
~1.6 mi = worth a look) that could cause wireless-mic / IEM interference —
Lafayette Park is one block away. Pulls LA City's public event feed, geocodes
venues (cached in `geocode_cache.json`), projects annually-recurring events
forward, and emails a digest. State/data in `all_la_events.json`,
`public_events.json`.

## Server health monitoring

`Utilities/dashboard/` — see `Utilities/dashboard/SERVER_HEALTH.md`.

Monitors the four Node servers on the church's **Studio Mini** Mac (QR/URL
server :3000, Vertical Stream / ATEM :4200,
[Content Display](https://github.com/cju-media/content-display) control :1031,
YouTube OSC :3671).

- **`check_server_health.py`** (`server_health.yml`, self-hosted, every 10 min)
  — polls each URL in `servers.json`, publishes status to a secret Gist,
  emails on up↔down transitions (after 2 consecutive fails). State:
  `server_status_state.json`.
- **`check_health_freshness.py`** (`server_health_watchdog.yml`, GitHub-hosted,
  every 30 min) — reads the Gist and emails once if the poller has gone silent
  >40 min (catches a fully-down Studio Mini). State: `health_watchdog_state.json`.
- The dashboard reads the same Gist (URL in `server-health-config.json`) for
  its status cards.

## RTMP display watchdog (Raspberry Pi fleet)

`Utilities/RTMP Display Watchdog/` — see its `README.md`. The lobby/narthex
video displays (`hallway-display`, `narthex-display`) are Raspberry Pis driven
by **[Content Display](https://github.com/cju-media/content-display)** — that
project sources the on-screen content and publishes the RTMP feed
(`rtmp://192.168.112.33/live/content-display`) plus the control server on
`studio-mini:1031` that the [server health monitor](#server-health-monitoring)
polls. Each Pi pulls that feed with VLC rendered straight to the DRM
framebuffer as `content-display.service`. This directory is the client-side
half of an auto-recovery + notification system layered on top of those Pis.

- **`vlc-watchdog.py`** (systemd timer, every 60s) — checks four independent
  liveness signals (VLC playback clock, process CPU delta, DRM plane
  framebuffer id, and actual hashed pixel content via the `read-plane-hash.c`
  helper built against libdrm). On a stuck signal it force-restarts
  `content-display.service`; only a *failed* recovery fires a
  `repository_dispatch` → iMessage. Local log:
  `/var/log/vlc-watchdog-restarts.log`.
- **`soft-reconnect.py`** — gap-free planned stream reconnect via VLC's CLI
  (no service restart, no black screen).
- **`memory-check.sh`** (systemd timer) — read-only memory snapshot logging,
  investigating a leak theory.
- systemd unit files and the `content-display.service` template are included;
  the README has per-Pi deployment steps and documented hardware quirks
  (WiFi roaming / `brcmfmac` kernel bug, masked `getty@tty1`, cursor
  suppression, an ffplay-vs-VLC experiment on hallway).

## Google Drive housekeeping

`Worship Scripts/worship workflows/`.

- **`cleanup_events_folder.py`** (`cleanup_events_folder.yml`, daily) — trashes
  event-ad flyers from the flat `Events_Ads` Drive folder once the event has
  passed. No date metadata exists, so Gemini vision reads the date printed on
  the flyer image; cached per file id in `events_cleanup_date_cache.json`.
  Has year-misread sanity guards (regression-tested) and iMessages a human
  when it needs manual confirmation.
- **`dedupe_events_folder.py`** (`dedupe_events_folder.yml`, manual) — trashes
  byte-identical (same MD5) duplicate flyers, keeping the earliest copy.
  Cleans up after historical overlapping upload runs.
- **`upload_queue_to_drive.py`** (`process_uploads.yml`, on push to
  `Utilities/uploads_queue/**`) — drains the upload dashboard's queue to the
  right Drive folders (thumbnails, sermon recordings), and either creates the
  YouTube stream directly or defers it to `pending_stream/`. Serialized by a
  concurrency gate so a batch upload can't double-send.

## Video migration

`Utilities/Video Migration/migrate_videos.py` (`video_migration.yml`, Mondays
17:00 UTC). Copies the recorded service from the source Drive folder to the
archive folder and uploads it to YouTube (into the sermon playlist). Writes
the `last_upload.json` breadcrumb on a successful upload; if it copies to
Drive but *skips* the YouTube upload (missing title/description text, or auth
failure) it dispatches `video_migration_upload_skipped` → iMessage.

## GitHub Actions workflows

All under `.github/workflows/`. Most also expose `workflow_dispatch` with a
`dry_run` input.

| Workflow | Schedule / trigger | Runner |
| -------- | ------------------ | ------ |
| `worship_scripts_checker.yml` | hourly | ubuntu |
| `youtube_description.yml` | after *Check Worship Scripts* | ubuntu |
| `update_youtube_stream.yml` | after *Generate YouTube Description* / *Service Titles Checker*; push to `timings.txt` | ubuntu |
| `create_pending_youtube_stream.yml` | after *Generate YouTube Description* / *Service Titles Checker* | ubuntu |
| `service_titles_checker.yml` | hourly; `ow_uploaded` dispatch | ubuntu |
| `sync_service_titles.yml` | push to `service-titles/**` | ubuntu |
| `sermon_series.yml` | push to `sermon-title.txt` / `sermon-minister.txt` | ubuntu |
| `backfill_sermon_series_link.yml` | `youtube_stream_created` dispatch | ubuntu |
| `archive_service_files.yml` | Mon 05:00 UTC | ubuntu |
| `schedule_notifications.yml` | hourly | ubuntu |
| `imessage_notifications.yml` | hourly; many `repository_dispatch` types | self-hosted macOS |
| `mark_past_sheet_events.yml` | daily 09:00 UTC | ubuntu |
| `sync_calendar_notes.yml` | daily 09:30 UTC | ubuntu |
| `draft_upcoming_events.yml` | daily 09:45 UTC | ubuntu |
| `cleanup_events_folder.yml` | daily 09:15 UTC | ubuntu |
| `dedupe_events_folder.yml` | manual | ubuntu |
| `process_uploads.yml` | push to `Utilities/uploads_queue/**` | ubuntu |
| `rf_coordination.yml` | Wed 17:00 UTC | ubuntu |
| `video_migration.yml` | Mon 17:00 UTC | ubuntu |
| `server_health.yml` | every 10 min | self-hosted macOS |
| `server_health_watchdog.yml` | every 30 min | ubuntu |
| `pages_deployment.yml` | push to `main`; after *Check Worship Scripts* | ubuntu |
| `test_sermon_series_logic.yml` | push / PR touching the tested scripts | ubuntu |
| `test_worship_scripts.yml` | manual | ubuntu |

The live state of all of these (last run, next run, dependency graph) is
visible on `Utilities/dashboard/workflows.html`.

## Repository layout

```
index.html                     Crew-facing schedule page (GitHub Pages root)
event-draft.html               Renders event_draft.json
event-notes.html               Renders calendar notes
test_mermaid.js                Playwright snippet for debugging the dashboard's Mermaid render

.github/workflows/             ~24 GitHub Actions workflows

Utilities/
  dashboard/                   Dashboard hub, workflows dashboard, server-health poller + watchdog
  uploads/                     Upload dashboard (drag-and-drop weekly assets)
  Scheduling/
    Event Data/                send_weekly_schedule.py + schedule/notification scripts, state JSON
    RF Coordination/           Weekly RF interference scan
    Team Data/                 team_emails.json, team_phones.json, per-member schedule PDFs
  Video Migration/             migrate_videos.py + last_upload.json breadcrumb
  RTMP Display Watchdog/       Raspberry Pi VLC watchdog, systemd units, read-plane-hash.c
  Archives/<date>/             Weekly snapshots of the YouTube text files

Worship Scripts/
  worship-prompt.txt           Gemini prompt for parsing the Order of Worship
  worship_scripts.json         Parsed run-of-show
  service-titles/              Per-attribute .txt files (one section credit each)
  service-scripts/             Run-of-show PDFs
  Sermon-Series/               Generated sermon-series titles + descriptions
  worship workflows/           All the pipeline + Drive-housekeeping scripts (+ unit tests)

Youtube Processing/
  osc_server.js                Live OSC timestamp server + web UI (Node)
  public/index.html            OSC server web UI
  create_youtube_stream.py     Create the Sunday broadcast
  create_pending_stream.py     Finish a deferred broadcast
  generate_youtube_description.py / update_youtube_stream.py
  get_youtube_credentials.py   One-time OAuth token minting
  Description*.txt / chapters.txt / timings.txt / *_state.json
  YOUTUBE_API_SETUP.md
```

Most `*_state.json` / `*_cache.json` / `last_*.json` files are automation
state committed back by the workflows (`chore:` commits); they are the memory
that keeps hourly jobs idempotent and quiet.

## Infrastructure, secrets & external services

**External services**

- **Google Sheets** — master Tech Schedule + Tech Availability sheets (read by
  `index.html` via `gviz`, written by the scheduling scripts via the Sheets
  API).
- **Google Drive** — run-of-show PDFs, thumbnails, sermon recordings, mirrored
  service-title files.
- **Google Gemini** — parsing OWs / run-of-show PDFs, matching calendar events
  to sheet rows, reading dates off flyer images.
- **YouTube Data API v3** — creating broadcasts, updating descriptions,
  managing playlists.
- **Published Outlook calendar** (`.ics` feed on `outlook.office365.com`) —
  event notes and the upcoming-events draft.
- **GitHub Gist** — server-health heartbeat (`7f410b4a9b15c57104f85ce4f6068d81`).
- **[`cju-media/OW`](https://github.com/cju-media/ow)** — separate repo the upload dashboard pushes Orders of
  Worship into.
- **[`cju-media/content-display`](https://github.com/cju-media/content-display)**
  — drives the on-screen content and the RTMP feed the display Pis render; its
  control server is one of the four monitored by the server-health monitor.
- **SMTP** — outgoing email for all notification workflows.
- **OBS WebSocket** — recording control from the OSC server.

**Runners**

- GitHub-hosted `ubuntu-latest` for most workflows.
- Self-hosted **macOS** runner ("Studio Mini") at the church for anything that
  needs native iMessage (`osascript`) or must poll `localhost` media servers.
- Raspberry Pi display fleet (`hallway-display`, `narthex-display`) running the
  VLC watchdog via systemd.

**Repository secrets / variables** (configured in GitHub, not in the repo)

`GDRIVE_SERVICE_ACCOUNT_JSON`, `GDRIVE_OAUTH_JSON`, `GDRIVE_API_KEY`,
`GEMINI_API_KEY`, `YOUTUBE_CREDENTIALS_JSON`, `HEARTBEAT_GIST_TOKEN` (classic
PAT, `gist` scope), `PAT` (for cross-workflow `repository_dispatch` and Pages
deploys off `chore:` commits), `SMTP_EMAIL` / `SMTP_PASSWORD` / `SMTP_SERVER` /
`SMTP_PORT`, and optional `HEALTH_ALERT_EMAIL`. Client-side pages read an
optional GitHub PAT from `localStorage.GITHUB_PAT` to lift the API rate limit.

**Python dependencies** (installed per-workflow): `google-api-python-client`,
`google-auth`, `google-auth-oauthlib`, `google-genai`, `python-dateutil`,
`requests`, `pypdf`, `pandas`, `weasyprint`, `pytz`. Root `Utilities/requirements.txt`
covers the PDF-generation subset.

**Node dependencies** (`Youtube Processing/package.json`): `express`,
`socket.io`, `osc`, `obs-websocket-js`, `googleapis`, `node-fetch`.

## Tests

- `Worship Scripts/worship workflows/test_create_sermon_series.py` — "which
  Sunday is this run about" guard (regression for the 2026-08-30 miss).
- `Worship Scripts/worship workflows/test_cleanup_events_folder.py` — flyer
  date / year-misread sanity checks (regression for the 2026-08-31 bogus
  deletion alerts).
- `Worship Scripts/worship workflows/test_worship_scripts.py` — end-to-end
  worship-script sync check (`test_worship_scripts.yml`, manual).

```bash
cd "Worship Scripts/worship workflows"
python -m unittest test_create_sermon_series test_cleanup_events_folder -v
```

`test_sermon_series_logic.yml` runs the first two on every push / PR that
touches the relevant scripts.
