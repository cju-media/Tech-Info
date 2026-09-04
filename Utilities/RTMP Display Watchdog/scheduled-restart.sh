#!/bin/bash
# Proactive restart of content-display.service every 10 min, logging memory
# state first. This is a blunt preventative measure for the unresolved
# 2026-09-04 hallway freeze (every watchdog signal read healthy throughout;
# a memory leak was raised as a candidate theory but never confirmed) --
# see rtmp-display-watchdog-overview in Claude's project memory, or the
# README in Tech-Info's Utilities/RTMP Display Watchdog/.
LOG_FILE="/var/log/vlc-watchdog-restarts.log"
TS="$(date '+%Y-%m-%d %H:%M:%S')"

MEM="$(free -h | tr '\n' ' | ')"
RSS="$(ps -o rss=,etime= -C vlc,ffplay 2>/dev/null | tr -s ' ' | tr '\n' ' | ')"

echo "$TS scheduled-restart: mem=[$MEM] player=[$RSS]" >> "$LOG_FILE"
systemctl restart content-display.service
