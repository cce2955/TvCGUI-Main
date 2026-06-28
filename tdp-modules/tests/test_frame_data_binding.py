from __future__ import annotations
import unittest

from tvcgui.features.frame_data.binding import (
    binding_from_row,
    binding_from_snapshot,
    binding_matches,
    editable_row_for_live_slot,
    is_live_proven,
    live_binding,
    row_for_slot,
)


def row(slot="P1-C1", cid=7, table=0x90001000, base=0x91000000, moves=True):
    return {
        "slot_label": slot,
        "char_id": cid,
        "chr_tbl_abs": table,
        "fighter_base_abs": base,
        "profile_key": f"id_{cid}",
        "moves": [{"damage_addr": 0x90002000}] if moves else [],
    }


def snap(cid=7, base=0x91000000):
    return {"id": cid, "base": base}


class FrameDataBindingTests(unittest.TestCase):
    def test_row_for_slot_finds_exact_label(self):
        self.assertIs(row_for_slot([row("P2-C1"), row("P1-C1")], "P1-C1")["slot_label"], "P1-C1")

    def test_row_for_slot_does_not_fallback_to_first_row(self):
        self.assertIsNone(row_for_slot([row("P2-C1")], "P1-C1"))

    def test_snapshot_prefers_live_id_over_name(self):
        binding = binding_from_snapshot("P1-C1", {"id": 17, "name": "Jun"})
        self.assertEqual(binding["char_id"], 17)

    def test_live_binding_is_proven_when_preview_matches_hud(self):
        binding = live_binding("P1-C1", [row(cid=17)], snap(17))
        self.assertTrue(is_live_proven(binding))
        self.assertEqual(binding["chr_tbl_abs"], 0x90001000)

    def test_live_binding_rejects_stale_preview_character(self):
        binding = live_binding("P1-C1", [row(cid=1)], snap(17))
        self.assertFalse(is_live_proven(binding))
        self.assertEqual(binding["char_id"], 17)

    def test_live_binding_rejects_preview_without_table(self):
        bad = row(cid=17)
        bad["chr_tbl_abs"] = 0
        self.assertFalse(is_live_proven(live_binding("P1-C1", [bad], snap(17))))

    def test_binding_match_accepts_same_live_fighter(self):
        expected = live_binding("P1-C1", [row(cid=17)], snap(17))
        self.assertTrue(binding_matches(expected, row(cid=17)))

    def test_binding_match_rejects_old_ryu_for_new_jun_same_slot(self):
        expected = live_binding("P1-C1", [row(cid=17)], snap(17))
        self.assertFalse(binding_matches(expected, row(cid=1)))

    def test_binding_match_rejects_same_character_from_previous_table(self):
        expected = live_binding("P1-C1", [row(cid=17, table=0x90001000)], snap(17))
        self.assertFalse(binding_matches(expected, row(cid=17, table=0x90009000)))

    def test_binding_match_rejects_different_slot_even_same_character(self):
        expected = live_binding("P1-C1", [row("P1-C1", 17)], snap(17))
        self.assertFalse(binding_matches(expected, row("P2-C1", 17)))

    def test_binding_match_rejects_unknown_expected_identity(self):
        expected = binding_from_snapshot("P1-C1", snap(17))
        self.assertFalse(binding_matches(expected, row(cid=17)))

    def test_editable_row_returns_matching_current_row(self):
        current = row(cid=17)
        self.assertIs(editable_row_for_live_slot("P1-C1", [current], [current], snap(17)), current)

    def test_editable_row_blocks_stale_workbench_row(self):
        preview = row(cid=17)
        stale = row(cid=1)
        self.assertIsNone(editable_row_for_live_slot("P1-C1", [stale], [preview], snap(17)))

    def test_editable_row_blocks_previous_match_same_character(self):
        preview = row(cid=17, table=0x90001000)
        stale = row(cid=17, table=0x90009000)
        self.assertIsNone(editable_row_for_live_slot("P1-C1", [stale], [preview], snap(17)))

    def test_base_mismatch_blocks_when_both_bases_present(self):
        expected = live_binding("P1-C1", [row(cid=17, base=0x91000000)], snap(17, 0x91000000))
        self.assertFalse(binding_matches(expected, row(cid=17, base=0x92000000)))

    def test_legacy_candidate_without_base_can_match_if_table_matches(self):
        candidate = row(cid=17)
        candidate.pop("fighter_base_abs")
        expected = live_binding("P1-C1", [row(cid=17)], snap(17))
        self.assertTrue(binding_matches(expected, candidate))
