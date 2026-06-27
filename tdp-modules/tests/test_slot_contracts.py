from __future__ import annotations
import unittest
import constants


class SlotContractTests(unittest.TestCase):
    def test_four_visible_fighter_slots_are_defined(self):
        self.assertEqual(len(constants.SLOTS), 4)

    def test_slot_labels_are_unique(self):
        labels = [entry[0] for entry in constants.SLOTS]
        self.assertEqual(len(labels), len(set(labels)))

    def test_expected_slot_labels_exist(self):
        labels = {entry[0] for entry in constants.SLOTS}
        self.assertEqual(labels, {"P1-C1", "P1-C2", "P2-C1", "P2-C2"})

    def test_slot_pointer_addresses_are_unique(self):
        ptrs = [entry[1] for entry in constants.SLOTS]
        self.assertEqual(len(ptrs), len(set(ptrs)))

    def test_character_id_offset_is_positive(self):
        self.assertGreater(constants.OFF_CHAR_ID, 0)

    def test_character_pointer_slots_are_in_mem1_range(self):
        for _label, ptr, _team in constants.SLOTS:
            with self.subTest(ptr=ptr):
                self.assertGreaterEqual(ptr, constants.MEM1_LO)
                self.assertLessEqual(ptr, constants.MEM1_HI)
