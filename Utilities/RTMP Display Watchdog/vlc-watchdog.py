#!/usr/bin/env python3
"""Watchdog for content-display.service.

VLC's decode/render pipeline can wedge internally (e.g. a v4l2m2m hardware
decoder stall, or a WiFi driver roaming-event crash) without the process
actually exiting, so systemd's Restart=always never fires -- the process is
alive, just not making progress. Worse, VLC's own playback clock (get_time)
can keep advancing even while the actual picture is frozen, if the demux/
network thread is still alive while only the render path is wedged -- so
get_time alone is not a reliable liveness signal on its own.

This polls three independent liveness signals every cycle:
  1. get_time via VLC's CLI interface (is the playback clock advancing?)
  2. Total process CPU time (is *any* thread actually doing work?)
  3. The DRM plane's framebuffer ID, sampled in a tight burst (is a frame
     actually being presented to the screen?)

If ANY looks stuck for several consecutive checks, the service is
force-restarted, its recovery is verified, and a notification is sent.

Signal 3 exists because two real incidents showed signals 1 and 2 can both
look completely healthy -- VLC's own internal decode/clock bookkeeping
still ticking along -- while the picture is visibly frozen on screen,
something having failed between VLC's internal state and the actual DRM
commit. Signals 1 and 2 alone were blind to that failure mode entirely.

A fourth signal -- the RTMP socket's Recv-Q (bytes piling up unread) -- is
recorded for diagnostic context in notifications, but deliberately does NOT
drive the restart decision on its own: testing showed it produces false
positives on a WiFi-marginal Pi, where healthy playback can legitimately
show multi-hundred-KB to multi-MB buffering swings that drain on their own,
well beyond the few-KB fluctuations a clean connection produces.
"""
import json
import platform
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

HOST = "127.0.0.1"
PORT = 4212
STATE_FILE = "/var/lib/vlc-watchdog/state.json"
STALE_THRESHOLD = 3  # consecutive unchanged/stuck checks before forcing a restart
SERVICE = "content-display.service"

# A real, healthy decode+render pipeline burns some CPU every cycle. If total
# process CPU time barely moves over a ~60s check interval, nothing is
# actually being decoded/rendered, regardless of what get_time claims.
MIN_CPU_TICKS_PER_CHECK = 50  # ~0.5s of CPU time (at the usual 100 ticks/sec)

# A backlog this large sitting unread in the RTMP socket, and not shrinking
# between checks, means the consuming thread has stalled even though the
# network is still delivering data -- as opposed to the few-KB fluctuations
# normal bursty TCP delivery produces.
RTMP_DPORT_FILTER = "( dport = :1935 )"

# A service that just (re)started hasn't finished connecting/negotiating
# its DRM plane yet -- checking it too early (e.g. the very next 60s tick
# after a restart) can catch a normal startup window and misread it as a
# freeze, which would otherwise poison the count right after every
# restart, legitimate or not. Skip the whole check while still this young.
STARTUP_GRACE_SECONDS = 30

# Direct DRM-level check: is a frame actually being presented on screen?
# Both prior incidents showed get_time and CPU can look completely healthy
# (VLC's own internal decode/clock bookkeeping still ticking along) while
# the picture is visibly frozen -- something failing between VLC's internal
# state and the actual DRM commit reaching the display. debugfs exposes the
# framebuffer ID currently bound to whichever plane VLC owns; that ID
# changes on every real frame presented. It cycles through only a handful
# of buffer IDs (double/triple buffering), so a single comparison across
# the normal 60s check interval could coincidentally match by chance --
# instead, sample it several times in a tight burst within one check, and
# only call it frozen if it never moves across that whole burst.
DRM_STATE_FILE = "/sys/kernel/debug/dri/0/state"
FB_SAMPLE_COUNT = 5
FB_SAMPLE_INTERVAL_SECONDS = 2

# iMessage notifications, via the same repository_dispatch -> self-hosted
# macOS runner -> osascript pattern used elsewhere in Tech-Info.
GITHUB_TOKEN_FILE = "/etc/vlc-watchdog/github-token"
GITHUB_REPO = "cju-media/Tech-Info"
LAST_NOTIFY_FILE = "/var/lib/vlc-watchdog/last-notify"
NOTIFY_COOLDOWN_SECONDS = 15 * 60  # avoid texting every cycle during a prolonged outage

