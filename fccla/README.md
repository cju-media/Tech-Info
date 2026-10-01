# Weekly title graphics (Illustrator)

Builds the week's two 1920×1080 title graphics from the Order of Worship PDF by updating last
week's Illustrator files:

- **Service Title**: header "Sunday", the heading (page 1's lines above the date: series, series
  name, sermon title, however that week has them), the date, service times.
- **Sermon Title**: header "Sermon", same block, the preacher instead of the times.

| File | What it is |
| --- | --- |
| `UpdateWeek.jsx` | The Illustrator script. Fixed: no edits week to week. |
| `week-data.txt` | This week's values (written each week, next to the script). |
| `prepare_week.py` | Reads the PDF, pulls and upscales the cover photo, finds last week's files, suggests panel colors, writes `week-data.txt`. |
| `auto_build.py` | The automatic build (below): picks the OW, checks the text with Gemini, runs the two above. |
| `illustrator.py` | Runs `UpdateWeek.jsx` in Illustrator from Python (automatic build and tests). |
| `title_graphics_state.json`, `status/` | The current options, the pick, and small previews; the picker page and the upload dashboard read them. Written by the workflow. |
| `GEMINI.md` | Instructions Gemini follows to produce `week-data.txt` by hand, and only that. |
| `test/` | Tests (see below). |

The working copy lives in iCloud at `Worship and Sermon Series/Scripts/`. This folder in the
repo is the source. Each automatic build copies any changed scripts there, and
`./deploy_to_icloud.sh` does it by hand.

## Each week (automatic)

1. **Upload the OW** on the [upload dashboard](https://cju-media.github.io/Tech-Info/Utilities/uploads/index.html),
   as usual.
2. **Studio Mini builds three versions** a few minutes later (`.github/workflows/title_graphics.yml`),
   one per suggested panel color. It reads the fields from the PDF, has Gemini check them, and
   updates last week's files in Illustrator. The versions wait in a staging folder on Studio Mini;
   nothing goes into iCloud yet.
3. **You get a text with a link** to the [picker](https://cju-media.github.io/Tech-Info/Utilities/title-graphics/),
   which shows both graphics in each color. Tap **Use this one**, or build and use a color of
   your own. To fix something by hand first, tap **Use, don't send to Drive** instead (see
   below). Picking needs the GitHub token the upload dashboard uses; the page asks for it once
   per device.
4. **Studio Mini finishes** in under a minute:
   - It copies that version into `Worship and Sermon Series/<week>/` (with `log.txt` and
     `week-data.txt`).
   - It queues both JPGs for Drive exactly like the dashboard's Worship Service and Sermon
     Series thumbnail zones. The Worship Service one creates the livestream, or waits until
     that week's title and description are ready (the description comes from the worship
     script), as a manual upload would.
   - You get a second text saying where it went.

**Editing before it goes out.** **Use, don't send to Drive** copies the version into iCloud only
and texts you a link to the upload dashboard. Edit the `.ai` files, export the JPGs, and drop them
on the dashboard's Worship Service and Sermon Series thumbnail zones. The schedule never sends a
pick like this.

**Replacing.** Every thumbnail dropped on the dashboard, and every pick, replaces that Sunday's
thumbnail instead of adding a second one:
- In Drive, the image in the Sunday's folder is overwritten in place (earlier versions stay in
  its version history) and any other images there go to the Drive trash. The sermon video
  (`migrate_videos.py`) uses the one image it finds there.
- An existing livestream's thumbnail is re-uploaded (`create_youtube_stream.py --reconcile`).
- A pick waiting for its week never replaces something uploaded on the dashboard after the pick.

`upload_queue_to_drive.py` does this for queued thumbnails whose `.meta.json` sidecar has
`{"replace": true}` (a pick also sends its date and `chosen_at`). Changing your mind is fine:
pick again, or upload on the dashboard.

The Sermon Series zone always files under the coming Sunday. An edited sermon graphic reaches
that week's video only if it's uploaded before the Monday video upload.

