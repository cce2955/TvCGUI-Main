from __future__ import annotations

"""Regression contract for the live Character Select presentation routes.

The old chrsel.seq material-ID tile experiment is retired. The working route is
now DOL presentation tags plus the renderer-facing focus cache. This test makes
sure Extra Characters services both routes each tick, including the Solo/null
ID 0 -> Zero path owned by the DOL tag service.
"""

import unittest

import tvcgui.features.character_select.runtime as runtime


class YamiHoverRouteWiringTests(unittest.TestCase):
    def setUp(self) -> None:
        self.saved_state = dict(runtime._ROSTER_STATE)
        self.saved_queue = list(runtime._ROSTER_QUEUE)
        self.saved = {
            "_tick_extra_characters_request": runtime._tick_extra_characters_request,
            "_restore_yami_runtime_preview_route_only": runtime._restore_yami_runtime_preview_route_only,
            "_restore_yami_wheel_random_icon_route_only": runtime._restore_yami_wheel_random_icon_route_only,
            "_restore_yami_hover_icon_id_route_only": runtime._restore_yami_hover_icon_id_route_only,
            "_tick_yami_dol_icon_tag_route": runtime._tick_yami_dol_icon_tag_route,
            "_tick_yami_hover_display_profile_route": runtime._tick_yami_hover_display_profile_route,
        }
        runtime._ROSTER_STATE["extra_characters_requested"] = True
        runtime._ROSTER_STATE["solo_team_requested"] = False
        runtime._ROSTER_QUEUE.clear()
        self.calls: list[str] = []
        runtime._tick_extra_characters_request = lambda: self.calls.append("extras")
        runtime._restore_yami_runtime_preview_route_only = lambda: (self.calls.append("preview_restore") or (0, 0))
        runtime._restore_yami_wheel_random_icon_route_only = lambda: (self.calls.append("random_restore") or (0, 0))
        runtime._restore_yami_hover_icon_id_route_only = lambda: (self.calls.append("legacy_tile_restore") or (0, 0))
        runtime._tick_yami_dol_icon_tag_route = lambda: (self.calls.append("dol_tags") or (0, 0))
        runtime._tick_yami_hover_display_profile_route = lambda: (self.calls.append("profile") or (0, 0))

    def tearDown(self) -> None:
        runtime._ROSTER_STATE.clear()
        runtime._ROSTER_STATE.update(self.saved_state)
        runtime._ROSTER_QUEUE.clear()
        runtime._ROSTER_QUEUE.extend(self.saved_queue)
        for name, value in self.saved.items():
            setattr(runtime, name, value)

    def test_extra_characters_tick_services_tag_and_profile_routes(self) -> None:
        runtime._tick_roster_actions()
        self.assertIn("dol_tags", self.calls)
        self.assertIn("profile", self.calls)
        self.assertLess(self.calls.index("dol_tags"), self.calls.index("profile"))
        self.assertIn("legacy_tile_restore", self.calls)


if __name__ == "__main__":
    unittest.main(verbosity=2)
