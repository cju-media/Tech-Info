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
PENDING_STREAM_META = os.path.join(PENDING_STREAM_DIR, "meta.json")

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


def _target_date_matches(state_path, date_str):
    if not os.path.exists(state_path):
        return False
    try:
        with open(state_path) as f:
            state = json.load(f)
    except Exception:
        return False
    target = state.get("target_date")
    if not target:
        return False
    wanted = _parse_flexible_date(date_str)
    if not wanted:
        return False
    return target == wanted.isoformat()


def title_matches_target_week(date_str):
    return _target_date_matches(os.path.join("Worship Scripts", "service_titles_state.json"), date_str)


def description_matches_target_week(date_str):
    return _target_date_matches(os.path.join("Youtube Processing", "description_state.json"), date_str)


def _write_meta(pending):
    with open(PENDING_STREAM_META, "w") as f:
        json.dump(pending, f, indent=2)


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
          f"{PENDING_STREAM_DIR} keeps the image for a manual upload.")

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


def main():
    if not os.path.exists(PENDING_STREAM_META):
        print("No pending stream waiting on title/description. Exiting.")
        return

    with open(PENDING_STREAM_META) as f:
        pending = json.load(f)

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

    title_path = os.path.join("Worship Scripts", "service-titles", "title.txt")
    desc_path = os.path.join("Youtube Processing", "Description.txt")

    def has_content(path):
        try:
            with open(path) as f:
                return bool(f.read().strip())
        except Exception:
            return False

    has_override = bool(override_title and override_desc)

    # A recorded attempt means create_youtube_stream.py already built the
    # broadcast and only the thumbnail is outstanding. Its title and
    # description have been on it since then, so don't hold the retry behind
    # a freshness check -- once the pipeline rolls on to next week's service
    # that check would never pass again and the thumbnail would be stuck.
    thumbnail_only = bool(pending.get("thumbnail_attempts"))

    if not has_override and not thumbnail_only:
        title_ready = os.path.exists(title_path) and has_content(title_path) and title_matches_target_week(date_str)
        desc_ready = os.path.exists(desc_path) and has_content(desc_path) and description_matches_target_week(date_str)

        if not (title_ready and desc_ready):
            print(f"Still waiting on title/description for {date_str} (this week's OW likely hasn't been posted yet). Will check again next run.")
            return

        print(f"Title/description are ready for {date_str}. Creating the deferred stream now...")
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
        note_thumbnail_attempt(pending, date_str)
        return
    if result.returncode != 0:
        print(f"create_youtube_stream.py failed (exit {result.returncode}). Leaving the thumbnail pending.")
        return

    # Clean up now that the stream has actually been created -- leave
    # nothing pending for the next run to retry unnecessarily.
    try:
        os.remove(thumbnail_path)
    except Exception as e:
        print(f"Could not remove pending thumbnail {thumbnail_path}: {e}")
    try:
        os.remove(PENDING_STREAM_META)
    except Exception as e:
        print(f"Could not remove pending stream metadata: {e}")

    print("Deferred stream created; pending state cleared.")


if __name__ == '__main__':
    main()
