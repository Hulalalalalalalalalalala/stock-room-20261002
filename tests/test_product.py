import csv
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from stock_room import StockRoom

HEADER = "code,name,unit,quantity,minimum,active"
HEADER_ONLY = HEADER + "\n"

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


class MovementBatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = StockRoom(self.root)
        self.app.register("PAPER", "包装纸", "张")
        self.app.register("BOX", "纸箱", "个")
        self.app.movement("PAPER", 20, "IN-001")
        self.app.movement("PAPER", -6, "OUT-001")

    def test_fixed_sample_balances_and_reopen(self):
        results = self.app.movement_batch([
            {"code": "PAPER", "quantity": -4, "reference": "B-OUT-1"},
            {"code": "PAPER", "quantity": 2, "reference": "B-IN-1"},
            {"code": "BOX", "quantity": 3, "reference": "B-BOX-IN"},
        ])
        self.assertEqual([row["balance"] for row in results], [10, 12, 3])
        self.assertEqual([row["quantity"] for row in results], [-4, 2, 3])
        self.assertEqual([row["reference"] for row in results], ["B-OUT-1", "B-IN-1", "B-BOX-IN"])
        reopened = StockRoom(self.root)
        self.assertEqual(reopened.stock("PAPER")["quantity"], 12)
        self.assertEqual(reopened.stock("BOX")["quantity"], 3)
        self.assertEqual([row["reference"] for row in reopened.history("PAPER")], ["IN-001", "OUT-001", "B-OUT-1", "B-IN-1"])
        self.assertEqual([row["reference"] for row in reopened.history("BOX")], ["B-BOX-IN"])

    def test_later_replenishment_does_not_save_negative_row(self):
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.movement_batch([
                {"code": "BOX", "quantity": -1, "reference": "B-BOX-OUT"},
                {"code": "BOX", "quantity": 4, "reference": "B-BOX-IN"},
                {"code": "PAPER", "quantity": 1, "reference": "B-PAPER-IN"},
            ])
        self.assertEqual(before, self.app.path.read_bytes())
        reopened = StockRoom(self.root)
        self.assertEqual(reopened.stock("PAPER")["quantity"], 14)
        self.assertEqual(reopened.stock("BOX")["quantity"], 0)
        self.assertEqual([row["reference"] for row in reopened.history("BOX")], [])
        self.assertEqual(len(reopened.history("PAPER")), 2)

    def test_failed_batch_after_first_sample_batch_preserves_state(self):
        self.app.movement_batch([
            {"code": "PAPER", "quantity": -4, "reference": "B-OUT-1"},
            {"code": "PAPER", "quantity": 2, "reference": "B-IN-1"},
            {"code": "BOX", "quantity": 3, "reference": "B-BOX-IN"},
        ])
        with self.assertRaises(ValueError):
            self.app.movement_batch([
                {"code": "PAPER", "quantity": 1, "reference": "B-PAPER-IN"},
                {"code": "BOX", "quantity": -4, "reference": "B-BOX-OUT"},
            ])
        reopened = StockRoom(self.root)
        self.assertEqual(reopened.stock("PAPER")["quantity"], 12)
        self.assertEqual(reopened.stock("BOX")["quantity"], 3)
        self.assertEqual([row["reference"] for row in reopened.history("PAPER")][-2:], ["B-OUT-1", "B-IN-1"])
        self.assertEqual([row["reference"] for row in reopened.history("BOX")], ["B-BOX-IN"])

    def test_invalid_rows_shape_rejected(self):
        for rows in (None, [], {}, "x", 1):
            with self.subTest(rows=rows):
                with self.assertRaises(ValueError):
                    self.app.movement_batch(rows)

    def test_invalid_row_shape_rejected(self):
        good = {"code": "PAPER", "quantity": 1, "reference": "OK"}
        for rows in (
            [None],
            ["PAPER"],
            [[]],
            [{"code": "PAPER", "quantity": 1}],
            [{"code": "PAPER", "reference": "R"}],
            [{"quantity": 1, "reference": "R"}],
            [{"code": "PAPER", "quantity": 1, "reference": "R", "extra": 1}],
            [good, good],
        ):
            with self.subTest(rows=rows):
                with self.assertRaises(ValueError):
                    self.app.movement_batch(rows)

    def test_invalid_identifiers_and_quantity_rejected(self):
        for code in ("", "   ", 11, None, "UNKNOWN", "paper"):
            with self.subTest(code=code):
                with self.assertRaises(ValueError):
                    self.app.movement_batch([{"code": code, "quantity": 1, "reference": "R"}])
        for reference in ("", "   ", 11, None, "IN-001"):
            with self.subTest(reference=reference):
                with self.assertRaises(ValueError):
                    self.app.movement_batch([{"code": "PAPER", "quantity": 1, "reference": reference}])
        for quantity in (0, True, False, 1.5, "1", None, [1]):
            with self.subTest(quantity=quantity):
                with self.assertRaises(ValueError):
                    self.app.movement_batch([{"code": "PAPER", "quantity": quantity, "reference": "R"}])

    def test_identifiers_are_stripped_and_case_sensitive(self):
        results = self.app.movement_batch([
            {"code": "  PAPER  ", "quantity": 1, "reference": "  B-STRIP  "},
        ])
        self.assertEqual(results[0]["code"], "PAPER")
        self.assertEqual(results[0]["reference"], "B-STRIP")
        with self.assertRaises(ValueError):
            self.app.movement_batch([{"code": "PAPER", "quantity": 1, "reference": "  B-STRIP  "}])
        with self.assertRaises(ValueError):
            self.app.movement_batch([{"code": "paper", "quantity": 1, "reference": "B-LOWER-CODE"}])

    def test_reference_conflicts_within_batch_rejected(self):
        with self.assertRaises(ValueError):
            self.app.movement_batch([
                {"code": "PAPER", "quantity": 1, "reference": "DUP"},
                {"code": "BOX", "quantity": 1, "reference": "  DUP  "},
            ])

    def test_reference_conflicts_with_existing_histories_rejected(self):
        self.app.count("PAPER", 14, "CNT-ZERO")
        self.app.reverse("OUT-001", "REV-OUT")
        for reference in ("IN-001", "CNT-ZERO", "REV-OUT"):
            with self.subTest(reference=reference):
                with self.assertRaises(ValueError):
                    self.app.movement_batch([{"code": "PAPER", "quantity": 1, "reference": reference}])

    def test_failed_batch_preserves_file_and_all_state(self):
        self.app.set_minimum("BOX", 2)
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.movement_batch([
                {"code": "PAPER", "quantity": 1, "reference": "B-OK"},
                {"code": "BOX", "quantity": -1, "reference": "B-BAD"},
            ])
        with self.assertRaises(ValueError):
            self.app.movement_batch([{"code": "PAPER", "quantity": 1, "reference": "IN-001"}])
        self.assertEqual(before, self.app.path.read_bytes())
        self.assertEqual(self.app.stock("PAPER")["quantity"], 14)
        self.assertEqual(self.app.stock("BOX")["quantity"], 0)
        self.assertEqual(len(self.app.history("PAPER")), 2)
        self.assertEqual(self.app.counts("PAPER"), [])
        self.assertEqual(self.app.reversals("PAPER"), [])
        self.assertEqual([item["code"] for item in self.app.shortages()], ["BOX"])

    def test_failed_batch_on_empty_directory_creates_no_file(self):
        empty = Path(self.temp.name) / "empty"
        app = StockRoom(empty)
        with self.assertRaises(ValueError):
            app.movement_batch([])
        with self.assertRaises(ValueError):
            app.movement_batch([{"code": "PAPER", "quantity": 1, "reference": "R"}])
        self.assertFalse((empty / "data.json").exists())

    def test_batch_rows_can_be_reversed_one_by_one(self):
        self.app.movement_batch([
            {"code": "PAPER", "quantity": -4, "reference": "B-OUT-1"},
            {"code": "PAPER", "quantity": 2, "reference": "B-IN-1"},
        ])
        record = self.app.reverse("B-OUT-1", "REV-B-OUT")
        self.assertEqual((record["quantity"], record["balance"]), (4, 16))
        with self.assertRaises(ValueError):
            self.app.reverse("B-OUT-1", "REV-AGAIN")

    def test_cli_move_batch_success_and_failure(self):
        payload = self.root / "batch.json"
        payload.write_text(json.dumps({"rows": [
            {"code": "PAPER", "quantity": -4, "reference": "B-OUT-1"},
            {"code": "PAPER", "quantity": 2, "reference": "B-IN-1"},
            {"code": "BOX", "quantity": 3, "reference": "B-BOX-IN"},
        ]}), encoding="utf-8")
        result = subprocess.run([sys.executable, "-m", "stock_room", "--root", str(self.root), "move-batch", str(payload)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([row["balance"] for row in json.loads(result.stdout)], [10, 12, 3])
        bad = self.root / "bad-batch.json"
        bad.write_text(json.dumps({"rows": [
            {"code": "PAPER", "quantity": 1, "reference": "B-PAPER-IN"},
            {"code": "BOX", "quantity": -4, "reference": "B-BOX-OUT"},
        ]}), encoding="utf-8")
        failed = subprocess.run([sys.executable, "-m", "stock_room", "--root", str(self.root), "move-batch", str(bad)], text=True, capture_output=True)
        self.assertEqual(failed.returncode, 2)
        self.assertIn("error", failed.stderr)
        self.assertEqual(StockRoom(self.root).stock("PAPER")["quantity"], 12)
        self.assertEqual(StockRoom(self.root).stock("BOX")["quantity"], 3)

    def test_cli_move_batch_array_keeps_earlier_batch(self):
        payload = self.root / "batches.json"
        payload.write_text(json.dumps([
            {"rows": [{"code": "PAPER", "quantity": 1, "reference": "B-ONE"}]},
            {"rows": [{"code": "BOX", "quantity": -9, "reference": "B-TWO"}]},
        ]), encoding="utf-8")
        result = subprocess.run([sys.executable, "-m", "stock_room", "--root", str(self.root), "move-batch", str(payload)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("error", result.stderr)
        self.assertEqual(self.app.stock("PAPER")["quantity"], 15)
        self.assertEqual(self.app.stock("BOX")["quantity"], 0)
        self.assertEqual([row["reference"] for row in self.app.history("PAPER")][-1], "B-ONE")


class ActiveStatusTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = StockRoom(self.root)
        self.app.register("PAPER", "包装纸", "张")
        self.app.register("BOX", "纸箱", "个")
        self.app.movement("PAPER", 20, "IN-001")
        self.app.movement("PAPER", -6, "OUT-001")

    def test_new_and_legacy_materials_default_active(self):
        self.assertEqual(self.app.material_status("PAPER"), {"code": "PAPER", "active": True})
        self.assertEqual(self.app.material_status("BOX"), {"code": "BOX", "active": True})

    def test_fixed_sample_deactivate_rejects_movements_then_count_reverse_resume(self):
        self.assertEqual(self.app.set_active("PAPER", False), {"code": "PAPER", "active": False})
        self.assertEqual(self.app.material_status("PAPER"), {"code": "PAPER", "active": False})
        before = self.app.path.read_bytes()
        for quantity, reference in ((1, "IN-002"), (-1, "OUT-002")):
            with self.subTest(reference=reference):
                with self.assertRaises(ValueError):
                    self.app.movement("PAPER", quantity, reference)
        with self.assertRaises(ValueError):
            self.app.movement_batch([
                {"code": "BOX", "quantity": 3, "reference": "B-BOX-IN"},
                {"code": "PAPER", "quantity": -1, "reference": "B-PAPER-OUT"},
            ])
        self.assertEqual(before, self.app.path.read_bytes())
        self.assertEqual(self.app.stock("PAPER")["quantity"], 14)
        self.assertEqual(self.app.stock("BOX")["quantity"], 0)
        self.assertEqual(self.app.history("BOX"), [])
        self.app.count("PAPER", 11, "CNT-001")
        self.assertEqual(self.app.stock("PAPER")["quantity"], 11)
        self.app.reverse("OUT-001", "REV-OUT")
        self.assertEqual(self.app.stock("PAPER")["quantity"], 17)
        reopened = StockRoom(self.root)
        self.assertEqual(reopened.material_status("PAPER"), {"code": "PAPER", "active": False})
        self.assertEqual(reopened.stock("PAPER")["quantity"], 17)
        reopened.set_active("PAPER", True)
        record = reopened.movement("PAPER", 1, "IN-002")
        self.assertEqual(record["balance"], 18)
        self.assertEqual(StockRoom(self.root).stock("PAPER")["quantity"], 18)
        self.assertEqual(StockRoom(self.root).stock("BOX")["quantity"], 0)

    def test_repeat_same_state_returns_original_state(self):
        self.assertEqual(self.app.set_active("PAPER", False), {"code": "PAPER", "active": False})
        self.assertEqual(self.app.set_active("PAPER", False), {"code": "PAPER", "active": False})
        self.assertEqual(self.app.set_active("PAPER", True), {"code": "PAPER", "active": True})
        self.assertEqual(self.app.set_active("PAPER", True), {"code": "PAPER", "active": True})

    def test_status_change_creates_no_movement_or_history(self):
        self.app.set_active("PAPER", False)
        self.app.set_active("PAPER", True)
        self.assertEqual([row["reference"] for row in self.app.history("PAPER")], ["IN-001", "OUT-001"])
        self.assertEqual(self.app.counts("PAPER"), [])
        self.assertEqual(self.app.reversals("PAPER"), [])

    def test_batch_with_inactive_material_rejects_entire_batch(self):
        self.app.set_active("PAPER", False)
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.movement_batch([{"code": "PAPER", "quantity": 1, "reference": "B-PAPER-IN"}])
        with self.assertRaises(ValueError):
            self.app.movement_batch([
                {"code": "BOX", "quantity": 3, "reference": "B-BOX-IN"},
                {"code": "PAPER", "quantity": -1, "reference": "B-PAPER-OUT"},
            ])
        self.assertEqual(before, self.app.path.read_bytes())
        self.assertEqual(self.app.stock("BOX")["quantity"], 0)
        self.assertEqual(StockRoom(self.root).stock("BOX")["quantity"], 0)

    def test_count_and_reverse_still_apply_to_inactive_material(self):
        self.app.set_active("PAPER", False)
        self.app.count("PAPER", 11, "CNT-001")
        record = self.app.reverse("OUT-001", "REV-OUT")
        self.assertEqual((record["quantity"], record["balance"]), (6, 17))
        self.assertEqual(self.app.stock("PAPER")["quantity"], 17)

    def test_status_code_is_stripped_and_case_sensitive(self):
        self.assertEqual(self.app.set_active("  PAPER  ", False)["code"], "PAPER")
        self.assertEqual(self.app.material_status("  PAPER  ")["active"], False)
        with self.assertRaises(ValueError):
            self.app.set_active("paper", True)
        with self.assertRaises(ValueError):
            self.app.material_status("paper")

    def test_invalid_status_arguments_rejected(self):
        for code in ("", "   ", 11, None, "UNKNOWN"):
            with self.subTest(code=code):
                with self.assertRaises(ValueError):
                    self.app.set_active(code, False)
                with self.assertRaises(ValueError):
                    self.app.material_status(code)
        for active in (1, 0, "false", None, 1.0, [False]):
            with self.subTest(active=active):
                with self.assertRaises(ValueError):
                    self.app.set_active("PAPER", active)

    def test_failed_status_change_preserves_everything(self):
        self.app.set_minimum("BOX", 2)
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.set_active("PAPER", 1)
        with self.assertRaises(ValueError):
            self.app.set_active("UNKNOWN", False)
        self.assertEqual(before, self.app.path.read_bytes())
        self.assertEqual(self.app.material_status("PAPER")["active"], True)
        self.assertEqual(len(self.app.history("PAPER")), 2)
        self.assertEqual([item["code"] for item in self.app.shortages()], ["BOX"])

    def test_status_query_does_not_modify_file(self):
        before = self.app.path.read_bytes()
        self.app.material_status("PAPER")
        self.assertEqual(before, self.app.path.read_bytes())

    def test_status_calls_on_empty_directory_create_no_file(self):
        empty = Path(self.temp.name) / "empty"
        app = StockRoom(empty)
        with self.assertRaises(ValueError):
            app.set_active("PAPER", False)
        with self.assertRaises(ValueError):
            app.set_active(11, False)
        with self.assertRaises(ValueError):
            app.material_status("PAPER")
        self.assertFalse((empty / "data.json").exists())

    def test_resumed_movement_keeps_existing_validations(self):
        self.app.set_active("PAPER", False)
        with self.assertRaises(ValueError):
            self.app.movement("PAPER", -100, "OUT-BAD")
        self.app.set_active("PAPER", True)
        with self.assertRaises(ValueError):
            self.app.movement("PAPER", -100, "OUT-BAD")
        self.assertEqual(self.app.stock("PAPER")["quantity"], 14)

    def test_cli_set_active_and_material_status(self):
        payload = self.root / "active.json"
        payload.write_text(json.dumps({"code": "PAPER", "active": False}), encoding="utf-8")
        result = subprocess.run([sys.executable, "-m", "stock_room", "--root", str(self.root), "set-active", str(payload)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"code": "PAPER", "active": False})
        query = self.root / "query.json"
        query.write_text(json.dumps({"code": "PAPER"}), encoding="utf-8")
        result = subprocess.run([sys.executable, "-m", "stock_room", "--root", str(self.root), "material-status", str(query)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"code": "PAPER", "active": False})

    def test_cli_set_active_invalid_argument_returns_2(self):
        payload = self.root / "bad-active.json"
        payload.write_text(json.dumps({"code": "PAPER", "active": "false"}), encoding="utf-8")
        result = subprocess.run([sys.executable, "-m", "stock_room", "--root", str(self.root), "set-active", str(payload)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("error", result.stderr)
        self.assertEqual(self.app.material_status("PAPER")["active"], True)

    def test_cli_set_active_array_keeps_earlier_success(self):
        payload = self.root / "active-batch.json"
        payload.write_text(json.dumps([
            {"code": "BOX", "active": False},
            {"code": "PAPER", "active": 1},
        ]), encoding="utf-8")
        result = subprocess.run([sys.executable, "-m", "stock_room", "--root", str(self.root), "set-active", str(payload)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("error", result.stderr)
        self.assertEqual(self.app.material_status("BOX")["active"], False)
        self.assertEqual(self.app.material_status("PAPER")["active"], True)


class UpdateMaterialTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = StockRoom(self.root)
        self.app.register("PAPER", "包装纸", "张")
        self.app.register("BOX", "纸箱", "个")

    def seed_paper(self):
        self.app.movement("PAPER", 20, "IN-001")
        self.app.movement("PAPER", -6, "OUT-001")

    def test_fixed_sample_rename_then_unit_change_fails_atomically(self):
        self.seed_paper()
        result = self.app.update_material("PAPER", "加厚包装纸", "张")
        self.assertEqual(result, {"code": "PAPER", "name": "加厚包装纸", "unit": "张"})
        self.assertEqual(self.app.stock("PAPER"), {"code": "PAPER", "name": "加厚包装纸", "unit": "张", "quantity": 14})
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.update_material("PAPER", "包装用纸", "包")
        self.assertEqual(before, self.app.path.read_bytes())
        self.assertEqual(self.app.stock("PAPER"), {"code": "PAPER", "name": "加厚包装纸", "unit": "张", "quantity": 14})

    def test_fixed_sample_box_unit_then_zero_count_locks_unit(self):
        self.assertEqual(self.app.update_material("BOX", "纸箱", "只"), {"code": "BOX", "name": "纸箱", "unit": "只"})
        self.app.count("BOX", 0, "CNT-ZERO")
        self.assertEqual(self.app.stock("BOX")["quantity"], 0)
        self.assertEqual(len(self.app.history("BOX")), 0)
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.update_material("BOX", "纸箱", "个")
        self.assertEqual(before, self.app.path.read_bytes())
        self.assertEqual(self.app.stock("BOX")["unit"], "只")

    def test_update_survives_reopen_with_stock_and_history(self):
        self.seed_paper()
        self.app.update_material("PAPER", "加厚包装纸", "张")
        self.app.update_material("BOX", "纸箱", "只")
        reopened = StockRoom(self.root)
        self.assertEqual(reopened.stock("PAPER"), {"code": "PAPER", "name": "加厚包装纸", "unit": "张", "quantity": 14})
        self.assertEqual(reopened.stock("BOX"), {"code": "BOX", "name": "纸箱", "unit": "只", "quantity": 0})
        self.assertEqual([row["reference"] for row in reopened.history("PAPER")], ["IN-001", "OUT-001"])

    def test_repeat_same_profile_and_rename_with_same_unit_succeed(self):
        self.seed_paper()
        expected = {"code": "PAPER", "name": "包装纸", "unit": "张"}
        self.assertEqual(self.app.update_material("PAPER", "包装纸", "张"), expected)
        self.assertEqual(self.app.update_material("  PAPER  ", "包装用纸", "  张  "), {"code": "PAPER", "name": "包装用纸", "unit": "张"})
        self.assertEqual(self.app.stock("PAPER")["quantity"], 14)

    def test_unit_change_without_history_allowed_with_internal_whitespace(self):
        result = self.app.update_material("BOX", " 纸 箱 ", " 只 ")
        self.assertEqual(result, {"code": "BOX", "name": "纸 箱", "unit": "只"})
        self.assertEqual(StockRoom(self.root).stock("BOX")["name"], "纸 箱")

    def test_unit_locked_by_movement_even_when_stock_is_zero(self):
        self.app.movement("BOX", 5, "BOX-IN")
        self.app.movement("BOX", -5, "BOX-OUT")
        with self.assertRaises(ValueError):
            self.app.update_material("BOX", "纸箱", "只")
        self.assertEqual(self.app.stock("BOX")["unit"], "个")

    def test_unit_locked_by_reversed_movement(self):
        self.app.movement("BOX", 5, "BOX-IN")
        self.app.reverse("BOX-IN", "REV-BOX")
        self.assertEqual(self.app.stock("BOX")["quantity"], 0)
        with self.assertRaises(ValueError):
            self.app.update_material("BOX", "纸箱", "只")
        self.assertEqual(self.app.update_material("BOX", "新纸箱", "个")["name"], "新纸箱")

    def test_unit_locked_by_nonzero_count(self):
        self.app.count("BOX", 3, "CNT-BOX")
        with self.assertRaises(ValueError):
            self.app.update_material("BOX", "纸箱", "只")
        self.assertEqual(self.app.stock("BOX")["unit"], "个")

    def test_inactive_material_update_keeps_status(self):
        self.app.set_active("BOX", False)
        self.assertEqual(self.app.update_material("BOX", "停用箱", "只"), {"code": "BOX", "name": "停用箱", "unit": "只"})
        self.assertEqual(self.app.material_status("BOX"), {"code": "BOX", "active": False})
        self.assertEqual(StockRoom(self.root).material_status("BOX"), {"code": "BOX", "active": False})

    def test_update_rewrites_no_history_and_consumes_no_reference(self):
        self.seed_paper()
        self.app.count("PAPER", 14, "CNT-ZERO")
        before = self.app.path.read_bytes()
        self.app.update_material("PAPER", "加厚包装纸", "张")
        self.assertEqual([row["reference"] for row in self.app.history("PAPER")], ["IN-001", "OUT-001"])
        self.assertEqual([row["reference"] for row in self.app.counts("PAPER")], ["CNT-ZERO"])
        self.assertEqual(self.app.reversals("PAPER"), [])
        self.app.movement("PAPER", 1, "IN-002")
        self.assertNotEqual(before, self.app.path.read_bytes())

    def test_update_reflected_in_shortages_with_same_order_and_numbers(self):
        self.seed_paper()
        self.app.set_minimum("PAPER", 15)
        self.app.set_minimum("BOX", 3)
        self.app.update_material("PAPER", "加厚包装纸", "张")
        self.app.update_material("BOX", "纸箱", "只")
        self.assertEqual(self.app.shortages(), [
            {"code": "BOX", "name": "纸箱", "unit": "只", "quantity": 0, "minimum": 3, "shortage": 3},
            {"code": "PAPER", "name": "加厚包装纸", "unit": "张", "quantity": 14, "minimum": 15, "shortage": 1},
        ])

    def test_code_is_case_sensitive_and_unknown_rejected(self):
        with self.assertRaises(ValueError):
            self.app.update_material("paper", "纸", "张")
        with self.assertRaises(ValueError):
            self.app.update_material("UNKNOWN", "纸", "张")
        self.assertEqual(self.app.stock("PAPER")["name"], "包装纸")

    def test_invalid_arguments_rejected(self):
        for value in ("", "   ", 11, None, True, ["PAPER"]):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    self.app.update_material(value, "纸", "张")
        for name in ("", "   ", 11, None, True):
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    self.app.update_material("PAPER", name, "张")
        for unit in ("", "   ", 11, None, True):
            with self.subTest(unit=unit):
                with self.assertRaises(ValueError):
                    self.app.update_material("PAPER", "纸", unit)

    def test_failed_update_on_empty_directory_creates_no_file(self):
        empty = Path(self.temp.name) / "empty"
        app = StockRoom(empty)
        with self.assertRaises(ValueError):
            app.update_material("PAPER", "纸", "张")
        with self.assertRaises(ValueError):
            app.update_material("PAPER", "纸", "  ")
        self.assertFalse((empty / "data.json").exists())

    def test_failed_update_preserves_bytes_minimum_and_status(self):
        self.seed_paper()
        self.app.set_minimum("PAPER", 15)
        self.app.set_active("BOX", False)
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.update_material("PAPER", "包装用纸", "包")
        with self.assertRaises(ValueError):
            self.app.update_material("PAPER", "", "张")
        with self.assertRaises(ValueError):
            self.app.update_material("UNKNOWN", "纸", "张")
        self.assertEqual(before, self.app.path.read_bytes())
        self.assertEqual(self.app.stock("PAPER")["name"], "包装纸")
        self.assertEqual([item["code"] for item in self.app.shortages()], ["PAPER"])
        self.assertEqual(self.app.material_status("BOX")["active"], False)

    def test_cli_update_success_and_failure(self):
        payload = self.root / "update.json"
        payload.write_text(json.dumps({"code": "BOX", "name": "纸箱", "unit": "只"}), encoding="utf-8")
        result = subprocess.run([sys.executable, "-m", "stock_room", "--root", str(self.root), "update-material", str(payload)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"code": "BOX", "name": "纸箱", "unit": "只"})
        self.seed_paper()
        bad = self.root / "bad-update.json"
        bad.write_text(json.dumps({"code": "PAPER", "name": "包装用纸", "unit": "包"}), encoding="utf-8")
        failed = subprocess.run([sys.executable, "-m", "stock_room", "--root", str(self.root), "update-material", str(bad)], text=True, capture_output=True)
        self.assertEqual(failed.returncode, 2)
        self.assertIn("error", failed.stderr)
        self.assertEqual(self.app.stock("PAPER")["name"], "包装纸")

    def test_cli_update_array_keeps_earlier_success(self):
        payload = self.root / "update-batch.json"
        payload.write_text(json.dumps([
            {"code": "BOX", "name": "纸箱", "unit": "只"},
            {"code": "PAPER", "name": "包装用纸", "unit": "包"},
        ]), encoding="utf-8")
        self.seed_paper()
        result = subprocess.run([sys.executable, "-m", "stock_room", "--root", str(self.root), "update-material", str(payload)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("error", result.stderr)
        self.assertEqual(self.app.stock("BOX")["unit"], "只")
        self.assertEqual(self.app.stock("PAPER"), {"code": "PAPER", "name": "包装纸", "unit": "张", "quantity": 14})


class CsvImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = StockRoom(self.root)
        self.app.register("PAPER", "包装纸", "张")
        self.app.movement("PAPER", 20, "IN-001")
        self.app.movement("PAPER", -6, "OUT-001")

    def test_fixed_sample_conflict_rejects_whole_file(self):
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.import_materials_csv("code,name,unit\nTAPE,胶带,卷\nPAPER,包装纸,张\n")
        self.assertEqual(before, self.app.path.read_bytes())
        with self.assertRaises(ValueError):
            self.app.stock("TAPE")
        self.assertEqual(self.app.stock("PAPER")["quantity"], 14)

    def test_fixed_sample_import_survives_reopen(self):
        result = self.app.import_materials_csv("code,name,unit\nTAPE,胶带,卷\nBAG,包装袋,个\n")
        self.assertEqual(result, [
            {"code": "TAPE", "name": "胶带", "unit": "卷"},
            {"code": "BAG", "name": "包装袋", "unit": "个"},
        ])
        reopened = StockRoom(self.root)
        self.assertEqual(reopened.stock("TAPE"), {"code": "TAPE", "name": "胶带", "unit": "卷", "quantity": 0})
        self.assertEqual(reopened.stock("BAG")["quantity"], 0)
        self.assertEqual(reopened.stock("PAPER")["quantity"], 14)
        self.assertEqual(reopened.material_status("TAPE"), {"code": "TAPE", "active": True})
        rows = {row["code"]: row for row in reopened.inventory()}
        self.assertEqual(rows["BAG"]["minimum"], 0)
        self.assertEqual(rows["BAG"]["active"], True)
        self.assertEqual(reopened.history("TAPE"), [])
        self.assertEqual(reopened.counts("TAPE"), [])
        self.assertEqual(reopened.reversals("TAPE"), [])
        record = reopened.movement("TAPE", 3, "TAPE-IN")
        self.assertEqual(record["balance"], 3)
        self.assertEqual(StockRoom(self.root).stock("TAPE")["quantity"], 3)

    def test_bom_crlf_reordered_header_and_quoted_fields(self):
        content = "﻿unit,code,name\r\n卷,GLUE,\"强力\r\n胶\"\"水\"\"\"\r\n\r\n个, BOX , 箱 子 \r\n"
        result = self.app.import_materials_csv(content)
        self.assertEqual(result, [
            {"code": "GLUE", "name": "强力\r\n胶\"水\"", "unit": "卷"},
            {"code": "BOX", "name": "箱 子", "unit": "个"},
        ])
        self.assertEqual(StockRoom(self.root).stock("GLUE")["name"], "强力\r\n胶\"水\"")

    def test_quoted_fields_may_contain_commas(self):
        result = self.app.import_materials_csv('code,name,unit\n"G,LUE","胶,水",卷\n')
        self.assertEqual(result, [{"code": "G,LUE", "name": "胶,水", "unit": "卷"}])

    def test_header_only_returns_empty_without_writing(self):
        empty = Path(self.temp.name) / "empty"
        app = StockRoom(empty)
        self.assertEqual(app.import_materials_csv("code,name,unit\n"), [])
        self.assertFalse((empty / "data.json").exists())
        before = self.app.path.read_bytes()
        self.assertEqual(self.app.import_materials_csv("unit,code,name\r\n\r\n"), [])
        self.assertEqual(before, self.app.path.read_bytes())

    def test_invalid_content_and_header_rejected(self):
        for content in ("", "﻿", None, 11, ["code"], {"c": "x"}):
            with self.subTest(content=content):
                with self.assertRaises(ValueError):
                    self.app.import_materials_csv(content)
        for content in (
            "code,name\n",
            "code,name,unit,extra\n",
            "code,code,unit\n",
            "code,name\nA,纸\n",
            " code,name,unit\n",
            "code ,name,unit\n",
            "CODE,name,unit\n",
        ):
            with self.subTest(content=content):
                with self.assertRaises(ValueError):
                    self.app.import_materials_csv(content)

    def test_invalid_quotes_rejected(self):
        for content in (
            'code,name,unit\n"unclosed,纸,张\n',
            'code,name,unit\nA"x,纸,张\n',
            'code,name,unit\n"x"y,纸,张\n',
            'code,name,unit\n"x" y,纸,张\n',
            'code,name,unit\n "x",纸,张\n',
        ):
            with self.subTest(content=content):
                with self.assertRaises(ValueError):
                    self.app.import_materials_csv(content)

    def test_wrong_column_count_and_empty_fields_rejected(self):
        for content in (
            "code,name,unit\nA,纸\n",
            "code,name,unit\nA,纸,张,多\n",
            "code,name,unit\nA,,张\n",
            "code,name,unit\nA,  ,张\n",
            "code,name,unit\n,,\n",
            "code,name,unit\n   \n",
            "code,name,unit\nA,纸,张,\n",
        ):
            with self.subTest(content=content):
                with self.assertRaises(ValueError):
                    self.app.import_materials_csv(content)

    def test_duplicate_codes_rejected_and_codes_are_case_sensitive(self):
        for content in (
            "code,name,unit\nA,纸,张\nA,纸,张\n",
            "code,name,unit\nA,纸,张\n A ,纸,张\n",
            "code,name,unit\nPAPER,包装纸,张\n",
        ):
            with self.subTest(content=content):
                with self.assertRaises(ValueError):
                    self.app.import_materials_csv(content)
        result = self.app.import_materials_csv("code,name,unit\npaper,复印纸,张\n")
        self.assertEqual(result, [{"code": "paper", "name": "复印纸", "unit": "张"}])

    def test_inactive_material_code_still_conflicts(self):
        self.app.set_active("PAPER", False)
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.import_materials_csv("code,name,unit\nPAPER,包装纸,张\n")
        self.assertEqual(before, self.app.path.read_bytes())
        self.assertEqual(self.app.material_status("PAPER")["active"], False)

    def test_failed_import_preserves_file_and_all_state(self):
        self.app.set_minimum("PAPER", 15)
        self.app.set_active("PAPER", False)
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.import_materials_csv("code,name,unit\nTAPE,胶带,卷\nTAPE,胶带,卷\n")
        with self.assertRaises(ValueError):
            self.app.import_materials_csv("code,name,unit\nTAPE,胶带\n")
        with self.assertRaises(ValueError):
            self.app.import_materials_csv(11)
        self.assertEqual(before, self.app.path.read_bytes())
        self.assertEqual(self.app.stock("PAPER")["quantity"], 14)
        self.assertEqual(len(self.app.history("PAPER")), 2)
        self.assertEqual(self.app.material_status("PAPER")["active"], False)
        self.assertEqual([item["code"] for item in self.app.shortages()], ["PAPER"])

    def test_failed_import_on_empty_directory_creates_no_file(self):
        empty = Path(self.temp.name) / "empty"
        app = StockRoom(empty)
        with self.assertRaises(ValueError):
            app.import_materials_csv("code,name,unit\nA,纸\n")
        with self.assertRaises(ValueError):
            app.import_materials_csv("")
        self.assertFalse((empty / "data.json").exists())

    def run_cli(self, *args):
        return subprocess.run([sys.executable, "-m", "stock_room", "--root", str(self.root), *args], text=True, capture_output=True)

    def test_cli_import_success_and_failure(self):
        payload = self.root / "import.json"
        payload.write_text(json.dumps({"content": "code,name,unit\nTAPE,胶带,卷\nBAG,包装袋,个\n"}), encoding="utf-8")
        result = self.run_cli("import-materials-csv", str(payload))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([row["code"] for row in json.loads(result.stdout)], ["TAPE", "BAG"])
        bad = self.root / "bad-import.json"
        bad.write_text(json.dumps({"content": "code,name,unit\nGLUE,胶水,瓶\nPAPER,包装纸,张\n"}), encoding="utf-8")
        failed = self.run_cli("import-materials-csv", str(bad))
        self.assertEqual(failed.returncode, 2)
        self.assertIn("error", json.loads(failed.stderr))
        with self.assertRaises(ValueError):
            self.app.stock("GLUE")
        self.assertEqual(self.app.stock("PAPER")["quantity"], 14)

    def test_cli_import_array_keeps_earlier_success(self):
        payload = self.root / "imports.json"
        payload.write_text(json.dumps([
            {"content": "code,name,unit\nBOX,纸箱,个\n"},
            {"content": "code,name,unit\nPAPER,包装纸,张\n"},
        ]), encoding="utf-8")
        result = self.run_cli("import-materials-csv", str(payload))
        self.assertEqual(result.returncode, 2)
        self.assertIn("error", result.stderr)
        self.assertEqual(self.app.stock("BOX")["quantity"], 0)
        self.assertEqual(self.app.stock("PAPER")["quantity"], 14)


class ImportCountsCsvTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = StockRoom(self.root)
        self.app.register("PAPER", "包装纸", "张")
        self.app.register("BOX", "纸箱", "个")
        self.app.movement("PAPER", 20, "IN-001")
        self.app.movement("PAPER", -6, "OUT-001")

    def test_fixed_sample_paper_minus_two_box_zero_survives_reopen(self):
        result = self.app.import_counts_csv("code,counted,reference\nPAPER,12,CNT-001\nBOX,0,CNT-002\n")
        self.assertEqual(result, [
            {"code": "PAPER", "reference": "CNT-001", "before": 14, "counted": 12, "difference": -2},
            {"code": "BOX", "reference": "CNT-002", "before": 0, "counted": 0, "difference": 0},
        ])
        self.assertEqual(self.app.stock("PAPER")["quantity"], 12)
        self.assertEqual(self.app.stock("BOX")["quantity"], 0)
        self.assertEqual(len(self.app.counts("PAPER")), 1)
        self.assertEqual(len(self.app.counts("BOX")), 1)
        self.assertEqual([row["quantity"] for row in self.app.history("PAPER")], [20, -6, -2])
        self.assertEqual(self.app.history("PAPER")[-1]["reference"], "CNT-001")
        self.assertEqual(self.app.history("BOX"), [])
        reopened = StockRoom(self.root)
        self.assertEqual([row["counted"] for row in reopened.counts("PAPER")], [12])
        self.assertEqual([row["reference"] for row in reopened.counts("BOX")], ["CNT-002"])
        self.assertEqual(reopened.stock("PAPER")["quantity"], 12)
        self.assertEqual(reopened.stock("BOX")["quantity"], 0)
        self.assertEqual(len(reopened.history("PAPER")), 3)

    def test_reimport_same_references_rejects_whole_document(self):
        content = "code,counted,reference\nPAPER,12,CNT-001\nBOX,0,CNT-002\n"
        self.app.import_counts_csv(content)
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.import_counts_csv(content)
        self.assertEqual(before, self.app.path.read_bytes())
        self.assertEqual(self.app.stock("PAPER")["quantity"], 12)
        self.assertEqual(self.app.stock("BOX")["quantity"], 0)
        self.assertEqual(len(self.app.counts("PAPER")), 1)
        self.assertEqual(len(self.app.counts("BOX")), 1)

    def test_bom_crlf_reordered_header_quoted_and_blank_lines(self):
        content = "﻿reference,counted,code\r\nCNT-001,\"12\",PAPER\r\n\r\n\"CNT-002\",000,BOX\r\n"
        result = self.app.import_counts_csv(content)
        self.assertEqual([(row["code"], row["reference"], row["counted"], row["difference"]) for row in result], [
            ("PAPER", "CNT-001", 12, -2),
            ("BOX", "CNT-002", 0, 0),
        ])

    def test_quoted_fields_support_commas_newlines_and_doubled_quotes(self):
        content = 'code,counted,reference\r\nPAPER,12,"CNT,\r\n00""1"\r\n'
        result = self.app.import_counts_csv(content)
        self.assertEqual(result[0]["reference"], 'CNT,\r\n00"1')
        self.assertEqual(self.app.counts("PAPER")[0]["reference"], 'CNT,\r\n00"1')

    def test_header_only_returns_empty_without_writing(self):
        empty = Path(self.temp.name) / "empty"
        app = StockRoom(empty)
        self.assertEqual(app.import_counts_csv("code,counted,reference\n"), [])
        self.assertEqual(app.import_counts_csv("reference,code,counted\r\n"), [])
        self.assertFalse((empty / "data.json").exists())
        before = self.app.path.read_bytes()
        self.assertEqual(self.app.import_counts_csv("counted,reference,code\n\n\r\n"), [])
        self.assertEqual(before, self.app.path.read_bytes())
        with self.assertRaises(ValueError):
            self.app.import_counts_csv("counted,reference,code\n\n   \n")

    def test_whitespace_only_and_separator_only_records_are_validated(self):
        for content in (
            "code,counted,reference\n   \n",
            "code,counted,reference\n,,\n",
            "code,counted,reference\n , , \n",
        ):
            with self.subTest(content=content):
                with self.assertRaises(ValueError):
                    self.app.import_counts_csv(content)

    def test_invalid_content_and_header_rejected(self):
        for content in ("", "﻿", None, 11, ["code"], {"c": "x"}):
            with self.subTest(content=content):
                with self.assertRaises(ValueError):
                    self.app.import_counts_csv(content)
        for content in (
            "code,counted\n",
            "code,counted,reference,extra\n",
            "code,code,reference\n",
            "code,counted,counted\n",
            " code,counted,reference\n",
            "code ,counted,reference\n",
            "CODE,counted,reference\n",
            "code,count,reference\n",
        ):
            with self.subTest(content=content):
                with self.assertRaises(ValueError):
                    self.app.import_counts_csv(content)

    def test_invalid_quotes_rejected(self):
        for content in (
            'code,counted,reference\n"PAPER,12,CNT-001\n',
            'code,counted,reference\nPAP"ER,12,CNT-001\n',
            'code,counted,reference\n"PAPER"x,12,CNT-001\n',
            'code,counted,reference\n "PAPER",12,CNT-001\n',
        ):
            with self.subTest(content=content):
                with self.assertRaises(ValueError):
                    self.app.import_counts_csv(content)

    def test_wrong_column_count_and_empty_identifiers_rejected(self):
        for content in (
            "code,counted,reference\nPAPER,12\n",
            "code,counted,reference\nPAPER,12,CNT-X,extra\n",
            "code,counted,reference\n,12,CNT-X\n",
            "code,counted,reference\n   ,12,CNT-X\n",
            "code,counted,reference\nPAPER,12,  \n",
            "code,counted,reference\nPAPER,12,\n",
        ):
            with self.subTest(content=content):
                with self.assertRaises(ValueError):
                    self.app.import_counts_csv(content)

    def test_counted_accepts_digits_only_with_zero_and_leading_zeros(self):
        for value in ("0", "00", "007", "12", "  012  "):
            with self.subTest(value=value):
                reference = "CNT-" + value.strip().replace(" ", "B")
                result = self.app.import_counts_csv(
                    "code,counted,reference\nPAPER," + value + "," + reference + "\n"
                )
                self.assertEqual(result[0]["counted"], int(value.strip()))
                self.assertEqual(result[0]["reference"], reference)

    def test_counted_rejects_signs_decimals_scientific_and_other_characters(self):
        for value in ("", "  ", "+", "-1", "+1", "1.0", ".5", "1e3", "1E3", "0x1",
                      "1 2", "１２", "①", "1_000", "nan", "inf", "1."):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    self.app.import_counts_csv("code,counted,reference\nPAPER," + value + ",CNT-X\n")

    def test_identifiers_stripped_internal_whitespace_kept_and_case_sensitive(self):
        self.app.register("PA PER", "纸 张", "张")
        result = self.app.import_counts_csv('code,counted,reference\n" PA PER ",12," CNT 001 "\n')
        self.assertEqual(result[0]["code"], "PA PER")
        self.assertEqual(result[0]["reference"], "CNT 001")
        with self.assertRaises(ValueError):
            self.app.import_counts_csv("code,counted,reference\npaper,12,CNT-LOWER\n")

    def test_duplicate_codes_and_references_within_document_rejected(self):
        before = self.app.path.read_bytes()
        for content in (
            "code,counted,reference\nPAPER,12,CNT-001\nPAPER,10,CNT-002\n",
            "code,counted,reference\n PAPER ,12,CNT-001\nPAPER,10,CNT-002\n",
            "code,counted,reference\nPAPER,12,CNT-001\nBOX,0, CNT-001 \n",
        ):
            with self.subTest(content=content):
                with self.assertRaises(ValueError):
                    self.app.import_counts_csv(content)
        self.assertEqual(before, self.app.path.read_bytes())
        self.assertEqual(self.app.counts("PAPER"), [])
        self.assertEqual(self.app.counts("BOX"), [])

    def test_references_conflict_with_movements_counts_and_reversals(self):
        self.app.count("PAPER", 14, "CNT-ZERO")
        self.app.reverse("OUT-001", "REV-OUT")
        for content in (
            "code,counted,reference\nPAPER,12,IN-001\n",
            "code,counted,reference\nPAPER,12,CNT-ZERO\n",
            "code,counted,reference\nPAPER,12,REV-OUT\n",
        ):
            with self.subTest(content=content):
                with self.assertRaises(ValueError):
                    self.app.import_counts_csv(content)

    def test_unknown_material_rejects_whole_document(self):
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.import_counts_csv("code,counted,reference\nPAPER,12,CNT-001\nOTHER,0,CNT-002\n")
        self.assertEqual(before, self.app.path.read_bytes())
        self.assertEqual(self.app.counts("PAPER"), [])
        self.assertEqual(self.app.stock("PAPER")["quantity"], 14)

    def test_inactive_materials_can_be_counted(self):
        self.app.set_active("BOX", False)
        result = self.app.import_counts_csv("code,counted,reference\nBOX,3,CNT-BOX\nPAPER,12,CNT-PAPER\n")
        self.assertEqual([row["code"] for row in result], ["BOX", "PAPER"])
        self.assertEqual(self.app.stock("BOX")["quantity"], 3)
        self.assertEqual(self.app.material_status("BOX")["active"], False)

    def test_failed_import_preserves_bytes_and_all_state_and_frees_references(self):
        self.app.set_minimum("BOX", 2)
        self.app.set_active("BOX", False)
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.import_counts_csv("code,counted,reference\nPAPER,12,CNT-NEW\nOTHER,0,CNT-OTHER\n")
        with self.assertRaises(ValueError):
            self.app.import_counts_csv("code,counted,reference\nPAPER,12,IN-001\n")
        with self.assertRaises(ValueError):
            self.app.import_counts_csv(11)
        self.assertEqual(before, self.app.path.read_bytes())
        self.assertEqual(self.app.stock("PAPER")["quantity"], 14)
        self.assertEqual(self.app.counts("PAPER"), [])
        self.assertEqual(self.app.material_status("BOX")["active"], False)
        self.assertEqual([item["code"] for item in self.app.shortages()], ["BOX"])
        result = self.app.import_counts_csv("code,counted,reference\nPAPER,12,CNT-NEW\n")
        self.assertEqual(result[0]["difference"], -2)

    def test_failed_import_on_empty_directory_creates_no_file(self):
        empty = Path(self.temp.name) / "empty"
        app = StockRoom(empty)
        with self.assertRaises(ValueError):
            app.import_counts_csv("code,counted,reference\nPAPER,12\n")
        with self.assertRaises(ValueError):
            app.import_counts_csv("")
        with self.assertRaises(ValueError):
            app.import_counts_csv("code,counted,reference\nPAPER,12,X\n")
        self.assertFalse((empty / "data.json").exists())

    def test_zero_difference_locks_unit_occupies_reference_and_cannot_be_reversed(self):
        self.app.import_counts_csv("code,counted,reference\nBOX,0,CNT-BOX\n")
        with self.assertRaises(ValueError):
            self.app.update_material("BOX", "纸箱", "只")
        with self.assertRaises(ValueError):
            self.app.movement("BOX", 1, "CNT-BOX")
        with self.assertRaises(ValueError):
            self.app.reverse("CNT-BOX", "REV-BOX")
        self.assertEqual(self.app.stock("BOX")["unit"], "个")

    def run_cli(self, *args):
        return subprocess.run([sys.executable, "-m", "stock_room", "--root", str(self.root), *args], text=True, capture_output=True)

    def test_cli_import_success_and_failure(self):
        payload = self.root / "import.json"
        payload.write_text(json.dumps({"content": "code,counted,reference\nPAPER,12,CNT-001\nBOX,0,CNT-002\n"}), encoding="utf-8")
        result = self.run_cli("import-counts-csv", str(payload))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([row["difference"] for row in json.loads(result.stdout)], [-2, 0])
        bad = self.root / "bad-import.json"
        bad.write_text(json.dumps({"content": "code,counted,reference\nPAPER,12,CNT-001\n"}), encoding="utf-8")
        failed = self.run_cli("import-counts-csv", str(bad))
        self.assertEqual(failed.returncode, 2)
        self.assertIn("error", json.loads(failed.stderr))
        self.assertEqual(self.app.stock("PAPER")["quantity"], 12)
        self.assertEqual(len(self.app.counts("PAPER")), 1)

    def test_cli_import_array_keeps_earlier_success(self):
        payload = self.root / "imports.json"
        payload.write_text(json.dumps([
            {"content": "code,counted,reference\nPAPER,12,CNT-001\n"},
            {"content": "code,counted,reference\nPAPER,12,CNT-001\n"},
        ]), encoding="utf-8")
        result = self.run_cli("import-counts-csv", str(payload))
        self.assertEqual(result.returncode, 2)
        self.assertIn("error", result.stderr)
        self.assertEqual(self.app.stock("PAPER")["quantity"], 12)
        self.assertEqual(len(self.app.counts("PAPER")), 1)


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = StockRoom(self.root)
        self.app.register("PAPER", "包装纸", "张")
        self.app.register("BOX", "纸箱", "个")
        self.app.movement("PAPER", 20, "IN-001")
        self.app.movement("PAPER", -6, "OUT-001")
        self.app.set_minimum("PAPER", 15)
        self.app.set_minimum("BOX", 3)
        self.app.set_active("BOX", False)

    def test_fixed_sample_no_filter_and_keyword_filters(self):
        self.assertEqual(self.app.inventory(), [
            {"code": "BOX", "name": "纸箱", "unit": "个", "quantity": 0, "minimum": 3, "active": False},
            {"code": "PAPER", "name": "包装纸", "unit": "张", "quantity": 14, "minimum": 15, "active": True},
        ])
        self.assertEqual([row["code"] for row in self.app.inventory(keyword="纸")], ["BOX", "PAPER"])
        self.assertEqual([row["code"] for row in self.app.inventory(keyword="纸", active=True)], ["PAPER"])
        self.assertEqual(self.app.inventory(keyword="paper"), [])

    def test_fixed_sample_rename_and_count_survive_reopen(self):
        self.app.update_material("PAPER", "加厚包装纸", "张")
        self.app.count("PAPER", 11, "CNT-001")
        reopened = StockRoom(self.root)
        rows = reopened.inventory()
        self.assertEqual(rows[1], {"code": "PAPER", "name": "加厚包装纸", "unit": "张", "quantity": 11, "minimum": 15, "active": True})
        self.assertEqual(rows[0]["quantity"], 0)

    def test_defaults_match_explicit_empty_keyword_and_none_active(self):
        self.assertEqual(self.app.inventory(), self.app.inventory(keyword="", active=None))
        self.assertEqual(self.app.inventory(keyword="  纸  "), self.app.inventory(keyword="纸"))

    def test_keyword_matches_code_case_sensitively_and_literally(self):
        self.assertEqual([row["code"] for row in self.app.inventory(keyword="BOX")], ["BOX"])
        self.assertEqual(self.app.inventory(keyword="box"), [])
        self.assertEqual(self.app.inventory(keyword="."), [])
        self.assertEqual(self.app.inventory(keyword=".*"), [])
        self.assertEqual(self.app.inventory(keyword="装 纸"), [])
        self.app.update_material("BOX", "纸 箱", "个")
        self.assertEqual([row["code"] for row in self.app.inventory(keyword="纸 箱")], ["BOX"])

    def test_active_filter_false_returns_only_inactive(self):
        self.assertEqual([row["code"] for row in self.app.inventory(active=False)], ["BOX"])
        self.assertEqual([row["code"] for row in self.app.inventory(active=True)], ["PAPER"])

    def test_legacy_material_without_status_recorded_is_active(self):
        self.app.register("TAPE", "胶带", "卷")
        rows = {row["code"]: row for row in self.app.inventory()}
        self.assertEqual(rows["TAPE"], {"code": "TAPE", "name": "胶带", "unit": "卷", "quantity": 0, "minimum": 0, "active": True})
        self.assertIn("TAPE", [row["code"] for row in self.app.inventory(active=True)])

    def test_unset_minimum_treated_as_zero(self):
        self.app.register("TAPE", "胶带", "卷")
        rows = {row["code"]: row for row in self.app.inventory()}
        self.assertEqual(rows["TAPE"]["minimum"], 0)

    def test_quantity_reflects_count_and_reversal(self):
        self.app.count("PAPER", 11, "CNT-001")
        self.app.reverse("OUT-001", "REV-OUT")
        rows = {row["code"]: row for row in self.app.inventory()}
        self.assertEqual(rows["PAPER"]["quantity"], 17)

    def test_sorted_by_unicode_code_points(self):
        self.app.register("纸张", "A4纸", "张")
        self.assertEqual([row["code"] for row in self.app.inventory()], ["BOX", "PAPER", "纸张"])

    def test_empty_directory_returns_empty_without_file(self):
        empty = Path(self.temp.name) / "empty"
        app = StockRoom(empty)
        self.assertEqual(app.inventory(), [])
        self.assertEqual(app.inventory(keyword="纸", active=True), [])
        self.assertFalse((empty / "data.json").exists())

    def test_invalid_arguments_rejected(self):
        for keyword in (None, 1, True, 1.5, ["纸"], {"k": "纸"}):
            with self.subTest(keyword=keyword):
                with self.assertRaises(ValueError):
                    self.app.inventory(keyword=keyword)
        for active in (0, 1, "true", "false", 1.0, [True], {"a": True}):
            with self.subTest(active=active):
                with self.assertRaises(ValueError):
                    self.app.inventory(active=active)

    def test_query_does_not_modify_file_or_state(self):
        before = self.app.path.read_bytes()
        self.app.inventory()
        self.app.inventory(keyword="纸", active=True)
        self.app.inventory(keyword="paper")
        self.assertEqual(before, self.app.path.read_bytes())
        with self.assertRaises(ValueError):
            self.app.inventory(keyword=1)
        with self.assertRaises(ValueError):
            self.app.inventory(active="true")
        self.assertEqual(before, self.app.path.read_bytes())
        self.assertEqual(self.app.stock("PAPER")["quantity"], 14)
        self.assertEqual(len(self.app.history("PAPER")), 2)
        self.assertEqual(self.app.counts("PAPER"), [])
        self.assertEqual(self.app.reversals("PAPER"), [])
        self.assertEqual([item["code"] for item in self.app.shortages()], ["BOX", "PAPER"])

    def test_failed_query_on_empty_directory_creates_no_file(self):
        empty = Path(self.temp.name) / "empty"
        app = StockRoom(empty)
        with self.assertRaises(ValueError):
            app.inventory(keyword=11)
        with self.assertRaises(ValueError):
            app.inventory(active=1)
        self.assertFalse((empty / "data.json").exists())

    def run_cli(self, *args):
        return subprocess.run([sys.executable, "-m", "stock_room", "--root", str(self.root), *args], text=True, capture_output=True)

    def test_cli_inventory_without_input_and_with_filters(self):
        result = self.run_cli("inventory")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([row["code"] for row in json.loads(result.stdout)], ["BOX", "PAPER"])
        payload = self.root / "query.json"
        payload.write_text(json.dumps({"keyword": "纸", "active": True}), encoding="utf-8")
        result = self.run_cli("inventory", str(payload))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [
            {"code": "PAPER", "name": "包装纸", "unit": "张", "quantity": 14, "minimum": 15, "active": True},
        ])
        payload.write_text(json.dumps({"keyword": "纸", "active": None}), encoding="utf-8")
        result = self.run_cli("inventory", str(payload))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([row["code"] for row in json.loads(result.stdout)], ["BOX", "PAPER"])

    def test_cli_inventory_array_returns_results_in_order(self):
        payload = self.root / "queries.json"
        payload.write_text(json.dumps([
            {"keyword": "纸"},
            {"keyword": "paper"},
            {},
        ]), encoding="utf-8")
        result = self.run_cli("inventory", str(payload))
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual([len(row) for row in value], [2, 0, 2])
        self.assertEqual([row["code"] for row in value[0]], ["BOX", "PAPER"])

    def test_cli_inventory_invalid_arguments_return_2_without_writes(self):
        before = self.app.path.read_bytes()
        for payload_value in ({"keyword": 11}, {"active": "true"}, {"active": 1}):
            with self.subTest(payload=payload_value):
                payload = self.root / "bad.json"
                payload.write_text(json.dumps(payload_value), encoding="utf-8")
                result = self.run_cli("inventory", str(payload))
                self.assertEqual(result.returncode, 2)
                self.assertIn("error", result.stderr)
        self.assertEqual(before, self.app.path.read_bytes())


class ExportInventoryCsvTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = StockRoom(self.root)
        self.app.register("PAPER", "包装纸", "张")
        self.app.register("BOX", "纸箱", "个")
        self.app.movement("PAPER", 20, "IN-001")
        self.app.movement("PAPER", -6, "OUT-001")
        self.app.set_minimum("PAPER", 15)
        self.app.set_minimum("BOX", 3)
        self.app.set_active("BOX", False)

    def seed_final_paper(self):
        self.app.count("PAPER", 11, "CNT-001")
        self.app.reverse("OUT-001", "REV-OUT")

    def parse(self, content):
        return list(csv.reader(io.StringIO(content), strict=True))

    def codes(self, content):
        return [row[0] for row in self.parse(content)[1:]]

    def test_fixed_sample_default_then_count_and_reverse_final_export(self):
        self.assertEqual(self.app.export_inventory_csv(),
                         HEADER_ONLY + "BOX,纸箱,个,0,3,false\nPAPER,包装纸,张,14,15,true\n")
        self.seed_final_paper()
        expected = HEADER_ONLY + "BOX,纸箱,个,0,3,false\nPAPER,包装纸,张,17,15,true\n"
        self.assertEqual(self.app.export_inventory_csv(), expected)
        self.assertEqual(StockRoom(self.root).export_inventory_csv(), expected)

    def test_header_column_count_order_and_cell_values(self):
        self.seed_final_paper()
        rows = self.parse(self.app.export_inventory_csv())
        self.assertEqual(rows[0], ["code", "name", "unit", "quantity", "minimum", "active"])
        self.assertEqual([len(row) for row in rows], [6, 6, 6])
        self.assertEqual(rows[1], ["BOX", "纸箱", "个", "0", "3", "false"])
        self.assertEqual(rows[2], ["PAPER", "包装纸", "张", "17", "15", "true"])
        self.assertEqual(int(rows[1][3]), 0)
        self.assertEqual(int(rows[2][3]), 17)

    def test_rows_sorted_by_unicode_code_points(self):
        self.app.register("纸张", "A4纸", "张")
        self.seed_final_paper()
        self.assertEqual(self.codes(self.app.export_inventory_csv()), ["BOX", "PAPER", "纸张"])

    def test_integers_have_no_grouping_and_status_is_lowercase(self):
        self.app.register("BULK", "散包", "个")
        self.app.movement("BULK", 1000, "BULK-IN")
        self.app.set_minimum("BULK", 2000)
        content = self.app.export_inventory_csv()
        self.assertIn("BULK,散包,个,1000,2000,true\n", content)
        self.assertNotIn("1,000", content)
        self.assertNotIn("True", content)
        self.assertNotIn("False", content)
        for row in self.parse(content)[1:]:
            self.assertRegex(row[3], r"^-?\d+$")
            self.assertRegex(row[4], r"^\d+$")
            self.assertIn(row[5], ("true", "false"))

    def test_rename_changes_export_but_keeps_history_values(self):
        self.seed_final_paper()
        self.app.update_material("PAPER", "加厚包装纸", "张")
        content = self.app.export_inventory_csv()
        paper = next(row for row in self.parse(content) if row[0] == "PAPER")
        self.assertEqual(paper, ["PAPER", "加厚包装纸", "张", "17", "15", "true"])
        self.assertEqual([row["quantity"] for row in self.app.history("PAPER")], [20, -6, -3, 6])
        self.assertEqual([row["reference"] for row in self.app.history("PAPER")],
                         ["IN-001", "OUT-001", "CNT-001", "REV-OUT"])
        self.assertEqual([row["counted"] for row in self.app.counts("PAPER")], [11])
        self.assertEqual([row["original_reference"] for row in self.app.reversals("PAPER")], ["OUT-001"])
        box = next(row for row in self.parse(content) if row[0] == "BOX")
        self.assertEqual(box[1], "纸箱")

    def test_special_name_quoting_escaping_and_round_trip(self):
        special_name = '特,殊"纸\n单\r行 内\t部'
        self.app.register("SPECIAL", special_name, "个")
        content = self.app.export_inventory_csv()
        quoted = '"' + special_name.replace('"', '""') + '"'
        self.assertIn("SPECIAL," + quoted + ",个,0,0,true\n", content)
        rows = self.parse(content)
        self.assertEqual(len(rows), 4)
        record = next(row for row in rows if row[0] == "SPECIAL")
        self.assertEqual(record, ["SPECIAL", special_name, "个", "0", "0", "true"])

    def test_no_bom_lf_separators_and_trailing_newline(self):
        content = self.app.export_inventory_csv()
        encoded = content.encode("utf-8")
        self.assertFalse(encoded.startswith(b"\xef\xbb\xbf"))
        self.assertNotIn(b"\xef\xbb\xbf", encoded)
        self.assertNotIn("\r", content)
        self.assertTrue(content.startswith(HEADER + "\n"))
        self.assertTrue(content.endswith("\n"))
        self.assertFalse(content.endswith("\n\n"))
        lines = content.split("\n")
        self.assertEqual(lines[-1], "")
        self.assertTrue(all(lines[:-1]))
        self.assertEqual(len(lines), 4)

    def test_keyword_is_trimmed_and_matched_literally_case_sensitively(self):
        export = self.app.export_inventory_csv
        self.assertEqual(self.codes(export(keyword="  PAPER  ")), ["PAPER"])
        self.assertEqual(self.codes(export(keyword="paper")), [])
        self.assertEqual(self.codes(export(keyword="BOX")), ["BOX"])
        self.assertEqual(self.codes(export(keyword="box")), [])
        self.assertEqual(self.codes(export(keyword="纸箱")), ["BOX"])
        self.assertEqual(self.codes(export(keyword="纸")), ["BOX", "PAPER"])
        self.assertEqual(self.codes(export(keyword="   ")), ["BOX", "PAPER"])
        self.assertEqual(self.codes(export(keyword=".")), [])
        self.assertEqual(self.codes(export(keyword=".*")), [])
        self.assertEqual(self.codes(export(keyword="不存在")), [])

    def test_active_filter_alone_and_combined_with_keyword(self):
        export = self.app.export_inventory_csv
        self.assertEqual(self.codes(export(active=True)), ["PAPER"])
        self.assertEqual(self.codes(export(active=False)), ["BOX"])
        self.assertEqual(self.codes(export(keyword="纸", active=True)), ["PAPER"])
        self.assertEqual(self.codes(export(keyword="纸", active=False)), ["BOX"])
        self.assertEqual(self.codes(export(keyword="PAPER", active=False)), [])

    def test_no_match_returns_header_only(self):
        self.assertEqual(self.app.export_inventory_csv(keyword="不存在"), HEADER_ONLY)
        self.assertEqual(self.app.export_inventory_csv(keyword="PAPER", active=False), HEADER_ONLY)

    def test_empty_directory_exports_header_only_without_creating_file(self):
        empty = self.root / "empty"
        app = StockRoom(empty)
        self.assertEqual(app.export_inventory_csv(), HEADER_ONLY)
        self.assertEqual(app.export_inventory_csv(keyword="纸", active=True), HEADER_ONLY)
        self.assertFalse((empty / "data.json").exists())
        with self.assertRaises(ValueError):
            app.export_inventory_csv(keyword=11)
        with self.assertRaises(ValueError):
            app.export_inventory_csv(active=1)
        self.assertFalse((empty / "data.json").exists())

    def test_invalid_arguments_rejected(self):
        for keyword in (None, 1, True, 1.5, ["纸"], {"k": "纸"}):
            with self.subTest(keyword=keyword):
                with self.assertRaises(ValueError):
                    self.app.export_inventory_csv(keyword=keyword)
        for active in (0, 1, "true", "false", 1.0, [True], {"a": True}):
            with self.subTest(active=active):
                with self.assertRaises(ValueError):
                    self.app.export_inventory_csv(active=active)

    def test_export_is_repeatable_and_read_only(self):
        self.seed_final_paper()
        before = self.app.path.read_bytes()
        first = self.app.export_inventory_csv()
        filters = ({}, {"keyword": "纸"}, {"keyword": "纸", "active": True}, {"active": False}, {"keyword": "不存在"})
        for kwargs in filters:
            with self.subTest(kwargs=kwargs):
                self.assertEqual(self.app.export_inventory_csv(**kwargs),
                                 StockRoom(self.root).export_inventory_csv(**kwargs))
        self.assertEqual(self.app.export_inventory_csv(), first)
        self.assertEqual(self.app.path.read_bytes(), before)
        self.assertEqual(StockRoom(self.root).path.read_bytes(), before)
        self.assertEqual(self.app.stock("PAPER")["quantity"], 17)
        self.assertEqual(self.app.stock("BOX")["quantity"], 0)
        self.assertEqual([row["quantity"] for row in self.app.history("PAPER")], [20, -6, -3, 6])
        self.assertEqual(len(self.app.counts("PAPER")), 1)
        self.assertEqual(len(self.app.reversals("PAPER")), 1)
        self.assertEqual([item["code"] for item in self.app.shortages()], ["BOX"])
        self.assertEqual(self.app.material_status("BOX")["active"], False)

    def test_rejected_export_preserves_bytes_stock_config_and_histories(self):
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.export_inventory_csv(keyword=11)
        with self.assertRaises(ValueError):
            self.app.export_inventory_csv(active=1)
        with self.assertRaises(ValueError):
            self.app.export_inventory_csv(active=0)
        self.assertEqual(self.app.path.read_bytes(), before)
        self.assertEqual(self.app.stock("PAPER")["quantity"], 14)
        self.assertEqual(self.app.stock("BOX")["quantity"], 0)
        self.assertEqual(len(self.app.history("PAPER")), 2)
        self.assertEqual(self.app.counts("PAPER"), [])
        self.assertEqual(self.app.reversals("PAPER"), [])
        self.assertEqual([item["code"] for item in self.app.shortages()], ["BOX", "PAPER"])
        self.assertEqual(self.app.material_status("BOX")["active"], False)

    def test_legacy_data_defaults_to_zero_minimum_and_true_after_reopen(self):
        legacy = self.root / "legacy"
        legacy.mkdir()
        path = legacy / "data.json"
        document = {
            "materials": {
                "OLD": {"code": "OLD", "name": "旧料", "unit": "个"},
                "NEW": {"code": "NEW", "name": "新料", "unit": "卷"},
            },
            "movements": [{"code": "OLD", "quantity": 5, "reference": "OLD-IN"}],
        }
        path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
        before = path.read_bytes()
        expected = HEADER_ONLY + "NEW,新料,卷,0,0,true\nOLD,旧料,个,5,0,true\n"
        self.assertEqual(StockRoom(legacy).export_inventory_csv(), expected)
        self.assertEqual(StockRoom(legacy).export_inventory_csv(), expected)
        self.assertEqual(path.read_bytes(), before)

    def run_cli(self, root, *args):
        return subprocess.run([sys.executable, "-m", "stock_room", "--root", str(root), *args],
                              text=True, capture_output=True)

    def test_cli_export_success_prints_csv_carried_by_json_string(self):
        self.seed_final_paper()
        result = self.run_cli(self.root, "export-inventory-csv")
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertIsInstance(value, str)
        self.assertEqual(value, self.app.export_inventory_csv())
        payload = self.root / "filter.json"
        payload.write_text(json.dumps({"keyword": "  纸  ", "active": True}), encoding="utf-8")
        result = self.run_cli(self.root, "export-inventory-csv", str(payload))
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual(value, self.app.export_inventory_csv(keyword="纸", active=True))
        self.assertEqual(self.codes(value), ["PAPER"])

    def test_cli_export_array_returns_strings_in_order(self):
        payload = self.root / "queries.json"
        payload.write_text(json.dumps([
            {"keyword": "纸"},
            {"keyword": "paper"},
            {},
        ]), encoding="utf-8")
        result = self.run_cli(self.root, "export-inventory-csv", str(payload))
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertIsInstance(value, list)
        self.assertEqual(value, [
            self.app.export_inventory_csv(keyword="纸"),
            self.app.export_inventory_csv(keyword="paper"),
            self.app.export_inventory_csv(),
        ])
        self.assertTrue(all(isinstance(item, str) for item in value))
        self.assertEqual(self.codes(value[0]), ["BOX", "PAPER"])
        self.assertEqual(value[1], HEADER_ONLY)

    def test_cli_invalid_filter_returns_2_empty_stdout_json_error(self):
        before = self.app.path.read_bytes()
        for body in ({"keyword": 11}, {"active": 1}, {"active": 0}):
            with self.subTest(body=body):
                payload = self.root / "bad-filter.json"
                payload.write_text(json.dumps(body), encoding="utf-8")
                result = self.run_cli(self.root, "export-inventory-csv", str(payload))
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
                self.assertIn("error", json.loads(result.stderr))
        payload = self.root / "bad-filter-array.json"
        payload.write_text(json.dumps([{"keyword": "纸"}, {"active": 1}]), encoding="utf-8")
        result = self.run_cli(self.root, "export-inventory-csv", str(payload))
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("error", json.loads(result.stderr))
        self.assertEqual(self.app.path.read_bytes(), before)

    def test_cli_empty_directory_exports_header_only_without_file(self):
        empty = self.root / "empty"
        result = self.run_cli(empty, "export-inventory-csv")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), HEADER_ONLY)
        self.assertFalse((empty / "data.json").exists())


class PurchaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = StockRoom(self.root)
        self.app.register("PAPER", "包装纸", "张")
        self.app.register("BOX", "纸箱", "个")
        self.app.movement("PAPER", 20, "IN-001")
        self.app.movement("PAPER", -6, "OUT-001")

    def run_cli(self, *args):
        return subprocess.run([sys.executable, "-m", "stock_room", "--root", str(self.root), *args],
                              text=True, capture_output=True)

    def write_payload(self, name, body):
        payload = self.root / name
        payload.write_text(json.dumps(body), encoding="utf-8")
        return str(payload)

    def test_create_purchase_success_and_stock_unchanged(self):
        order = self.app.create_purchase("PO-1", " 甲 供应商 ", [
            {"code": "PAPER", "quantity": 5},
            {"code": "BOX", "quantity": 2},
        ])
        self.assertEqual(order, {
            "reference": "PO-1",
            "supplier": "甲 供应商",
            "status": "open",
            "rows": [
                {"code": "PAPER", "name": "包装纸", "unit": "张", "quantity": 5},
                {"code": "BOX", "name": "纸箱", "unit": "个", "quantity": 2},
            ],
        })
        self.assertEqual(self.app.stock("PAPER")["quantity"], 14)
        self.assertEqual(self.app.stock("BOX")["quantity"], 0)
        self.assertEqual(len(self.app.history("PAPER")), 2)

    def test_create_purchase_persists_across_reopen(self):
        self.app.create_purchase("PO-1", "供应商", [{"code": "PAPER", "quantity": 5}])
        reopened = StockRoom(self.root)
        self.assertEqual(reopened.purchase_order("PO-1")["status"], "open")
        self.assertEqual(reopened.purchase_order("PO-1")["rows"],
                         [{"code": "PAPER", "name": "包装纸", "unit": "张", "quantity": 5}])

    def test_purchase_reference_unique_only_among_purchases(self):
        order = self.app.create_purchase("IN-001", "供应商", [{"code": "PAPER", "quantity": 1}])
        self.assertEqual(order["reference"], "IN-001")
        with self.assertRaises(ValueError):
            self.app.create_purchase("IN-001", "供应商", [{"code": "PAPER", "quantity": 1}])
        with self.assertRaises(ValueError):
            self.app.create_purchase(" IN-001 ", "供应商", [{"code": "PAPER", "quantity": 1}])
        # 采购编号不占用出入库编号，反之亦然
        self.app.create_purchase("PO-9", "供应商", [{"code": "PAPER", "quantity": 1}])
        moved = self.app.movement("PAPER", 1, "PO-9")
        self.assertEqual(moved["balance"], 15)

    def test_create_purchase_validation_failures_preserve_file(self):
        before = self.app.path.read_bytes()
        bad_rows = [
            {"reference": "PO-2", "supplier": "供应商", "rows": []},
            {"reference": "PO-2", "supplier": "供应商", "rows": "PAPER"},
            {"reference": "PO-2", "supplier": "供应商", "rows": ["PAPER"]},
            {"reference": "PO-2", "supplier": "供应商", "rows": [{"code": "PAPER"}]},
            {"reference": "PO-2", "supplier": "供应商", "rows": [{"code": "PAPER", "quantity": 1, "name": "x"}]},
            {"reference": "PO-2", "supplier": "供应商", "rows": [{"code": "  ", "quantity": 1}]},
            {"reference": "PO-2", "supplier": "供应商", "rows": [{"code": "PAPER", "quantity": True}]},
            {"reference": "PO-2", "supplier": "供应商", "rows": [{"code": "PAPER", "quantity": 0}]},
            {"reference": "PO-2", "supplier": "供应商", "rows": [{"code": "PAPER", "quantity": -1}]},
            {"reference": "PO-2", "supplier": "供应商", "rows": [{"code": "PAPER", "quantity": 1.5}]},
            {"reference": "PO-2", "supplier": "供应商", "rows": [{"code": "PAPER", "quantity": "1"}]},
            {"reference": "PO-2", "supplier": "供应商", "rows": [{"code": "UNKNOWN", "quantity": 1}]},
            {"reference": "PO-2", "supplier": "供应商", "rows": [{"code": "PAPER", "quantity": 1}, {"code": " PAPER ", "quantity": 2}]},
            {"reference": "", "supplier": "供应商", "rows": [{"code": "PAPER", "quantity": 1}]},
            {"reference": "PO-2", "supplier": "  ", "rows": [{"code": "PAPER", "quantity": 1}]},
            {"reference": "PO-2", "supplier": 1, "rows": [{"code": "PAPER", "quantity": 1}]},
        ]
        for body in bad_rows:
            with self.subTest(body=body):
                with self.assertRaises(ValueError):
                    self.app.create_purchase(**body)
        self.assertEqual(self.app.path.read_bytes(), before)
        with self.assertRaises(ValueError):
            self.app.purchase_order("PO-2")

    def test_create_purchase_inactive_material_rejected(self):
        self.app.set_active("BOX", False)
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.create_purchase("PO-2", "供应商", [{"code": "BOX", "quantity": 1}])
        self.assertEqual(self.app.path.read_bytes(), before)

    def test_create_purchase_failure_creates_no_file(self):
        empty = self.root / "empty"
        app = StockRoom(empty)
        with self.assertRaises(ValueError):
            app.create_purchase("PO-1", "供应商", [{"code": "PAPER", "quantity": 1}])
        self.assertFalse((empty / "data.json").exists())

    def test_purchase_order_query_errors_and_no_rewrite(self):
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.purchase_order("MISSING")
        with self.assertRaises(ValueError):
            self.app.purchase_order("  ")
        with self.assertRaises(ValueError):
            self.app.cancel_purchase("MISSING")
        self.assertEqual(self.app.path.read_bytes(), before)
        empty = self.root / "empty"
        with self.assertRaises(ValueError):
            StockRoom(empty).purchase_order("PO-1")
        self.assertFalse((empty / "data.json").exists())

    def test_cancel_purchase_and_reopen(self):
        self.app.create_purchase("PO-1", "供应商", [{"code": "PAPER", "quantity": 5}])
        cancelled = self.app.cancel_purchase(" PO-1 ")
        self.assertEqual(cancelled["status"], "cancelled")
        again = self.app.cancel_purchase("PO-1")
        self.assertEqual(again, cancelled)
        reopened = StockRoom(self.root)
        order = reopened.purchase_order("PO-1")
        self.assertEqual(order["status"], "cancelled")
        self.assertEqual(order["rows"], [{"code": "PAPER", "name": "包装纸", "unit": "张", "quantity": 5}])
        self.assertEqual(reopened.stock("PAPER")["quantity"], 14)

    def test_cancel_unaffected_by_material_status(self):
        self.app.create_purchase("PO-1", "供应商", [{"code": "BOX", "quantity": 2}])
        self.app.set_active("BOX", False)
        self.assertEqual(self.app.cancel_purchase("PO-1")["status"], "cancelled")

    def test_snapshot_survives_material_updates(self):
        self.app.create_purchase("PO-1", "供应商", [{"code": "PAPER", "quantity": 5}])
        self.app.update_material("PAPER", "高级包装纸", "张")
        order = self.app.purchase_order("PO-1")
        self.assertEqual(order["rows"][0]["name"], "包装纸")
        self.assertEqual(order["rows"][0]["unit"], "张")
        # 采购单不算库存历史，不锁定单位变更
        self.app.register("INK", "油墨", "瓶")
        self.app.create_purchase("PO-2", "供应商", [{"code": "INK", "quantity": 3}])
        self.app.update_material("INK", "油墨", "桶")
        self.assertEqual(self.app.purchase_order("PO-2")["rows"][0]["unit"], "瓶")

    def test_legacy_data_without_purchases_behaves_as_empty(self):
        data = json.loads(self.app.path.read_text(encoding="utf-8"))
        data.pop("purchases", None)
        self.app.path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            StockRoom(self.root).purchase_order("PO-1")
        self.assertEqual(self.app.path.read_bytes(), before)

    def test_cli_create_query_cancel(self):
        payload = self.write_payload("po.json", {
            "reference": "PO-1", "supplier": "供应商",
            "rows": [{"code": "PAPER", "quantity": 5}, {"code": "BOX", "quantity": 2}],
        })
        result = self.run_cli("create-purchase", payload)
        self.assertEqual(result.returncode, 0, result.stderr)
        created = json.loads(result.stdout)
        self.assertEqual(created["status"], "open")
        query = self.write_payload("query.json", {"reference": "PO-1"})
        result = self.run_cli("purchase-order", query)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), created)
        result = self.run_cli("cancel-purchase", query)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "cancelled")
        result = self.run_cli("purchase-order", query)
        self.assertEqual(json.loads(result.stdout)["status"], "cancelled")
        self.assertEqual(StockRoom(self.root).stock("PAPER")["quantity"], 14)
        self.assertEqual(StockRoom(self.root).stock("BOX")["quantity"], 0)

    def test_cli_failures_return_2(self):
        before = self.app.path.read_bytes()
        payload = self.write_payload("bad-po.json", {"reference": "PO-2", "supplier": "供应商", "rows": []})
        result = self.run_cli("create-purchase", payload)
        self.assertEqual(result.returncode, 2)
        self.assertIn("error", json.loads(result.stderr))
        missing = self.write_payload("missing.json", {"reference": "MISSING"})
        for action in ("purchase-order", "cancel-purchase"):
            result = self.run_cli(action, missing)
            self.assertEqual(result.returncode, 2)
            self.assertIn("error", json.loads(result.stderr))
        self.assertEqual(self.app.path.read_bytes(), before)

    def test_cli_array_partial_success_preserved(self):
        payload = self.write_payload("po-array.json", [
            {"reference": "PO-1", "supplier": "供应商", "rows": [{"code": "PAPER", "quantity": 5}]},
            {"reference": "PO-2", "supplier": "供应商", "rows": [{"code": "UNKNOWN", "quantity": 1}]},
        ])
        result = self.run_cli("create-purchase", payload)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(StockRoom(self.root).purchase_order("PO-1")["status"], "open")


class PurchaseReceiptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = StockRoom(self.root)
        self.app.register("PAPER", "包装纸", "张")
        self.app.register("BOX", "纸箱", "个")
        self.app.movement("PAPER", 20, "IN-001")
        self.app.movement("PAPER", -6, "OUT-001")
        self.app.create_purchase("PO-1", " 甲 供应商 ", [
            {"code": "PAPER", "quantity": 5},
            {"code": "BOX", "quantity": 2},
        ])

    def run_cli(self, *args):
        return subprocess.run([sys.executable, "-m", "stock_room", "--root", str(self.root), *args],
                              text=True, capture_output=True)

    def write_payload(self, name, body):
        payload = self.root / name
        payload.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
        return str(payload)

    def test_fixed_sample_split_receipts_survive_reopen(self):
        first = self.app.receive_purchase(" PO-1 ", [
            {"code": "  PAPER  ", "quantity": 3, "reference": "  RCV-P-1  "},
        ])
        self.assertEqual(first, [{"code": "PAPER", "quantity": 3, "reference": "RCV-P-1", "balance": 17}])
        with self.assertRaises(ValueError):
            self.app.receive_purchase("PO-1", [
                {"code": "PAPER", "quantity": 3, "reference": "RCV-P-2"},
                {"code": "BOX", "quantity": 1, "reference": "RCV-B-1"},
            ])
        self.assertEqual(self.app.stock("PAPER")["quantity"], 17)
        self.assertEqual(self.app.stock("BOX")["quantity"], 0)
        self.assertEqual([row["reference"] for row in self.app.purchase_receipts("PO-1")], ["RCV-P-1"])
        second = self.app.receive_purchase("PO-1", [
            {"code": "PAPER", "quantity": 2, "reference": "RCV-P-3"},
            {"code": "BOX", "quantity": 2, "reference": "RCV-B-2"},
        ])
        self.assertEqual([(row["code"], row["quantity"], row["balance"]) for row in second],
                         [("PAPER", 2, 19), ("BOX", 2, 2)])
        self.assertEqual(self.app.stock("PAPER")["quantity"], 19)
        self.assertEqual(self.app.stock("BOX")["quantity"], 2)
        reopened = StockRoom(self.root)
        self.assertEqual(reopened.stock("PAPER")["quantity"], 19)
        self.assertEqual(reopened.stock("BOX")["quantity"], 2)
        receipts = reopened.purchase_receipts("PO-1")
        self.assertEqual(receipts, [
            {"code": "PAPER", "quantity": 3, "reference": "RCV-P-1", "balance": 17},
            {"code": "PAPER", "quantity": 2, "reference": "RCV-P-3", "balance": 19},
            {"code": "BOX", "quantity": 2, "reference": "RCV-B-2", "balance": 2},
        ])
        self.assertEqual([row["reference"] for row in reopened.history("PAPER")],
                         ["IN-001", "OUT-001", "RCV-P-1", "RCV-P-3"])
        self.assertEqual([row["reference"] for row in reopened.history("BOX")], ["RCV-B-2"])

    def test_purchase_structure_snapshot_and_status_unchanged(self):
        self.app.receive_purchase("PO-1", [{"code": "PAPER", "quantity": 5, "reference": "RCV-P-1"}])
        order = self.app.purchase_order("PO-1")
        self.assertEqual(order["status"], "open")
        self.assertEqual(order["rows"], [
            {"code": "PAPER", "name": "包装纸", "unit": "张", "quantity": 5},
            {"code": "BOX", "name": "纸箱", "unit": "个", "quantity": 2},
        ])
        self.app.update_material("PAPER", "高级包装纸", "张")
        self.assertEqual(self.app.purchase_order("PO-1")["rows"][0]["name"], "包装纸")

    def test_cancel_after_receipt_keeps_stock_and_records(self):
        self.app.receive_purchase("PO-1", [{"code": "PAPER", "quantity": 3, "reference": "RCV-P-1"}])
        cancelled = self.app.cancel_purchase("PO-1")
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(self.app.cancel_purchase("PO-1"), cancelled)
        self.assertEqual(self.app.stock("PAPER")["quantity"], 17)
        self.assertEqual(self.app.purchase_receipts("PO-1")[0]["reference"], "RCV-P-1")

    def test_unknown_and_cancelled_purchase_rejected(self):
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.receive_purchase("MISSING", [{"code": "PAPER", "quantity": 1, "reference": "RCV-X"}])
        with self.assertRaises(ValueError):
            self.app.receive_purchase("  ", [{"code": "PAPER", "quantity": 1, "reference": "RCV-X"}])
        self.assertEqual(self.app.path.read_bytes(), before)
        self.assertEqual(self.app.stock("PAPER")["quantity"], 14)
        self.app.cancel_purchase("PO-1")
        after_cancel = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.receive_purchase("PO-1", [{"code": "PAPER", "quantity": 1, "reference": "RCV-X"}])
        self.assertEqual(self.app.path.read_bytes(), after_cancel)
        self.assertEqual(self.app.stock("PAPER")["quantity"], 14)
        self.assertEqual(self.app.purchase_receipts("PO-1"), [])

    def test_invalid_rows_and_row_shape_rejected(self):
        for rows in (None, [], {}, "x", 1):
            with self.subTest(rows=rows):
                with self.assertRaises(ValueError):
                    self.app.receive_purchase("PO-1", rows)
        good = {"code": "PAPER", "quantity": 1, "reference": "RCV-X"}
        for rows in (
            [None],
            ["PAPER"],
            [[]],
            [{"code": "PAPER", "quantity": 1}],
            [{"code": "PAPER", "reference": "R"}],
            [{"quantity": 1, "reference": "R"}],
            [{"code": "PAPER", "quantity": 1, "reference": "R", "extra": 1}],
            [good, good],
        ):
            with self.subTest(rows=rows):
                with self.assertRaises(ValueError):
                    self.app.receive_purchase("PO-1", rows)

    def test_invalid_identifiers_and_quantity_rejected(self):
        for code in ("", "   ", 11, None):
            with self.subTest(code=code):
                with self.assertRaises(ValueError):
                    self.app.receive_purchase("PO-1", [{"code": code, "quantity": 1, "reference": "R"}])
        for reference in ("", "   ", 11, None):
            with self.subTest(reference=reference):
                with self.assertRaises(ValueError):
                    self.app.receive_purchase("PO-1", [{"code": "PAPER", "quantity": 1, "reference": reference}])
        for quantity in (0, -1, True, False, 1.5, "1", None, [1]):
            with self.subTest(quantity=quantity):
                with self.assertRaises(ValueError):
                    self.app.receive_purchase("PO-1", [{"code": "PAPER", "quantity": quantity, "reference": "R"}])

    def test_unknown_inactive_unlisted_and_unit_mismatch_rejected(self):
        self.app.register("INK", "油墨", "瓶")
        self.app.set_active("BOX", False)
        self.app.create_purchase("PO-2", "供应商", [{"code": "INK", "quantity": 3}])
        self.app.update_material("INK", "油墨", "桶")
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.receive_purchase("PO-1", [{"code": "UNKNOWN", "quantity": 1, "reference": "RCV-X"}])
        with self.assertRaises(ValueError):
            self.app.receive_purchase("PO-1", [{"code": "BOX", "quantity": 1, "reference": "RCV-X"}])
        with self.assertRaises(ValueError):
            self.app.receive_purchase("PO-1", [{"code": "INK", "quantity": 1, "reference": "RCV-X"}])
        with self.assertRaises(ValueError):
            self.app.receive_purchase("PO-2", [{"code": "INK", "quantity": 1, "reference": "RCV-X"}])
        self.assertEqual(self.app.path.read_bytes(), before)
        self.assertEqual([row["reference"] for row in self.app.history("INK")], [])

    def test_duplicate_code_within_batch_rejected(self):
        with self.assertRaises(ValueError):
            self.app.receive_purchase("PO-1", [
                {"code": "PAPER", "quantity": 1, "reference": "RCV-A"},
                {"code": " PAPER ", "quantity": 1, "reference": "RCV-B"},
            ])

    def test_cumulative_quantity_capped_by_order(self):
        self.app.receive_purchase("PO-1", [{"code": "PAPER", "quantity": 3, "reference": "RCV-P-1"}])
        with self.assertRaises(ValueError):
            self.app.receive_purchase("PO-1", [{"code": "PAPER", "quantity": 3, "reference": "RCV-P-2"}])
        self.assertEqual(self.app.stock("PAPER")["quantity"], 17)
        with self.assertRaises(ValueError):
            self.app.receive_purchase("PO-1", [
                {"code": "BOX", "quantity": 1, "reference": "RCV-B-1"},
                {"code": "BOX", "quantity": 2, "reference": "RCV-B-2"},
            ])
        self.assertEqual(self.app.stock("BOX")["quantity"], 0)
        # Exactly the remainder succeeds.
        self.app.receive_purchase("PO-1", [
            {"code": "PAPER", "quantity": 2, "reference": "RCV-P-3"},
            {"code": "BOX", "quantity": 2, "reference": "RCV-B-3"},
        ])
        with self.assertRaises(ValueError):
            self.app.receive_purchase("PO-1", [{"code": "PAPER", "quantity": 1, "reference": "RCV-P-4"}])

    def test_received_totals_follow_only_associations(self):
        self.app.receive_purchase("PO-1", [{"code": "PAPER", "quantity": 5, "reference": "RCV-P-1"}])
        self.app.movement("PAPER", -10, "OUT-002")
        self.assertEqual(self.app.stock("PAPER")["quantity"], 9)
        # Lower stock does not free ordered capacity; other receipts stay capped at zero.
        with self.assertRaises(ValueError):
            self.app.receive_purchase("PO-1", [{"code": "PAPER", "quantity": 1, "reference": "RCV-P-2"}])
        self.app.count("PAPER", 20, "CNT-001")
        with self.assertRaises(ValueError):
            self.app.receive_purchase("PO-1", [{"code": "PAPER", "quantity": 1, "reference": "RCV-P-3"}])

    def test_receipt_reference_scope_is_shared_with_all_histories(self):
        self.app.count("BOX", 0, "CNT-ZERO")
        self.app.reverse("OUT-001", "REV-OUT")
        for reference in ("IN-001", "CNT-ZERO", "REV-OUT"):
            with self.subTest(reference=reference):
                with self.assertRaises(ValueError):
                    self.app.receive_purchase("PO-1", [{"code": "PAPER", "quantity": 1, "reference": reference}])
        with self.assertRaises(ValueError):
            self.app.receive_purchase("PO-1", [
                {"code": "PAPER", "quantity": 1, "reference": "DUP"},
                {"code": "BOX", "quantity": 1, "reference": "  DUP  "},
            ])

    def test_purchase_reference_stays_independent(self):
        self.app.create_purchase("RCV-P-1", "供应商", [{"code": "PAPER", "quantity": 1}])
        record = self.app.receive_purchase("PO-1", [{"code": "PAPER", "quantity": 1, "reference": "RCV-P-1"}])
        self.assertEqual(record[0]["reference"], "RCV-P-1")

    def test_failed_receipt_preserves_file_and_all_state(self):
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.receive_purchase("PO-1", [
                {"code": "PAPER", "quantity": 1, "reference": "RCV-OK"},
                {"code": "BOX", "quantity": 3, "reference": "RCV-BAD"},
            ])
        with self.assertRaises(ValueError):
            self.app.receive_purchase("PO-1", [{"code": "PAPER", "quantity": 1, "reference": "IN-001"}])
        self.assertEqual(self.app.path.read_bytes(), before)
        self.assertEqual(self.app.stock("PAPER")["quantity"], 14)
        self.assertEqual(self.app.stock("BOX")["quantity"], 0)
        self.assertEqual(self.app.purchase_receipts("PO-1"), [])
        self.assertEqual(len(self.app.history("PAPER")), 2)
        # Rejected references are reusable.
        record = self.app.receive_purchase("PO-1", [
            {"code": "PAPER", "quantity": 1, "reference": "RCV-OK"},
        ])
        self.assertEqual(record[0]["balance"], 15)

    def test_failed_receipt_on_empty_directory_creates_no_file(self):
        empty = self.root / "empty"
        app = StockRoom(empty)
        with self.assertRaises(ValueError):
            app.receive_purchase("PO-1", [])
        with self.assertRaises(ValueError):
            app.receive_purchase("PO-1", [{"code": "PAPER", "quantity": 1, "reference": "R"}])
        self.assertFalse((empty / "data.json").exists())

    def test_receipt_movement_cannot_be_reversed(self):
        self.app.receive_purchase("PO-1", [{"code": "PAPER", "quantity": 3, "reference": "RCV-P-1"}])
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.reverse("RCV-P-1", "REV-RCV")
        self.assertEqual(self.app.path.read_bytes(), before)
        record = self.app.reverse("OUT-001", "REV-OUT")
        self.assertEqual(record["balance"], 23)

    def test_purchase_receipts_query_variants_and_read_only(self):
        self.assertEqual(self.app.purchase_receipts("PO-1"), [])
        with self.assertRaises(ValueError):
            self.app.purchase_receipts("MISSING")
        with self.assertRaises(ValueError):
            self.app.purchase_receipts("  ")
        before = self.app.path.read_bytes()
        self.app.purchase_receipts("PO-1")
        self.assertEqual(self.app.path.read_bytes(), before)

    def test_legacy_data_without_receipts_returns_empty(self):
        data = json.loads(self.app.path.read_text(encoding="utf-8"))
        data.pop("purchase_receipts", None)
        self.app.path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        reopened = StockRoom(self.root)
        self.assertEqual(reopened.purchase_receipts("PO-1"), [])
        record = reopened.receive_purchase("PO-1", [{"code": "PAPER", "quantity": 1, "reference": "RCV-P-1"}])
        self.assertEqual(record[0]["balance"], 15)

    def test_query_on_empty_directory_creates_no_file(self):
        empty = self.root / "empty"
        app = StockRoom(empty)
        with self.assertRaises(ValueError):
            app.purchase_receipts("PO-1")
        self.assertFalse((empty / "data.json").exists())

    def test_cli_receive_and_query(self):
        payload = self.write_payload("receive.json", {"purchase_reference": "PO-1", "rows": [
            {"code": "PAPER", "quantity": 3, "reference": "RCV-P-1"},
        ]})
        result = self.run_cli("receive-purchase", payload)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout),
                         [{"code": "PAPER", "quantity": 3, "reference": "RCV-P-1", "balance": 17}])
        query = self.write_payload("query.json", {"purchase_reference": "PO-1"})
        result = self.run_cli("purchase-receipts", query)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([row["reference"] for row in json.loads(result.stdout)], ["RCV-P-1"])
        bad = self.write_payload("bad.json", {"purchase_reference": "PO-1", "rows": [
            {"code": "PAPER", "quantity": 3, "reference": "RCV-P-2"},
        ]})
        failed = self.run_cli("receive-purchase", bad)
        self.assertEqual(failed.returncode, 2)
        self.assertIn("error", json.loads(failed.stderr))
        self.assertEqual(StockRoom(self.root).stock("PAPER")["quantity"], 17)

    def test_cli_array_keeps_earlier_commit(self):
        payload = self.write_payload("receives.json", [
            {"purchase_reference": "PO-1", "rows": [{"code": "PAPER", "quantity": 1, "reference": "RCV-1"}]},
            {"purchase_reference": "PO-1", "rows": [{"code": "PAPER", "quantity": 5, "reference": "RCV-2"}]},
        ])
        result = self.run_cli("receive-purchase", payload)
        self.assertEqual(result.returncode, 2)
        self.assertIn("error", json.loads(result.stderr))
        self.assertEqual(self.app.stock("PAPER")["quantity"], 15)
        self.assertEqual([row["reference"] for row in self.app.purchase_receipts("PO-1")], ["RCV-1"])


class PurchaseReturnTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = StockRoom(self.root)
        self.app.register("PAPER", "包装纸", "张")
        self.app.register("BOX", "纸箱", "个")
        self.app.create_purchase("PO-1", "供应商", [
            {"code": "PAPER", "quantity": 10},
            {"code": "BOX", "quantity": 2},
        ])
        self.app.receive_purchase("PO-1", [
            {"code": "PAPER", "quantity": 10, "reference": "RCV-P-1"},
            {"code": "BOX", "quantity": 2, "reference": "RCV-B-1"},
        ])

    def run_cli(self, *args):
        return subprocess.run([sys.executable, "-m", "stock_room", "--root", str(self.root), *args],
                              text=True, capture_output=True)

    def write_payload(self, name, body):
        payload = self.root / name
        payload.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
        return str(payload)

    def test_fixed_sample_10_received_then_3_and_2_then_6_rejected(self):
        first = self.app.return_purchase("  RCV-P-1  ", 3, "  RET-P-1  ")
        self.assertEqual(first, {
            "purchase_reference": "PO-1",
            "receipt_reference": "RCV-P-1",
            "code": "PAPER",
            "quantity": 3,
            "reference": "RET-P-1",
            "balance": 7,
        })
        self.assertEqual(self.app.stock("PAPER")["quantity"], 7)
        second = self.app.return_purchase("RCV-P-1", 2, "RET-P-2")
        self.assertEqual(second["balance"], 5)
        self.assertEqual(second["quantity"], 2)
        self.assertEqual(self.app.stock("PAPER")["quantity"], 5)
        with self.assertRaises(ValueError):
            self.app.return_purchase("RCV-P-1", 6, "RET-P-3")
        self.assertEqual(self.app.stock("PAPER")["quantity"], 5)
        reopened = StockRoom(self.root)
        records = reopened.purchase_returns("PO-1")
        self.assertEqual([(row["quantity"], row["reference"], row["balance"]) for row in records],
                         [(3, "RET-P-1", 7), (2, "RET-P-2", 5)])
        self.assertEqual(reopened.stock("PAPER")["quantity"], 5)
        with self.assertRaises(ValueError):
            reopened.return_purchase("RCV-P-1", 6, "RET-P-3")

    def test_return_appends_negative_movement_and_updates_shortages(self):
        self.app.set_minimum("PAPER", 8)
        self.assertEqual([item["code"] for item in self.app.shortages()], [])
        self.app.return_purchase("RCV-P-1", 3, "RET-P-1")
        self.assertEqual([row["reference"] for row in self.app.history("PAPER")][-2:],
                         ["RCV-P-1", "RET-P-1"])
        self.assertEqual(self.app.history("PAPER")[-1]["quantity"], -3)
        self.assertEqual(self.app.shortages(), [
            {"code": "PAPER", "name": "包装纸", "unit": "张", "quantity": 7, "minimum": 8, "shortage": 1},
        ])

    def test_partial_returns_accumulate_per_receipt(self):
        self.app.return_purchase("RCV-P-1", 4, "RET-P-1")
        self.app.return_purchase("RCV-P-1", 4, "RET-P-2")
        with self.assertRaises(ValueError):
            self.app.return_purchase("RCV-P-1", 3, "RET-P-3")
        # Exactly the remainder succeeds.
        record = self.app.return_purchase("RCV-P-1", 2, "RET-P-3")
        self.assertEqual(record["balance"], 0)
        with self.assertRaises(ValueError):
            self.app.return_purchase("RCV-P-1", 1, "RET-P-4")
        self.assertEqual(self.app.stock("PAPER")["quantity"], 0)
        self.assertEqual(len(self.app.purchase_returns("PO-1")), 3)

    def test_returns_are_tied_to_each_receipt_and_purchase(self):
        self.app.create_purchase("PO-2", "供应商", [{"code": "PAPER", "quantity": 5}])
        self.app.receive_purchase("PO-2", [{"code": "PAPER", "quantity": 5, "reference": "RCV-P-2"}])
        self.app.return_purchase("RCV-P-1", 3, "RET-P-1")
        self.app.return_purchase("RCV-P-2", 2, "RET-P-2")
        records = self.app.purchase_returns("  PO-1  ")
        self.assertEqual([(row["purchase_reference"], row["receipt_reference"], row["quantity"]) for row in records],
                         [("PO-1", "RCV-P-1", 3)])
        self.assertEqual([(row["purchase_reference"], row["receipt_reference"], row["quantity"]) for row in self.app.purchase_returns("PO-2")],
                         [("PO-2", "RCV-P-2", 2)])
        self.assertEqual(self.app.stock("PAPER")["quantity"], 10)

    def test_insufficient_stock_rejected_even_within_receipt_capacity(self):
        self.app.movement("PAPER", -8, "OUT-002")
        self.assertEqual(self.app.stock("PAPER")["quantity"], 2)
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.return_purchase("RCV-P-1", 3, "RET-P-1")
        self.assertEqual(self.app.path.read_bytes(), before)
        self.assertEqual(self.app.stock("PAPER")["quantity"], 2)
        record = self.app.return_purchase("RCV-P-1", 2, "RET-P-1")
        self.assertEqual(record["balance"], 0)

    def test_unknown_receipt_reference_rejected(self):
        before = self.app.path.read_bytes()
        for reference in ("MISSING", "  ", "", 11, None):
            with self.subTest(reference=reference):
                with self.assertRaises(ValueError):
                    self.app.return_purchase(reference, 1, "RET-X")
        # Ordinary movements, counts and purchase references are not receipts.
        self.app.movement("PAPER", 1, "IN-EXTRA")
        self.app.count("BOX", 2, "CNT-BOX")
        before = self.app.path.read_bytes()
        for reference in ("IN-EXTRA", "CNT-BOX", "PO-1"):
            with self.subTest(reference=reference):
                with self.assertRaises(ValueError):
                    self.app.return_purchase(reference, 1, "RET-X")
        self.assertEqual(self.app.path.read_bytes(), before)

    def test_invalid_quantity_rejected(self):
        for quantity in (0, -1, True, False, 1.5, "1", None, [1]):
            with self.subTest(quantity=quantity):
                with self.assertRaises(ValueError):
                    self.app.return_purchase("RCV-P-1", quantity, "RET-" + repr(quantity))
        for reference in ("", "   ", 11, None):
            with self.subTest(reference=reference):
                with self.assertRaises(ValueError):
                    self.app.return_purchase("RCV-P-1", 1, reference)

    def test_new_reference_conflicts_with_all_histories(self):
        self.app.movement("BOX", -1, "OUT-BOX")
        self.app.count("BOX", 0, "CNT-ZERO")
        self.app.reverse("OUT-BOX", "REV-BOX")
        for reference in ("RCV-P-1", "CNT-ZERO", "REV-BOX"):
            with self.subTest(reference=reference):
                with self.assertRaises(ValueError):
                    self.app.return_purchase("RCV-P-1", 1, reference)

    def test_cancelled_purchase_and_inactive_material_still_returnable(self):
        self.app.cancel_purchase("PO-1")
        self.app.set_active("PAPER", False)
        record = self.app.return_purchase("RCV-P-1", 3, "RET-P-1")
        self.assertEqual(record["balance"], 7)
        self.assertEqual(self.app.purchase_order("PO-1")["status"], "cancelled")
        self.assertEqual(self.app.material_status("PAPER")["active"], False)
        box = self.app.return_purchase("RCV-B-1", 2, "RET-B-1")
        self.assertEqual((box["code"], box["balance"]), ("BOX", 0))

    def test_returns_do_not_restore_receipt_quota_or_change_records(self):
        self.app.create_purchase("PO-2", "供应商", [{"code": "PAPER", "quantity": 5}])
        self.app.receive_purchase("PO-2", [{"code": "PAPER", "quantity": 3, "reference": "RCV-2-1"}])
        self.app.return_purchase("RCV-2-1", 3, "RET-2-1")
        with self.assertRaises(ValueError):
            self.app.receive_purchase("PO-2", [{"code": "PAPER", "quantity": 3, "reference": "RCV-2-2"}])
        self.app.receive_purchase("PO-2", [{"code": "PAPER", "quantity": 2, "reference": "RCV-2-2"}])
        self.assertEqual(self.app.purchase_receipts("PO-2"), [
            {"code": "PAPER", "quantity": 3, "reference": "RCV-2-1", "balance": 13},
            {"code": "PAPER", "quantity": 2, "reference": "RCV-2-2", "balance": 12},
        ])
        receipts = self.app.purchase_receipts("PO-1")
        self.app.return_purchase("RCV-P-1", 1, "RET-P-1")
        self.assertEqual(self.app.purchase_receipts("PO-1"), receipts)
        self.assertEqual(self.app.purchase_order("PO-1")["rows"][0],
                         {"code": "PAPER", "name": "包装纸", "unit": "张", "quantity": 10})

    def test_return_movement_cannot_be_reversed(self):
        self.app.return_purchase("RCV-P-1", 3, "RET-P-1")
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.reverse("RET-P-1", "REV-RET")
        self.assertEqual(self.app.path.read_bytes(), before)
        self.assertEqual(self.app.stock("PAPER")["quantity"], 7)

    def test_historical_balance_is_frozen(self):
        first = self.app.return_purchase("RCV-P-1", 3, "RET-P-1")
        self.app.movement("PAPER", 4, "IN-EXTRA")
        self.app.count("PAPER", 1, "CNT-001")
        self.assertEqual(self.app.purchase_returns("PO-1")[0]["balance"], first["balance"])

    def test_purchase_returns_query_variants_and_read_only(self):
        self.assertEqual(self.app.purchase_returns("PO-1"), [])
        for reference in ("MISSING", "  ", "", 11, None):
            with self.subTest(reference=reference):
                with self.assertRaises(ValueError):
                    self.app.purchase_returns(reference)
        before = self.app.path.read_bytes()
        self.app.purchase_returns("PO-1")
        self.assertEqual(self.app.path.read_bytes(), before)

    def test_failure_creates_no_file(self):
        empty = self.root / "empty"
        app = StockRoom(empty)
        with self.assertRaises(ValueError):
            app.return_purchase("RCV-P-1", 1, "RET-P-1")
        with self.assertRaises(ValueError):
            app.purchase_returns("PO-1")
        self.assertFalse((empty / "data.json").exists())

    def test_legacy_data_without_returns_behaves_as_empty(self):
        data = json.loads(self.app.path.read_text(encoding="utf-8"))
        data.pop("purchase_returns", None)
        self.app.path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        reopened = StockRoom(self.root)
        self.assertEqual(reopened.purchase_returns("PO-1"), [])
        record = reopened.return_purchase("RCV-P-1", 1, "RET-P-1")
        self.assertEqual(record["balance"], 9)

    def test_cli_return_and_query(self):
        payload = self.write_payload("return.json", {
            "receipt_reference": "RCV-P-1", "quantity": 3, "reference": "RET-P-1",
        })
        result = self.run_cli("return-purchase", payload)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {
            "purchase_reference": "PO-1",
            "receipt_reference": "RCV-P-1",
            "code": "PAPER",
            "quantity": 3,
            "reference": "RET-P-1",
            "balance": 7,
        })
        query = self.write_payload("query.json", {"purchase_reference": "PO-1"})
        result = self.run_cli("purchase-returns", query)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([row["reference"] for row in json.loads(result.stdout)], ["RET-P-1"])
        bad = self.write_payload("bad.json", {
            "receipt_reference": "RCV-P-1", "quantity": 8, "reference": "RET-P-2",
        })
        failed = self.run_cli("return-purchase", bad)
        self.assertEqual(failed.returncode, 2)
        self.assertIn("error", json.loads(failed.stderr))
        self.assertEqual(StockRoom(self.root).stock("PAPER")["quantity"], 7)
        missing = self.write_payload("missing.json", {"purchase_reference": "MISSING"})
        failed = self.run_cli("purchase-returns", missing)
        self.assertEqual(failed.returncode, 2)

    def test_cli_array_keeps_earlier_commit(self):
        payload = self.write_payload("returns.json", [
            {"receipt_reference": "RCV-P-1", "quantity": 3, "reference": "RET-P-1"},
            {"receipt_reference": "RCV-P-1", "quantity": 8, "reference": "RET-P-2"},
        ])
        result = self.run_cli("return-purchase", payload)
        self.assertEqual(result.returncode, 2)
        self.assertIn("error", json.loads(result.stderr))
        self.assertEqual(self.app.stock("PAPER")["quantity"], 7)
        records = self.app.purchase_returns("PO-1")
        self.assertEqual([(row["quantity"], row["reference"]) for row in records], [(3, "RET-P-1")])


class PurchaseReturnBatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = StockRoom(self.root)
        self.app.register("PAPER", "包装纸", "张")
        self.app.register("BOX", "纸箱", "个")
        self.app.create_purchase("PO-1", "供应商", [{"code": "PAPER", "quantity": 10}])
        self.app.receive_purchase("PO-1", [{"code": "PAPER", "quantity": 6, "reference": "RCV-A"}])
        self.app.receive_purchase("PO-1", [{"code": "PAPER", "quantity": 4, "reference": "RCV-B"}])

    def run_cli(self, *args):
        return subprocess.run([sys.executable, "-m", "stock_room", "--root", str(self.root), *args],
                              text=True, capture_output=True)

    def write_payload(self, name, body):
        payload = self.root / name
        payload.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
        return str(payload)

    def test_fixed_sample_balances_and_failed_second_batch(self):
        result = self.app.return_purchase_batch([
            {"receipt_reference": "RCV-A", "quantity": 2, "reference": "RET-1"},
            {"receipt_reference": "RCV-B", "quantity": 3, "reference": "RET-2"},
            {"receipt_reference": "RCV-A", "quantity": 1, "reference": "RET-3"},
        ])
        self.assertEqual([row["balance"] for row in result], [8, 5, 4])
        self.assertEqual([(row["receipt_reference"], row["quantity"], row["reference"]) for row in result],
                         [("RCV-A", 2, "RET-1"), ("RCV-B", 3, "RET-2"), ("RCV-A", 1, "RET-3")])
        self.assertEqual(self.app.stock("PAPER")["quantity"], 4)
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.return_purchase_batch([
                {"receipt_reference": "RCV-A", "quantity": 2, "reference": "RET-4"},
                {"receipt_reference": "RCV-A", "quantity": 2, "reference": "RET-5"},
            ])
        self.assertEqual(self.app.path.read_bytes(), before)
        self.assertEqual(self.app.stock("PAPER")["quantity"], 4)
        reopened = StockRoom(self.root)
        self.assertEqual([row["reference"] for row in reopened.purchase_returns("PO-1")],
                         ["RET-1", "RET-2", "RET-3"])
        self.assertEqual([row["reference"] for row in reopened.history("PAPER")],
                         ["RCV-A", "RCV-B", "RET-1", "RET-2", "RET-3"])
        with self.assertRaises(ValueError):
            reopened.return_purchase_batch([
                {"receipt_reference": "RCV-A", "quantity": 2, "reference": "RET-4"},
                {"receipt_reference": "RCV-A", "quantity": 2, "reference": "RET-5"},
            ])

    def test_cross_purchase_and_material_batch(self):
        self.app.create_purchase("PO-2", "供应商", [
            {"code": "PAPER", "quantity": 3},
            {"code": "BOX", "quantity": 2},
        ])
        self.app.receive_purchase("PO-2", [
            {"code": "PAPER", "quantity": 3, "reference": "RCV-C"},
            {"code": "BOX", "quantity": 2, "reference": "RCV-D"},
        ])
        result = self.app.return_purchase_batch([
            {"receipt_reference": "RCV-C", "quantity": 3, "reference": "RET-1"},
            {"receipt_reference": "RCV-D", "quantity": 2, "reference": "RET-2"},
            {"receipt_reference": "RCV-A", "quantity": 2, "reference": "RET-3"},
        ])
        self.assertEqual([(row["purchase_reference"], row["code"], row["balance"]) for row in result],
                         [("PO-2", "PAPER", 10), ("PO-2", "BOX", 0), ("PO-1", "PAPER", 8)])
        self.assertEqual([row["reference"] for row in self.app.purchase_returns("PO-1")], ["RET-3"])
        self.assertEqual([row["reference"] for row in self.app.purchase_returns("PO-2")], ["RET-1", "RET-2"])
        self.assertEqual([row["reference"] for row in self.app.history("PAPER")][-2:], ["RET-1", "RET-3"])

    def test_shared_stock_checked_row_by_row(self):
        self.app.movement("PAPER", -9, "OUT-1")
        self.assertEqual(self.app.stock("PAPER")["quantity"], 1)
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.return_purchase_batch([
                {"receipt_reference": "RCV-A", "quantity": 1, "reference": "RET-1"},
                {"receipt_reference": "RCV-B", "quantity": 1, "reference": "RET-2"},
            ])
        self.assertEqual(self.app.path.read_bytes(), before)
        self.assertEqual(self.app.stock("PAPER")["quantity"], 1)
        result = self.app.return_purchase_batch([
            {"receipt_reference": "RCV-A", "quantity": 1, "reference": "RET-1"},
        ])
        self.assertEqual(result[0]["balance"], 0)

    def test_cumulative_limit_includes_history_and_batch(self):
        self.app.return_purchase("RCV-A", 4, "RET-OLD")
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.return_purchase_batch([
                {"receipt_reference": "RCV-A", "quantity": 1, "reference": "RET-1"},
                {"receipt_reference": "RCV-A", "quantity": 2, "reference": "RET-2"},
            ])
        self.assertEqual(self.app.path.read_bytes(), before)
        result = self.app.return_purchase_batch([
            {"receipt_reference": "RCV-A", "quantity": 1, "reference": "RET-1"},
            {"receipt_reference": "RCV-A", "quantity": 1, "reference": "RET-2"},
        ])
        self.assertEqual([row["balance"] for row in result], [5, 4])
        with self.assertRaises(ValueError):
            self.app.return_purchase("RCV-A", 1, "RET-3")

    def test_cancelled_purchase_and_inactive_material_still_returnable(self):
        self.app.cancel_purchase("PO-1")
        self.app.set_active("PAPER", False)
        result = self.app.return_purchase_batch([
            {"receipt_reference": "RCV-A", "quantity": 2, "reference": "RET-1"},
        ])
        self.assertEqual(result[0]["balance"], 8)
        self.assertEqual(self.app.purchase_order("PO-1")["status"], "cancelled")
        self.assertEqual(self.app.material_status("PAPER")["active"], False)

    def test_invalid_rows_shape_rejected(self):
        before = self.app.path.read_bytes()
        for rows in (None, {}, "rows", 1, True, []):
            with self.subTest(rows=rows):
                with self.assertRaises(ValueError):
                    self.app.return_purchase_batch(rows)
        bad_rows = [
            ["not-an-object"],
            [{"receipt_reference": "RCV-A", "quantity": 1}],
            [{"receipt_reference": "RCV-A", "quantity": 1, "reference": "RET-1", "extra": 1}],
            [{"receipt_reference": "  ", "quantity": 1, "reference": "RET-1"}],
            [{"receipt_reference": "RCV-A", "quantity": 1, "reference": ""}],
            [{"receipt_reference": "RCV-A", "quantity": 1, "reference": 11}],
        ]
        for rows in bad_rows:
            with self.subTest(rows=rows):
                with self.assertRaises(ValueError):
                    self.app.return_purchase_batch(rows)
        self.assertEqual(self.app.path.read_bytes(), before)

    def test_invalid_quantity_rejected(self):
        before = self.app.path.read_bytes()
        for quantity in (0, -1, True, False, 1.5, "1", None, [1]):
            with self.subTest(quantity=quantity):
                with self.assertRaises(ValueError):
                    self.app.return_purchase_batch([
                        {"receipt_reference": "RCV-A", "quantity": quantity, "reference": "RET-1"},
                    ])
        self.assertEqual(self.app.path.read_bytes(), before)

    def test_unknown_and_non_receipt_references_rejected(self):
        self.app.movement("PAPER", 1, "IN-EXTRA")
        self.app.count("PAPER", 11, "CNT-1")
        before = self.app.path.read_bytes()
        for reference in ("MISSING", "IN-EXTRA", "CNT-1", "PO-1", "RET-OLD"):
            with self.subTest(reference=reference):
                with self.assertRaises(ValueError):
                    self.app.return_purchase_batch([
                        {"receipt_reference": reference, "quantity": 1, "reference": "RET-1"},
                    ])
        self.assertEqual(self.app.path.read_bytes(), before)

    def test_reference_conflicts_rejected(self):
        self.app.movement("PAPER", 1, "IN-EXTRA")
        self.app.count("PAPER", 11, "CNT-1")
        self.app.reverse("IN-EXTRA", "REV-1")
        before = self.app.path.read_bytes()
        for reference in ("RCV-A", "IN-EXTRA", "CNT-1", "REV-1"):
            with self.subTest(reference=reference):
                with self.assertRaises(ValueError):
                    self.app.return_purchase_batch([
                        {"receipt_reference": "RCV-A", "quantity": 1, "reference": reference},
                    ])
        with self.assertRaises(ValueError):
            self.app.return_purchase_batch([
                {"receipt_reference": "RCV-A", "quantity": 1, "reference": "RET-1"},
                {"receipt_reference": "RCV-B", "quantity": 1, "reference": "RET-1"},
            ])
        self.assertEqual(self.app.path.read_bytes(), before)
        # Purchase references live in their own namespace.
        result = self.app.return_purchase_batch([
            {"receipt_reference": "RCV-A", "quantity": 1, "reference": "PO-1"},
        ])
        self.assertEqual(result[0]["reference"], "PO-1")

    def test_failure_creates_no_file(self):
        empty = self.root / "empty"
        app = StockRoom(empty)
        with self.assertRaises(ValueError):
            app.return_purchase_batch([{"receipt_reference": "RCV-A", "quantity": 1, "reference": "RET-1"}])
        self.assertFalse((empty / "data.json").exists())

    def test_legacy_data_without_returns_behaves_as_empty(self):
        data = json.loads(self.app.path.read_text(encoding="utf-8"))
        data.pop("purchase_returns", None)
        self.app.path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        reopened = StockRoom(self.root)
        result = reopened.return_purchase_batch([
            {"receipt_reference": "RCV-A", "quantity": 6, "reference": "RET-1"},
        ])
        self.assertEqual(result[0]["balance"], 4)

    def test_return_movements_cannot_be_reversed_and_progress_reflects_returns(self):
        self.app.return_purchase_batch([
            {"receipt_reference": "RCV-A", "quantity": 2, "reference": "RET-1"},
        ])
        with self.assertRaises(ValueError):
            self.app.reverse("RET-1", "REV-RET")
        progress = self.app.purchase_progress("PO-1")
        self.assertEqual(progress["rows"][0]["returned"], 2)
        self.assertEqual(progress["rows"][0]["net_received"], 8)

    def test_returns_do_not_restore_receipt_quota_or_change_records(self):
        receipts = self.app.purchase_receipts("PO-1")
        order = self.app.purchase_order("PO-1")
        self.app.return_purchase_batch([
            {"receipt_reference": "RCV-A", "quantity": 6, "reference": "RET-1"},
        ])
        with self.assertRaises(ValueError):
            self.app.receive_purchase("PO-1", [{"code": "PAPER", "quantity": 1, "reference": "RCV-E"}])
        self.assertEqual(self.app.purchase_receipts("PO-1"), receipts)
        self.assertEqual(self.app.purchase_order("PO-1"), order)

    def test_cli_batch_and_query(self):
        payload = self.write_payload("batch.json", {"rows": [
            {"receipt_reference": "RCV-A", "quantity": 2, "reference": "RET-1"},
            {"receipt_reference": "RCV-B", "quantity": 3, "reference": "RET-2"},
            {"receipt_reference": "RCV-A", "quantity": 1, "reference": "RET-3"},
        ]})
        result = self.run_cli("return-purchase-batch", payload)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([row["balance"] for row in json.loads(result.stdout)], [8, 5, 4])
        bad = self.write_payload("bad.json", {"rows": [
            {"receipt_reference": "RCV-A", "quantity": 2, "reference": "RET-4"},
            {"receipt_reference": "RCV-A", "quantity": 2, "reference": "RET-5"},
        ]})
        failed = self.run_cli("return-purchase-batch", bad)
        self.assertEqual(failed.returncode, 2)
        self.assertIn("error", json.loads(failed.stderr))
        self.assertEqual(StockRoom(self.root).stock("PAPER")["quantity"], 4)
        query = self.write_payload("query.json", {"purchase_reference": "PO-1"})
        result = self.run_cli("purchase-returns", query)
        self.assertEqual([row["reference"] for row in json.loads(result.stdout)],
                         ["RET-1", "RET-2", "RET-3"])

    def test_cli_array_keeps_earlier_commit(self):
        payload = self.write_payload("batches.json", [
            {"rows": [{"receipt_reference": "RCV-A", "quantity": 2, "reference": "RET-1"}]},
            {"rows": [{"receipt_reference": "RCV-A", "quantity": 5, "reference": "RET-2"}]},
        ])
        result = self.run_cli("return-purchase-batch", payload)
        self.assertEqual(result.returncode, 2)
        self.assertIn("error", json.loads(result.stderr))
        self.assertEqual(self.app.stock("PAPER")["quantity"], 8)
        self.assertEqual([row["reference"] for row in self.app.purchase_returns("PO-1")], ["RET-1"])


class PreviewPurchaseReturnsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = StockRoom(self.root)
        self.app.register("PAPER", "包装纸", "张")
        self.app.register("BOX", "纸箱", "个")
        self.app.create_purchase("PO-1", "供应商", [{"code": "PAPER", "quantity": 10}])
        self.app.receive_purchase("PO-1", [{"code": "PAPER", "quantity": 6, "reference": "RCV-6"}])

    def run_cli(self, *args):
        return subprocess.run([sys.executable, "-m", "stock_room", "--root", str(self.root), *args],
                              text=True, capture_output=True)

    def write_payload(self, name, body):
        payload = self.root / name
        payload.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
        return str(payload)

    def rows(self):
        return [
            {"receipt_reference": "RCV-6", "quantity": 2, "reference": "RET-P-2"},
            {"receipt_reference": "RCV-6", "quantity": 1, "reference": "RET-P-1"},
        ]

    def test_preview_before_balance_and_receipt_remaining(self):
        self.app.return_purchase("RCV-6", 1, "RET-OLD")
        self.assertEqual(self.app.stock("PAPER")["quantity"], 5)
        self.app.movement("PAPER", -1, "OUT-1")
        self.assertEqual(self.app.stock("PAPER")["quantity"], 4)
        before = self.app.path.read_bytes()
        result = self.app.preview_purchase_returns(self.rows())
        self.assertEqual([(row["before"], row["balance"], row["receipt_remaining"]) for row in result],
                         [(4, 2, 3), (2, 1, 2)])
        self.assertEqual([row["quantity"] for row in result], [2, 1])
        self.assertEqual([row["reference"] for row in result], ["RET-P-2", "RET-P-1"])
        self.assertTrue(all(row["purchase_reference"] == "PO-1" and row["code"] == "PAPER"
                            and row["receipt_reference"] == "RCV-6" for row in result))
        # Preview changes nothing.
        self.assertEqual(self.app.path.read_bytes(), before)
        self.assertEqual(self.app.stock("PAPER")["quantity"], 4)
        self.assertEqual([row["reference"] for row in self.app.purchase_returns("PO-1")], ["RET-OLD"])

    def test_repeated_preview_is_identical_and_input_unchanged(self):
        self.app.return_purchase("RCV-6", 1, "RET-OLD")
        rows = self.rows()
        first = self.app.preview_purchase_returns(rows)
        second = self.app.preview_purchase_returns(json.loads(json.dumps(rows)))
        self.assertEqual(first, second)
        self.assertEqual(rows, self.rows())
        self.assertEqual(self.app.stock("PAPER")["quantity"], 5)

    def test_preview_then_commit_matches_without_extra_fields(self):
        self.app.return_purchase("RCV-6", 1, "RET-OLD")
        self.app.movement("PAPER", -1, "OUT-1")
        preview = self.app.preview_purchase_returns(self.rows())
        committed = self.app.return_purchase_batch(self.rows())
        self.assertEqual(len(preview), len(committed))
        for previewed, actual in zip(preview, committed):
            self.assertEqual({key: value for key, value in previewed.items()
                              if key not in ("before", "receipt_remaining")}, actual)
        reopened = StockRoom(self.root)
        self.assertEqual(reopened.stock("PAPER")["quantity"], 1)
        self.assertEqual([row["reference"] for row in reopened.purchase_returns("PO-1")],
                         ["RET-OLD", "RET-P-2", "RET-P-1"])

    def test_preview_independent_rows_do_not_chain_in_cli_array(self):
        self.app.return_purchase("RCV-6", 1, "RET-OLD")
        payload = self.write_payload("previews.json", [
            {"rows": self.rows()},
            {"rows": self.rows()},
        ])
        result = self.run_cli("preview-purchase-returns", payload)
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        for batch in output:
            self.assertEqual([(row["before"], row["balance"], row["receipt_remaining"]) for row in batch],
                             [(5, 3, 3), (3, 2, 2)])
        self.assertEqual(self.app.stock("PAPER")["quantity"], 5)

    def test_cross_purchase_material_and_repeated_receipt(self):
        self.app.create_purchase("PO-2", "供应商", [
            {"code": "PAPER", "quantity": 3},
            {"code": "BOX", "quantity": 2},
        ])
        self.app.receive_purchase("PO-2", [
            {"code": "PAPER", "quantity": 3, "reference": "RCV-C"},
            {"code": "BOX", "quantity": 2, "reference": "RCV-D"},
        ])
        result = self.app.preview_purchase_returns([
            {"receipt_reference": "RCV-C", "quantity": 3, "reference": "RET-1"},
            {"receipt_reference": "RCV-D", "quantity": 2, "reference": "RET-2"},
            {"receipt_reference": "RCV-6", "quantity": 2, "reference": "RET-3"},
            {"receipt_reference": "RCV-6", "quantity": 1, "reference": "RET-4"},
        ])
        self.assertEqual([(row["purchase_reference"], row["code"], row["before"], row["balance"],
                           row["receipt_remaining"]) for row in result],
                         [("PO-2", "PAPER", 9, 6, 0), ("PO-2", "BOX", 2, 0, 0),
                          ("PO-1", "PAPER", 6, 4, 4), ("PO-1", "PAPER", 4, 3, 3)])
        self.assertEqual(self.app.stock("PAPER")["quantity"], 9)
        self.assertEqual(self.app.stock("BOX")["quantity"], 2)
        self.assertEqual(self.app.purchase_returns("PO-1"), [])
        self.assertEqual(self.app.purchase_returns("PO-2"), [])

    def test_cancelled_purchase_and_inactive_material_still_previewable(self):
        self.app.cancel_purchase("PO-1")
        self.app.set_active("PAPER", False)
        result = self.app.preview_purchase_returns([
            {"receipt_reference": "RCV-6", "quantity": 2, "reference": "RET-1"},
        ])
        self.assertEqual(result[0]["before"], 6)
        self.assertEqual(result[0]["balance"], 4)
        self.assertEqual(result[0]["receipt_remaining"], 4)
        self.assertEqual(self.app.material_status("PAPER")["active"], False)

    def test_cumulative_over_return_including_history_rejected(self):
        self.app.return_purchase("RCV-6", 4, "RET-OLD")
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.preview_purchase_returns([
                {"receipt_reference": "RCV-6", "quantity": 1, "reference": "RET-1"},
                {"receipt_reference": "RCV-6", "quantity": 2, "reference": "RET-2"},
            ])
        self.assertEqual(self.app.path.read_bytes(), before)
        self.assertEqual([row["reference"] for row in self.app.purchase_returns("PO-1")], ["RET-OLD"])

    def test_insufficient_stock_rejected_row_by_row(self):
        self.app.movement("PAPER", -5, "OUT-1")
        self.assertEqual(self.app.stock("PAPER")["quantity"], 1)
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.preview_purchase_returns([
                {"receipt_reference": "RCV-6", "quantity": 1, "reference": "RET-1"},
                {"receipt_reference": "RCV-6", "quantity": 1, "reference": "RET-2"},
            ])
        self.assertEqual(self.app.path.read_bytes(), before)
        self.assertEqual(self.app.stock("PAPER")["quantity"], 1)

    def test_invalid_rows_shape_rejected(self):
        before = self.app.path.read_bytes()
        for rows in (None, {}, "rows", 1, True, []):
            with self.subTest(rows=rows):
                with self.assertRaises(ValueError):
                    self.app.preview_purchase_returns(rows)
        bad_rows = [
            ["not-an-object"],
            [{"receipt_reference": "RCV-6", "quantity": 1}],
            [{"receipt_reference": "RCV-6", "quantity": 1, "reference": "RET-1", "extra": 1}],
            [{"receipt_reference": "  ", "quantity": 1, "reference": "RET-1"}],
            [{"receipt_reference": "RCV-6", "quantity": 1, "reference": ""}],
            [{"receipt_reference": "RCV-6", "quantity": 1, "reference": 11}],
        ]
        for rows in bad_rows:
            with self.subTest(rows=rows):
                with self.assertRaises(ValueError):
                    self.app.preview_purchase_returns(rows)
        self.assertEqual(self.app.path.read_bytes(), before)

    def test_invalid_quantity_rejected(self):
        before = self.app.path.read_bytes()
        for quantity in (0, -1, True, False, 1.5, "1", None, [1]):
            with self.subTest(quantity=quantity):
                with self.assertRaises(ValueError):
                    self.app.preview_purchase_returns([
                        {"receipt_reference": "RCV-6", "quantity": quantity, "reference": "RET-1"},
                    ])
        self.assertEqual(self.app.path.read_bytes(), before)

    def test_unknown_and_non_receipt_references_rejected(self):
        self.app.movement("PAPER", 1, "IN-EXTRA")
        self.app.count("PAPER", 8, "CNT-1")
        before = self.app.path.read_bytes()
        for reference in ("MISSING", "IN-EXTRA", "CNT-1", "PO-1", "RET-OLD"):
            with self.subTest(reference=reference):
                with self.assertRaises(ValueError):
                    self.app.preview_purchase_returns([
                        {"receipt_reference": reference, "quantity": 1, "reference": "RET-1"},
                    ])
        self.assertEqual(self.app.path.read_bytes(), before)

    def test_reference_conflicts_rejected(self):
        self.app.movement("PAPER", 1, "IN-EXTRA")
        self.app.count("PAPER", 8, "CNT-1")
        self.app.reverse("IN-EXTRA", "REV-1")
        before = self.app.path.read_bytes()
        for reference in ("RCV-6", "IN-EXTRA", "CNT-1", "REV-1"):
            with self.subTest(reference=reference):
                with self.assertRaises(ValueError):
                    self.app.preview_purchase_returns([
                        {"receipt_reference": "RCV-6", "quantity": 1, "reference": reference},
                    ])
        with self.assertRaises(ValueError):
            self.app.preview_purchase_returns([
                {"receipt_reference": "RCV-6", "quantity": 1, "reference": "RET-1"},
                {"receipt_reference": "RCV-6", "quantity": 1, "reference": "RET-1"},
            ])
        self.assertEqual(self.app.path.read_bytes(), before)
        # Purchase references live in their own namespace.
        result = self.app.preview_purchase_returns([
            {"receipt_reference": "RCV-6", "quantity": 1, "reference": "PO-1"},
        ])
        self.assertEqual(result[0]["reference"], "PO-1")
        self.assertEqual(self.app.path.read_bytes(), before)

    def test_failure_creates_no_file(self):
        empty = self.root / "empty"
        app = StockRoom(empty)
        with self.assertRaises(ValueError):
            app.preview_purchase_returns(
                [{"receipt_reference": "RCV-6", "quantity": 1, "reference": "RET-1"}])
        self.assertFalse((empty / "data.json").exists())

    def test_legacy_data_without_returns_behaves_as_zero(self):
        data = json.loads(self.app.path.read_text(encoding="utf-8"))
        data.pop("purchase_returns", None)
        self.app.path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        reopened = StockRoom(self.root)
        result = reopened.preview_purchase_returns([
            {"receipt_reference": "RCV-6", "quantity": 6, "reference": "RET-1"},
        ])
        self.assertEqual(result[0]["before"], 6)
        self.assertEqual(result[0]["balance"], 0)
        self.assertEqual(result[0]["receipt_remaining"], 0)

    def test_cli_preview_and_failure(self):
        self.app.return_purchase("RCV-6", 1, "RET-OLD")
        payload = self.write_payload("preview.json", {"rows": self.rows()})
        result = self.run_cli("preview-purchase-returns", payload)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([(row["before"], row["balance"], row["receipt_remaining"])
                          for row in json.loads(result.stdout)],
                         [(5, 3, 3), (3, 2, 2)])
        bad = self.write_payload("bad.json", {"rows": [
            {"receipt_reference": "RCV-6", "quantity": 6, "reference": "RET-X"},
        ]})
        failed = self.run_cli("preview-purchase-returns", bad)
        self.assertEqual(failed.returncode, 2)
        self.assertIn("error", json.loads(failed.stderr))
        self.assertEqual(StockRoom(self.root).stock("PAPER")["quantity"], 5)
        self.assertEqual([row["reference"] for row in self.app.purchase_returns("PO-1")], ["RET-OLD"])


class PurchaseProgressTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = StockRoom(self.root)
        self.app.register("PAPER", "包装纸", "张")
        self.app.register("BOX", "纸箱", "个")
        self.app.create_purchase("  PO-1  ", "供应商", [
            {"code": "PAPER", "quantity": 10},
            {"code": "BOX", "quantity": 4},
        ])

    def run_cli(self, *args):
        return subprocess.run([sys.executable, "-m", "stock_room", "--root", str(self.root), *args],
                              text=True, capture_output=True)

    def write_payload(self, name, body):
        payload = self.root / name
        payload.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
        return str(payload)

    def figures(self, progress):
        return [(row["received"], row["returned"], row["net_received"], row["remaining"]) for row in progress["rows"]]

    def test_pending_structure_preserves_snapshot_and_order(self):
        progress = self.app.purchase_progress("PO-1")
        self.assertEqual(list(progress), ["reference", "supplier", "status", "rows", "progress"])
        self.assertEqual(progress["reference"], "PO-1")
        self.assertEqual(progress["supplier"], "供应商")
        self.assertEqual(progress["status"], "open")
        self.assertEqual(progress["progress"], "pending")
        self.assertEqual([row["code"] for row in progress["rows"]], ["PAPER", "BOX"])
        self.assertEqual(progress["rows"][0], {
            "code": "PAPER", "name": "包装纸", "unit": "张", "quantity": 10,
            "received": 0, "returned": 0, "net_received": 0, "remaining": 10,
        })
        self.assertEqual(progress["rows"][1], {
            "code": "BOX", "name": "纸箱", "unit": "个", "quantity": 4,
            "received": 0, "returned": 0, "net_received": 0, "remaining": 4,
        })

    def test_fixed_sample_partial_then_complete_survives_reopen_and_cancel(self):
        self.app.receive_purchase("PO-1", [{"code": "PAPER", "quantity": 6, "reference": "RCV-P-1"}])
        self.app.return_purchase("RCV-P-1", 2, "RET-P-1")
        progress = StockRoom(self.root).purchase_progress("  PO-1  ")
        self.assertEqual(progress["progress"], "partial")
        self.assertEqual(self.figures(progress), [(6, 2, 4, 4), (0, 0, 0, 4)])
        # Return does not restore receipt quota: remaining stays based on receipts only.
        self.app.receive_purchase("PO-1", [
            {"code": "PAPER", "quantity": 4, "reference": "RCV-P-2"},
            {"code": "BOX", "quantity": 4, "reference": "RCV-B-1"},
        ])
        progress = StockRoom(self.root).purchase_progress("PO-1")
        self.assertEqual(progress["progress"], "complete")
        self.assertEqual(self.figures(progress), [(10, 2, 8, 0), (4, 0, 4, 0)])
        cancelled = self.app.cancel_purchase("PO-1")
        progress = self.app.purchase_progress("PO-1")
        self.assertEqual(progress["status"], "cancelled")
        self.assertEqual(progress["progress"], "complete")
        self.assertEqual(progress["supplier"], cancelled["supplier"])
        self.assertEqual(self.figures(progress), [(10, 2, 8, 0), (4, 0, 4, 0)])
        with self.assertRaises(ValueError):
            self.app.receive_purchase("PO-1", [{"code": "PAPER", "quantity": 1, "reference": "RCV-X"}])

    def test_partial_when_any_row_unreceived_even_if_another_complete(self):
        self.app.receive_purchase("PO-1", [{"code": "PAPER", "quantity": 10, "reference": "RCV-P-1"}])
        progress = self.app.purchase_progress("PO-1")
        self.assertEqual(progress["progress"], "partial")
        self.assertEqual(self.figures(progress), [(10, 0, 10, 0), (0, 0, 0, 4)])

    def test_returns_never_make_order_pending_or_change_remaining(self):
        self.app.receive_purchase("PO-1", [{"code": "PAPER", "quantity": 10, "reference": "RCV-P-1"}])
        self.app.receive_purchase("PO-1", [{"code": "BOX", "quantity": 4, "reference": "RCV-B-1"}])
        self.app.return_purchase("RCV-P-1", 10, "RET-P-1")
        progress = self.app.purchase_progress("PO-1")
        self.assertEqual(progress["progress"], "complete")
        self.assertEqual(progress["rows"][0]["remaining"], 0)
        self.assertEqual(self.figures(progress)[0], (10, 10, 0, 0))

    def test_movements_counts_and_reversals_do_not_affect_stats(self):
        self.app.receive_purchase("PO-1", [{"code": "PAPER", "quantity": 6, "reference": "RCV-P-1"}])
        self.app.movement("PAPER", 5, "IN-1")
        self.app.movement("PAPER", -3, "OUT-1")
        self.app.count("PAPER", 9, "CNT-1")
        self.app.reverse("IN-1", "REV-1")
        progress = self.app.purchase_progress("PO-1")
        self.assertEqual(self.figures(progress), [(6, 0, 6, 4), (0, 0, 0, 4)])
        self.assertEqual(progress["progress"], "partial")

    def test_snapshot_and_row_scope_survive_rename_and_deactivation(self):
        self.app.receive_purchase("PO-1", [{"code": "PAPER", "quantity": 6, "reference": "RCV-P-1"}])
        self.app.update_material("PAPER", "新名称", "张")
        self.app.set_active("BOX", False)
        progress = self.app.purchase_progress("PO-1")
        self.assertEqual([(row["code"], row["name"], row["unit"]) for row in progress["rows"]],
                         [("PAPER", "包装纸", "张"), ("BOX", "纸箱", "个")])
        self.assertEqual(progress["rows"][0]["received"], 6)

    def test_reference_trimming_case_and_internal_whitespace(self):
        self.app.create_purchase("PO 2", "供应商", [{"code": "PAPER", "quantity": 1}])
        self.assertEqual(self.app.purchase_progress("  PO 2  ")["reference"], "PO 2")
        for reference in ("po-1", "PO-2", "po 2", "PO  2"):
            with self.assertRaises(ValueError):
                self.app.purchase_progress(reference)

    def test_invalid_references_raise_value_error(self):
        for reference in (None, 123, "", "   ", [], {}):
            with self.assertRaises(ValueError):
                self.app.purchase_progress(reference)
        with self.assertRaises(ValueError):
            self.app.purchase_progress("UNKNOWN")

    def test_query_is_read_only_and_creates_no_file(self):
        self.app.receive_purchase("PO-1", [{"code": "PAPER", "quantity": 6, "reference": "RCV-P-1"}])
        before = self.app.path.read_bytes()
        self.app.purchase_progress("PO-1")
        with self.assertRaises(ValueError):
            self.app.purchase_progress("MISSING")
        self.assertEqual(self.app.path.read_bytes(), before)
        empty = self.root / "empty"
        app = StockRoom(empty)
        with self.assertRaises(ValueError):
            app.purchase_progress("PO-1")
        self.assertFalse((empty / "data.json").exists())

    def test_legacy_data_without_receipts_or_returns_counts_as_zero(self):
        self.app.receive_purchase("PO-1", [{"code": "PAPER", "quantity": 6, "reference": "RCV-P-1"}])
        self.app.return_purchase("RCV-P-1", 2, "RET-P-1")
        data = json.loads(self.app.path.read_text(encoding="utf-8"))
        data.pop("purchase_receipts", None)
        data.pop("purchase_returns", None)
        self.app.path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        progress = StockRoom(self.root).purchase_progress("PO-1")
        self.assertEqual(progress["progress"], "pending")
        self.assertEqual(self.figures(progress), [(0, 0, 0, 10), (0, 0, 0, 4)])

    def test_cli_progress_object_and_array_success_and_failure(self):
        self.app.receive_purchase("PO-1", [{"code": "PAPER", "quantity": 6, "reference": "RCV-P-1"}])
        self.app.return_purchase("RCV-P-1", 2, "RET-P-1")
        payload = self.write_payload("progress.json", {"purchase_reference": "PO-1"})
        result = self.run_cli("purchase-progress", payload)
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual(value["progress"], "partial")
        self.assertEqual(value["rows"][0]["received"], 6)
        array_payload = self.write_payload("progresses.json", [
            {"purchase_reference": "PO-1"}, {"purchase_reference": "PO-1"},
        ])
        result = self.run_cli("purchase-progress", array_payload)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(json.loads(result.stdout)), 2)
        bad = self.write_payload("bad.json", {"purchase_reference": "MISSING"})
        before = self.app.path.read_bytes()
        failed = self.run_cli("purchase-progress", bad)
        self.assertEqual(failed.returncode, 2)
        self.assertIn("error", json.loads(failed.stderr))
        self.assertEqual(self.app.path.read_bytes(), before)


class PurchaseOrdersTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = StockRoom(self.root)
        self.app.register("PAPER", "包装纸", "张")
        # Fixed sample: create PO-B before PO-A.
        self.app.create_purchase("PO-B", "北方", [{"code": "PAPER", "quantity": 10}])
        self.app.receive_purchase("PO-B", [{"code": "PAPER", "quantity": 6, "reference": "RCV-B-1"}])
        self.app.return_purchase("RCV-B-1", 2, "RET-B-1")
        self.app.create_purchase("PO-A", "南方", [{"code": "PAPER", "quantity": 2}])
        self.app.receive_purchase("PO-A", [{"code": "PAPER", "quantity": 2, "reference": "RCV-A-1"}])
        self.app.return_purchase("RCV-A-1", 2, "RET-A-1")
        self.app.cancel_purchase("PO-A")

    def run_cli(self, *args):
        return subprocess.run([sys.executable, "-m", "stock_room", "--root", str(self.root), *args],
                              text=True, capture_output=True)

    def write_payload(self, name, body):
        payload = self.root / name
        payload.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
        return str(payload)

    def figures(self, item):
        return [(row["received"], row["returned"], row["net_received"], row["remaining"]) for row in item["rows"]]

    def test_default_list_order_and_structure_matches_progress(self):
        items = self.app.purchase_orders()
        self.assertEqual([item["reference"] for item in items], ["PO-A", "PO-B"])
        for item, reference in zip(items, ("PO-A", "PO-B")):
            self.assertEqual(item, self.app.purchase_progress(reference))
            self.assertEqual(list(item), ["reference", "supplier", "status", "rows", "progress"])
            self.assertEqual(item["rows"][0]["code"], "PAPER")
            self.assertEqual([item["rows"][0]["name"], item["rows"][0]["unit"]], ["包装纸", "张"])
        self.assertEqual(items[0]["status"], "cancelled")
        self.assertEqual(items[0]["progress"], "complete")
        self.assertEqual(self.figures(items[0]), [(2, 2, 0, 0)])
        self.assertEqual(items[1]["status"], "open")
        self.assertEqual(items[1]["progress"], "partial")
        self.assertEqual(self.figures(items[1]), [(6, 2, 4, 4)])

    def test_combined_filters_return_only_po_b(self):
        items = self.app.purchase_orders(supplier="北", status="open", progress="partial")
        self.assertEqual([item["reference"] for item in items], ["PO-B"])
        self.assertEqual(self.figures(items[0]), [(6, 2, 4, 4)])

    def test_filters_are_independent_and_substring_is_case_sensitive(self):
        self.assertEqual([i["reference"] for i in self.app.purchase_orders(status="cancelled")], ["PO-A"])
        self.assertEqual([i["reference"] for i in self.app.purchase_orders(status="open")], ["PO-B"])
        # Fully returned but fully received stays complete, independently of cancelled status.
        self.assertEqual([i["reference"] for i in self.app.purchase_orders(progress="complete")], ["PO-A"])
        self.assertEqual([i["reference"] for i in self.app.purchase_orders(progress="partial")], ["PO-B"])
        self.assertEqual([i["reference"] for i in self.app.purchase_orders(progress="pending")], [])
        self.assertEqual([i["reference"] for i in self.app.purchase_orders(supplier=" 南 ")], ["PO-A"])
        self.assertEqual(self.app.purchase_orders(supplier="po"), [])
        self.assertEqual(len(self.app.purchase_orders(supplier="方")), 2)
        self.assertEqual(self.app.purchase_orders(supplier="北", progress="complete"), [])

    def test_results_survive_reopen(self):
        reopened = StockRoom(self.root)
        self.assertEqual([i["reference"] for i in reopened.purchase_orders()], ["PO-A", "PO-B"])
        items = reopened.purchase_orders(supplier="北", status="open", progress="partial")
        self.assertEqual([i["reference"] for i in items], ["PO-B"])
        self.assertEqual(self.figures(items[0]), [(6, 2, 4, 4)])

    def test_empty_directory_returns_empty_list_and_creates_no_file(self):
        empty = self.root / "empty"
        app = StockRoom(empty)
        self.assertEqual(app.purchase_orders(), [])
        self.assertFalse((empty / "data.json").exists())

    def test_legacy_data_without_purchases_behaves_as_empty(self):
        legacy = self.root / "legacy"
        legacy.mkdir()
        (legacy / "data.json").write_text(json.dumps({"materials": {}}, ensure_ascii=False), encoding="utf-8")
        self.assertEqual(StockRoom(legacy).purchase_orders(), [])

    def test_validation_runs_without_data_and_preserves_bytes(self):
        empty = self.root / "empty"
        app = StockRoom(empty)
        for kwargs in (
            {"supplier": None}, {"supplier": 123}, {"supplier": []},
            {"status": "OPEN"}, {"status": "closed"}, {"status": True}, {"status": 0},
            {"progress": "done"}, {"progress": "Pending"}, {"progress": True}, {"progress": 1},
        ):
            with self.assertRaises(ValueError):
                app.purchase_orders(**kwargs)
        self.assertFalse((empty / "data.json").exists())
        before = self.app.path.read_bytes()
        for kwargs in ({"supplier": None}, {"status": "bad"}, {"progress": "bad"}):
            with self.assertRaises(ValueError):
                self.app.purchase_orders(**kwargs)
        self.assertEqual(self.app.path.read_bytes(), before)

    def test_query_is_read_only(self):
        before = self.app.path.read_bytes()
        self.app.purchase_orders(supplier="北", status="open", progress="partial")
        self.app.purchase_orders()
        self.assertEqual(self.app.path.read_bytes(), before)

    def test_unicode_codepoint_ordering(self):
        self.app.create_purchase("PO-10", "北方", [{"code": "PAPER", "quantity": 1}])
        self.assertEqual(
            [i["reference"] for i in self.app.purchase_orders()],
            ["PO-10", "PO-A", "PO-B"],
        )

    def test_cli_object_array_omitted_file_and_errors(self):
        result = self.run_cli("purchase-orders")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([i["reference"] for i in json.loads(result.stdout)], ["PO-A", "PO-B"])
        payload = self.write_payload("filter.json", {"supplier": " 北 ", "status": "open", "progress": "partial"})
        result = self.run_cli("purchase-orders", payload)
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual([i["reference"] for i in value], ["PO-B"])
        self.assertEqual(self.figures(value[0]), [(6, 2, 4, 4)])
        array_payload = self.write_payload("array.json", [{}, {"supplier": "北", "status": "open", "progress": "partial"}])
        result = self.run_cli("purchase-orders", array_payload)
        self.assertEqual(result.returncode, 0, result.stderr)
        lists = json.loads(result.stdout)
        self.assertEqual([[i["reference"] for i in lists[0]], [i["reference"] for i in lists[1]]],
                         [["PO-A", "PO-B"], ["PO-B"]])
        bad = self.write_payload("bad.json", {"status": "closed"})
        before = self.app.path.read_bytes()
        failed = self.run_cli("purchase-orders", bad)
        self.assertEqual(failed.returncode, 2)
        self.assertIn("error", json.loads(failed.stderr))
        self.assertEqual(self.app.path.read_bytes(), before)
        # Empty root: success prints [] and no data file is created.
        empty = self.root / "cli-empty"
        empty_result = subprocess.run(
            [sys.executable, "-m", "stock_room", "--root", str(empty), "purchase-orders"],
            text=True, capture_output=True)
        self.assertEqual(empty_result.returncode, 0, empty_result.stderr)
        self.assertEqual(json.loads(empty_result.stdout), [])
        self.assertFalse((empty / "data.json").exists())


