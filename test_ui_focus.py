import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import sticky_panote as app


class DeferredFocusTest(unittest.TestCase):
    def test_successful_focus_does_not_repeat_in_main_loop(self):
        widget = SimpleNamespace(grab_focus=Mock(return_value=True))
        context = app.GLib.MainContext.new()
        source = app.GLib.idle_source_new()
        source.set_callback(lambda *_: app.grab_focus_once(widget))
        source.attach(context)
        for _ in range(10):
            context.iteration(False)
        widget.grab_focus.assert_called_once_with()
        self.assertTrue(source.is_destroyed())

    def test_failed_focus_also_removes_callback(self):
        widget = SimpleNamespace(grab_focus=Mock(return_value=False))
        self.assertEqual(app.grab_focus_once(widget), app.GLib.SOURCE_REMOVE)

    def test_search_uses_callback_that_removes_itself(self):
        window = SimpleNamespace(
            search_entry=object(),
            search_revealer=Mock(),
            search_button=Mock(),
        )
        window.search_button.get_active.return_value = True
        with patch.object(app.GLib, "idle_add") as idle:
            app.StickyPanoteWindow.focus_search(window)
            idle.assert_called_once_with(app.grab_focus_once, window.search_entry)
            idle.reset_mock()
            app.StickyPanoteWindow.on_search_toggled(window, window.search_button)
            idle.assert_called_once_with(app.grab_focus_once, window.search_entry)


if __name__ == "__main__":
    unittest.main()
