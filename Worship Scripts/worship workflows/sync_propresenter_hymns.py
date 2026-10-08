#!/usr/bin/env python3
"""Make the hymn decks in ProPresenter match the Order of Worship, and put them in the playlist.

update_service_titles.py writes, for each hymn, its title (hymn-of-gathering.txt,
hymn-for-journey.txt) and its words as printed in the OW (hymn-of-gathering-lyrics.txt, ...) to
the Drive-synced Worship Titles folder. For each hymn this:

  1. finds its deck in Libraries/Hymns by title, ignoring punctuation and case; if several match
     ("Wake Now My Senses", "Wake, Now My Senses"), the one whose words are closest to the OW;
  2. rewrites the deck's slides from the OW if the words differ, or makes a new deck (styled like
     TEMPLATE) if there is none: one slide per verse, its lines joined in pairs onto two lines;
  3. points the hymn slots of the Sunday playlists (PLAYLIST_NAMES) at it: the first Hymns-library item is the
     Hymn of Gathering, the second the Hymn for the Journey.

Every file is backed up to BACKUP_DIR before it changes. Like sync_propresenter_prelude.py it edits
ProPresenter's files directly and can run while ProPresenter is open: the operator quits and
reopens ProPresenter to load the new slides.

  python3 sync_propresenter_hymns.py --dry-run
  python3 sync_propresenter_hymns.py --skip-if-running   # for a scheduled job
"""
import argparse
import difflib
import glob
import os
import re
import sys
import unicodedata
import urllib.parse

import hymn_lyrics
from propresenter_pb import (CUE_FIELD, GROUP_FIELD, UUID_RE, cue_uuid, field, fields, identifier,
                             new_uuid, propresenter_running, transform, write_with_backup)

HOME = os.path.expanduser("~")
PROPRESENTER = os.path.join(HOME, "Documents/ProPresenter")
HYMNS_DIR = "Libraries/Hymns"
PLAYLISTS_FILE = "Playlists/Library"
PLAYLIST_NAMES = ("Sunday Service", "Communion Sunday Service")
TEMPLATE = "Creator God You Gave Us Life.pro"     # the look for new decks (any hymn deck works)
BACKUP_DIR = os.path.join(HOME, "Documents/ProPresenter Sync Backups")
TITLES_GLOB = os.path.join(HOME, "Library/CloudStorage/GoogleDrive-*/My Drive/Worship Titles")
HYMN_KEYS = ("hymn-of-gathering", "hymn-for-journey")   # in playlist order

SLIDE_CHARS = 170   # about two lines' worth of words on a slide
KEEP_CAPS = {"i", "i'm", "i’m", "i'll", "i’ll", "i've", "i’ve", "god", "god's", "god’s", "christ",
             "christ's", "christ’s", "jesus", "lord", "spirit", "o"}


# ---------------------------------------------------------------- words

def normalize(text):
    """Lowercase words only: for comparing titles and lyrics regardless of punctuation."""
    text = unicodedata.normalize("NFKD", text).replace("’", "'").replace("‘", "'")
    text = "".join(c for c in text if not unicodedata.combining(c)).casefold()
    return " ".join(re.sub(r"[^a-z0-9']+", " ", text).replace("'", "").split())


def similarity(a, b):
    return difflib.SequenceMatcher(None, normalize(a).split(), normalize(b).split()).ratio()


def join_lines(lines):
    """OW lines run together into one slide line (a line continuing a sentence loses its capital)."""
    out = lines[0]
    for line in lines[1:]:
        word = line.split(" ", 1)[0]
        sentence_ended = out.rstrip("\"”’'). ").endswith(("!", "?")) or \
            out.rstrip("\"”’') ").endswith(".")
        if not sentence_ended and word.casefold().strip(",.;:!?\"“”") not in KEEP_CAPS:
            line = line[:1].lower() + line[1:]
        out += " " + line
    return out


def split_even(lines, k):
    """`lines` cut into k runs of about the same length (in characters), in order."""
    k = max(1, min(k, len(lines)))
    total = sum(len(l) for l in lines)
    groups, current, done = [], [], 0
    for i, line in enumerate(lines):
        current.append(line)
        done += len(line)
        left_lines, left_groups = len(lines) - i - 1, k - len(groups) - 1
        # close this run once it reaches its share, keeping a line for every run still to come
        if left_groups and (done >= total * (len(groups) + 1) / k - len(line) / 2
                            or left_lines == left_groups):
            groups.append(current)
            current = []
    groups.append(current)
    return [g for g in groups if g]


