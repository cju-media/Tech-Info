#!/usr/bin/env python3
"""Reconnect VLC's stream input over its CLI interface, without ever
killing the process.

A full `systemctl restart content-display.service` tears down and
recreates VLC's DRM plane binding, which briefly exposes whatever's
underneath -- a blinking login prompt originally (fixed by masking
getty@tty1), and even with that masked, a black gap where the plane is
simply unbound (2026-09-04).

Reconnecting via the CLI interface instead keeps the process (and its
plane binding) alive the entire time, so there is no gap at all --
confirmed live, 2026-09-04.

IMPORTANT CAVEAT: this is for a *planned* reconnect (refreshing a stale
connection, swapping the source URL, routine testing) where the process
itself is known to be healthy. It is NOT a verified substitute for a full
process restart when the decode/render pipeline is actually wedged (the
failure mode vlc-watchdog.py exists to catch) -- if the vout/decode
threads are genuinely hung on a blocking call, tearing down the old input
via this same command channel could hang for the same reason a full
restart is needed in the first place. Do not use this in place of
`systemctl restart content-display.service` for real freeze recovery.
"""
import socket
import sys
import time

HOST = "127.0.0.1"
PORT = 4212
STREAM_URL = "rtmp://192.168.112.33/live/content-display"


def main():
    url = sys.argv[1] if len(sys.argv) > 1 else STREAM_URL
    with socket.create_connection((HOST, PORT), timeout=5) as s:
        s.sendall(b"clear\n")
        time.sleep(0.5)
        s.recv(500)
        s.sendall(f"add {url}\n".encode())
        time.sleep(1)
        s.recv(500)
    print(f"Reconnected to {url} without restarting the process.")


if __name__ == "__main__":
    main()
