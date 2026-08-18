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
    try:
        wanted = datetime.datetime.strptime(date_str, "%m-%d-%Y").date().isoformat()
    except ValueError:
        return False
    return target == wanted


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

    title_ready = os.path.exists(title_path) and has_content(title_path) and title_matches_target_week(date_str)
    desc_ready = os.path.exists(desc_path) and has_content(desc_path) and description_matches_target_week(date_str)

    if not (title_ready and desc_ready):
        print(f"Still waiting on title/description for {date_str} (this week's OW likely hasn't been posted yet). Will check again next run.")
        return

    print(f"Title/description are ready for {date_str}. Creating the deferred stream now...")

    script_path = os.path.join("Youtube Processing", "create_youtube_stream.py")
    try:
        subprocess.run(["python", script_path, date_str, thumbnail_path, stream_time], check=True)
    except Exception as e:
        print(f"Error running create_youtube_stream.py for the deferred stream: {e}")
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
