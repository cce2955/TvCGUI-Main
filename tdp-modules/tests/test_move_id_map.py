from __future__ import annotations

import importlib
import unittest


class MoveIdMapTests(unittest.TestCase):
    def setUp(self) -> None:
        import move_id_map

        self.move_id_map = importlib.reload(move_id_map)

    def test_global_normal_lookup_returns_decimal_id_labels(self) -> None:
        self.assertEqual("5A", self.move_id_map.lookup_move_name(256))
        self.assertEqual("5B", self.move_id_map.lookup_move_name(257))
        self.assertEqual("assist standby", self.move_id_map.lookup_move_name(430))

    def test_unknown_move_returns_none_instead_of_guessing(self) -> None:
        self.assertIsNone(self.move_id_map.lookup_move_name(999999))


if __name__ == "__main__":
    unittest.main()
