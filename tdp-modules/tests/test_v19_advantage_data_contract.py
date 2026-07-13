from __future__ import annotations

import json
import unittest
from collections import Counter

from tests.v19_contract_helpers import path, read
from tvcgui.ui import advantage_window as adv


class V19AdvantageDataContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        adv._ADVANTAGE_CACHE.update({"next_check": 0.0, "stamp": None, "data": None})
        cls.data = adv.load_observed_advantage_data(force=True)
        cls.ryu = next(char for char in cls.data["chars"] if char.get("name") == "Ryu")

    def test_category_rank_is_normal_special_super(self):
        self.assertEqual(adv._ADV_CATEGORY_RANK, {"normal": 0, "special": 1, "super": 2})

    def test_category_labels_are_present(self):
        self.assertEqual(adv._ADV_CATEGORY_LABEL["normal"], "Normals")
        self.assertEqual(adv._ADV_CATEGORY_LABEL["special"], "Specials")
        self.assertEqual(adv._ADV_CATEGORY_LABEL["super"], "Supers")

    def test_special_kind_is_classified(self):
        self.assertEqual(adv._adv_move_category({"kind": "special"}, "Fireball"), "special")

    def test_super_kind_is_classified(self):
        self.assertEqual(adv._adv_move_category({"kind": "super"}, "Hyper"), "super")

    def test_normal_kind_is_classified(self):
        self.assertEqual(adv._adv_move_category({"kind": "normal"}, "5A"), "normal")

    def test_unknown_named_move_is_not_forced_to_normal(self):
        self.assertIsNone(adv._adv_move_category({}, ""))

    def test_synthetic_profile_keeps_all_three_categories(self):
        doc = {"profiles": {"id_99_test": {"char_id": 99, "char_name": "Test", "moves": [
            {"move_name": "5A", "kind": "normal", "adv_block_observed": "-2", "startup": 4},
            {"move_name": "Burst", "kind": "special", "adv_block_observed": "-8", "startup": 7},
            {"move_name": "Final", "kind": "super", "adv_block_observed": "-20", "startup": 10},
        ]}}}
        data = adv._adv_build_profile_data(doc)
        self.assertEqual([item["category"] for item in data["chars"][0]["attacks"]], ["normal", "special", "super"])

    def test_special_key_uses_category_prefix(self):
        doc = {"profiles": {"id_99_test": {"char_name": "Test", "moves": [
            {"move_name": "Burst", "kind": "special", "adv_block_observed": "-8"},
        ]}}}
        item = adv._adv_build_profile_data(doc)["chars"][0]["attacks"][0]
        self.assertTrue(item["key"].startswith("special:"))

    def test_super_key_uses_category_prefix(self):
        doc = {"profiles": {"id_99_test": {"char_name": "Test", "moves": [
            {"move_name": "Final", "kind": "super", "adv_block_observed": "-20"},
        ]}}}
        item = adv._adv_build_profile_data(doc)["chars"][0]["attacks"][0]
        self.assertTrue(item["key"].startswith("super:"))

    def test_same_label_in_different_categories_does_not_collide(self):
        doc = {"profiles": {"id_99_test": {"char_name": "Test", "moves": [
            {"move_name": "5A", "kind": "normal", "adv_block_observed": "-2"},
            {"move_name": "5A", "kind": "special", "adv_block_observed": "-8"},
            {"move_name": "5A", "kind": "super", "adv_block_observed": "-20"},
        ]}}}
        attacks = adv._adv_build_profile_data(doc)["chars"][0]["attacks_by_key"]
        self.assertEqual(len(attacks), 3)

    def test_slash_advantage_value_is_preserved(self):
        self.assertEqual(adv._adv_clean_value("-26/-30/-42"), "-26/-30/-42")

    def test_slash_advantage_first_window_is_parseable(self):
        self.assertEqual(adv._adv_int_value("-26/-30/-42"), -26)

    def test_startup_below_minimum_is_rejected(self):
        self.assertIsNone(adv._adv_startup_value({"startup": 2}))

    def test_valid_startup_is_kept(self):
        self.assertEqual(adv._adv_startup_value({"startup": 3}), 3)

    def test_default_category_checkboxes_are_enabled(self):
        source = read("tvcgui/ui/advantage_window.py")
        self.assertIn('"normal": tk.BooleanVar(value=True)', source)
        self.assertIn('"special": tk.BooleanVar(value=True)', source)
        self.assertIn('"super": tk.BooleanVar(value=True)', source)

    def test_toolbar_contains_specials_label(self):
        self.assertIn('("special", "Specials")', read("tvcgui/ui/advantage_window.py"))

    def test_toolbar_contains_supers_label(self):
        self.assertIn('("super", "Supers")', read("tvcgui/ui/advantage_window.py"))

    def test_primary_profile_data_exists(self):
        self.assertTrue(path("data/frame_data/frame_data_preview_profiles.json").is_file())

    def test_observed_profile_data_exists(self):
        self.assertTrue(path("data/frame_data/observed_block_advantage_profiles.json").is_file())

    def test_primary_profile_has_28_characters(self):
        doc = json.loads(path("data/frame_data/frame_data_preview_profiles.json").read_text(encoding="utf-8"))
        self.assertEqual(len(doc.get("profiles") or {}), 28)

    def test_observed_profile_has_at_least_26_characters(self):
        doc = json.loads(path("data/frame_data/observed_block_advantage_profiles.json").read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(doc.get("profiles") or {}), 26)

    def test_loaded_matrix_has_28_characters(self):
        self.assertEqual(len(self.data["chars"]), 28)

    def test_loaded_matrix_has_observed_rows_for_every_character(self):
        self.assertEqual(len(self.data["observed_chars"]), 28)

    def test_loaded_matrix_contains_many_specials(self):
        counts = Counter(item["category"] for char in self.data["chars"] for item in char["attacks"])
        self.assertGreaterEqual(counts["special"], 70)

    def test_loaded_matrix_contains_many_supers(self):
        counts = Counter(item["category"] for char in self.data["chars"] for item in char["attacks"])
        self.assertGreaterEqual(counts["super"], 35)

    def test_ryu_has_12_normal_rows(self):
        counts = Counter(item["category"] for item in self.ryu["attacks"])
        self.assertEqual(counts["normal"], 12)

    def test_ryu_has_3_special_rows(self):
        counts = Counter(item["category"] for item in self.ryu["attacks"])
        self.assertEqual(counts["special"], 3)

    def test_ryu_has_2_super_rows(self):
        counts = Counter(item["category"] for item in self.ryu["attacks"])
        self.assertEqual(counts["super"], 2)

    def test_ryu_special_labels_are_exact(self):
        labels = [item["label"] for item in self.ryu["attacks"] if item["category"] == "special"]
        self.assertEqual(labels, ["Shoryuken", "Tatsumaki", "Joudan Sokutogeri"])

    def test_ryu_super_labels_are_exact(self):
        labels = [item["label"] for item in self.ryu["attacks"] if item["category"] == "super"]
        self.assertEqual(labels, ["Shinku Tatsumaki Senpukyaku", "Shin Shoryuken"])

    def test_ryu_shoryuken_keeps_multi_strength_advantage(self):
        move = next(item for item in self.ryu["attacks"] if item["label"] == "Shoryuken")
        self.assertEqual(move["adv"], "-26/-30/-42")

    def test_special_and_super_rows_follow_normals(self):
        categories = [item["category"] for item in self.ryu["attacks"]]
        self.assertEqual(categories, sorted(categories, key=adv._ADV_CATEGORY_RANK.get))

    def test_unique_special_window_is_enabled(self):
        self.assertTrue(adv._adv_uses_unique_attack_window({"category": "special"}))

    def test_unique_super_window_is_enabled(self):
        self.assertTrue(adv._adv_uses_unique_attack_window({"category": "super"}))

    def test_normal_does_not_use_unique_attack_window(self):
        self.assertFalse(adv._adv_uses_unique_attack_window({"category": "normal"}))


if __name__ == "__main__":
    unittest.main()
