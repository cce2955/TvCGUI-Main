"""Source contracts for the master overlay event queue safety guard."""
from __future__ import annotations

import ast
from pathlib import Path
import unittest


MASTER_RENDERER = (
    Path(__file__).resolve().parents[1]
    / "tvcgui"
    / "features"
    / "overlay"
    / "master_renderer.py"
)


class MasterEventQueueGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = MASTER_RENDERER.read_text(encoding="utf-8")
        ast.parse(cls.source)

    def test_event_get_is_protected_by_exception_handler(self) -> None:
        start = self.source.index("    def handle_events(self) -> None:")
        end = self.source.index("\n    def clear(self) -> None:", start)
        block = self.source[start:end]
        self.assertIn("try:\n            events = pygame.event.get()", block)
        self.assertIn("except Exception as exc:", block)
        self.assertIn("return", block)

    def test_event_failures_are_rate_limited(self) -> None:
        start = self.source.index("    def handle_events(self) -> None:")
        end = self.source.index("\n    def clear(self) -> None:", start)
        block = self.source[start:end]
        self.assertIn("_event_poll_resume_time", block)
        self.assertIn("_event_poll_last_log", block)
        self.assertIn("pygame.event.clear()", block)

    def test_successful_read_resets_error_state(self) -> None:
        start = self.source.index("    def handle_events(self) -> None:")
        end = self.source.index("\n    def clear(self) -> None:", start)
        block = self.source[start:end]
        self.assertIn("self._event_poll_error_count = 0", block)
        self.assertIn("self._event_poll_resume_time = 0.0", block)


if __name__ == "__main__":
    unittest.main()
