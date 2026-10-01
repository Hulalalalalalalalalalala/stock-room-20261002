import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from stock_room import StockRoom

class ProductTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = StockRoom(self.root)

    def test_balance_from_receipts_and_issues(self):
        self.app.register("P", "Paper", "sheet")
        self.app.movement("P", 10, "IN")
        self.app.movement("P", -4, "OUT")
        self.assertEqual(StockRoom(self.root).stock("P")["quantity"], 6)
        self.assertEqual(len(self.app.history("P")), 2)

    def test_invalid_issue_preserves_ledger(self):
        self.app.register("P", "Paper", "sheet")
        self.app.movement("P", 3, "IN")
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.movement("P", -4, "OUT")
        with self.assertRaises(ValueError):
            self.app.movement("P", 2, "IN")
        self.assertEqual(before, self.app.path.read_bytes())

    def test_material_balances_are_separate(self):
        self.app.register("P", "Paper", "sheet")
        self.app.register("B", "Box", "piece")
        self.app.movement("P", 3, "IN")
        self.assertEqual(self.app.stock("B")["quantity"], 0)

    def test_cli_demo_and_invalid_action(self):
        result = subprocess.run([sys.executable, "-m", "stock_room", "--root", str(self.root), "demo"], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual(value["quantity"], 14)
        failed = subprocess.run([sys.executable, "-m", "stock_room", "--root", str(self.root), "not-an-action"], text=True, capture_output=True)
        self.assertEqual(failed.returncode, 2)


class CountTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = StockRoom(self.root)
        self.app.register("PAPER", "包装纸", "张")

    def seed_paper(self):
        self.app.movement("PAPER", 20, "IN-001")
        self.app.movement("PAPER", -6, "OUT-001")

    def test_count_adjusts_stock_and_appends_movement(self):
        self.seed_paper()
        record = self.app.count("PAPER", 11, "CNT-001")
        self.assertEqual(record, {"code": "PAPER", "reference": "CNT-001", "before": 14, "counted": 11, "difference": -3})
        self.assertEqual(self.app.stock("PAPER")["quantity"], 11)
        self.assertEqual([row["quantity"] for row in self.app.history("PAPER")], [20, -6, -3])
        self.assertEqual(self.app.history("PAPER")[-1]["reference"], "CNT-001")

    def test_fixed_sample_two_counts_then_movement_survives_reopen(self):
        self.seed_paper()
        first = self.app.count("PAPER", 11, "CNT-001")
        second = self.app.count("PAPER", 11, "CNT-002")
        self.assertEqual((first["before"], first["difference"]), (14, -3))
        self.assertEqual((second["before"], second["counted"], second["difference"]), (11, 11, 0))
        self.assertEqual(len(self.app.history("PAPER")), 3)
        self.app.movement("PAPER", 2, "IN-002")
        self.assertEqual(self.app.stock("PAPER")["quantity"], 13)
        reopened = StockRoom(self.root)
        records = reopened.counts("PAPER")
        self.assertEqual([row["counted"] for row in records], [11, 11])
        self.assertEqual([row["before"] for row in records], [14, 11])
        self.assertEqual([row["difference"] for row in records], [-3, 0])
        self.assertEqual(reopened.stock("PAPER")["quantity"], 13)
        self.assertEqual(len(reopened.history("PAPER")), 4)

    def test_zero_difference_count_adds_no_movement(self):
        self.seed_paper()
        record = self.app.count("PAPER", 14, "CNT-ZERO")
        self.assertEqual(record["difference"], 0)
        self.assertEqual(len(self.app.history("PAPER")), 2)
        self.assertEqual(len(self.app.counts("PAPER")), 1)
        self.assertEqual(self.app.stock("PAPER")["quantity"], 14)

    def test_counts_empty_for_registered_material_without_counts(self):
        self.assertEqual(self.app.counts("PAPER"), [])

    def test_counts_unknown_material_rejected(self):
        with self.assertRaises(ValueError):
            self.app.counts("OTHER")

    def test_count_identifiers_are_stripped_and_case_sensitive(self):
        self.seed_paper()
        record = self.app.count("  PAPER  ", 11, "  CNT-001  ")
        self.assertEqual(record["code"], "PAPER")
        self.assertEqual(record["reference"], "CNT-001")
        with self.assertRaises(ValueError):
            self.app.count("paper", 11, "CNT-LOWER")

    def test_invalid_counted_values_rejected(self):
        self.seed_paper()
        for counted in (-1, True, False, 1.5, "11", None, [11]):
            with self.subTest(counted=counted):
                with self.assertRaises(ValueError):
                    self.app.count("PAPER", counted, "CNT-" + repr(counted))

    def test_invalid_identifiers_rejected(self):
        for code in ("", "   ", 11, None):
            with self.subTest(code=code):
                with self.assertRaises(ValueError):
                    self.app.count(code, 11, "CNT-X")
        with self.assertRaises(ValueError):
            self.app.count("PAPER", 11, "   ")
        with self.assertRaises(ValueError):
            self.app.count("PAPER", 11, 7)
        with self.assertRaises(ValueError):
            self.app.count("UNKNOWN", 11, "CNT-U")

    def test_reference_scope_is_shared(self):
        self.seed_paper()
        with self.assertRaises(ValueError):
            self.app.count("PAPER", 14, "IN-001")
        self.app.count("PAPER", 14, "CNT-ZERO")
        with self.assertRaises(ValueError):
            self.app.movement("PAPER", 1, "CNT-ZERO")
        self.app.count("PAPER", 13, "CNT-002")
        with self.assertRaises(ValueError):
            self.app.count("PAPER", 13, "CNT-002")

    def test_failed_count_preserves_file_and_histories(self):
        self.seed_paper()
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.count("PAPER", -1, "CNT-BAD")
        with self.assertRaises(ValueError):
            self.app.count("PAPER", 11, "IN-001")
        with self.assertRaises(ValueError):
            self.app.count("UNKNOWN", 11, "CNT-U")
        self.assertEqual(before, self.app.path.read_bytes())
        self.assertEqual(self.app.counts("PAPER"), [])
        self.assertEqual(self.app.stock("PAPER")["quantity"], 14)

    def test_other_materials_are_unaffected(self):
        self.app.register("BOX", "纸箱", "个")
        self.seed_paper()
        self.app.movement("BOX", 5, "BOX-IN")
        self.app.count("PAPER", 11, "CNT-001")
        self.assertEqual(self.app.stock("BOX")["quantity"], 5)
        self.assertEqual(self.app.history("BOX"), [{"code": "BOX", "quantity": 5, "reference": "BOX-IN"}])
        self.assertEqual(self.app.counts("BOX"), [])

    def test_cli_count_and_counts(self):
        self.seed_paper()
        payload = self.root / "count.json"
        payload.write_text(json.dumps({"code": "PAPER", "counted": 11, "reference": "CNT-001"}), encoding="utf-8")
        result = subprocess.run([sys.executable, "-m", "stock_room", "--root", str(self.root), "count", str(payload)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["difference"], -3)
        query = self.root / "query.json"
        query.write_text(json.dumps({"code": "PAPER"}), encoding="utf-8")
        result = subprocess.run([sys.executable, "-m", "stock_room", "--root", str(self.root), "counts", str(query)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([row["reference"] for row in json.loads(result.stdout)], ["CNT-001"])

    def test_cli_count_array_keeps_earlier_success(self):
        self.seed_paper()
        payload = self.root / "counts-batch.json"
        payload.write_text(json.dumps([
            {"code": "PAPER", "counted": 11, "reference": "CNT-001"},
            {"code": "PAPER", "counted": 10, "reference": "CNT-001"},
        ]), encoding="utf-8")
        result = subprocess.run([sys.executable, "-m", "stock_room", "--root", str(self.root), "count", str(payload)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("error", result.stderr)
        self.assertEqual(len(self.app.counts("PAPER")), 1)
        self.assertEqual(self.app.stock("PAPER")["quantity"], 11)

class ReversalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = StockRoom(self.root)
        self.app.register("PAPER", "包装纸", "张")
        self.app.movement("PAPER", 20, "IN-001")
        self.app.movement("PAPER", -6, "OUT-001")

    def test_fixed_sample_reverse_issue_survives_reopen(self):
        with self.assertRaises(ValueError):
            self.app.reverse("IN-001", "REV-IN")
        self.assertEqual(self.app.stock("PAPER")["quantity"], 14)
        record = self.app.reverse("OUT-001", "REV-OUT")
        self.assertEqual(record, {"code": "PAPER", "original_reference": "OUT-001", "reference": "REV-OUT", "quantity": 6, "balance": 20})
        reopened = StockRoom(self.root)
        self.assertEqual([row["quantity"] for row in reopened.history("PAPER")], [20, -6, 6])
        self.assertEqual(reopened.reversals("PAPER"), [record])
        self.assertEqual(reopened.stock("PAPER")["quantity"], 20)

    def test_reversals_empty_for_registered_material(self):
        self.assertEqual(self.app.reversals("PAPER"), [])

    def test_reversals_unknown_material_rejected(self):
        with self.assertRaises(ValueError):
            self.app.reversals("OTHER")

    def test_reverse_identifiers_are_stripped_and_case_sensitive(self):
        record = self.app.reverse("  OUT-001  ", "  REV-OUT  ")
        self.assertEqual(record["original_reference"], "OUT-001")
        self.assertEqual(record["reference"], "REV-OUT")
        with self.assertRaises(ValueError):
            self.app.reverse("out-001", "REV-LOWER")

    def test_reverse_invalid_identifiers_rejected(self):
        for value in ("", "   ", 11, None):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    self.app.reverse(value, "REV-X")
                with self.assertRaises(ValueError):
                    self.app.reverse("OUT-001", value)

    def test_reverse_rejects_missing_count_and_reversal_references(self):
        self.app.count("PAPER", 11, "CNT-001")
        with self.assertRaises(ValueError):
            self.app.reverse("MISSING", "REV-1")
        with self.assertRaises(ValueError):
            self.app.reverse("CNT-001", "REV-2")
        self.app.count("PAPER", 11, "CNT-ZERO")
        with self.assertRaises(ValueError):
            self.app.reverse("CNT-ZERO", "REV-3")
        self.app.reverse("OUT-001", "REV-OUT")
        with self.assertRaises(ValueError):
            self.app.reverse("REV-OUT", "REV-4")

    def test_movement_reversed_at_most_once(self):
        self.app.reverse("OUT-001", "REV-OUT")
        with self.assertRaises(ValueError):
            self.app.reverse("OUT-001", "REV-AGAIN")
        self.assertEqual(len(self.app.reversals("PAPER")), 1)

    def test_reverse_reference_scope_is_shared(self):
        self.app.count("PAPER", 14, "CNT-ZERO")
        for reference in ("IN-001", "OUT-001", "CNT-ZERO"):
            with self.subTest(reference=reference):
                with self.assertRaises(ValueError):
                    self.app.reverse("OUT-001", reference)
        self.app.reverse("OUT-001", "REV-OUT")
        with self.assertRaises(ValueError):
            self.app.movement("PAPER", 1, "REV-OUT")
        with self.assertRaises(ValueError):
            self.app.count("PAPER", 20, "REV-OUT")

    def test_reverse_acts_on_current_stock_without_rewriting_history(self):
        self.app.count("PAPER", 11, "CNT-001")
        self.app.movement("PAPER", 2, "IN-002")
        record = self.app.reverse("OUT-001", "REV-OUT")
        self.assertEqual(record["balance"], 19)
        self.assertEqual(self.app.stock("PAPER")["quantity"], 19)
        self.assertEqual([row["difference"] for row in self.app.counts("PAPER")], [-3])
        self.assertEqual([row["quantity"] for row in self.app.history("PAPER")], [20, -6, -3, 2, 6])

    def test_failed_reverse_preserves_file_and_histories(self):
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.reverse("IN-001", "REV-NEG")
        with self.assertRaises(ValueError):
            self.app.reverse("MISSING", "REV-MISS")
        with self.assertRaises(ValueError):
            self.app.reverse("OUT-001", "IN-001")
        self.assertEqual(before, self.app.path.read_bytes())
        self.assertEqual(self.app.reversals("PAPER"), [])
        self.assertEqual(self.app.stock("PAPER")["quantity"], 14)
        self.assertEqual(len(self.app.history("PAPER")), 2)

    def test_cli_reverse_and_reversals(self):
        payload = self.root / "reverse.json"
        payload.write_text(json.dumps({"original_reference": "OUT-001", "reference": "REV-OUT"}), encoding="utf-8")
        result = subprocess.run([sys.executable, "-m", "stock_room", "--root", str(self.root), "reverse", str(payload)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual((value["quantity"], value["balance"]), (6, 20))
        query = self.root / "query.json"
        query.write_text(json.dumps({"code": "PAPER"}), encoding="utf-8")
        result = subprocess.run([sys.executable, "-m", "stock_room", "--root", str(self.root), "reversals", str(query)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([row["reference"] for row in json.loads(result.stdout)], ["REV-OUT"])

    def test_cli_reverse_array_keeps_earlier_success(self):
        payload = self.root / "reverse-batch.json"
        payload.write_text(json.dumps([
            {"original_reference": "OUT-001", "reference": "REV-OUT"},
            {"original_reference": "OUT-001", "reference": "REV-AGAIN"},
        ]), encoding="utf-8")
        result = subprocess.run([sys.executable, "-m", "stock_room", "--root", str(self.root), "reverse", str(payload)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("error", result.stderr)
        self.assertEqual(len(self.app.reversals("PAPER")), 1)
        self.assertEqual(self.app.stock("PAPER")["quantity"], 20)


class MinimumStockTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = StockRoom(self.root)
        self.app.register("PAPER", "包装纸", "张")
        self.app.register("BOX", "纸箱", "个")
        self.app.movement("PAPER", 20, "IN-001")
        self.app.movement("PAPER", -6, "OUT-001")

    def test_set_minimum_returns_object_and_persists(self):
        self.assertEqual(self.app.set_minimum("  PAPER  ", 15), {"code": "PAPER", "minimum": 15})
        reopened = StockRoom(self.root)
        self.assertEqual(reopened.set_minimum("BOX", 3), {"code": "BOX", "minimum": 3})
        self.assertEqual(StockRoom(self.root).shortages()[0]["minimum"], 3)

    def test_set_minimum_overwrites_and_zero_cancels(self):
        self.app.set_minimum("PAPER", 15)
        self.assertEqual(self.app.set_minimum("PAPER", 2)["minimum"], 2)
        self.assertEqual([item["code"] for item in self.app.shortages()], [])
        self.assertEqual(self.app.set_minimum("PAPER", 0)["minimum"], 0)
        self.assertEqual(self.app.shortages(), [])

    def test_invalid_minimum_rejected(self):
        for minimum in (-1, True, False, 1.5, "15", None, [15]):
            with self.subTest(minimum=minimum):
                with self.assertRaises(ValueError):
                    self.app.set_minimum("PAPER", minimum)
        for code in ("", "   ", 11, None, "paper", "UNKNOWN"):
            with self.subTest(code=code):
                with self.assertRaises(ValueError):
                    self.app.set_minimum(code, 15)

    def test_failed_set_minimum_preserves_file_and_ledger(self):
        self.app.set_minimum("PAPER", 15)
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.set_minimum("PAPER", True)
        with self.assertRaises(ValueError):
            self.app.set_minimum("UNKNOWN", 1)
        self.assertEqual(before, self.app.path.read_bytes())
        self.assertEqual(len(self.app.history("PAPER")), 2)
        self.assertEqual(self.app.counts("PAPER"), [])
        self.assertEqual(self.app.reversals("PAPER"), [])

    def test_failed_set_minimum_on_empty_directory_creates_no_file(self):
        empty = Path(self.temp.name) / "empty"
        app = StockRoom(empty)
        with self.assertRaises(ValueError):
            app.set_minimum("PAPER", 1)
        with self.assertRaises(ValueError):
            app.set_minimum(11, 1)
        self.assertFalse((empty / "data.json").exists())

    def test_shortages_fixed_sample_survives_reopen_and_count(self):
        self.app.set_minimum("PAPER", 15)
        self.app.set_minimum("BOX", 3)
        expected = [
            {"code": "BOX", "name": "纸箱", "unit": "个", "quantity": 0, "minimum": 3, "shortage": 3},
            {"code": "PAPER", "name": "包装纸", "unit": "张", "quantity": 14, "minimum": 15, "shortage": 1},
        ]
        self.assertEqual(self.app.shortages(), expected)
        self.app.count("PAPER", 11, "CNT-001")
        self.assertEqual(self.app.shortages()[1], {"code": "PAPER", "name": "包装纸", "unit": "张", "quantity": 11, "minimum": 15, "shortage": 4})
        reopened = StockRoom(self.root)
        self.assertEqual(reopened.shortages()[1]["shortage"], 4)
        reopened.set_minimum("BOX", 0)
        self.assertEqual([item["code"] for item in StockRoom(self.root).shortages()], ["PAPER"])

    def test_shortages_unset_minimum_treated_as_zero(self):
        self.assertEqual(self.app.shortages(), [])

    def test_shortages_empty_directory_returns_empty_without_file(self):
        empty = Path(self.temp.name) / "empty"
        app = StockRoom(empty)
        self.assertEqual(app.shortages(), [])
        self.assertFalse((empty / "data.json").exists())

    def test_shortages_sorted_by_unicode_code_points(self):
        self.app.register("纸张", "A4纸", "张")
        for code, minimum in (("PAPER", 15), ("BOX", 3), ("纸张", 1)):
            self.app.set_minimum(code, minimum)
        self.assertEqual([item["code"] for item in self.app.shortages()], ["BOX", "PAPER", "纸张"])

    def test_shortages_query_does_not_modify_file(self):
        self.app.set_minimum("PAPER", 15)
        before = self.app.path.read_bytes()
        self.app.shortages()
        self.assertEqual(before, self.app.path.read_bytes())

    def test_shortages_reflect_later_movement_and_reversal(self):
        self.app.set_minimum("PAPER", 15)
        self.assertEqual(self.app.shortages()[0]["shortage"], 1)
        self.app.movement("PAPER", 1, "IN-002")
        self.assertEqual(self.app.shortages(), [])
        self.app.movement("PAPER", -1, "OUT-002")
        self.app.reverse("OUT-002", "REV-OUT")
        self.assertEqual(self.app.shortages(), [])

    def test_cli_set_minimum_and_shortages(self):
        payload = self.root / "minimum.json"
        payload.write_text(json.dumps({"code": "PAPER", "minimum": 15}), encoding="utf-8")
        result = subprocess.run([sys.executable, "-m", "stock_room", "--root", str(self.root), "set-minimum", str(payload)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"code": "PAPER", "minimum": 15})
        result = subprocess.run([sys.executable, "-m", "stock_room", "--root", str(self.root), "shortages"], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([row["code"] for row in json.loads(result.stdout)], ["PAPER"])

    def test_cli_set_minimum_array_keeps_earlier_success(self):
        payload = self.root / "minimum-batch.json"
        payload.write_text(json.dumps([
            {"code": "PAPER", "minimum": 15},
            {"code": "PAPER", "minimum": True},
        ]), encoding="utf-8")
        result = subprocess.run([sys.executable, "-m", "stock_room", "--root", str(self.root), "set-minimum", str(payload)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("error", result.stderr)
        self.assertEqual([row["code"] for row in self.app.shortages()], ["PAPER"])

    def test_cli_set_minimum_invalid_argument_returns_2(self):
        payload = self.root / "bad-minimum.json"
        payload.write_text(json.dumps({"code": "PAPER", "minimum": -1}), encoding="utf-8")
        result = subprocess.run([sys.executable, "-m", "stock_room", "--root", str(self.root), "set-minimum", str(payload)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("error", result.stderr)


if __name__ == "__main__":
    unittest.main()
