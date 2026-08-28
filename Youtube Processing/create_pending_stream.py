import os
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

    title_path = os.path.join("Worship Scripts", "service-titles", "title.txt")
    desc_path = os.path.join("Youtube Processing", "Description.txt")

    def has_content(path):
        try:
            with open(path) as f:
                return bool(f.read().strip())
        except Exception:
            return False

    has_override = bool(override_title and override_desc)

    if not has_override:
        title_ready = os.path.exists(title_path) and has_content(title_path) and title_matches_target_week(date_str)
        desc_ready = os.path.exists(desc_path) and has_content(desc_path) and description_matches_target_week(date_str)

        if not (title_ready and desc_ready):
            print(f"Still waiting on title/description for {date_str} (this week's OW likely hasn't been posted yet). Will check again next run.")
            return

        print(f"Title/description are ready for {date_str}. Creating the deferred stream now...")
    else:
        print(f"Pending stream for {date_str} has an explicit title/description override. Creating it now...")

    # create_youtube_stream.py runs its own freshness gate and exits
    # EXIT_NOT_READY (3) if it can't confirm this week's title/description.
    # That's not an error -- just leave the thumbnail pending and try again
    # on the next run.
    EXIT_NOT_READY = 3
    script_path = os.path.join("Youtube Processing", "create_youtube_stream.py")
    cmd = ["python", script_path, date_str, thumbnail_path, stream_time]
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
