#!/bin/bash
# Pure read-only memory snapshot every 10 min -- does NOT touch
# content-display.service. Building a dataset to confirm or rule out a
# memory-leak theory for the unresolved 2026-09-04 freeze (see
# rtmp-display-watchdog-overview in Claude's project memory, or the
# README in Tech-Info's Utilities/RTMP Display Watchdog/).
LOG_FILE="/var/log/vlc-memory-checks.log"
TS="$(date '+%Y-%m-%d %H:%M:%S')"

MEM="$(free -h | tr '\n' ' | ')"
RSS="$(ps -o rss=,etime= -C vlc,ffplay 2>/dev/null | tr -s ' ' | tr '\n' ' | ')"

echo "$TS mem=[$MEM] player=[$RSS]" >> "$LOG_FILE"
