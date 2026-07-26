# Scheduling Workflows

The scheduling and notification system has been consolidated into a single GitHub Actions workflow (`.github/workflows/schedule_notifications.yml`) that runs every hour at minute 0 (using the cron `0 * * * *`).

The logic for determining which notifications and processes run is handled by the unified script `Scheduling/Event Data/send_weekly_schedule.py` running in `RUN_MODE=auto`. When it executes, the script evaluates the current UTC time and triggers specific processing blocks.

## Schedule Overview

The unified workflow checks the time and triggers the following modes automatically:

### 1. Availability Check (`avail_check`)
- **When it runs:** Every hour (00:00 - 23:00 UTC).
- **What it does:** Compares the current availability list for upcoming events against the saved state (`avail_state.json`). If new availability has been added, it sends a summary email to `cjohnston@fccla.org`.

### 2. Schedule Updates (`update`)
- **When it runs:** Daily at 21:00 UTC (2 PM PDT / 1 PM PST).
- **What it does:** Scans the entire schedule against the saved assignment state (`state.json`). It looks for globally new events, new assignments for team members, event cancellations, or time changes.
- **Notifications:**
  - Broadcasts a master PDF to the team for globally new events.
  - Emails targeted update PDFs to individually affected members.
  - Sends immediate iMessage cancellations for removed events.

### 3. Daily Event Reminders (`daily_reminder`)
- **When it runs:** Daily at 10:00 UTC (3 AM PDT / 2 AM PST).
- **What it does:** Looks ahead specifically at the current day's events.
- **Notifications:** Emails a generated PDF containing just that day's scheduled shifts to any assigned team member.

### 4. Weekly Schedule Generation (`weekly`)
- **When it runs:** Every Friday at 11:00 UTC (4 AM PDT / 3 AM PST).
- **What it does:** Looks ahead to the next 14 days of events.
- **Notifications:** Emails a personalized PDF schedule containing the next two weeks of assignments to every team member.

### 5. iMessage Reminders (`imessage_reminder`)
- **When it runs:** Three times a day at specific intervals:
  - **03:00 UTC (8 PM PDT):** Checks for *early morning shifts* (Call time <= 7:00 AM) occurring the next day.
  - **12:00 UTC (5 AM PDT):** Checks for *regular shifts* (Call time > 7:00 AM) occurring today.
  - **17:00 UTC (10 AM PDT):** Checks for *all shifts* occurring the next day (Day-Before Reminder).
- **What it does:** Sends quick, native text message reminders to team members directly to their phones (using AppleScript on the macOS runner). It also texts a summary digest to the Admin.

## Manual Execution

You can manually trigger the workflow from the **Actions** tab in GitHub by selecting the `Schedule and Notifications` workflow and clicking **Run workflow**.

By default, the workflow will use `auto` mode and simulate the exact behaviors for the current hour. However, you can explicitly override the `run_mode` input to forcefully run any mode out-of-schedule (e.g., `admin`, `test`, `weekly`, `update`).