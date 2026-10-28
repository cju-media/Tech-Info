#!/usr/bin/env python3
"""Keep the Musical Gift deck and the Sunday playlist in step with the Order of Worship.

update_service_titles.py writes one pair of files per Musical Gift piece to the Drive-synced
Worship Titles folder: musical-gift-title-composer.txt / musical-gift-group.txt for the first,
then ...1.txt, ...2.txt for the next (all blank in weeks without a Musical Gift). The deck has
one slide per piece, its text linked to that piece's pair. This:

  1. counts the pieces (the highest-numbered non-empty musical-gift-title-composer file);
  2. adds a slide for any piece the deck has no slide for yet, a copy of the first piece's slide
     linked to the new piece's files, and enables the slides up to the count, disabling the rest;
  3. puts the deck in the Sunday playlist (after the sermon deck) in weeks with a Musical Gift,
     and takes it out in weeks without one.

Like the other sync scripts it edits ProPresenter's files directly, backing each one up first,
and can run while ProPresenter is open (quit and reopen to load the change).

  python3 sync_propresenter_musical_gift.py --dry-run
"""
import argparse
import os
import re
import sys

from propresenter_pb import (CUE_FIELD, GROUP_FIELD, copy_slide, cue_uuid, field, fields,
                             identifier, new_uuid, outside_uuids, propresenter_running, transform,
                             write_with_backup)
from sync_propresenter_hymns import (BACKUP_DIR, PROPRESENTER, PLAYLISTS_FILE, cues_in_order,
                                     item_path, playlist_name, point_item, shared_uuids, titles_dir)
from sync_propresenter_prelude import set_cue_enabled

DECK = "Libraries/Service Elements/Musical Gift.pro"
PLAYLIST_NAMES = ("Sunday Service",)
AFTER = "Sermon"        # the deck goes right after the first playlist item whose name has this

SLOT_RE = re.compile(rb"Worship%20Titles/musical-gift-title-composer(\d*)\.txt")


# ---------------------------------------------------------------- pieces

def count_pieces(folder):
    """Pieces in the OW: 1 + the highest slot with a non-empty title (slot 0 is the unnumbered
    pair), so a blank in the middle never hides a later piece. 0 without a Musical Gift."""
    count = 0
    for name in os.listdir(folder):
        m = re.fullmatch(r"musical-gift-title-composer(\d*)\.txt", name)
        if not m:
            continue
        with open(os.path.join(folder, name), encoding="utf-8") as f:
            if f.read().strip():
                count = max(count, int(m.group(1) or 0) + 1)
    return count


def slot(cue):
    """Which piece a slide shows (0 for the unnumbered pair), or None."""
    found = {int(n or 0) for n in SLOT_RE.findall(cue)}
    return found.pop() if len(found) == 1 else None


def relink(cue, n):
    """The slide, showing piece n's files instead of the first piece's."""
    suffix = b"%d" % n if n else b""

    def swap(p):
        if p.startswith(b"file://") and b"Worship%20Titles/musical-gift-" in p:
            return re.sub(rb"/musical-gift-(title-composer|group)\d*\.txt",
                          lambda m: b"/musical-gift-" + m.group(1) + suffix + b".txt", p)
        return None
    return transform(cue, swap)


# ---------------------------------------------------------------- deck

def insert_after(data, anchor, new_cues):
    """The deck with new_cues placed right after the slide `anchor`, in the file and in its group."""
    new_ids = [cue_uuid(c) for c in new_cues]
    out = []
    for n, raw, p in fields(data):
        if n == CUE_FIELD and cue_uuid(p) == anchor:
            out += [raw] + [field(CUE_FIELD, c) for c in new_cues]
            continue
        if n == GROUP_FIELD and any(m == 2 and identifier(x) == anchor for m, _, x in fields(p)):
            parts = []
            for m, r, x in fields(p):
                parts.append(r)
                if m == 2 and identifier(x) == anchor:
                    parts += [field(2, field(1, u.encode())) for u in new_ids]
            raw = field(GROUP_FIELD, b"".join(parts))
        out.append(raw)
    return b"".join(out)


