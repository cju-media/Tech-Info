# Weekly Content Pipeline section

The **Weekly Content Pipeline** section on the dashboard (`index.html`) shows,
for each stage of the Sunday-content pipeline, when it **last produced fresh
output** and where to see that output.

The times deliberately come from *the commit or upload that last changed a
stage's output* — **not** from a workflow simply running. The hourly checker
workflows (`Check Worship Scripts`, `Service Titles Checker`, `Create Pending
YouTube Stream`, …) "succeed" every run whether or not they did anything, so
"last successful run" would always read "an hour ago" and tell you nothing.

## Stages and their time source

| Stage | Time source | Signal |
| ----- | ----------- | ------ |
| Worship script | GitHub commits API — last commit touching `Worship Scripts/worship_scripts.json` | run-of-show PDF pulled from Drive and parsed by `update_worship_scripts.py` |
| Order of Worship | GitHub commits API — last commit touching `Worship Scripts/service_titles_state.json` | a new OW ingested from the `cju-media/OW` repo (`OWs/`) and sent to Gemini by `update_service_titles.py` |
| Service titles | GitHub commits API — last commit touching `Worship Scripts/service-titles/` | the individual title `.txt` files regenerated from the OW and synced to Drive |
| YouTube livestream | `Youtube Processing/last_stream.json` (`created_at`) | the Sunday-service live broadcast created by `create_youtube_stream.py`. Fallback if the file is missing: last commit touching `Youtube Processing/description_push_state.json` |
| Sermon video | `Utilities/Video Migration/last_upload.json` (`uploaded_at`) | the recorded service copied from Drive and uploaded to YouTube by `migrate_videos.py` |

## The two breadcrumb files

Neither the livestream nor the sermon-video step otherwise leaves a dated
artifact in the repo, so the scripts write a small JSON breadcrumb on success
and the workflow that ran them commits it.

### `Youtube Processing/last_stream.json`

```json
{
  "created_at": "2026-08-27T15:30:09+00:00",
  "service_date": "08-30-2026",
  "stream_url": "https://www.youtube.com/watch?v=<broadcast_id>"
}
```

Written by `record_last_stream()` in `Youtube Processing/create_youtube_stream.py`
right after the broadcast is created. Committed by whichever workflow ran that
script:

- `create_pending_youtube_stream.yml` (via `create_pending_stream.py`)
- `process_uploads.yml` (via `upload_queue_to_drive.py`, when it creates the
  stream directly)

### `Utilities/Video Migration/last_upload.json`

```json
{
  "uploaded_at": "2026-09-01T02:25:32+00:00",
  "service_date": "08-30-2026",
  "video_title": "Understanding Community - Rev. Michael Lehman || FCCLA Sermon",
  "video_url": "https://www.youtube.com/watch?v=<video_id>"
}
```

Written by `record_last_upload()` in `Utilities/Video Migration/migrate_videos.py`
**only on a successful YouTube upload**. Committed by `video_migration.yml`
(which gained `permissions: contents: write`, `fetch-depth: 0`, and a commit
step for this).

`service_date` feeds the "for MM-DD-YYYY" hint shown on hover; `stream_url` /
`video_url` become a "Watch stream" / "Watch video" link on the row.

If a file is missing (it doesn't exist until the next stream/migration runs),
the dashboard degrades gracefully — the livestream row uses its fallback and
the sermon-video row shows "no upload recorded yet". To seed a file by hand,
pull the ID and timestamp from the relevant Actions run log (`Successfully
created broadcast with ID:` / `Video uploaded successfully! Video ID:`).

## GitHub API usage

The script / OW / titles rows each make one call to
`GET /repos/cju-media/Tech-Info/commits?per_page=1&path=<path>`. Unauthenticated
that shares the 60-requests/hour-per-IP limit with `workflows.html`; set
`localStorage.GITHUB_PAT` (same key `workflows.html` uses) to authenticate. The
section fetches on load and refreshes hourly.

## Related alert

When Video Migration copies the recording to Drive but **skips** the YouTube
upload — because the title/description text files aren't in the dated Drive
folder yet, or YouTube auth failed — `migrate_videos.py` dispatches a
`video_migration_upload_skipped` `repository_dispatch`, which
`imessage_notifications.yml` turns into an iMessage. That's the signal that
was missing when the 2026-08-30 upload silently didn't happen.
