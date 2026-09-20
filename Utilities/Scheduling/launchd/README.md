# launchd timers for the screen cards

GitHub's `schedule:` trigger is best effort, and on this repo it is not close
to reliable. Measured over 46 hours on 2026-09-20, two workflows each asking
for an hourly run got **about one in four** — an average gap of 3.7 hours and
a worst gap of 6.1.

That is survivable for the weather card. It is not survivable for the service
card: on 2026-09-20 every scheduled run between 09:52 and 11:07 was dropped,
so the card sat on *"Upcoming Service — Today at 10:30 AM"* for 37 minutes of
the service it was advertising.

Adding more cron entries doesn't help, because the scheduler isn't running
late, it's dropping runs. The fix is a clock that doesn't: `launchd` on the
self-hosted Mac, firing a `workflow_dispatch`. The job still runs on GitHub's
runners where the API keys live — this only pulls the trigger.

A second benefit: launchd schedules in **local time**, so "10:27" means 10:27
in Los Angeles year round. The cron entries had to list both `17:xx` and
`18:xx` UTC to hit the same moment either side of daylight saving.

## What's here

| Plist | Workflow | Fires |
| --- | --- | --- |
| `org.fccla.service-card` | `generate_service_ad.yml` | hourly `:27`, plus Sun 10:42 / 10:57 / 11:12 |
| `org.fccla.weather-card` | `generate_weather_ad.yml` | hourly `:37` |
| `org.fccla.rotation` | `renumber_rotation.yml` | every 15 min |
| `org.fccla.events-card` | `generate_events_ad.yml` | hourly `:47` |

All four call the same `dispatch_workflow.py`; they differ only in label,
workflow name, log path and schedule.

## Install (on the self-hosted Mac)

1. **Give it a token.** Any token that can dispatch workflows on this repo —
   a fine-grained PAT with *Actions: read and write* is enough.

   ```bash
   mkdir -p ~/.config/fccla
   printf '%s' 'ghp_yourtokenhere' > ~/.config/fccla/dispatch-token
   chmod 600 ~/.config/fccla/dispatch-token
   ```

2. **Check the paths in the plist.** It assumes the repo is at
   `/Users/soundteam/Tech-Info`. Edit `ProgramArguments` and the two log
   paths if it lives somewhere else.

3. **Install and load them.**

   ```bash
   for f in org.fccla.*.plist; do
     cp "$f" ~/Library/LaunchAgents/
     launchctl unload ~/Library/LaunchAgents/"$f" 2>/dev/null
     launchctl load ~/Library/LaunchAgents/"$f"
   done
   ```

4. **Prove it works** without waiting for the clock:

   ```bash
   launchctl start org.fccla.service-card
   cat ~/Library/Logs/fccla-service-card.log
   ```

   You want a line like
   `2026-09-20 11:09:08 PDT  dispatched generate_service_ad.yml (main)`,
   and a new run at
   <https://github.com/cju-media/Tech-Info/actions/workflows/generate_service_ad.yml>.

## Schedule

| When | Why |
| --- | --- |
| Every hour at `:27` | Keeps the card current the rest of the week. |
| Sunday 10:42, 10:57, 11:12 | The service. The hourly `:27` is the first attempt; these follow so one failure isn't a dead card. |

Dispatching more often than strictly needed costs nothing — a run whose
render is byte identical skips the Drive write entirely.

## What GitHub was actually delivering

Measured 2026-09-20:

| Workflow | Asks for | Was getting |
| --- | --- | --- |
| `generate_service_ad.yml` | hourly | ~1 in 4 (avg gap 3.8h, worst 6.1h) |
| `generate_weather_ad.yml` | hourly | ~1 in 4 (avg gap 3.6h, worst 5.0h) |
| `renumber_rotation.yml` | every 15 min | ~1 in 14 (gaps of 3.3h and 4.4h) |
| `generate_events_ad.yml` | daily 08:45 UTC | arrived, ~4h late |

The events card has since been moved to hourly, so that gap now applies to
the "new event on the calendar" text as well as the card itself.

The rotation was hit hardest by a wide margin: 96 requested runs a day,
about seven delivered. That gap is how long a newly uploaded flyer sat at
the back of the lap instead of in its slot.

Leave the `schedule:` blocks in the workflows as they are. They cost nothing
when they do fire and are a useful backstop if the Mac is off.

## Minutes in use

Each timer sits on its own minute so they never fire together:

| Minute | Timer |
| --- | --- |
| `:11` `:26` `:41` `:56` | rotation |
| `:27` | service card |
| `:37` | weather card |
| `:47` | events card |

The events card is hourly rather than daily because it also sends the "new
event on the calendar" text, and daily meant that text could be a day behind.
An unchanged calendar skips the Gemini call, the Drive write and the state
commit, so an idle hourly run is one scrape and a fingerprint comparison.

## Caveats

- A **LaunchAgent** runs only while that user is logged in. The Mac already
  stays logged in for the Actions runner, so this is the same assumption the
  runner makes. If that ever stops being true, this belongs in
  `/Library/LaunchDaemons` instead.
- If the Mac is asleep at the scheduled moment, launchd runs the job once
  when it wakes rather than skipping it.
