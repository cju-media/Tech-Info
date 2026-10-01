"""Run UpdateWeek.jsx in Illustrator from Python (tests and auto_build.py).

Illustrator is driven through AppleScript's `do javascript`. Two things learned the hard way:
the JavaScript has to be passed as text (handing Illustrator the .jsx file itself stops at a
dialog), and a run takes longer than AppleScript's default 2-minute timeout.
"""

import os
import subprocess
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
UPDATE_WEEK = os.path.join(HERE, "UpdateWeek.jsx")


class IllustratorError(Exception):
    pass


def do_javascript(js, timeout=900):
    """Run JavaScript in Illustrator (launching it if needed) and return its result as text."""
    script = ('with timeout of %d seconds\n  tell application id "com.adobe.illustrator"\n'
              '    do javascript "%s"\n  end tell\nend timeout\n'
              % (timeout, js.replace("\\", "\\\\").replace('"', '\\"')))
    with tempfile.NamedTemporaryFile("w", suffix=".applescript", delete=False) as fh:
        fh.write(script)
    try:
        r = subprocess.run(["osascript", fh.name], capture_output=True, text=True, timeout=timeout + 60)
    except subprocess.TimeoutExpired:
        raise IllustratorError("Illustrator didn't finish within %d s (is a dialog open on that Mac?)" % timeout)
    finally:
        os.unlink(fh.name)
    if r.returncode:
        raise IllustratorError((r.stderr or r.stdout).strip())
    return r.stdout.replace("\r", "\n").rstrip("\n")


def run_updateweek(data_path, timeout=900):
    """Run UpdateWeek.jsx on a week-data.txt without any dialogs; returns the script's log."""
    js = ("$.global.UPDATEWEEK_DATA = '%s'; $.global.UPDATEWEEK_QUIET = true; $.evalFile(new File('%s'))"
          % (data_path.replace("'", "\\'"), UPDATE_WEEK.replace("'", "\\'")))
    return do_javascript(js, timeout)
