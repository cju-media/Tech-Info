#!/bin/sh
# Copy the title-graphics scripts from the repo to the iCloud working copy.
# Leaves week-data.txt and panel-colors.png there alone.
set -e
cd "$(dirname "$0")"
DEST="$HOME/Library/Mobile Documents/com~apple~CloudDocs/FCCLA/Worship and Sermon Series/Scripts"
mkdir -p "$DEST"
cp UpdateWeek.jsx prepare_week.py GEMINI.md README.md "$DEST/"
echo "copied to $DEST"
