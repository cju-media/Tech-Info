# Scheduling Workflows

The scheduling and notification system has been consolidated into two main GitHub Actions workflows:
1. **Schedule and Notifications** (`.github/workflows/schedule_notifications.yml`): Runs general tasks and email notifications on a standard Ubuntu runner.
2. **iMessage Notifications** (`.github/workflows/imessage_notifications.yml`): Runs tasks that strictly require sending native text messages via AppleScript on the self-hosted macOS runner.

Both workflows run every hour at minute 0 (using the cron `0 * * * *`).

The logic for determining which notifications and processes run is handled by the unified script `Utilities/Scheduling/Event Data/send_weekly_schedule.py`. The standard workflow runs in `RUN_MODE=auto`, and the iMessage workflow runs in `RUN_MODE=auto_imessage`. When executing, the script evaluates the current UTC time and triggers specific processing blocks.

## Schedule Overview

The unified workflow checks the time and triggers the following modes automatically:

### 1. Availability Check (`avail_check`)
- **When it runs:** Every hour (00:00 - 23:00 UTC).
- **What it does:** Compares the current availability list for upcoming events against the saved state (`avail_state.json`). If new availability has been added, it sends a summary email to `cjohnston@fccla.org`.

### 2. Schedule Updates (`update`)
- **When it runs:** Once daily, on the first run at or after 2 PM local. Runs on the `imessage_notifications.yml` workflow since it needs macOS natively to send cancellation texts.
- **What it does:** Scans the entire schedule against the saved assignment state (`state.json`). It looks for globally new events, new assignments for team members, event cancellations, or time changes.
- **Notifications:**
  - Broadcasts a master PDF to the team for globally new events.
  - Emails targeted update PDFs to individually affected members.
  - Sends immediate iMessage cancellations for removed events.

### 3. Daily Event Reminders (`daily_reminder`)
- **When it runs:** Once daily, on the first run at or after 10:00 UTC (3 AM PDT / 2 AM PST), deadline 18:00 UTC.
- **What it does:** Looks ahead specifically at the current day's events.
- **Notifications:** Emails a generated PDF containing just that day's scheduled shifts to any assigned team member.

### 4. Weekly Schedule Generation (`weekly`)
- **When it runs:** Once each Friday, on the first run at or after 11:00 UTC (4 AM PDT / 3 AM PST), deadline 23:00 UTC.
- **What it does:** Looks ahead to the next 14 days of events.
- **Notifications:** Emails a personalized PDF schedule containing the next two weeks of assignments to every team member.

### 5. iMessage Reminders (`imessage_reminder`)
- **When it runs:** Three times a day, each once per day within a window (see
  "Catch-up windows" below). All times are local to the macOS runner:
  - **`morning` — 5 AM, deadline 11 AM:** *regular shifts* (Call time > 7:00 AM) occurring today.
  - **`day_before` — 3 PM, deadline 9 PM:** *all shifts* occurring the next day (Day-Before Reminder).
  - **`night` — 8 PM, deadline midnight:** *early morning shifts* (Call time <= 7:00 AM) occurring the next day.
- **What it does:** Sends quick, native text message reminders to team members directly to their phones (using AppleScript on the macOS runner). It also texts a summary digest to the Admin.
- If `day_before` and `night` both come due in the same run, the `night` nudge is
  skipped — `day_before` already covers every one of tomorrow's shifts.

## Catch-up windows

Both workflows are on an hourly `schedule:` cron, but GitHub treats that as
best-effort and has been dropping most firings — this repo went from ~23 runs a
day to ~6 at arbitrary minutes past arbitrary hours in late August 2026. Every
daily mode used to match on an exact hour, so nearly all of them silently
no-op'd (`No modes to run at this hour.`). Replaying the real run times from
Aug 27 – Sep 13 2026: the iMessage reminders fired 12 times out of ~72, and the
email side 5 out of 21 — the Friday two-week PDF went out on 0 of 3 Fridays.

Each daily mode is therefore a **window** with a `trigger` and a `deadline`,
defined in `IMESSAGE_WINDOWS` / `EMAIL_WINDOWS` in `send_weekly_schedule.py`:

- It fires on the **first run at or after `trigger`**, not on an exact hour.
- It fires **at most once per day** — a state file records the last date each
  window ran on, and the workflow commits it back:
  `reminder_state.json` for the iMessage windows, `email_window_state.json` for
  the email ones. Separate files, because the two workflows run on different
  runners and both push.
- Past its `deadline` it is **skipped, not run late**, and the run logs which
  window was missed. A reminder about a shift that already started is worse
  than no reminder.
- State is written only after every mode in the run has finished, so a crash
  part-way through leaves the window due and the next run retries it.
- A window may carry a `weekday` (as `weekly` does) to restrict it to one day.

Under the same replayed run times the new windows fire 63/72 and 21/21. The
iMessage misses are days where no run landed inside the window at all, which
correctly skip rather than send late.

### A note on clocks

Windows are evaluated against `datetime.now()` on whichever runner is
executing — the same clock the rest of the script uses to decide what "today"
and "tomorrow" mean. **The two workflows do not share a clock:**

- `IMESSAGE_WINDOWS` runs on the church's self-hosted macOS box, so those are
  America/Los_Angeles wall-clock times, and they stay put across DST.
- `EMAIL_WINDOWS` runs on `ubuntu-latest`, which is UTC. Their deadlines are
  kept inside 07:00–23:59 UTC, the span where the UTC date still matches the
  church's local date, so `today` keeps meaning the right day. Widening an
  email window past 23:59 UTC would roll `today` onto the next local day.

The availability check (`avail_check`) has no window — it is a diff against
`avail_state.json`, so it is self-healing and simply runs on every pass.

## Manual Execution

You can manually trigger the workflow from the **Actions** tab in GitHub by selecting the `Schedule and Notifications` workflow and clicking **Run workflow**.

By default, the workflow will use `auto` mode and run whichever windows are currently due. However, you can explicitly override the `run_mode` input to forcefully run any mode out-of-schedule (e.g., `admin`, `test`, `weekly`, `update`).

A manual `imessage_reminder` picks its window from the local clock (before 2 PM
= `morning`, 2-7 PM = `day_before`, after 7 PM = `night`). To force a specific
one, pass `imessage_reminder:morning`, `imessage_reminder:day_before` or
`imessage_reminder:night`. Manual runs bypass the window state entirely, so
they will re-send something that already went out today — and they do not
record themselves as having run, so the automatic window still fires later.