if __name__ == "__main__":
    unittest.main()


class MovementLedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = StockRoom(self.root)
        self.app.register("A", "物料A", "个")

    def seed_fixed_sample(self):
        self.app.movement("A", 10, "M1")
        self.app.movement("A", -2, "M2")
        self.app.reverse("M2", "R1")
        self.app.create_purchase("P1", "S", [{"code": "A", "quantity": 4}])
        self.app.receive_purchase("P1", [{"code": "A", "quantity": 4, "reference": "IN1"}])
        self.app.count("A", 12, "C1")
        self.app.return_purchase("IN1", 1, "RET1")

    def test_fixed_sample_full_ledger_survives_reopen(self):
        self.seed_fixed_sample()
        expected = [
            {"code": "A", "quantity": 10, "reference": "M1", "before": 0, "balance": 10,
             "kind": "movement", "purchase_reference": None, "related_reference": None},
            {"code": "A", "quantity": -2, "reference": "M2", "before": 10, "balance": 8,
             "kind": "movement", "purchase_reference": None, "related_reference": None},
            {"code": "A", "quantity": 2, "reference": "R1", "before": 8, "balance": 10,
             "kind": "reversal", "purchase_reference": None, "related_reference": "M2"},
            {"code": "A", "quantity": 4, "reference": "IN1", "before": 10, "balance": 14,
             "kind": "purchase_receipt", "purchase_reference": "P1", "related_reference": None},
            {"code": "A", "quantity": -2, "reference": "C1", "before": 14, "balance": 12,
             "kind": "count", "purchase_reference": None, "related_reference": None},
            {"code": "A", "quantity": -1, "reference": "RET1", "before": 12, "balance": 11,
             "kind": "purchase_return", "purchase_reference": "P1", "related_reference": "IN1"},
        ]
        self.assertEqual(self.app.movement_ledger("A"), expected)
        self.assertEqual(StockRoom(self.root).movement_ledger("A"), expected)

    def test_kind_filter_keeps_before_and_balance_from_full_stream(self):
        self.seed_fixed_sample()
        rows = self.app.movement_ledger("A", kind="purchase_return")
        self.assertEqual(rows, [
            {"code": "A", "quantity": -1, "reference": "RET1", "before": 12, "balance": 11,
             "kind": "purchase_return", "purchase_reference": "P1", "related_reference": "IN1"},
        ])
        self.assertEqual([row["reference"] for row in self.app.movement_ledger("A", "movement")], ["M1", "M2"])
        self.assertEqual([row["reference"] for row in self.app.movement_ledger("A", "reversal")], ["R1"])
        self.assertEqual([row["reference"] for row in self.app.movement_ledger("A", "purchase_receipt")], ["IN1"])
        self.assertEqual([row["reference"] for row in self.app.movement_ledger("A", "count")], ["C1"])
        self.assertEqual(self.app.movement_ledger("A"), self.app.movement_ledger("A", kind=None))

    def test_empty_for_registered_material_without_movements(self):
        self.assertEqual(self.app.movement_ledger("A"), [])
        self.assertEqual(self.app.movement_ledger("A", "movement"), [])

    def test_inactive_material_still_queryable(self):
        self.seed_fixed_sample()
        expected = self.app.movement_ledger("A")
        self.app.set_active("A", False)
        self.assertEqual(self.app.movement_ledger("A"), expected)

    def test_zero_difference_count_does_not_appear(self):
        self.app.movement("A", 5, "IN-1")
        self.app.count("A", 5, "CNT-ZERO")
        rows = self.app.movement_ledger("A")
        self.assertEqual([row["reference"] for row in rows], ["IN-1"])
        self.assertEqual(self.app.movement_ledger("A", "count"), [])

    def test_reversed_original_stays_and_legacy_rows_are_movements(self):
        self.app.movement("A", 3, "IN-1")
        self.app.reverse("IN-1", "REV-1")
        rows = self.app.movement_ledger("A")
        self.assertEqual([row["kind"] for row in rows], ["movement", "reversal"])
        self.assertEqual(rows[0]["related_reference"], None)
        self.assertEqual(rows[1]["related_reference"], "IN-1")

    def test_code_stripped_and_case_sensitive(self):
        self.app.movement("A", 2, "IN-1")
        self.assertEqual(len(self.app.movement_ledger("  A  ")), 1)
        with self.assertRaises(ValueError):
            self.app.movement_ledger("a")

    def test_invalid_arguments_rejected(self):
        for code in ("", "   ", 11, None, True, ["A"], "UNKNOWN"):
            with self.subTest(code=code):
                with self.assertRaises(ValueError):
                    self.app.movement_ledger(code)
        for kind in ("Movement", "movement ", "", 1, True, ["movement"], {"k": "count"}):
            with self.subTest(kind=kind):
                with self.assertRaises(ValueError):
                    self.app.movement_ledger("A", kind)

    def test_query_success_and_failure_preserve_file_bytes(self):
        self.seed_fixed_sample()
        before = self.app.path.read_bytes()
        self.app.movement_ledger("A")
        self.app.movement_ledger("A", "purchase_return")
        with self.assertRaises(ValueError):
            self.app.movement_ledger("UNKNOWN")
        with self.assertRaises(ValueError):
            self.app.movement_ledger("A", "bad")
        self.assertEqual(before, self.app.path.read_bytes())
        self.assertEqual(self.app.stock("A")["quantity"], 11)
        self.assertEqual(len(self.app.history("A")), 6)

    def test_failed_query_on_empty_directory_creates_no_file(self):
        empty = Path(self.temp.name) / "empty"
        app = StockRoom(empty)
        with self.assertRaises(ValueError):
            app.movement_ledger("A")
        with self.assertRaises(ValueError):
            app.movement_ledger("A", "bad")
        self.assertFalse((empty / "data.json").exists())

    def run_cli(self, *args):
        return subprocess.run([sys.executable, "-m", "stock_room", "--root", str(self.root), *args], text=True, capture_output=True)

    def test_cli_movement_ledger_and_kind_filter(self):
        self.seed_fixed_sample()
        payload = self.root / "query.json"
        payload.write_text(json.dumps({"code": "A"}), encoding="utf-8")
        result = self.run_cli("movement-ledger", str(payload))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([row["balance"] for row in json.loads(result.stdout)], [10, 8, 10, 14, 12, 11])
        payload.write_text(json.dumps({"code": "A", "kind": "purchase_return"}), encoding="utf-8")
        result = self.run_cli("movement-ledger", str(payload))
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = json.loads(result.stdout)
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["before"], rows[0]["balance"]), (12, 11))

    def test_cli_movement_ledger_array_and_invalid_input(self):
        self.seed_fixed_sample()
        payload = self.root / "queries.json"
        payload.write_text(json.dumps([{"code": "A", "kind": "count"}, {"code": "A", "kind": "reversal"}]), encoding="utf-8")
        result = self.run_cli("movement-ledger", str(payload))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([row[0]["reference"] for row in json.loads(result.stdout)], ["C1", "R1"])
        before = self.app.path.read_bytes()
        for bad in ({"code": "A", "kind": "bad"}, {"code": "UNKNOWN"}, {"code": "  "}):
            with self.subTest(bad=bad):
                payload.write_text(json.dumps(bad), encoding="utf-8")
                failed = self.run_cli("movement-ledger", str(payload))
                self.assertEqual(failed.returncode, 2)
                self.assertIn("error", failed.stderr)
        self.assertEqual(before, self.app.path.read_bytes())
