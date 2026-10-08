"""Just enough protobuf to edit ProPresenter 7 files (.pro decks, Playlists/Library) in place.

There's no schema here: fields are read by number and every byte we don't change is kept exactly,
so ProPresenter sees the file as it wrote it apart from the edit. Field numbers used by the sync
scripts:

  Presentation (.pro):  2 uuid, 3 name, 12 cue group (1 header, 2 cue ids in slide order), 13 cue
  Cue (a slide):        1 uuid, 2 name, 10 actions (the slide; its text is RTF), 12 is_enabled
  Playlist file:        3 root playlist; a playlist is 1 uuid, 2 name, 12 child playlists (1 each),
                        13 items (1 each); an item is 1 uuid, 2 name, 4 the deck's file URL
"""
import os
import re
import shutil
import subprocess
import time
import uuid as _uuid

CUE_FIELD = 13
GROUP_FIELD = 12        # a cue group: its header, then the cues (field 2) in slide order
UUID_RE = re.compile(rb"[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}")


def read_varint(b, i):
    r = s = 0
    while True:
        c = b[i]
        i += 1
        r |= (c & 0x7F) << s
        s += 7
        if not c & 0x80:
            return r, i


def write_varint(n):
    out = bytearray()
    while True:
        c = n & 0x7F
        n >>= 7
        out.append(c | (0x80 if n else 0))
        if not n:
            return bytes(out)


def fields(b):
    """Split a protobuf message into (field_number, raw_bytes, payload) keeping the exact bytes."""
    i, out = 0, []
    while i < len(b):
        start = i
        key, i = read_varint(b, i)
        num, wire = key >> 3, key & 7
        if wire == 0:
            _, i = read_varint(b, i)
            payload = b[start:i]
        elif wire == 1:
            i += 8
            payload = b[start:i]
        elif wire == 5:
            i += 4
            payload = b[start:i]
        elif wire == 2:
            n, i = read_varint(b, i)
            i += n
            payload = b[i - n:i]
        else:
            raise ValueError("unsupported wire type %d" % wire)
        if i > len(b):
            raise ValueError("truncated message")
        out.append((num, b[start:i], payload))
    return out


def try_fields(b):
    try:
        return fields(b)
    except (ValueError, IndexError):
        return None


def field(num, payload):
    """Encode a length-delimited field."""
    return write_varint(num << 3 | 2) + write_varint(len(payload)) + payload


def identifier(msg):
    """The string in a UUID message ({1: "<uuid>"})."""
    for num, _, payload in fields(msg):
        if num == 1:
            return payload.decode()
    return None


def cue_uuid(cue):
    """A slide's UUID: its field 1 is a UUID message."""
    for num, _, payload in fields(cue):
        if num == 1:
            return identifier(payload)
    return None


def transform(msg, fn, depth=0):
    """Rewrite length-delimited payloads anywhere in `msg`: fn(payload) returns the new payload, or
    None to leave it (and look inside it). Subtrees with no change keep their exact bytes."""
    out, changed = [], False
    for num, raw, payload in fields(msg):
        if read_varint(raw, 0)[0] & 7 == 2:
            new = fn(payload)
            if new is None and depth < 40 and payload and try_fields(payload) is not None:
                new = transform(payload, fn, depth + 1)
                if new == payload:
                    new = None
            if new is not None:
                out.append(field(num, new))
                changed = True
                continue
        out.append(raw)
    return b"".join(out) if changed else msg


def new_uuid():
    return str(_uuid.uuid4()).upper()


def propresenter_running():
    return subprocess.run(["pgrep", "-xq", "ProPresenter"]).returncode == 0


def write_with_backup(path, data, backup_dir=None):
    """Back up `path` (next to it, or into backup_dir), then replace it atomically."""
    if os.path.exists(path):
        stamp = time.strftime("%Y%m%d-%H%M%S")
        if backup_dir:
            os.makedirs(backup_dir, exist_ok=True)
            backup = os.path.join(backup_dir, "%s.bak-%s" % (os.path.basename(path), stamp))
        else:
            backup = "%s.bak-%s" % (path, stamp)
        shutil.copy2(path, backup)
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)
