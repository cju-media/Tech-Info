# Checking the Christmas Eve build on Studio Mini

The unit tests (`test/test_christmas_eve.py`) cover everything up to Illustrator: folders,
week-data.txt, the picker state, Drive sidecars. Only Studio Mini can show that `UpdateWeek.jsx`
changes the real Service Title file the way last year's hand-made ones look (`Past Weeks/12-24-25 8pm`):
header **Thursday**, heading **Christmas Eve**, **December 24, 2026**, **Organ Prelude-Concert 7:30 PM pst**,
**Virtual Service 8:00 PM pst**. Do part A any time after this branch is merged; part B once the
real 2026 OWs exist (mid-December). Allow about 30 minutes for A.

## A. Dry run in a scratch folder (nothing reaches iCloud's week folders, Drive or YouTube)

Run in Terminal on Studio Mini, from the Tech-Info checkout, with Illustrator open and no
dialogs showing. `ROOT` is the real iCloud folder; `T` is a scratch copy of it.

1. **Set up.**
   ```bash
   git pull && python3 fccla/auto_build.py --probe
   ```
   Every line should say OK.
   ```bash
   ROOT="$HOME/Library/Mobile Documents/com~apple~CloudDocs/FCCLA/Worship and Sermon Series"
   T="$HOME/tg-xmas-test"; mkdir -p "$T/Past Weeks" "$T/Scripts" "$T/ow"
   ls "$ROOT"
   ```
   Copy the newest **Sunday** folder (the one `ls` shows with the latest date, say `12-13-26`)
   and one Christmas folder of last year's, to prove it's never used as a template:
   ```bash
   cp -R "$ROOT/12-13-26" "$T/"
   cp -R "$ROOT/Past Weeks/12-24-25 8pm" "$T/Past Weeks/"
   ```
   Download last year's two text OWs into `$T/ow/` (from cju-media/OW, `OWs/12.24.25 8pm.pdf`
   and `OWs/12.24.25_11pm.pdf`, e.g. with `gh api` or the GitHub web page).

2. **Write week-data.txt for the 8pm**, dated this year so the template is this year's Sunday:
   ```bash
   python3 fccla/prepare_week.py "$T/ow/12.24.25 8pm.pdf" --time 8pm \
     --set dateText="December 24, 2026" --root "$T" --data "$T/Scripts/week-data.txt" --color 1 --write
   ```
   - [ ] No `PROBLEM:` lines. A `WARNING: newest earlier week is 12-13-26 (11 days before)` is
         expected when the folder you copied isn't 12-20-26.
   - [ ] It shows `dayName = Thursday`, `graphics = service`, `serviceTime = 8:00 PM`,
         `timeZone = PST`, `outputFolder = 12-24-26 8pm`, and a `serviceTemplate` in the Sunday
         folder you copied (not `12-24-25 8pm`). No `preacher` or `sermon...` lines.

3. **Run UpdateWeek.jsx on it** (the same call the workflow makes):
   ```bash
   python3 -c "import sys; sys.path.insert(0, 'fccla'); from illustrator import run_updateweek; print(run_updateweek(sys.argv[1]))" "$T/Scripts/week-data.txt"
   ```
   In the output (also saved as `$T/12-24-26 8pm/log.txt`):
   - [ ] Only `Service Title_12-24-26 8pm` is built. There's no `Sermon Title` section.
   - [ ] `header: Sunday -> Thursday`
   - [ ] `times: 10:30 AM, 11:00 AM -> 7:30 PM, 8:00 PM`
   - [ ] `date: December 13, 2026 -> December 24, 2026` (or whichever Sunday you copied)
   - [ ] No `!!` lines, apart from `photo shift limited ...`, which most weeks show. In particular,
         none saying `the "Sunday" header wasn't found` or `the service times weren't found`.
         Either one means the template's text isn't laid out as expected. Send the log and the
         JPG back to the session that made this change.

4. **Look at the JPG:** `open "$T/12-24-26 8pm/Worship Service 12-24-26 8pm/Service Title_12-24-26 8pm.jpg"`,
   next to last year's: `open "$T/Past Weeks/12-24-25 8pm/Service Title_2025-12-25-25 8pm.jpg"`.
   - [ ] The header reads **THURSDAY** in the same small-caps style the Sunday header had, centered in its bar.
   - [ ] **Christmas Eve** is one line with the rules beside it, centered like last year's.
   - [ ] **December 24, 2026**, with its rules.
   - [ ] **7:30 PM pst** under Organ Prelude-Concert and **8:00 PM pst** under Virtual Service. The
         digits and AM/PM are the same size and font as before, and "pst" is still small.
   - [ ] The photo fills the left side; the panel is the suggested color.
   - [ ] Open the `.ai` in Illustrator: the header and times are still live text in their
         original frames (nothing outlined or duplicated). Close it without saving.

5. **The 11pm and 5:30pm.** Run steps 2 and 3 again with the 11pm OW and `--time 11pm`, then
   with the 8pm OW and `--time 5:30pm` (the 5pm OW was a picture with no text to read):
   - [ ] 11pm: `times: ... -> 10:30 PM, 11:00 PM`, folder `12-24-26 11pm`.
   - [ ] 5:30pm: `times: ... -> 5:00 PM, 5:30 PM`, folder `12-24-26 530pm`.

6. **The Sunday after is still built from a Sunday.** Copy any Sunday OW of yours as a stand-in:
   ```bash
   python3 fccla/prepare_week.py "$T/ow/<a Sunday OW>.pdf" --set dateText="December 27, 2026" \
     --root "$T" --data "$T/Scripts/week-data.txt" --color 1
   ```
   - [ ] `templates from:` names the Sunday folder you copied, not one of the three
         `12-24-26 ...` folders made above.
   - [ ] The draft has `preacher` and the `sermon...` keys, and no `dayName`/`serviceTime`.

7. **Clean up:** `rm -rf "$T"`.

## B. The real thing (once Christmas Eve 2026's OWs are in cju-media/OW)

1. Upload the three Christmas Eve OWs and the 12/27 one on the upload dashboard, one after another.
   - [ ] Within about 15 minutes, texts arrive covering all four services. The last run's text
         names the services the cancelled runs never got to. Actions > Title Graphics shows some
         runs as cancelled; that's expected.
   - [ ] If an OW can't be read (no heading on page 1), its text says so and the others are
         still built. Make that one by hand and upload its Worship Service thumbnail on the dashboard
         with the date and time set.
2. Open the picker.
   - [ ] One tab each: Thu, Dec 24 · 5:30pm / 8pm / 11pm, Sun, Dec 27. A dot marks the unpicked ones.
   - [ ] The Christmas Eve tabs show one graphic per option (Worship Service only); 12/27 shows two.
3. On the 5:30pm tab, tap **Use, don't send to Drive** on one option.
   - [ ] `12-24-26 530pm/` appears in iCloud with the Worship Service JPG and no Sermon Series folder.
   - [ ] The other tabs are unchanged.
4. Tap **Send it to Drive** on that option.
   - [ ] Drive's Worship Service thumbnails get a `12-24-2026 5:30pm` folder holding that JPG. No
         Sermon Series thumbnail is added under 12-27-2026.
   - [ ] Once that service's title and description are ready, its own livestream has the thumbnail.
5. Pick 8pm, 11pm and 12/27 the same way.
   - [ ] 12/27's pick goes to Drive in the week of 12/27, or right away from Monday 12/21 on.
