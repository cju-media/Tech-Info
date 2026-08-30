# RTMP Display Watchdog

Client-side half of the RTMP display auto-recovery + notification system.
The server-side half is the `rtmp_watchdog_restart` / `rtmp_watchdog_restart_failed`
steps in [`imessage_notifications.yml`](../../.github/workflows/imessage_notifications.yml).

## What it is

Each display Pi (currently `hallway-display` @ 192.168.112.40 and
`narthex-display` @ 192.168.112.41) runs VLC as `content-display.service`,
pulling an RTMP stream and rendering it directly to the console framebuffer
via VLC's `drm_vout` module (no X11/Wayland). VLC's decode/render pipeline
can occasionally wedge internally -- a hardware decoder stall, or (on
hallway specifically) a kernel bug in the WiFi driver's roaming-event
handler -- without the process actually exiting, so plain `Restart=always`
never fires.

`vlc-watchdog.py`, run every 60s via `vlc-watchdog.timer`, catches that by
polling three independent liveness signals against VLC's CLI interface,
`/proc`, and the kernel's DRM debugfs interface:

1. **`get_time`** -- is VLC's playback clock advancing?
2. **Process CPU time delta** -- is any thread actually doing work?
3. **DRM plane framebuffer ID** (`/sys/kernel/debug/dri/0/state`) -- is a
   frame actually being presented on screen?

Signal 3 exists because two real incidents (2026-08-26, 2026-08-29) showed
signals 1 and 2 can both look completely healthy -- VLC's own internal
decode/clock bookkeeping still ticking along -- while the picture is
visibly frozen on screen, i.e. something failing between VLC's internal
state and the actual DRM commit. It samples the plane's bound framebuffer
ID several times in a tight ~8s burst per check (rather than a single
comparison 60s apart), since it only cycles through a handful of buffer
IDs and a single far-apart comparison could coincidentally match by chance.

If any of the three looks stuck for 3 consecutive checks (~3-4 min), it
force-restarts `content-display.service` and verifies the restart actually
succeeded. **Only a failed recovery texts** -- a successful auto-restart is
the watchdog doing its job silently, logged locally to
`/var/log/vlc-watchdog-restarts.log` (timestamp, outcome, and the specific
reason(s) that triggered it) but not worth interrupting anyone for. A
failed recovery fires a `repository_dispatch` event to this repo so the
self-hosted macOS runner sends an iMessage (rate-limited to one per 15 min
per Pi, in case recovery keeps failing repeatedly).

A fourth signal -- the RTMP socket's Recv-Q -- is recorded in notifications
for diagnostic context but deliberately does **not** drive the restart
decision; testing showed a WiFi-marginal Pi can legitimately show
multi-hundred-KB to multi-MB buffering swings during genuinely healthy
playback, which made it an unreliable trigger on its own.

Two correctness fixes worth knowing about if you're reading the source:

- **CPU delta is only compared within the same VLC process (`pid` is now
  tracked in state).** `/proc/<pid>/stat`'s utime+stime resets near zero
  for a fresh process, so comparing it against the previous (now-dead)
  process's much higher count right after a restart produced a nonsensical
  -- often negative -- delta that got misread as "no work happening,"
  poisoning the very next check after *every* restart and contributing to
  a self-reinforcing restart cascade (observed 2026-08-30: displays
  failing roughly every 20 min).
- **`STARTUP_GRACE_SECONDS` (30s) skips checks entirely** right after a
  (re)start -- a service that just started hasn't finished
  connecting/negotiating its DRM plane yet, and checking too early can
  catch that normal window and misread it as frozen.

## Files

- `vlc-watchdog.py` -- the watchdog script, deployed to `/usr/local/bin/` on each Pi
- `vlc-watchdog.service` / `vlc-watchdog.timer` -- systemd units running it every 60s (identical on every Pi)
- `content-display.service.template` -- the VLC playback service; `Description` and `User` are host-specific, copy and fill in per Pi

## Deploying to a Pi

```bash
scp vlc-watchdog.py <user>@<pi>:/tmp/
ssh <user>@<pi> '
  sudo mv /tmp/vlc-watchdog.py /usr/local/bin/vlc-watchdog.py
  sudo chmod +x /usr/local/bin/vlc-watchdog.py
  sudo mkdir -p /var/lib/vlc-watchdog
'
scp vlc-watchdog.service vlc-watchdog.timer <user>@<pi>:/tmp/
ssh <user>@<pi> '
  sudo mv /tmp/vlc-watchdog.service /etc/systemd/system/
  sudo mv /tmp/vlc-watchdog.timer /etc/systemd/system/
  sudo systemctl daemon-reload
  sudo systemctl enable --now vlc-watchdog.timer
'
```

`content-display.service` is deployed the same way after filling in the
template's `Description`/`User` placeholders for that Pi.

## GitHub token

The watchdog needs a token to fire the `repository_dispatch` call:

```bash
sudo mkdir -p /etc/vlc-watchdog
echo "<token>" | sudo tee /etc/vlc-watchdog/github-token
sudo chmod 600 /etc/vlc-watchdog/github-token
sudo chown root:root /etc/vlc-watchdog/github-token
```

Without this file, the watchdog still restarts a frozen service -- it just
skips the notification silently (see `notify()`'s `except OSError: return`).

## Known per-Pi quirks

- **`hallway-display`** has `options brcmfmac roamoff=1` in
  `/etc/modprobe.d/brcmfmac.conf` (requires a reboot to take effect, module
  parameter is load-time only). Its WiFi signal is weaker than narthex's
  (~51/100 vs ~81/100 on the same `1C-Stream` mesh network, different
  BSSIDs), which was triggering the WiFi chip's own firmware roaming logic
  -- and a real kernel bug in `brcmfmac`'s roam-completion handler
  (`cfg80211_roamed` / `brcmf_bss_roaming_done`) that wedged VLC's
  decode/render pipeline. `narthex-display` has never logged a roaming
  event and doesn't need this.
- Both Pis also have `vt.global_cursor_default=0` appended to
  `/boot/firmware/cmdline.txt` to stop the console's blinking cursor from
  showing through the video output.
