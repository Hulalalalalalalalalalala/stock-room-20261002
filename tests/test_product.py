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

    def test_fixed_sample_unfiltered_sorted_by_code(self):
        self.assertEqual(self.app.inventory(), [
            {"code": "BOX", "name": "纸箱", "unit": "个", "quantity": 0, "minimum": 3, "active": False},
            {"code": "PAPER", "name": "包装纸", "unit": "张", "quantity": 14, "minimum": 15, "active": True},
        ])

    def test_fixed_sample_keyword_and_active_filters(self):
        self.assertEqual([item["code"] for item in self.app.inventory(keyword="纸")], ["BOX", "PAPER"])
        self.assertEqual(self.app.inventory(keyword="纸", active=True), [
            {"code": "PAPER", "name": "包装纸", "unit": "张", "quantity": 14, "minimum": 15, "active": True},
        ])
        self.assertEqual(self.app.inventory(keyword="paper"), [])

    def test_fixed_sample_rename_and_count_survive_reopen(self):
        self.app.update_material("PAPER", "加厚包装纸", "张")
        self.app.count("PAPER", 11, "CNT-001")
        reopened = StockRoom(self.root)
        self.assertEqual(reopened.inventory()[1], {"code": "PAPER", "name": "加厚包装纸", "unit": "张", "quantity": 11, "minimum": 15, "active": True})

    def test_defaults_match_explicit_empty_keyword_and_none_active(self):
        self.assertEqual(self.app.inventory(), self.app.inventory(keyword="", active=None))
        self.assertEqual(self.app.inventory(keyword="   "), self.app.inventory())

    def test_keyword_matches_code_and_name_literally(self):
        self.assertEqual([item["code"] for item in self.app.inventory(keyword="BOX")], ["BOX"])
        self.assertEqual([item["code"] for item in self.app.inventory(keyword="箱")], ["BOX"])
        self.assertEqual(self.app.inventory(keyword="box"), [])
        self.assertEqual(self.app.inventory(keyword=".*"), [])
        self.assertEqual([item["code"] for item in self.app.inventory(keyword=" 纸 ")], ["BOX", "PAPER"])
        self.assertEqual(self.app.inventory(keyword="纸 箱"), [])

    def test_active_false_returns_only_inactive(self):
        self.assertEqual([item["code"] for item in self.app.inventory(active=False)], ["BOX"])

    def test_invalid_arguments_rejected(self):
        for keyword in (11, None, True, ["纸"]):
            with self.subTest(keyword=keyword):
                with self.assertRaises(ValueError):
                    self.app.inventory(keyword=keyword)
        for active in (0, 1, "true", "false", 1.0, [True]):
            with self.subTest(active=active):
                with self.assertRaises(ValueError):
                    self.app.inventory(active=active)

    def test_query_does_not_modify_file_or_consume_reference(self):
        before = self.app.path.read_bytes()
        self.app.inventory()
        self.app.inventory(keyword="纸", active=True)
        with self.assertRaises(ValueError):
            self.app.inventory(keyword=11)
        self.assertEqual(before, self.app.path.read_bytes())
        self.assertEqual([row["reference"] for row in self.app.history("PAPER")], ["IN-001", "OUT-001"])
        self.assertEqual(self.app.counts("PAPER"), [])
        self.assertEqual(self.app.reversals("PAPER"), [])

    def test_empty_directory_returns_empty_without_file(self):
        empty = Path(self.temp.name) / "empty"
        app = StockRoom(empty)
        self.assertEqual(app.inventory(), [])
        self.assertFalse((empty / "data.json").exists())

    def test_unset_minimum_and_legacy_status_defaults(self):
        self.app.register("FILM", "薄膜", "卷")
        self.assertEqual(self.app.inventory(keyword="FILM"), [
            {"code": "FILM", "name": "薄膜", "unit": "卷", "quantity": 0, "minimum": 0, "active": True},
        ])

    def test_cli_inventory_without_input_and_with_filters(self):
        result = subprocess.run([sys.executable, "-m", "stock_room", "--root", str(self.root), "inventory"], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([row["code"] for row in json.loads(result.stdout)], ["BOX", "PAPER"])
        payload = self.root / "query.json"
        payload.write_text(json.dumps({"keyword": "纸", "active": True}), encoding="utf-8")
        result = subprocess.run([sys.executable, "-m", "stock_room", "--root", str(self.root), "inventory", str(payload)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([row["code"] for row in json.loads(result.stdout)], ["PAPER"])
        payload.write_text(json.dumps({"keyword": "纸", "active": None}), encoding="utf-8")
        result = subprocess.run([sys.executable, "-m", "stock_room", "--root", str(self.root), "inventory", str(payload)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([row["code"] for row in json.loads(result.stdout)], ["BOX", "PAPER"])

    def test_cli_inventory_array_returns_results_in_order(self):
        payload = self.root / "queries.json"
        payload.write_text(json.dumps([
            {"keyword": "纸", "active": True},
            {"keyword": "paper"},
            {},
        ]), encoding="utf-8")
        result = subprocess.run([sys.executable, "-m", "stock_room", "--root", str(self.root), "inventory", str(payload)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual([[row["code"] for row in rows] for rows in value], [["PAPER"], [], ["BOX", "PAPER"]])

    def test_cli_inventory_invalid_arguments_return_2_and_preserve_file(self):
        before = self.app.path.read_bytes()
        for payload_data in ({"keyword": 11}, {"active": "true"}, {"active": 1}):
            with self.subTest(payload_data=payload_data):
                payload = self.root / "bad-query.json"
                payload.write_text(json.dumps(payload_data), encoding="utf-8")
                result = subprocess.run([sys.executable, "-m", "stock_room", "--root", str(self.root), "inventory", str(payload)], text=True, capture_output=True)
                self.assertEqual(result.returncode, 2)
                self.assertIn("error", result.stderr)
        self.assertEqual(before, self.app.path.read_bytes())


if __name__ == "__main__":
    unittest.main()
