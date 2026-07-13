from __future__ import annotations

import contextlib
import io
import unittest
from unittest import mock

from tvcgui.ui import advantage_window as adv


class _Widget:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.text = kwargs.get("text", "")
        self.destroyed = False
        self.configured = []

    def pack(self, *args, **kwargs):
        return self

    def grid(self, *args, **kwargs):
        return self

    def configure(self, **kwargs):
        self.configured.append(kwargs)
        if "text" in kwargs:
            self.text = kwargs["text"]

    config = configure

    def destroy(self):
        self.destroyed = True


class _Window(_Widget):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.title_value = None
        self.protocols = {}
        self.exists = True
        self.lifted = False
        self.focused = False
        self.updated = False

    def title(self, value):
        self.title_value = value

    def geometry(self, value):
        self.geometry_value = value

    def minsize(self, width, height):
        self.minimum = (width, height)

    def protocol(self, name, callback):
        self.protocols[name] = callback

    def update_idletasks(self):
        self.updated = True

    def winfo_exists(self):
        return self.exists

    def destroy(self):
        self.destroyed = True
        self.exists = False

    def lift(self):
        self.lifted = True

    def focus_force(self):
        self.focused = True


class _FakeTk:
    def __init__(self):
        self.windows = []
        self.labels = []
        self.Frame = _Widget

    def Toplevel(self, master):
        win = _Window(master)
        self.windows.append(win)
        return win

    def Label(self, *args, **kwargs):
        label = _Widget(*args, **kwargs)
        self.labels.append(label)
        return label


class _FakeTtk:
    class Style:
        def __init__(self, *args, **kwargs):
            pass

        def theme_use(self, *args, **kwargs):
            pass

        def configure(self, *args, **kwargs):
            pass

        def map(self, *args, **kwargs):
            pass


class V19AdvantagePopupContractTests(unittest.TestCase):
    def setUp(self):
        self.fake_tk = _FakeTk()
        self.old_tk = adv.tk
        self.old_ttk = adv.ttk
        self.old_tk_call = adv.tk_call
        self.old_win = adv._ADV_TK_WIN
        self.old_source = adv._ADV_TK_SOURCE_KEY
        adv.tk = self.fake_tk
        adv.ttk = _FakeTtk
        adv.tk_call = lambda callback: callback(object())
        adv._ADV_TK_WIN = None
        adv._ADV_TK_SOURCE_KEY = None

    def tearDown(self):
        adv.tk = self.old_tk
        adv.ttk = self.old_ttk
        adv.tk_call = self.old_tk_call
        adv._ADV_TK_WIN = self.old_win
        adv._ADV_TK_SOURCE_KEY = self.old_source

    def _open_empty(self):
        with mock.patch.object(adv, "load_observed_advantage_data", return_value={"chars": [], "by_key": {}, "by_name": {}, "by_id": {}}):
            adv.open_advantage_window([], {})
        return self.fake_tk.windows[-1]

    def test_empty_data_still_creates_window(self):
        win = self._open_empty()
        self.assertIs(adv._ADV_TK_WIN, win)

    def test_empty_data_window_has_exact_title(self):
        win = self._open_empty()
        self.assertEqual(win.title_value, "Advantage Matrix")

    def test_empty_data_window_has_expected_geometry(self):
        win = self._open_empty()
        self.assertEqual(win.geometry_value, "1240x720")

    def test_empty_data_window_has_minimum_size(self):
        win = self._open_empty()
        self.assertEqual(win.minimum, (900, 520))

    def test_empty_data_message_is_visible(self):
        self._open_empty()
        texts = [label.text for label in self.fake_tk.labels]
        self.assertTrue(any("No observed frame-data profiles were found." in text for text in texts))

    def test_empty_data_window_is_lifted(self):
        win = self._open_empty()
        self.assertTrue(win.lifted)

    def test_empty_data_window_is_focused(self):
        win = self._open_empty()
        self.assertTrue(win.focused)

    def test_popup_updates_shell_before_loading(self):
        win = self._open_empty()
        self.assertTrue(win.updated)

    def test_close_protocol_destroys_window(self):
        win = self._open_empty()
        win.protocols["WM_DELETE_WINDOW"]()
        self.assertTrue(win.destroyed)

    def test_close_protocol_clears_global(self):
        win = self._open_empty()
        win.protocols["WM_DELETE_WINDOW"]()
        self.assertIsNone(adv._ADV_TK_WIN)

    def test_repeated_open_replaces_existing_window(self):
        first = self._open_empty()
        second = self._open_empty()
        self.assertTrue(first.destroyed)
        self.assertIs(adv._ADV_TK_WIN, second)
        self.assertIsNot(first, second)

    def test_reopen_after_close_creates_new_window(self):
        first = self._open_empty()
        first.protocols["WM_DELETE_WINDOW"]()
        second = self._open_empty()
        self.assertIsNot(first, second)
        self.assertIs(adv._ADV_TK_WIN, second)

    def test_data_exception_keeps_window_open(self):
        with mock.patch.object(adv, "load_observed_advantage_data", side_effect=RuntimeError("broken data")):
            with contextlib.redirect_stdout(io.StringIO()):
                adv.open_advantage_window([], {})
        win = self.fake_tk.windows[-1]
        self.assertTrue(win.exists)
        self.assertIs(adv._ADV_TK_WIN, win)

    def test_data_exception_is_visible_in_window(self):
        with mock.patch.object(adv, "load_observed_advantage_data", side_effect=RuntimeError("broken data")):
            with contextlib.redirect_stdout(io.StringIO()):
                adv.open_advantage_window([], {})
        texts = [label.text for label in self.fake_tk.labels]
        self.assertTrue(any("RuntimeError: broken data" in text for text in texts))

    def test_data_exception_prints_failure_and_traceback(self):
        out = io.StringIO()
        err = io.StringIO()
        with mock.patch.object(adv, "load_observed_advantage_data", side_effect=RuntimeError("broken data")):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                adv.open_advantage_window([], {})
        self.assertIn("[advantage] data load failed", out.getvalue())
        self.assertIn("RuntimeError: broken data", err.getvalue())

    def test_tk_unavailable_prints_visible_console_message(self):
        out = io.StringIO()
        with mock.patch.object(adv, "_adv_ensure_tk", return_value=False):
            with contextlib.redirect_stdout(out):
                adv.open_advantage_window([], {})
        self.assertIn("[advantage] tkinter window unavailable", out.getvalue())


if __name__ == "__main__":
    unittest.main()
