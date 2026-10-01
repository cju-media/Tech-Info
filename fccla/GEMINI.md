# Weekly title graphics: instructions for Gemini

Each week the church gets an Order of Worship PDF (e.g. `9.27.26_OW_Draft.pdf`). From it,
two Illustrator graphics are made by updating last week's files:

- `Worship Service M-D-YY/Service Title_M-D-YY.ai` + `.jpg`: header "Sunday", shows service times.
- `Sermon Series M-D-YY/Sermon Title_M-D-YY.ai` + `.jpg`: header "Sermon", shows the preacher.

`UpdateWeek.jsx` (run by a person in Illustrator) does the editing. It reads everything that
changes week to week from **`week-data.txt`**. **Your only job is to produce a correct
`week-data.txt`.**

Usually you aren't needed. When the OW is uploaded on the upload dashboard, Studio Mini builds
the graphics in each suggested color by itself, and the user picks one on a web page
(`auto_build.py`, see `README.md`). You're for the by-hand route: special services, Studio Mini
being unavailable, or a field the automatic build got wrong. If this week's graphics already
exist, say so and ask before the user replaces them.

## Hard rules

- **Only create or edit `week-data.txt`.** Never modify `UpdateWeek.jsx`, `prepare_week.py`,
  `auto_build.py`, `GEMINI.md`, `README.md`, `title_graphics_state.json`, or any `.ai`/`.jpg`. The script is tested and working; a "quick fix"
  there can break every future week. If something seems to need a script change, stop and tell
  the user what you found.
- **Never move, rename, or delete folders or files.** Don't move last week into `Past Weeks`;
  the user does that.
- **Never pick the panel color yourself.** Show the options and ask (step 3).
- Don't run Illustrator or `osascript`. The user runs the script.
- If the PDF is ambiguous (two dates, no sermon row, a special service), ask; don't guess.

## Where things are

Root: `~/Library/Mobile Documents/com~apple~CloudDocs/FCCLA/Worship and Sermon Series/`

```
<root>/
  Scripts/                  UpdateWeek.jsx, prepare_week.py, this file, week-data.txt
  9-27-26/                  the current week
    Worship Service 9-27-26/Service Title_9-27-26.ai + .jpg
    Sermon Series 9-27-26/Sermon Title_9-27-26.ai + .jpg
    Cover_9-27-26.png       the cover photo (made by prepare_week.py)
    log.txt, week-data.txt  written by UpdateWeek.jsx
  Past Weeks/9-20-26/...    earlier weeks, moved here by the user
```

- Week names are `M-D-YY` with no leading zeros: September 27, 2026 is `9-27-26`; October 4 is `10-4-26`.
- Older weeks aren't named consistently. Sermon files may be `SermonSeries_M-D-YY.ai` (no space)
  or even `Service Title_M-D-YY.ai` inside the Sermon Series folder, and one folder name has a
  trailing space. Don't "fix" them; `prepare_week.py` copes.
- The user drops the OW PDF into `Scripts/` (or tells you its path). If they don't say, use
  the newest `*OW*.pdf` in `Scripts/` and tell them which file you used.

## Steps

Run commands from the `Scripts` folder.

### 1. Run the helper

```bash
python3 prepare_week.py "9.27.26_OW_Draft.pdf"
```

It reads the PDF, finds last week's templates, saves the upscaled cover photo as
`<week>/Cover_<week>.png`, makes `panel-colors.png`, and prints a draft `week-data.txt`
(with `panelColor = CHOOSE`). Read every `WARNING:` line. If it fails because `pypdf` or
`Pillow` is missing, tell the user to run `pip3 install pypdf pillow`.

### 2. Check every text field against the PDF yourself

Open the PDF and compare. The helper is usually right, but you are the check.

| Field | Where in the PDF | Example | Rule |
| --- | --- | --- | --- |
| `heading` | page 1, **every line above the date**, in order | `Fall Series 4 \| Painting the Stars \| An Anticipatory Universe` | Lines joined with ` \| `. A line written `Name ~ Title` is two lines (split on the `~`). Copy each line exactly: capitalization, punctuation, curly apostrophes. |
| `dateText` | page 1, e.g. `27 September 2026` or `September 28th, 2025` | `September 27, 2026` | Rewrite as `Month D, YYYY`: full month name, no leading zero, no "th", comma. It must be a Sunday. |
| `preacher` | the **Sermon** row of the order of service: `Sermon   <title>   Rev. Laura Vail Fregin` | `Rev. Laura Vail Fregin` | The name at the end of that row, with its title (Rev., Dr., ...). No Sermon row? Use the Reflection or Homily row, never a *musical* reflection. Only the sermon graphic uses it, but it is always required. |

The heading changes shape through the church year. All of these are right:

| Page 1 above the date | `heading` |
| --- | --- |
| Fall Series 4 / Painting the Stars ~ An Anticipatory Universe | `Fall Series 4 \| Painting the Stars \| An Anticipatory Universe` |
| Pentecost 11 / Another Kind of Freedom ~ Healing Division | `Pentecost 11 \| Another Kind of Freedom \| Healing Division` |
| Lent 4 / The Only Thing More Powerful Than Hate is Love / Edge Walking | `Lent 4 \| The Only Thing More Powerful Than Hate is Love \| Edge Walking` |
| Another Kind of Freedom / The God Who Sees Us | `Another Kind of Freedom \| The God Who Sees Us` |
| Fulfilling The Dream For Freedom | `Fulfilling The Dream For Freedom` |

The script lays out one to four lines. The first line gets the rules beside it, and a line too
long for the panel is split in two or shrunk. Don't rewrite or shorten lines yourself.

