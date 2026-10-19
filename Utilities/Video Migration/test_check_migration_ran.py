"""Unit tests for check_migration_ran.evaluate.

Run from this directory:  python -m unittest test_check_migration_ran -v
"""
import unittest
from datetime import datetime, timedelta, timezone

from check_migration_ran import evaluate

NOW = datetime(2026, 10, 20, 4, 0, tzinfo=timezone.utc)


def run(minutes_ago, status='completed', conclusion='success'):
    created = NOW - timedelta(minutes=minutes_ago)
    return {'created_at': created.isoformat().replace('+00:00', 'Z'),
            'status': status, 'conclusion': conclusion}


class EvaluateTests(unittest.TestCase):
    def test_successful_recent_run_is_fine(self):
        self.assertIsNone(evaluate([run(300)], NOW))

    def test_no_runs_at_all(self):
        self.assertIn("No Video Migration run", evaluate([], NOW))

    def test_only_an_old_run_counts_as_missing(self):
        self.assertIn("No Video Migration run", evaluate([run(60 * 24 * 7)], NOW))

    def test_failed_run(self):
        self.assertIn("'failure'", evaluate([run(300, conclusion='failure')], NOW))

    def test_cancelled_run(self):
        self.assertIn("'cancelled'", evaluate([run(300, conclusion='cancelled')], NOW))

    def test_queued_for_too_long_means_runner_offline(self):
        self.assertIn("waiting", evaluate([run(45, status='queued', conclusion=None)], NOW))

    def test_just_queued_is_fine(self):
        self.assertIsNone(evaluate([run(5, status='queued', conclusion=None)], NOW))

    def test_hung_run(self):
        self.assertIn("hung", evaluate([run(120, status='in_progress', conclusion=None)], NOW))

    def test_running_normally_is_fine(self):
        self.assertIsNone(evaluate([run(15, status='in_progress', conclusion=None)], NOW))

    def test_newest_run_decides(self):
        # A failure followed by a successful manual re-run is resolved.
        self.assertIsNone(evaluate([run(200, conclusion='failure'), run(100)], NOW))
        self.assertIn("'failure'", evaluate([run(200), run(100, conclusion='failure')], NOW))


if __name__ == '__main__':
    unittest.main()