def slides_from_verses(verses):
    """Each verse spread evenly over slides of two lines, about SLIDE_CHARS of words a slide."""
    slides = []
    for verse in verses:
        n = max(1, round(sum(len(l) for l in verse) / SLIDE_CHARS))
        for chunk in split_even(verse, n):
            slides.append("\n".join(join_lines(half) for half in split_even(chunk, 2)))
    return slides


# ---------------------------------------------------------------- RTF

def rtf_text(rtf):
    """The visible text of a Cocoa RTF blob."""
    s = rtf.decode("utf-8", "replace")
    out, i, depth, skip_below = [], 0, 0, None     # skip_below: inside a header group
    while i < len(s):
        c = s[i]
        if c == "{":
            depth += 1
            if skip_below is None and re.match(r"\{(\\\*|\\fonttbl|\\colortbl)", s[i:]):
                skip_below = depth
            i += 1
        elif c == "}":
            if skip_below == depth:
                skip_below = None
            depth -= 1
            i += 1
        elif c == "\\":
            nxt = s[i + 1:i + 2]
            m = re.match(r"\\([a-zA-Z]+)(-?\d+)? ?", s[i:])
            if nxt in ("\\", "{", "}"):
                ch, i = nxt, i + 2
            elif nxt == "\n":
                ch, i = "\n", i + 2
            elif nxt == "'":
                ch, i = bytes([int(s[i + 2:i + 4], 16)]).decode("cp1252"), i + 4
            elif m:
                ch, i = (chr(int(m.group(2)) % 65536) if m.group(1) == "u" and m.group(2) else ""), \
                    i + m.end()
            else:
                ch, i = "", i + 2
            if skip_below is None:
                out.append(ch)
        else:
            if skip_below is None and c not in "\r\n":
                out.append(c)
            i += 1
    return "".join(out).strip()


def rtf_escape(text):
    out = []
    for c in text:
        if c in "\\{}":
            out.append("\\" + c)
        elif c == "\n":
            out.append("\\\n")
        elif ord(c) > 127:
            out.append("\\uc0\\u%d " % ord(c))
        else:
            out.append(c)
    return "".join(out)


def text_start(rtf):
    """Index where the text begins: after the header groups and the last paragraph's formatting."""
    s = rtf.decode("utf-8", "replace")
    i = s.rfind("\\pard")
    if i < 0:
        i = 0
    while i < len(s):
        c = s[i]
        if c == "\\":
            m = re.match(r"\\([a-zA-Z]+)(-?\d+)? ?", s[i:])
            if not m:
                break                   # an escaped character: that's text
            i += m.end()
        elif c in "{}\n\r":
            i += 1
        else:
            break
    return len(s[:i].encode("utf-8"))


def set_rtf_text(rtf, text):
    """The same RTF with its text replaced, in the formatting the text started with."""
    return rtf[:text_start(rtf)] + rtf_escape(text).encode("utf-8") + b"}"


def rtfs(msg):
    found = []

    def collect(payload):
        if payload.startswith(b"{\\rtf1"):
            found.append(payload)
            return payload
        return None
    transform(msg, collect)
    return found


def slide_text(cue):
    """The slide's words: its RTF with the most text."""
    texts = [rtf_text(r) for r in rtfs(cue)]
    return max(texts, key=len) if texts else ""


def set_slide_text(cue, text):
    target = max(rtfs(cue), key=lambda r: len(rtf_text(r)))
    return transform(cue, lambda p: set_rtf_text(p, text) if p == target else None)


# ---------------------------------------------------------------- decks

def cues_in_order(data):
    """[(cue uuid, cue bytes)] in slide order (cue groups, top to bottom)."""
    cues = {cue_uuid(p): p for n, _, p in fields(data) if n == CUE_FIELD}
    order = []
    for n, _, p in fields(data):
        if n == GROUP_FIELD:
            order += [identifier(x) for m, _, x in fields(p) if m == 2]
    return [(u, cues[u]) for u in order if u in cues]


def deck_words(data):
    return "\n".join(slide_text(c) for _, c in cues_in_order(data))