Details:
- **Hand-made graphics are safe.** A week in iCloud that was made or edited by hand is never
  replaced unless you tick **Replace hand edits** on the picker.
- **When Drive gets them.** Drive files Sermon Series thumbnails under the next Sunday, so a
  pick for a later week waits. The schedule (every 3 hours) sends it on the Monday of its week.
  A pick made on the Sunday itself isn't sent.
- **A re-uploaded OW** rebuilds the options (your earlier pick's color is kept as an option), and
  you pick again. If it reads the same as the graphics in use (same heading lines, capitals
  included, date, preacher and cover photo), nothing is rebuilt and you get a text saying so.
  Before a pick, it's compared with the options already built. Tick **Rebuild title graphics
  even if the text hasn't changed** in the dashboard's OW box (or `force_rebuild` on a manual
  run) to rebuild anyway.
- **The schedule** also builds for an OW that reached `cju-media/OW` without the dashboard. It
  never builds on Sundays.
- **Moving last week's folder** into `Past Weeks` stays manual.

## Each week (by hand)

Use this when Studio Mini is unavailable, for special services, or to fix a field.

1. **Drop the PDF** (e.g. `10.4.26_OW_Draft.pdf`) into `Worship and Sermon Series/Scripts/`.
2. **Have Gemini make the data file.** Open Gemini CLI in that folder and say
   *"make this week's week-data.txt from 10.4.26_OW_Draft.pdf"*. It runs `prepare_week.py`,
   checks the fields against the PDF, and saves the cover to `10-4-26/Cover_10-4-26.png`.
   No Gemini? Run `python3 prepare_week.py 10.4.26_OW_Draft.pdf` and read its output yourself.
3. **Pick the panel color.** Look at `Scripts/panel-colors.png` and choose 1, 2, 3 or any hex.
   Gemini then writes `week-data.txt`; by hand it's
   `python3 prepare_week.py 10.4.26_OW_Draft.pdf --color 2 --write`.
4. **Run the script.** In Illustrator: **File > Scripts > Other Script…** →
   `Scripts/UpdateWeek.jsx`. It asks before replacing graphics that already exist.
5. **Check the result.** `10-4-26/log.txt` lists everything it found and changed; lines with
   `!!` need a look. Then check the two JPGs, make any hand edits in Illustrator, and re-export.

## Studio Mini setup (once)

The workflow runs on the repo's self-hosted runner ("Studio-Mini"). That Mac needs:

- **Illustrator**, installed and signed in to Creative Cloud, with a user logged in (the runner
  already runs in that user's session for iMessage).
- **The "Worship and Sermon Series" folder** in its iCloud Drive (shared to that Apple ID if it
  isn't yours). If it isn't at `~/Library/Mobile Documents/com~apple~CloudDocs/FCCLA/Worship and Sermon Series`,
  set the repository variable `TITLE_GRAPHICS_ROOT` to its path.
- **Permission to control Illustrator.** The first run makes macOS ask whether the runner may
  control Adobe Illustrator. Allow it on that Mac (Screen Sharing works), or under System
  Settings > Privacy & Security > Automation.
- The options are staged in `~/Library/Application Support/FCCLA Title Graphics/` on that
  Mac (`TITLE_GRAPHICS_STAGING` to change it), and kept for three weeks.

To check, run **Actions > Title Graphics > Run workflow** with **probe** ticked. It texts a
checklist: the folder, Illustrator installed and answering, Gemini, `brctl`. Nothing is built.

## What the script does to last week's file

- **Photo:** replaces the image on the left half (the biggest visible image centered there) with
  the new cover, scaled to fill 934×1080 and centered. It clips the photo to that frame, since
  the photo sits above the panel and a wide photo would otherwise cover it, then embeds it so
  the `.ai` has no links to break. To reposition it, double-click into the clipping group, or
  set `photoShiftY` (points, + is up) and rerun.
- **Text:** edits only the changed characters, so each line keeps its formatting: the heading
  (one area-text frame), the date, the preacher (sermon graphic),
  and "pdt"/"pst" after the service times.
- **The heading** is page 1's lines above the date, one to four of them (`heading = Fall Series 4 |
  Painting the Stars | An Anticipatory Universe`; Lent, Pentecost, name-and-title and title-only
  weeks all work). Last week's heading is found by position, so any of those can follow any other.
- **Layout:** the top line (with the rules) stays exactly where the design has it, and the lines
  under it are centered between it and the date, moved by the leading of the first of them
  (recorded in the frame's note and undone the next week). Lines are added above the title in the
  title's style, or removed from under the top line. A title on its own is just the top line; one
  too long to sit beside its rules is split into a top line and a centered line under it (as done
  by hand for "Fulfilling The Dream / For Freedom").
- **Long lines.** A line under the top one that's too long is split in two at its middle; if they
  still don't fit above the date with 30pt to spare, they shrink evenly, line spacing included, and
  anything below 75% is flagged. Every shrink is noted on the frame and undone the next week, so a
  short heading comes back at full size. A long date or preacher is shrunk to fit the panel.
- **The rules beside the first line and the date** keep last week's gap: they grow when the text
  gets shorter and shrink when it gets longer, and their outer ends never move. If a longer first
  line would leave them under 60pt, it's split or shrunk instead so they stay 60pt.
- **Panel:** recolors the big rectangle on the right (and anything else in exactly its color).
- **Saves** `<name>.ai` (PDF-compatible) and `<name>.jpg` (artboard, 100%), plus `log.txt` and
  a copy of `week-data.txt` in the week folder.

## Setup (once per Mac)

- Illustrator 2026 (any recent version should work).
- `pip3 install pypdf pillow` for `prepare_week.py`.
- Gemini CLI, started in `Worship and Sermon Series/Scripts/` so it reads `GEMINI.md`.

## Tests

```bash
python3 -m unittest fccla/test/test_auto_build.py
```

The automatic build's decisions, with no Illustrator or network: which OW, whether graphics
were made or edited by hand, how the PDF parse and Gemini's reading are combined, and when a
pick goes to Drive. The Drive side has its own test:
`cd "Worship Scripts/worship workflows" && python3 -m unittest test_upload_queue_replace`.

The next two need Illustrator open on this Mac, and write only to `fccla/test/out/` (gitignored).

```bash
python3 fccla/test/test_9_27.py
```

Rebuilds 9-27-26 from the 9-20-26 files and compares it with the 9-27 graphics made by hand:
the text must be identical, and the right panel must match within JPEG noise. The photo
position and the preacher line are reported but not held to the limit, because both were
nudged by hand.

```bash
python3 fccla/test/test_chain.py
python3 fccla/test/test_formats.py
```

Chains two made-up weeks onto that output: a landscape photo, a title long enough to split, a new series,
a preacher without "Rev.", PST, and out-of-range photo nudges. Then a short title the following
week. `test_formats.py` chains real OWs through every heading shape (3, 2, 2, 1, 3, 3, 4 and back to 3
lines), measures in Illustrator that the top line never moves and the lines under it are centered,
and writes a review sheet, `fccla/test/out/formats/formats.jpg`. Run both after changing
`UpdateWeek.jsx`. `python3 -m unittest fccla/test/test_prepare_week.py` checks the PDF reading
against real OWs of each layout.

Illustrator scripting notes learned the hard way (the script's comments cover the rest): the
script is ES3 (no `let`, `JSON`, `trim`, or `Array.indexOf`); ExtendScript misparses nested
`a ? b : c ? d : e` (use if/else); the heading's leading is fixed, so shrink it with the type; the paragraphs collection starts a new one at a forced line break; `move()` returns nothing;
Illustrator keeps globals between script runs; and from AppleScript, pass the JavaScript as
text to `do javascript`, because a file alias stops at a dialog.