# Every restart (successful or not) is appended here regardless of whether
# it texts -- a local record so restart *frequency* can still be diagnosed
# later even though successful recoveries are now silent by design.
RESTART_LOG_FILE = "/var/log/vlc-watchdog-restarts.log"


def log_restart(outcome, reason_text):
    try:
        with open(RESTART_LOG_FILE, "a") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {outcome}: {reason_text}\n")
    except OSError:
        pass


def notify(event_type, message):
    """Best-effort iMessage notification via GitHub repository_dispatch.
    Never lets a notification failure affect the watchdog's own restart
    logic -- this is purely "and also tell someone", not load-bearing."""
    try:
        with open(GITHUB_TOKEN_FILE) as f:
            token = f.read().strip()
    except OSError:
        return  # no token provisioned -- notifications are opt-in

    now = time.time()
    try:
        with open(LAST_NOTIFY_FILE) as f:
            last = float(f.read().strip())
    except (OSError, ValueError):
        last = 0
    if now - last < NOTIFY_COOLDOWN_SECONDS:
        return
    try:
        with open(LAST_NOTIFY_FILE, "w") as f:
            f.write(str(now))
    except OSError:
        pass

    body = json.dumps(
        {
            "event_type": event_type,
            "client_payload": {"host": platform.node(), "message": message},
        }
    ).encode()
    req = urllib.request.Request(
        f"https://api.github.com/repos/{GITHUB_REPO}/dispatches",
        data=body,
        headers={
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=10)
    except (urllib.error.URLError, OSError):
        pass


def get_playback_time():
    """VLC's playback clock, in seconds, via its CLI interface. VLC sends a
    banner before the actual command response, and that banner alone
    contains newlines -- stopping at the first newline is racy, so keep
    reading (bounded) until a line that's purely digits shows up."""
    try:
        with socket.create_connection((HOST, PORT), timeout=5) as s:
            s.sendall(b"get_time\n")
            s.settimeout(2)
            data = b""
            while len(data) < 4096:
                for line in data.decode(errors="ignore").splitlines():
                    line = line.strip().lstrip(">").strip()
                    if line.isdigit():
                        return int(line)
                try:
                    chunk = s.recv(256)
                except socket.timeout:
                    break
                if not chunk:
                    break
                data += chunk
    except OSError:
        pass
    return None


def service_uptime_seconds():
    try:
        result = subprocess.run(
            ["systemctl", "show", SERVICE, "-p", "ActiveEnterTimestampMonotonic", "--value"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        active_since_us = int(result.stdout.strip())
    except (subprocess.SubprocessError, OSError, ValueError):
        return None
    try:
        with open("/proc/uptime") as f:
            now_s = float(f.read().split()[0])
    except OSError:
        return None
    return now_s - (active_since_us / 1_000_000)


def get_vlc_pid():
    result = subprocess.run(["pgrep", "-x", "vlc"], capture_output=True, text=True)
    pids = result.stdout.split()
    return int(pids[0]) if pids else None


def get_cpu_ticks(pid):
    """Total CPU time (utime+stime, in clock ticks) for the whole process --
    reading /proc/<tgid>/stat (as opposed to /proc/<tgid>/task/<tid>/stat
    for one specific thread) reports the sum across all of its threads."""
    try:
        with open(f"/proc/{pid}/stat") as f:
            content = f.read()
    except OSError:
        return None
    # comm field can itself contain spaces/parens, so split after the last ')'
    rest = content[content.rfind(")") + 2 :]
    fields = rest.split()
    try:
        return int(fields[11]) + int(fields[12])  # utime + stime
    except (IndexError, ValueError):
        return None


def get_recvq_bytes():
    """Bytes sitting unread in the RTMP socket's receive buffer."""
    try:
        result = subprocess.run(
            ["ss", "-tn", "state", "established", RTMP_DPORT_FILTER],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (subprocess.SubprocessError, OSError):
        return None
    lines = [
        l for l in result.stdout.splitlines() if l.strip() and not l.startswith("Recv-Q")
    ]
    if not lines:
        return None
    try:
        return int(lines[0].split()[0])
    except (IndexError, ValueError):
        return None


def get_vlc_plane_fb():
    """DRM framebuffer ID currently bound to the plane VLC is presenting
    to, parsed out of debugfs's state dump. Blocks are separated by
    "plane[N]: plane-M" headers; find the block VLC owns and pull its
    fb= line back out, rather than assuming a fixed plane index (which
    can shift across restarts)."""
    try:
        with open(DRM_STATE_FILE) as f:
            content = f.read()
    except OSError:
        return None
    for block in content.split("plane["):
        if "allocated by = vlc" not in block:
            continue
        for line in block.splitlines():
            line = line.strip()
            if line.startswith("fb="):
                try:
                    return int(line.split("=", 1)[1])
                except ValueError:
                    return None
    return None


def is_display_frozen():
    """True only if the plane's framebuffer ID never changes across a
    whole burst of closely-spaced samples -- see the module-level comment
    on FB_SAMPLE_COUNT for why a single 60s-apart comparison isn't
    trustworthy on its own. Returns None (inconclusive) rather than True
    if debugfs isn't readable or VLC doesn't currently own a plane, so a
    missing signal never masquerades as "frozen"."""
    samples = []
    for i in range(FB_SAMPLE_COUNT):
        fb = get_vlc_plane_fb()
        if fb is None:
            return None
        samples.append(fb)
        if i < FB_SAMPLE_COUNT - 1:
            time.sleep(FB_SAMPLE_INTERVAL_SECONDS)
    return len(set(samples)) == 1


def read_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def write_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


def main():
    uptime = service_uptime_seconds()
    if uptime is not None and uptime < STARTUP_GRACE_SECONDS:
        sys.exit(0)  # too soon after a (re)start to judge anything yet

    cur_time = get_playback_time()
    if cur_time is None:
        # Can't reach VLC's CLI -- either it's still starting up, or the
        # process is actually down, which Restart=always already handles.
        # Nothing for the watchdog to do this cycle.
        sys.exit(0)

    pid = get_vlc_pid()
    cur_cpu = get_cpu_ticks(pid) if pid else None
    cur_recvq = get_recvq_bytes()

    prev = read_state()
    prev_time = prev.get("time")
    prev_cpu = prev.get("cpu")
    prev_pid = prev.get("pid")
    prev_recvq = prev.get("recvq")
    count = prev.get("count", 0)

    reasons = []

    if prev_time is not None and cur_time == prev_time:
        reasons.append(f"playback clock stuck at {cur_time}s")

    # /proc/<pid>/stat's utime+stime is a per-process counter that starts
    # back near zero for a fresh process -- comparing it against the
    # previous (now-dead) process's much higher count after a restart
    # produces a meaningless, often negative "delta" that would otherwise
    # get misread as "no work happening". Only compare when it's actually
    # the same process both times.
    if (
        prev_cpu is not None
        and cur_cpu is not None
        and prev_pid is not None
        and pid is not None
        and prev_pid == pid
    ):
        delta = cur_cpu - prev_cpu
        if delta < MIN_CPU_TICKS_PER_CHECK:
            reasons.append(f"vlc process used almost no CPU this cycle ({delta} ticks)")

    frozen = is_display_frozen()
    if frozen:
        reasons.append("DRM plane framebuffer not changing across a sample burst -- picture isn't actually updating on screen")

    # Recv-Q is recorded for diagnostics but intentionally not a trigger --
    # see the module docstring for why.
    count = count + 1 if reasons else 0

    write_state(
        {
            "time": cur_time,
            "cpu": cur_cpu,
            "pid": pid,
            "recvq": cur_recvq,
            "count": count,
            "last_reasons": reasons,
        }
    )

    if count >= STALE_THRESHOLD:
        subprocess.run(["systemctl", "restart", SERVICE])
        write_state({"time": cur_time, "cpu": cur_cpu, "pid": pid, "recvq": cur_recvq, "count": 0})

        time.sleep(5)
        result = subprocess.run(
            ["systemctl", "is-active", SERVICE], capture_output=True, text=True
        )
        host = platform.node()
        reason_text = "; ".join(reasons) if reasons else "playback appeared stalled"
        if cur_recvq is not None:
            reason_text += f" [RTMP recv-q at time of restart: {cur_recvq} bytes]"
        # Only notify when the display FAILS to recover -- a successful
        # auto-restart is exactly the watchdog doing its job silently, not
        # something that needs a text. Every restart is still logged
        # locally either way, so frequency can be diagnosed later.
        if result.stdout.strip() == "active":
            log_restart("recovered", reason_text)
        else:
            log_restart("FAILED", reason_text)
            notify(
                "rtmp_watchdog_restart_failed",
                f"{SERVICE} on {host} froze ({reason_text}) and the automatic "
                f"restart FAILED (status: {result.stdout.strip() or 'unknown'}). "
                f"Needs manual attention.",
            )


if __name__ == "__main__":
    main()