def rebuild_deck(data, slides, name=None):
    """The deck with its lyric slides replaced by `slides`, each a copy of its first lyric slide.
    With `name`, it's a new deck: new UUIDs for the presentation and its groups, and that name."""
    ordered = cues_in_order(data)
    lyric = [(u, c) for u, c in ordered if slide_text(c)]
    if not lyric:
        raise ValueError("no slide with text to copy")
    proto = lyric[0][1]
    lyric_ids = {u for u, _ in lyric}

    # IDs the slide shares with other slides point outside it (a theme, media): keep those.
    counts = {}
    for _, c in ordered:
        for u in set(UUID_RE.findall(c)):
            counts[u] = counts.get(u, 0) + 1
    own = [u for u in set(UUID_RE.findall(proto)) if counts.get(u, 0) == 1]

    new_cues, new_ids = [], []
    for text in slides:
        c = set_slide_text(proto, text)
        for u in own:
            c = c.replace(u, new_uuid().encode())
        new_cues.append(c)
        new_ids.append(cue_uuid(c))

    out, placed_cues, placed_ids = [], False, False
    for n, raw, p in fields(data):
        if n == CUE_FIELD and cue_uuid(p) in lyric_ids:
            if not placed_cues:
                out += [field(CUE_FIELD, c) for c in new_cues]
                placed_cues = True
            continue
        if n == GROUP_FIELD:
            parts = []
            for m, r, x in fields(p):
                if m == 2 and identifier(x) in lyric_ids:
                    if not placed_ids:
                        parts += [field(2, field(1, u.encode())) for u in new_ids]
                        placed_ids = True
                    continue
                parts.append(r)
            raw = field(GROUP_FIELD, b"".join(parts))
        if name is not None and n == 3:
            raw = field(3, name.encode())
        out.append(raw)
    new = b"".join(out)

    if name is not None:        # a new deck mustn't share the template's identity
        ids = [identifier(p) for n, _, p in fields(new) if n == 2]
        for n, _, p in fields(new):
            if n == GROUP_FIELD:
                header = next((x for m, _, x in fields(p) if m == 1), b"")
                ids += [u.decode() for u in UUID_RE.findall(header)]
        for u in ids:
            if u:
                new = new.replace(u.encode(), new_uuid().encode())
    return new


def deck_file_name(title):
    name = re.sub(r'[/:\\]', "-", title).strip().strip("\"“”'‘’").strip()
    return name + ".pro"


def find_decks(hymns_dir, title):
    want = normalize(title)
    return [p for p in sorted(glob.glob(os.path.join(hymns_dir, "*.pro")))
            if normalize(os.path.splitext(os.path.basename(p))[0]) == want]


def pick_deck(paths, lyrics):
    """The deck whose words are closest to the OW's (newest if there are no lyrics to compare)."""
    if not lyrics:
        return max(paths, key=os.path.getmtime)

    def score(p):
        with open(p, "rb") as f:
            return similarity(deck_words(f.read()), lyrics), os.path.getmtime(p)
    return max(paths, key=score)


# ---------------------------------------------------------------- playlist

def file_url(path):
    return "file://" + urllib.parse.quote(path, safe="/,;:@&=+$!'()*-._~")


def playlist_name(pl):
    return next((p.decode() for n, _, p in fields(pl) if n == 2), "")


def item_path(item):
    """The deck an item plays, relative to the ProPresenter folder."""
    rel = []

    def grab(p):
        if p.startswith(b"Libraries/"):
            rel.append(p.decode())
            return p
        return None
    transform(item, grab)
    return rel[0] if rel else None


def point_item(item, rel_path, root):
    """The item, playing the deck at rel_path instead (its own uuid kept)."""
    old_rel = item_path(item)
    new_url = file_url(os.path.join(root, rel_path)).encode()
    name = os.path.splitext(os.path.basename(rel_path))[0].encode()

    def swap(p):
        if p == old_rel.encode():
            return rel_path.encode()
        if p.startswith(b"file://") and \
                urllib.parse.unquote(p.decode("utf-8", "replace")).endswith("/" + old_rel):
            return new_url
        return None
    item = transform(item, swap)
    return b"".join(field(2, name) if n == 2 else raw for n, raw, _ in fields(item))


def update_playlists(data, slots, root, names=PLAYLIST_NAMES):
    """Point the hymn slots of the named playlists at `slots` ([rel path or None] in playlist
    order). Returns (new bytes, [(playlist, old deck, new deck)])."""
    changes = []

    def fix_items(pl_name, items_msg):
        hymn_no, out = 0, []
        for k, raw, item in fields(items_msg):
            rel = item_path(item) if k == 1 else None
            if rel and rel.startswith(HYMNS_DIR + "/"):
                want = slots[hymn_no] if hymn_no < len(slots) else None
                hymn_no += 1
                if want and want != rel:
                    changes.append((pl_name, rel, want))
                    raw = field(1, point_item(item, want, root))
            out.append(raw)
        return b"".join(out)

    def fix_playlist(pl):
        name = playlist_name(pl)
        out = []
        for n, raw, p in fields(pl):
            if n == 12:             # child playlists
                raw = field(12, b"".join(field(1, fix_playlist(c)) if k == 1 else r
                                         for k, r, c in fields(p)))
            elif n == 13 and name in names:
                raw = field(13, fix_items(name, p))
            out.append(raw)
        new = b"".join(out)
        return new if new != b"".join(r for _, r, _ in fields(pl)) else pl

    out = [field(3, fix_playlist(p)) if n == 3 else raw for n, raw, p in fields(data)]
    new = b"".join(out)
    return (new if changes else data), changes


