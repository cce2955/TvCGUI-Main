# frame_data_gui.py
#
# Thin entrypoint so callers do not need to know about fd_window.

from fd_window import open_editable_frame_data_window

__all__ = ["open_editable_frame_data_window"]
