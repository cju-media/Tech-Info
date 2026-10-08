#!/usr/bin/env python3
"""Run every ProPresenter sync: the prelude slides, then the hymns. What the launch agent runs.

  python3 sync_propresenter.py --skip-if-running
  python3 sync_propresenter.py --dry-run

One failing doesn't stop the other; the exit code is non-zero if either failed.
"""
import sys
import traceback

import sync_propresenter_hymns
import sync_propresenter_prelude
from propresenter_pb import propresenter_running

SYNCS = (sync_propresenter_prelude, sync_propresenter_hymns)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--skip-if-running" in argv and propresenter_running():
        print("ProPresenter is running; leaving everything alone.")
        return 0
    failed = False
    for sync in SYNCS:
        try:
            sync.main(argv)
        except SystemExit as e:
            if e.code not in (None, 0):
                print("%s: %s" % (sync.__name__, e.code))
                failed = True
        except Exception:
            print("%s failed:" % sync.__name__)
            traceback.print_exc(file=sys.stdout)
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