def sync_deck(data, count, keep=frozenset()):
    """Return (new bytes, slides added, {slot: enabled}) with a slide for every piece up to
    `count`, those enabled and any further ones disabled."""
    ordered = cues_in_order(data)
    slots = {}
    for u, c in ordered:
        s = slot(c)
        if s is not None:
            slots.setdefault(s, c)
    if 0 not in slots:
        raise ValueError("no Musical Gift slide linked to musical-gift-title-composer.txt to copy")

    missing = [k for k in range(count) if k not in slots]
    if missing:
        shared = shared_uuids(ordered)
        last = [u for u, c in ordered if slot(c) is not None][-1]
        new = [copy_slide(relink(slots[0], k), keep, shared) for k in missing]
        data = insert_after(data, last, new)

    states, out = {}, []
    for n, raw, p in fields(data):
        if n == CUE_FIELD:
            s = slot(p)
            if s is not None:
                states[s] = s < count
                p2 = set_cue_enabled(p, s < count)
                if p2 != p:
                    raw = field(CUE_FIELD, p2)
        out.append(raw)
    return b"".join(out), len(missing), states


# ---------------------------------------------------------------- playlist

def update_playlists(data, present, root, names=PLAYLIST_NAMES, deck=DECK, after=AFTER):
    """Add the deck to (present) or take it out of (not present) the named playlists.
    Returns (new bytes, [(playlist, "added" | "removed")])."""
    changes = []

    def fix_items(pl_name, items_msg):
        items = [(k, r, it) for k, r, it in fields(items_msg)]
        has = [i for i, (k, _, it) in enumerate(items) if k == 1 and item_path(it) == deck]
        if present and not has:
            anchor = next((i for i, (k, _, it) in enumerate(items) if k == 1 and item_path(it) and
                           after.lower() in os.path.basename(item_path(it)).lower()), None)
            template = items[anchor][2] if anchor is not None else \
                next((it for k, _, it in items if k == 1 and item_path(it)), None)
            if template is None:
                return items_msg
            new = b"".join(field(1, field(1, new_uuid().encode())) if n == 1 else raw
                           for n, raw, _ in fields(template))
            new = point_item(new, deck, root)
            at = anchor + 1 if anchor is not None else len(items)
            items.insert(at, (1, field(1, new), new))
            changes.append((pl_name, "added"))
        elif not present and has:
            items = [x for i, x in enumerate(items) if i not in has]
            changes.append((pl_name, "removed"))
        return b"".join(r for _, r, _ in items)

    def fix_playlist(pl):
        name = playlist_name(pl)
        out = []
        for n, raw, p in fields(pl):
            if n == 12:
                raw = field(12, b"".join(field(1, fix_playlist(c)) if k == 1 else r
                                         for k, r, c in fields(p)))
            elif n == 13 and name in names:
                raw = field(13, fix_items(name, p))
            out.append(raw)
        return b"".join(out)

    new = b"".join(field(3, fix_playlist(p)) if n == 3 else raw for n, raw, p in fields(data))
    return (new if changes else data), changes


# ---------------------------------------------------------------- main

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--propresenter", default=PROPRESENTER, help="the ProPresenter documents folder")
    ap.add_argument("--titles", help="the Worship Titles folder (default: the Drive-synced one)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--skip-if-running", action="store_true",
                    help="do nothing while ProPresenter is open")
    ap.add_argument("--backup-dir", default=BACKUP_DIR)
    a = ap.parse_args(argv)

    if a.skip_if_running and propresenter_running():
        print("ProPresenter is running; leaving the Musical Gift alone.")
        return

    folder = a.titles or titles_dir()
    if not folder:
        sys.exit("Can't find the Google Drive 'Worship Titles' folder; pass --titles.")
    count = count_pieces(folder)
    print("Musical Gift pieces in the OW: %d" % count)

    deck_path = os.path.join(a.propresenter, DECK)
    if count:
        with open(deck_path, "rb") as f:
            data = f.read()
        new, added, states = sync_deck(data, count, outside_uuids(a.propresenter))
        on = sorted(s + 1 for s, e in states.items() if e)
        off = sorted(s + 1 for s, e in states.items() if not e)
        state = "unchanged" if new == data else ("would change" if a.dry_run else "updated")
        print("  %s: %s (%sslides on %s, off %s)" % (os.path.basename(deck_path), state,
                                                    "%d added; " % added if added else "", on, off))
        if new != data and not a.dry_run:
            write_with_backup(deck_path, new, a.backup_dir)

    playlist_path = os.path.join(a.propresenter, PLAYLISTS_FILE)
    with open(playlist_path, "rb") as f:
        data = f.read()
    new, changes = update_playlists(data, count > 0, a.propresenter)
    if not changes:
        print("Playlist %s: Musical Gift already %s" % (" / ".join(PLAYLIST_NAMES),
                                                       "in place" if count else "out"))
    for pl, what in changes:
        print("Playlist %s: %s Musical Gift" % (pl, ("would be " if a.dry_run else "") + what))
    if changes and not a.dry_run:
        write_with_backup(playlist_path, new, a.backup_dir)


if __name__ == "__main__":
    main()
