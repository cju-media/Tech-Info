"""Unit tests for montage_folder_reminder.py.

Run from this directory:  python -m unittest test_montage_folder_reminder -v

The reminder runs every day in July, so the thing to guard is that it goes
out exactly once a year, and not at all once the collector has moved on.
"""

import datetime
import unittest

from montage_folder_reminder import email_body, reminder_due, text_message

SINCE = datetime.date(2026, 5, 1)


class ReminderDue(unittest.TestCase):
    def test_due_in_july_of_the_next_year(self):
        self.assertIsNone(reminder_due(datetime.date(2027, 7, 1), SINCE, {}))

    def test_not_outside_july(self):
        self.assertIsNotNone(reminder_due(datetime.date(2027, 6, 30), SINCE, {}))
        self.assertIsNotNone(reminder_due(datetime.date(2027, 8, 1), SINCE, {}))

    def test_once_a_year(self):
        state = {'last_reminded_year': 2027}
        self.assertIsNotNone(reminder_due(datetime.date(2027, 7, 2), SINCE, state))
        self.assertIsNone(reminder_due(datetime.date(2028, 7, 1),
                                       datetime.date(2027, 5, 1), state))

    def test_not_once_moved_on(self):
        self.assertIsNotNone(reminder_due(datetime.date(2027, 7, 1),
                                          datetime.date(2027, 5, 1), {}))

    def test_not_in_the_first_july(self):
        # The folder was only just made; July 2026 has nothing to move on from.
        self.assertIsNotNone(reminder_due(datetime.date(2026, 7, 15), SINCE, {}))

    def test_force_overrides_everything(self):
        self.assertIsNone(reminder_due(datetime.date(2026, 9, 26), SINCE,
                                       {'last_reminded_year': 2026}, force=True))


class Messages(unittest.TestCase):
    def test_both_name_the_folder_and_what_to_change(self):
        for text in (text_message('abc123', SINCE),
                     email_body('abc123', SINCE, datetime.date(2027, 7, 1))):
            self.assertIn('https://drive.google.com/drive/folders/abc123', text)
            self.assertIn('DEFAULT_FOLDER_ID', text)
            self.assertIn('2026-05-01', text)

    def test_email_suggests_next_years_name(self):
        self.assertIn('2028 Assets', email_body('x', SINCE, datetime.date(2027, 7, 1)))


if __name__ == '__main__':
    unittest.main()
