#!/usr/bin/env python3
"""Watchdog for content-display.service.

VLC's decode/render pipeline can wedge internally (e.g. a v4l2m2m hardware
decoder stall, or a WiFi driver roaming-event crash) without the process
actually exiting, so systemd's Restart=always never fires -- the process is
alive, just not making progress. Worse, VLC's own playback clock (get_time)
can keep advancing even while the actual picture is frozen, if the demux/
network thread is still alive while only the render path is wedged -- so
get_time alone is not a reliable liveness signal on its own.

This polls two independent liveness signals every cycle:
  1. get_time via VLC's CLI interface (is the playback clock advancing?)
  2. Total process CPU time (is *any* thread actually doing work?)

If EITHER looks stuck for several consecutive checks, the service is
force-restarted, its recovery is verified, and a notification is sent.

A third signal -- the RTMP socket's Recv-Q (bytes piling up unread) -- is
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

# iMessage notifications, via the same repository_dispatch -> self-hosted
# macOS runner -> osascript pattern used elsewhere in Tech-Info.
GITHUB_TOKEN_FILE = "/etc/vlc-watchdog/github-token"
GITHUB_REPO = "cju-media/Tech-Info"
LAST_NOTIFY_FILE = "/var/lib/vlc-watchdog/last-notify"
NOTIFY_COOLDOWN_SECONDS = 15 * 60  # avoid texting every cycle during a prolonged outage


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
    prev_recvq = prev.get("recvq")
    count = prev.get("count", 0)

    reasons = []

    if prev_time is not None and cur_time == prev_time:
        reasons.append(f"playback clock stuck at {cur_time}s")

    if prev_cpu is not None and cur_cpu is not None:
        delta = cur_cpu - prev_cpu
        if delta < MIN_CPU_TICKS_PER_CHECK:
            reasons.append(f"vlc process used almost no CPU this cycle ({delta} ticks)")

    # Recv-Q is recorded for diagnostics but intentionally not a trigger --
    # see the module docstring for why.
    count = count + 1 if reasons else 0

    write_state(
        {
            "time": cur_time,
            "cpu": cur_cpu,
            "recvq": cur_recvq,
            "count": count,
            "last_reasons": reasons,
        }
    )

    if count >= STALE_THRESHOLD:
        subprocess.run(["systemctl", "restart", SERVICE])
        write_state({"time": cur_time, "cpu": cur_cpu, "recvq": cur_recvq, "count": 0})

        time.sleep(5)
        result = subprocess.run(
            ["systemctl", "is-active", SERVICE], capture_output=True, text=True
        )
        host = platform.node()
        reason_text = "; ".join(reasons) if reasons else "playback appeared stalled"
        if cur_recvq is not None:
            reason_text += f" [RTMP recv-q at time of restart: {cur_recvq} bytes]"
        if result.stdout.strip() == "active":
            notify(
                "rtmp_watchdog_restart",
                f"{SERVICE} on {host} appeared frozen ({reason_text}, for "
                f"{STALE_THRESHOLD} consecutive checks) and was automatically restarted.",
            )
        else:
            notify(
                "rtmp_watchdog_restart_failed",
                f"{SERVICE} on {host} froze ({reason_text}) and the automatic "
                f"restart FAILED (status: {result.stdout.strip() or 'unknown'}). "
                f"Needs manual attention.",
            )


if __name__ == "__main__":
    main()
