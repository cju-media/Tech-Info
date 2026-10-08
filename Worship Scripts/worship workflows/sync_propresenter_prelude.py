#!/usr/bin/env python3
"""Enable/disable the prelude lower-third slides in ProPresenter to match the Order of Worship.

update_service_titles.py writes prelude1.txt .. prelude9.txt (blank when the OW has fewer pieces).
Each prelude deck in ProPresenter has one slide per piece, its text linked to those Drive-synced
files. This counts the non-empty prelude files and disables every slide past that count (and
re-enables the rest), so stepping through the deck lines up with the OW.

ProPresenter 7 has no API call for a slide's enabled flag, so this edits the .pro file itself
(a protobuf; a Cue's field 12 is is_enabled, absent = disabled). Only that one field changes.

  python3 sync_propresenter_prelude.py                 # every prelude deck in the library
  python3 sync_propresenter_prelude.py --dry-run
  python3 sync_propresenter_prelude.py --skip-if-running   # for a scheduled job
  python3 sync_propresenter_prelude.py --count 4 "Service Prelude-HoG-COMMUNION.pro"

It can run while ProPresenter is open: ProPresenter keeps showing the slides it loaded until it's
quit and reopened. If ProPresenter saves the same deck in the meantime, its save wins until the
next run puts the change back.
"""
import argparse
import glob
import os
import re
import shutil
import sys
import time

from propresenter_pb import (CUE_FIELD, GROUP_FIELD, cue_uuid, fields, identifier,  # noqa: F401
                             propresenter_running, read_varint, write_varint)

HOME = os.path.expanduser("~")
LIBRARY = os.path.join(HOME, "Documents/ProPresenter/Libraries/Service Elements")
TITLES_GLOB = os.path.join(HOME, "Library/CloudStorage/GoogleDrive-*/My Drive/Worship Titles")
DECK_GLOB = "*Prelude*.pro"

ENABLED_FIELD = 12
PRELUDE_RE = re.compile(rb"Worship%20Titles/prelude(\d+)\.txt")


def prelude_number(cue):
    """N if this slide shows prelude<N>.txt, else None."""
    nums = {int(n) for n in PRELUDE_RE.findall(cue)}
    return nums.pop() if len(nums) == 1 else None


def set_cue_enabled(cue, enabled):
    flag = write_varint(ENABLED_FIELD << 3) + b"\x01"
    out, placed = [], False
    for num, raw, _ in fields(cue):
        if num == ENABLED_FIELD:
            if enabled:
                out.append(flag)
            placed = True
            continue
        if enabled and not placed and num > ENABLED_FIELD:
            out.append(flag)        # keep protobuf's field order
            placed = True
        out.append(raw)
    if enabled and not placed:
        out.append(flag)
    return b"".join(out)


def slide_after(data, prelude_uuids):
    """{uuid of the slide right after a prelude slide: that prelude's uuid}, by slide order.

    A prelude slide followed by another prelude slide has no follower."""
    after = {}
    for num, _, payload in fields(data):
        if num != GROUP_FIELD:
            continue
        order = [identifier(p) for n, _, p in fields(payload) if n == 2]
        for here, nxt in zip(order, order[1:]):
            if here in prelude_uuids and nxt not in prelude_uuids:
                after.setdefault(nxt, here)
    return after


def sync_deck(data, count):
    """Return (new_bytes, {N: enabled}, followers) with prelude slides 1..count enabled and the rest
    disabled; the slide right after each prelude slide gets the same state. `followers` is how many
    of those were found."""
    prelude = {}
    for num, _, payload in fields(data):
        if num == CUE_FIELD:
            n = prelude_number(payload)
            if n is not None:
                prelude[cue_uuid(payload)] = n
    after = slide_after(data, set(prelude))

    out, seen = [], {}
    for num, raw, payload in fields(data):
        if num == CUE_FIELD:
            uuid = cue_uuid(payload)
            n = prelude.get(uuid, prelude.get(after.get(uuid)))
            if n is not None:
                payload = set_cue_enabled(payload, n <= count)
                if uuid in prelude:
                    seen[n] = n <= count
                raw = write_varint(CUE_FIELD << 3 | 2) + write_varint(len(payload)) + payload
        out.append(raw)
    return b"".join(out), seen, len(after)


def titles_dir():
    found = sorted(glob.glob(TITLES_GLOB))
    return found[0] if found else None


def count_pieces(folder):
    """Highest N with a non-empty prelude<N>.txt (so a gap never hides a later piece)."""
    count = 0
    for name in os.listdir(folder):
        m = re.fullmatch(r"prelude(\d+)\.txt", name)
        if not m:
            continue
        with open(os.path.join(folder, name), encoding="utf-8") as f:
            if f.read().strip():
                count = max(count, int(m.group(1)))
    return count


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("decks", nargs="*", help="deck file names/paths (default: every prelude deck)")
    ap.add_argument("--count", type=int, help="number of prelude pieces (default: read Worship Titles)")
    ap.add_argument("--library", default=LIBRARY)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--skip-if-running", action="store_true",
                    help="do nothing while ProPresenter is open (it would overwrite the edit)")
    a = ap.parse_args(argv)

    if a.skip_if_running and propresenter_running():
        print("ProPresenter is running; leaving the decks alone.")
        return

    count = a.count
    if count is None:
        folder = titles_dir()
        if not folder:
            sys.exit("Can't find the Google Drive 'Worship Titles' folder; pass --count.")
        count = count_pieces(folder)
    print("Prelude pieces in the OW: %d" % count)

    paths = [d if os.path.isabs(d) else os.path.join(a.library, d) for d in a.decks] or \
        sorted(glob.glob(os.path.join(a.library, DECK_GLOB)))
    for path in paths:
        with open(path, "rb") as f:
            data = f.read()
        new, seen, followers = sync_deck(data, count)
        if not seen:
            print("  %s: no prelude slides found, skipped" % os.path.basename(path))
            continue
        on = sorted(n for n, e in seen.items() if e)
        off = sorted(n for n, e in seen.items() if not e)
        state = "unchanged" if new == data else ("would change" if a.dry_run else "updated")
        print("  %s: %s (pieces on %s, off %s; %d follow-on slides)"
              % (os.path.basename(path), state, on, off, followers))
        if new != data and not a.dry_run:
            shutil.copy2(path, "%s.bak-%s" % (path, time.strftime("%Y%m%d-%H%M%S")))
            tmp = path + ".tmp"
            with open(tmp, "wb") as f:
                f.write(new)
            os.replace(tmp, path)


if __name__ == "__main__":
    main()
