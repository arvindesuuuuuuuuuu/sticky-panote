import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import sticky_panote as app


class HubstaffLunchScheduleTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        config_patch = patch.object(
            app, "CONFIG_FILE", Path(self.directory.name) / "config.json"
        )
        config_patch.start()
        self.addCleanup(config_patch.stop)
        self.window = SimpleNamespace(hubstaff_lunch_resume_date=None)
        command_patch = patch.object(app.subprocess, "run")
        self.run_command = command_patch.start()
        self.addCleanup(command_patch.stop)
        self.run_command.return_value.returncode = 0

    def check(self, hour, minute=0, tracking=False, day=2):
        return app.StickyPanoteWindow.apply_hubstaff_lunch_schedule(
            self.window,
            tracking,
            datetime(2026, 10, day, hour, minute, tzinfo=app.HUBSTAFF_TIMEZONE),
        )

    def test_no_pause_before_noon(self):
        self.assertFalse(self.check(11, 59, tracking=True))
        self.run_command.assert_not_called()

    def test_pause_at_noon_and_resume_at_one(self):
        self.assertTrue(self.check(12, tracking=True))
        self.assertEqual(self.run_command.call_args.args[0][-1], "stop")
        self.assertEqual(
            app.load_app_config()["hubstaff_lunch_resume_date"], "2026-10-02"
        )
        self.run_command.reset_mock()
        self.assertFalse(self.check(12, 59))
        self.run_command.assert_not_called()
        self.assertTrue(self.check(13))
        self.assertEqual(self.run_command.call_args.args[0][-1], "resume")
        self.assertIsNone(app.load_app_config()["hubstaff_lunch_resume_date"])
        self.run_command.reset_mock()
        self.assertFalse(self.check(13, 1))
        self.run_command.assert_not_called()

    def test_already_stopped_timer_during_lunch_resumes_at_one(self):
        self.assertFalse(self.check(12))
        self.run_command.assert_not_called()
        self.assertEqual(
            app.load_app_config()["hubstaff_lunch_resume_date"], "2026-10-02"
        )
        self.assertTrue(self.check(13))
        self.assertEqual(self.run_command.call_args.args[0][-1], "resume")

    def test_restart_during_lunch_with_stopped_timer_keeps_resume(self):
        self.check(12, 30)
        self.window = SimpleNamespace(
            hubstaff_lunch_resume_date=app.load_app_config()["hubstaff_lunch_resume_date"]
        )
        self.assertFalse(self.check(12, 59))
        self.assertTrue(self.check(13))
        self.assertEqual(self.run_command.call_args.args[0][-1], "resume")

    def test_no_lunch_observed_does_not_resume_stopped_timer(self):
        self.assertFalse(self.check(11, 59))
        self.assertFalse(self.check(13))
        self.run_command.assert_not_called()

    def test_restart_and_wake_after_one_resumes_saved_pause(self):
        self.check(12, 30, tracking=True)
        self.window = SimpleNamespace(
            hubstaff_lunch_resume_date=app.load_app_config()["hubstaff_lunch_resume_date"]
        )
        self.assertTrue(self.check(14))
        self.assertEqual(self.run_command.call_args.args[0][-1], "resume")

    def test_previous_day_pause_does_not_start_tracking(self):
        self.check(12, tracking=True)
        self.run_command.reset_mock()
        self.assertFalse(self.check(13, day=3))
        self.assertIsNone(self.window.hubstaff_lunch_resume_date)
        self.run_command.assert_not_called()

    def test_failed_pause_and_resume_are_retried(self):
        self.run_command.return_value.returncode = 1
        self.assertFalse(self.check(12, tracking=True))
        self.assertIsNone(self.window.hubstaff_lunch_resume_date)
        self.run_command.return_value.returncode = 0
        self.assertTrue(self.check(12, 1, tracking=True))
        self.run_command.return_value.returncode = 1
        self.assertFalse(self.check(13))
        self.assertEqual(self.window.hubstaff_lunch_resume_date, "2026-10-02")
        self.run_command.return_value.returncode = 0
        self.assertTrue(self.check(13, 1))

    def test_manual_resume_after_one_clears_pending_schedule(self):
        self.check(12, tracking=True)
        self.run_command.reset_mock()
        self.assertFalse(self.check(13, tracking=True))
        self.assertIsNone(self.window.hubstaff_lunch_resume_date)
        self.run_command.assert_not_called()


if __name__ == "__main__":
    unittest.main()
