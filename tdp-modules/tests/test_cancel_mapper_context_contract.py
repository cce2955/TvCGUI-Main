from __future__ import annotations

import unittest

from tvcgui.features.frame_data import cancel_mapper as mapper


class CancelMapperContextContractTests(unittest.TestCase):
    def test_special_target_is_category_eligible_not_confirmed_allowed(self):
        source = {"id": 0x0105, "abs": 0x90955C7C, "move_name": "2C", "kind": "normal"}
        target = {
            "id": 0x0140,
            "abs": 0x90946F8E,
            "move_name": "Shooting Star Kick A",
            "kind": "special",
        }

        result = mapper.classify_cancel(source, target)

        self.assertEqual("ELIGIBLE", result["status"])
        self.assertIn("ground/air", result["reason"])

    def test_super_target_is_category_eligible_not_confirmed_allowed(self):
        source = {"id": 0x0105, "abs": 0x90955C7C, "move_name": "2C", "kind": "normal"}
        target = {
            "id": 0x0170,
            "abs": 0x90960F44,
            "move_name": "Super Destruction Beam",
            "kind": "super",
        }

        result = mapper.classify_cancel(source, target)

        self.assertEqual("ELIGIBLE", result["status"])
        self.assertIn("meter", result["reason"])

    def test_ground_normal_chain_remains_confirmed_allowed(self):
        source = {"id": 0x0100, "abs": 0x90954424, "move_name": "5A", "kind": "normal"}
        target = {"id": 0x0103, "abs": 0x909553A0, "move_name": "2A", "kind": "normal"}

        result = mapper.classify_cancel(source, target)

        self.assertEqual("ALLOWED", result["status"])


if __name__ == "__main__":
    unittest.main()
