import os
import sys
import json
import datetime
import subprocess

# Companion to "Worship Scripts/worship workflows/upload_queue_to_drive.py"'s
# defer_stream_creation(): when a worship-service thumbnail is uploaded
# before this week's OW is posted, upload_queue_to_drive.py stashes it here
# instead of creating the YouTube stream with a stale or generic
# placeholder title/description. This script is chained off the same
# hourly title/description pipeline (see
# ".github/workflows/create_pending_youtube_stream.yml") and finishes the
# job as soon as both are actually ready for the stashed date.
#
# Runs from the repo root (see the workflow), matching
# upload_queue_to_drive.py's own path conventions -- not "Youtube
# Processing", unlike its sibling scripts here.

PENDING_STREAM_DIR = os.path.join("Youtube Processing", "pending_stream")

sys.path.insert(0, os.path.join("Worship Scripts", "worship workflows"))
import service_keys  # noqa: E402

# create_youtube_stream.py's exit codes.
EXIT_NOT_READY = 3
EXIT_THUMBNAIL_PENDING = 4

# How many times to re-try a thumbnail that keeps refusing to stick before
# giving up and telling a human. This script runs roughly hourly, so that is
# several hours of retries -- past that it isn't a transient API failure, and
# re-uploading on every run only burns quota (thumbnails.set costs 50 units a
# call).
MAX_THUMBNAIL_ATTEMPTS = 6


def _parse_flexible_date(date_str):
    """Match upload_queue_to_drive.py's parse_flexible_date(): the stashed
    meta.json normally holds MM-DD-YYYY, but a filename-derived date can be
    M-D-YY or slash-separated. A format we can't parse must not silently
    wedge the pending stream forever."""
    for fmt in ("%m-%d-%Y", "%m-%d-%y", "%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%Y/%m/%d"):
        try:
            return datetime.datetime.strptime(date_str, fmt).date()
        except (ValueError, TypeError):
            continue
    return None


def pending_slots():
    """The meta.json of every stashed thumbnail: one folder per service
    (pending_stream/<service key>/meta.json), plus the single flat
    pending_stream/meta.json older runs wrote."""
    found = []
    legacy = os.path.join(PENDING_STREAM_DIR, "meta.json")
    if os.path.exists(legacy):
        found.append(legacy)
    try:
        names = sorted(os.listdir(PENDING_STREAM_DIR))
    except FileNotFoundError:
        return found
    for name in names:
        meta = os.path.join(PENDING_STREAM_DIR, name, "meta.json")
        if os.path.exists(meta):
            found.append(meta)
    return found


def _write_meta(pending):
    with open(pending["_meta_path"], "w") as f:
        json.dump({k: v for k, v in pending.items() if k != "_meta_path"}, f, indent=2)


def note_thumbnail_attempt(pending, date_str):
    """Count one failed thumbnail retry, and give up once they run out.

    The stream itself already exists at this point -- only the thumbnail is
    outstanding -- so giving up means leaving the image and this metadata in
    place for a human, not discarding them."""
    attempts = int(pending.get("thumbnail_attempts") or 0) + 1
    pending["thumbnail_attempts"] = attempts

    if attempts < MAX_THUMBNAIL_ATTEMPTS:
        _write_meta(pending)
        print(f"The stream for {date_str} still has no thumbnail "
              f"(attempt {attempts}/{MAX_THUMBNAIL_ATTEMPTS}). Retrying next run.")
        return

    pending["thumbnail_gave_up"] = True
    _write_meta(pending)
    print(f"The thumbnail for {date_str} has failed {attempts} times; giving up on it. "
          f"{os.path.dirname(pending['_meta_path'])} keeps the image for a manual upload.")

    # Announce it: a stream whose thumbnail silently never landed is exactly
    # the failure that went unnoticed for three days on 09-13.
    sys.path.insert(0, "Youtube Processing")
    try:
        from create_youtube_stream import dispatch_thumbnail_failed
    except Exception as e:
        print(f"Could not load the dispatcher to report the failure: {e}")
        return
    dispatch_thumbnail_failed(stream_url_for(date_str), date_str, attempts)


def stream_url_for(date_str):
    """The URL of the stream this pending thumbnail belongs to, or "".

    create_youtube_stream.py writes last_stream.json before it reports a
    missing thumbnail, so by the time we give up the breadcrumb names the
    broadcast -- which is what makes the message actionable."""
    wanted = _parse_flexible_date(date_str)
    try:
        with open(os.path.join("Youtube Processing", "last_stream.json")) as f:
            last = json.load(f)
    except Exception:
        return ""
    recorded = _parse_flexible_date(last.get("service_date") or "")
    if wanted and recorded == wanted:
        return last.get("stream_url", "")
    return ""


def put_title_and_description_in_drive(pending, date_str, title=None, description=None, key=None):
    """A stream made right away gets title.txt and Description.txt next to its thumbnail in the
    week's Drive folder (upload_queue_to_drive.py). One that had to wait for them gets them here,
    once, when it's created."""
    if pending.get("text_in_drive"):
        return
    sys.path.insert(0, os.path.join("Worship Scripts", "worship workflows"))
    try:
        import upload_queue_to_drive as uq
        service = uq.get_drive_service()
        if not service:
            print(f"No Drive credentials, so title.txt and Description.txt for {date_str} weren't put in Drive.")
            return
        folder_id = uq.get_or_create_date_folder(service, uq.THUMBNAILS_DEST_PARENT_FOLDER_ID,
                                                 uq.drive_folder_name(date_str, pending.get("time") or "10:30"))
        if uq.upload_title_and_description(service, folder_id, date_str, title=title, description=description,
                                           key=key):
            pending["text_in_drive"] = True
    except Exception as e:
        # The stream exists; a missing text file in Drive isn't worth failing over.
        print(f"Could not put title.txt and Description.txt in Drive for {date_str}: {e}")


