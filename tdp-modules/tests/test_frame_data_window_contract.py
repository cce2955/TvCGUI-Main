from __future__ import annotations
import inspect
import unittest
from unittest.mock import patch

import frame_data_window as fdw


class FrameDataWindowContractTests(unittest.TestCase):
    def test_editor_receives_requested_slot_and_data(self):
        data = [{"slot_label": "P1-C1", "char_name": "Jun", "moves": []}]
        with patch.object(fdw, "HAVE_NEW_EDITOR", True), patch.object(fdw, "_open_new_editor") as open_editor:
            fdw.open_frame_data_window("P1-C1", data)
        open_editor.assert_called_once_with("P1-C1", data)

    def test_editor_does_not_open_for_empty_data(self):
        with patch.object(fdw, "HAVE_NEW_EDITOR", True), patch.object(fdw, "_open_new_editor") as open_editor:
            fdw.open_frame_data_window("P1-C1", [])
        open_editor.assert_not_called()

    def test_loading_message_has_no_compact_hud_sentence(self):
        source = inspect.getsource(fdw.open_frame_data_loading_window).lower()
        self.assertNotIn("compact hud", source)

    def test_loading_message_describes_live_packets(self):
        source = inspect.getsource(fdw.open_frame_data_loading_window)
        self.assertIn("Loading live, writable move packets for this fighter.", source)