# ---------------------------------------------------------------- main

def titles_dir():
    found = sorted(glob.glob(TITLES_GLOB))
    return found[0] if found else None


def read(folder, name):
    try:
        with open(os.path.join(folder, name), encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:
        return ""


def sync_hymn(key, title, lyrics_text, hymns_dir, template_path, dry_run, backup_dir):
    """Make sure the deck for one hymn exists and matches the OW. Returns its path (or None)."""
    verses = hymn_lyrics.from_file_text(lyrics_text) if lyrics_text else []
    lyrics = "\n".join("\n".join(v) for v in verses)
    matches = find_decks(hymns_dir, title)
    path = pick_deck(matches, lyrics) if matches else None
    label = "%s (%s)" % (title, key)

    if not verses:
        print("  %s: no lyrics in the OW; %s" % (label, "using %s" % os.path.basename(path)
                                                  if path else "no deck, skipped"))
        return path

    if path:
        with open(path, "rb") as f:
            data = f.read()
        if normalize(deck_words(data)) == normalize(lyrics):
            print("  %s: %s matches the OW" % (label, os.path.basename(path)))
            return path
        note = "picked from %d matches; " % len(matches) if len(matches) > 1 else ""
        print("  %s: %s%s differs from the OW (%.0f%% the same), %s" % (
            label, note, os.path.basename(path), 100 * similarity(deck_words(data), lyrics),
            "would rewrite" if dry_run else "rewriting"))
        new = rebuild_deck(data, slides_from_verses(verses))
    else:
        path = os.path.join(hymns_dir, deck_file_name(title))
        print("  %s: no deck, %s %s" % (label, "would make" if dry_run else "making",
                                        os.path.basename(path)))
        with open(template_path, "rb") as f:
            new = rebuild_deck(f.read(), slides_from_verses(verses),
                               name=os.path.splitext(os.path.basename(path))[0])
    if not dry_run:
        write_with_backup(path, new, backup_dir)
    return path


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--propresenter", default=PROPRESENTER, help="the ProPresenter documents folder")
    ap.add_argument("--titles", help="the Worship Titles folder (default: the Drive-synced one)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--skip-if-running", action="store_true",
                    help="do nothing while ProPresenter is open (it would overwrite the edit)")
    ap.add_argument("--backup-dir", default=BACKUP_DIR)
    a = ap.parse_args(argv)

    if a.skip_if_running and propresenter_running():
        print("ProPresenter is running; leaving the hymns alone.")
        return

    folder = a.titles or titles_dir()
    if not folder:
        sys.exit("Can't find the Google Drive 'Worship Titles' folder; pass --titles.")
    hymns_dir = os.path.join(a.propresenter, HYMNS_DIR)
    template = os.path.join(hymns_dir, TEMPLATE)

    print("Hymns in the OW:")
    slots = []
    for key in HYMN_KEYS:
        title = read(folder, key + ".txt")
        if not title:
            print("  %s: no title in the OW, skipped" % key)
            slots.append(None)
            continue
        path = sync_hymn(key, title, read(folder, key + "-lyrics.txt"), hymns_dir, template,
                         a.dry_run, a.backup_dir)
        slots.append(os.path.relpath(path, a.propresenter) if path else None)

    playlist_path = os.path.join(a.propresenter, PLAYLISTS_FILE)
    with open(playlist_path, "rb") as f:
        data = f.read()
    new, changes = update_playlists(data, slots, a.propresenter)
    if not changes:
        print("Playlist %s: hymns already in place" % " / ".join(PLAYLIST_NAMES))
    for pl, old, want in changes:
        print("Playlist %s: %s %s -> %s" % (pl, "would swap" if a.dry_run else "swapped",
                                            os.path.basename(old), os.path.basename(want)))
    if changes and not a.dry_run:
        write_with_backup(playlist_path, new, a.backup_dir)


if __name__ == "__main__":
    main()