def main():
    slots = pending_slots()
    if not slots:
        print("No pending stream waiting on title/description. Exiting.")
        return
    # Each service waits on its own OW, so one not being ready yet must not
    # hold up another (Christmas Eve's 5:30, 8pm and 11pm can all be here).
    for meta_path in slots:
        try:
            process(meta_path)
        except Exception as e:
            print(f"Error processing {meta_path}: {e}")


def process(meta_path):
    with open(meta_path) as f:
        pending = json.load(f)
    pending["_meta_path"] = meta_path

    date_str = pending.get("date")
    stream_time = pending.get("time") or "10:30"
    thumbnail_path = pending.get("thumbnail_path")
    # Carried through from the upload dashboard only when a human typed an
    # explicit override. When present, these are trusted as-is and the
    # title.txt/Description.txt freshness checks below don't apply.
    override_title = (pending.get("title") or "").strip()
    override_desc = (pending.get("description") or "").strip()

    if not date_str or not thumbnail_path or not os.path.exists(thumbnail_path):
        print(f"Pending stream metadata is incomplete or the thumbnail is missing ({pending}). Leaving as-is.")
        return

    if pending.get("thumbnail_gave_up"):
        print(f"The thumbnail for {date_str} was already given up on after "
              f"{pending.get('thumbnail_attempts')} attempts (the stream itself exists). "
              f"Leaving it for a manual upload.")
        return

    key = service_keys.resolve_key(date_str, stream_time)

    # Only one half typed on the dashboard: the other comes from this
    # service's generated text once that's ready (see complete_override).
    if bool(override_title) != bool(override_desc):
        import upload_queue_to_drive as uq
        filled_title, filled_desc = uq.complete_override(key, override_title, override_desc)
        if not filled_title:
            print(f"Still waiting on the generated text for {date_str} {stream_time} to go with the "
                  f"typed {'title' if override_title else 'description'}. Will check again next run.")
            return
        override_title, override_desc = filled_title, filled_desc

    has_override = bool(override_title and override_desc)

    # A recorded attempt means create_youtube_stream.py already built the
    # broadcast and only the thumbnail is outstanding. Its title and
    # description have been on it since then, so don't hold the retry behind
    # a freshness check -- once the pipeline rolls on to next week's service
    # that check would never pass again and the thumbnail would be stuck.
    thumbnail_only = bool(pending.get("thumbnail_attempts"))

    if not has_override and not thumbnail_only:
        ready, reason = service_keys.content_ready(key) if key else (False, "its OW hasn't been processed")
        if not ready:
            print(f"Still waiting on title/description for {date_str} {stream_time} ({reason}). Will check again next run.")
            return

        print(f"Title/description are ready for {date_str} {stream_time} ({key}). Creating the deferred stream now...")
    elif thumbnail_only:
        print(f"Retrying the outstanding thumbnail for {date_str}'s stream "
              f"(attempt {int(pending['thumbnail_attempts']) + 1}/{MAX_THUMBNAIL_ATTEMPTS})...")
    else:
        print(f"Pending stream for {date_str} has an explicit title/description override. Creating it now...")

    # create_youtube_stream.py runs its own freshness gate and exits
    # EXIT_NOT_READY (3) if it can't confirm this week's title/description.
    # That's not an error -- just leave the thumbnail pending and try again
    # on the next run.
    script_path = os.path.join("Youtube Processing", "create_youtube_stream.py")
    # --reconcile: a stash only exists while something about this week's
    # stream is still outstanding, so when the broadcast turns out to already
    # exist, the thumbnail we are holding is the whole reason we are here.
    cmd = ["python", script_path, date_str, thumbnail_path, stream_time, "--reconcile"]
    if has_override:
        cmd += ["--title", override_title, "--description", override_desc]
    try:
        result = subprocess.run(cmd)
    except Exception as e:
        print(f"Error running create_youtube_stream.py for the deferred stream: {e}")
        return
    if result.returncode == EXIT_NOT_READY:
        print(f"create_youtube_stream.py could not confirm the title/description for {date_str} yet. "
              f"Leaving the thumbnail pending for the next run.")
        return
    if result.returncode == EXIT_THUMBNAIL_PENDING:
        # the stream exists; only its thumbnail is outstanding (note_thumbnail_attempt saves the flag)
        put_title_and_description_in_drive(pending, date_str, override_title or None, override_desc or None, key)
        note_thumbnail_attempt(pending, date_str)
        return
    if result.returncode != 0:
        print(f"create_youtube_stream.py failed (exit {result.returncode}). Leaving the thumbnail pending.")
        return

    put_title_and_description_in_drive(pending, date_str, override_title or None, override_desc or None, key)

    # Clean up now that the stream has actually been created -- leave
    # nothing pending for the next run to retry unnecessarily.
    try:
        os.remove(thumbnail_path)
    except Exception as e:
        print(f"Could not remove pending thumbnail {thumbnail_path}: {e}")
    try:
        os.remove(meta_path)
        if os.path.dirname(meta_path) != PENDING_STREAM_DIR:
            os.rmdir(os.path.dirname(meta_path))
    except Exception as e:
        print(f"Could not remove pending stream metadata: {e}")

    print("Deferred stream created; pending state cleared.")


if __name__ == '__main__':
    main()
