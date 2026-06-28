"""Contracts for the current runtime Yami preview-route tick.

The historical pointer bridge was retired because it wrote a scratch owner pair
that the renderer did not consume.  The active route is a guarded loaded-scene
patch, installed only while Extra Characters is active on Character Select.
"""
from __future__ import annotations

import unittest

import char_test_runtime as runtime


class YamiRandomHoverBridgeContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.saved = {
            "status": runtime._select_screen_status,
            "retired": runtime._restore_retired_yami_preview_bridge,
            "scene_restore": runtime._restore_yami_runtime_preview_route_only,
            "install": runtime._install_yami_runtime_preview_route,
            "state": dict(runtime._ROSTER_STATE),
        }
        self.calls: list[str] = []
        runtime._ROSTER_STATE["extra_characters_requested"] = False
        runtime._ROSTER_STATE["extra_characters_enabled"] = False

    def tearDown(self) -> None:
        runtime._select_screen_status = self.saved["status"]
        runtime._restore_retired_yami_preview_bridge = self.saved["retired"]
        runtime._restore_yami_runtime_preview_route_only = self.saved["scene_restore"]
        runtime._install_yami_runtime_preview_route = self.saved["install"]
        runtime._ROSTER_STATE.clear()
        runtime._ROSTER_STATE.update(self.saved["state"])

    def _stub_routes(
        self,
        retired_result: tuple[int, int],
        scene_restore_result: tuple[int, int],
        install_result: tuple[int, int],
    ) -> None:
        def retired() -> tuple[int, int]:
            self.calls.append("retired")
            return retired_result

        def scene_restore() -> tuple[int, int]:
            self.calls.append("scene_restore")
            return scene_restore_result

        def scene_install() -> tuple[int, int]:
            self.calls.append("scene_install")
            return install_result

        runtime._restore_retired_yami_preview_bridge = retired
        runtime._restore_yami_runtime_preview_route_only = scene_restore
        runtime._install_yami_runtime_preview_route = scene_install

    def test_inactive_character_select_does_not_touch_preview_route(self) -> None:
        runtime._select_screen_status = lambda: {"active": False, "patch_present": False}
        self._stub_routes((1, 0), (2, 0), (3, 0))

        self.assertEqual(runtime._tick_yami_runtime_preview_route(), (0, 0))
        self.assertEqual(self.calls, [])

    def test_disabled_extra_mode_restores_scene_route_only(self) -> None:
        runtime._select_screen_status = lambda: {"active": True, "patch_present": True}
        self._stub_routes((1, 0), (2, 0), (9, 0))

        self.assertEqual(runtime._tick_yami_runtime_preview_route(), (2, 0))
        self.assertEqual(self.calls, ["scene_restore"])

    def test_missing_roster_patch_restores_scene_route_only(self) -> None:
        runtime._ROSTER_STATE["extra_characters_requested"] = True
        runtime._ROSTER_STATE["extra_characters_enabled"] = True
        runtime._select_screen_status = lambda: {"active": True, "patch_present": False}
        self._stub_routes((1, 0), (3, 0), (9, 0))

        self.assertEqual(runtime._tick_yami_runtime_preview_route(), (3, 0))
        self.assertEqual(self.calls, ["scene_restore"])

    def test_enabled_extra_mode_retires_old_bridge_then_installs_scene_route(self) -> None:
        runtime._ROSTER_STATE["extra_characters_requested"] = True
        runtime._ROSTER_STATE["extra_characters_enabled"] = True
        runtime._select_screen_status = lambda: {"active": True, "patch_present": True}
        self._stub_routes((1, 0), (8, 0), (16, 0))

        self.assertEqual(runtime._tick_yami_runtime_preview_route(), (17, 0))
        self.assertEqual(self.calls, ["retired", "scene_install"])

    def test_enabled_route_aggregates_failures_without_skipping_scene_install(self) -> None:
        runtime._ROSTER_STATE["extra_characters_requested"] = True
        runtime._ROSTER_STATE["extra_characters_enabled"] = True
        runtime._select_screen_status = lambda: {"active": True, "patch_present": True}
        self._stub_routes((1, 1), (8, 0), (4, 2))

        self.assertEqual(runtime._tick_yami_runtime_preview_route(), (5, 3))
        self.assertEqual(self.calls, ["retired", "scene_install"])


if __name__ == "__main__":
    unittest.main()