- Ignore the photo caption (e.g. "Andromeda Galaxy / NASA James Webb Space Telescope") and the
  church address.
- The sermon row's title should match the last heading line. If it doesn't, say so. It happens
  (MLK Sunday's page 1 differs from its sermon row), and page 1 wins unless the user says otherwise.
- If a field is wrong, correct it with `--set` in step 4, e.g.
  `--set heading="Lent 4 | The Only Thing More Powerful Than Hate is Love | Edge Walking"`.

The other fields are computed; don't change them unless the user asks:

- `timeZone`: `PDT` or `PST` for 11 AM that Sunday in Los Angeles. PST runs from the first
  Sunday of November through the Sunday before the second Sunday of March. The script swaps
  the "pdt"/"pst" after the service times.
- `root`, `imagePath`, `outputFolder`: this week's folder `<M-D-YY>` in the root, and the
  cover saved there.
- `serviceTemplate` / `sermonTemplate`: **last week's** files. Take this Sunday minus 7 days,
  look for the `M-D-YY` folder first in the root and then in `Past Weeks/`, and use the `.ai`
  inside its `Worship Service ...` and `Sermon Series ...` folders. If last Sunday's folder
  doesn't exist (skipped week, special service), use the newest earlier week that has both
  files, and tell the user which week you used.
- `serviceFolder`/`serviceName`, `sermonFolder`/`sermonName`: `Worship Service M-D-YY`,
  `Service Title_M-D-YY`, `Sermon Series M-D-YY`, `Sermon Title_M-D-YY` (new files always use
  `Sermon Title_`, whatever last week's was called).
- `photoShiftX` / `photoShiftY`: `0` unless the user asks to nudge the photo (points; +Y is up).

### 3. Ask the user to pick the panel color

The right-hand panel color is a design choice, not something in the PDF, and the most common
color in the photo is often not the one the user wants (for the Andromeda cover the dominant
color was olive-gold; the user picked the blue `#325673`). Show the user:

- the path to `panel-colors.png` (each option drawn next to the photo), and
- the helper's three suggestions with their labels, e.g.
  `1 #6E6127 gold` · `2 #385261 blue` · `3 #4D441B deeper gold`.

Ask them to choose 1, 2, 3, or type any hex like `#325673`. Wait for the answer.
Recent picks, for reference: `#AB6854` rust, `#325673` blue, `#05293D` deep teal, `#853320` red.

### 4. Write week-data.txt

```bash
python3 prepare_week.py "9.27.26_OW_Draft.pdf" --color "#325673" --write
# with corrections:
python3 prepare_week.py "9.27.26_OW_Draft.pdf" --color 2 --set preacher="Dr. Jane Doe" --write
```

`--color` takes the hex or the suggestion number. This writes `Scripts/week-data.txt` and
validates it. It must end with `week-data.txt OK` or no `PROBLEM:` lines. Fix any problem and
run it again. To re-check at any time: `python3 prepare_week.py --check`.

If the helper can't run at all, write `Scripts/week-data.txt` by hand in exactly the format
below. Then tell the user it wasn't validated.

### 5. Hand off

Show the user the final `week-data.txt` and tell them:

> In Illustrator: **File > Scripts > Other Script…**, choose `Scripts/UpdateWeek.jsx`. When it
> finishes, check `<week>/log.txt` (lines with `!!` need attention) and the two JPGs.

## week-data.txt format

One `key = value` per line; `#` starts a comment; only the first `=` splits, so values may
contain `=`. Paths starting with `~/` or `/` are absolute; others are relative to `root`.
No quotes around values.

```
# week-data.txt for UpdateWeek.jsx: week of 9-27-26, from 9.27.26_OW_Draft.pdf
root            = ~/Library/Mobile Documents/com~apple~CloudDocs/FCCLA/Worship and Sermon Series
heading         = Fall Series 4 | Painting the Stars | An Anticipatory Universe
dateText        = September 27, 2026
preacher        = Rev. Laura Vail Fregin
panelColor      = #325673
timeZone        = PDT
imagePath       = 9-27-26/Cover_9-27-26.png
photoShiftX     = 0
photoShiftY     = 0
outputFolder    = 9-27-26
serviceTemplate = Past Weeks/9-20-26/Worship Service 9-20-26/Service Title_9-20-26.ai
serviceFolder   = Worship Service 9-27-26
serviceName     = Service Title_9-27-26
sermonTemplate  = Past Weeks/9-20-26/Sermon Series 9-20-26/SermonSeries_9-20-26.ai
sermonFolder    = Sermon Series 9-27-26
sermonName      = Sermon Title_9-27-26
```

Required: every key above except `timeZone`, `photoShiftX`, `photoShiftY`. `panelColor` is
`#` plus six hex digits. Unknown keys are rejected, so check spelling. (Older files have
`series`, `seriesName` and `title` instead of `heading`; those still work, but write `heading`.)

## If the user reports a problem after running the script

- `log.txt` lists what the script found and changed; lines with `!!` are problems.
- "had to shrink", "split … over two lines", "shrank the whole heading": a long heading. The script
  fits it on its own; below 75% it asks for a look. A shorter wording is the fix if it looks too small.
- "photo shift limited": the photo has no room to move that way; use a smaller value.
- "template not found": check the week folders (maybe last week was moved or named oddly) and
  fix the template path in `week-data.txt`.
- The photo crop or a text position needs a small tweak: that's a manual edit in Illustrator
  (the photo is inside a clipping mask; double-click it to move the photo), or rerun with
  `photoShiftY`.
- Anything else, including anything that looks like a script bug: report it to the user.
  Don't edit the script.
