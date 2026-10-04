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


class ReverseBatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = StockRoom(self.root)
        self.app.register("PAPER", "包装纸", "张")
        self.app.register("BOX", "纸箱", "个")
        self.app.movement("PAPER", 20, "IN-001")
        self.app.movement("PAPER", -6, "OUT-001")
        self.app.movement("BOX", 3, "BIN")

    def test_batch_reverses_in_order_with_running_balances(self):
        records = self.app.reverse_batch([
            {"original_reference": "OUT-001", "reference": "REV-OUT"},
            {"original_reference": "IN-001", "reference": "REV-IN"},
        ])
        self.assertEqual(records, [
            {"code": "PAPER", "original_reference": "OUT-001", "reference": "REV-OUT", "quantity": 6, "balance": 20},
            {"code": "PAPER", "original_reference": "IN-001", "reference": "REV-IN", "quantity": -20, "balance": 0},
        ])
        self.assertEqual(self.app.stock("PAPER")["quantity"], 0)
        self.assertEqual([row["quantity"] for row in self.app.history("PAPER")], [20, -6, 6, -20])
        ledger = self.app.movement_ledger("PAPER", kind="reversal")
        self.assertEqual([(row["reference"], row["related_reference"]) for row in ledger], [("REV-OUT", "OUT-001"), ("REV-IN", "IN-001")])
        reopened = StockRoom(self.root)
        self.assertEqual(reopened.stock("PAPER")["quantity"], 0)
        self.assertEqual([row["reference"] for row in reopened.reversals("PAPER")], ["REV-OUT", "REV-IN"])

    def test_batch_spans_materials_and_rejected_row_aborts_everything(self):
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.reverse_batch([
                {"original_reference": "BIN", "reference": "REV-BIN"},
                {"original_reference": "IN-001", "reference": "REV-IN"},
            ])
        self.assertEqual(before, self.app.path.read_bytes())
        self.assertEqual(self.app.stock("BOX")["quantity"], 3)
        self.assertEqual(self.app.stock("PAPER")["quantity"], 14)
        self.assertEqual(self.app.reversals("BOX"), [])
        self.assertEqual(self.app.reversals("PAPER"), [])
        records = self.app.reverse_batch([
            {"original_reference": "OUT-001", "reference": "REV-OUT"},
            {"original_reference": "IN-001", "reference": "REV-IN"},
            {"original_reference": "BIN", "reference": "REV-BIN"},
        ])
        self.assertEqual([row["balance"] for row in records], [20, 0, 0])
        self.assertEqual(self.app.stock("BOX")["quantity"], 0)

    def test_swapped_order_fails_and_leaves_stock_at_14(self):
        with self.assertRaises(ValueError):
            self.app.reverse_batch([
                {"original_reference": "IN-001", "reference": "REV-IN"},
                {"original_reference": "OUT-001", "reference": "REV-OUT"},
            ])
        self.assertEqual(self.app.stock("PAPER")["quantity"], 14)
        self.assertEqual(len(self.app.reversals("PAPER")), 0)
        # Failed request's references are reusable.
        record = self.app.reverse_batch([{"original_reference": "OUT-001", "reference": "REV-IN"}])
        self.assertEqual(record[0]["reference"], "REV-IN")

    def test_batch_validates_rows_and_identifiers(self):
        for rows in (None, [], "rows", 1, [None], ["x"], [{}]):
            with self.subTest(rows=rows):
                with self.assertRaises(ValueError):
                    self.app.reverse_batch(rows)
        for value in ("", "   ", 7, None):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    self.app.reverse_batch([{"original_reference": value, "reference": "R"}])
                with self.assertRaises(ValueError):
                    self.app.reverse_batch([{"original_reference": "OUT-001", "reference": value}])
        with self.assertRaises(ValueError):
            self.app.reverse_batch([{"original_reference": "OUT-001"}])
        with self.assertRaises(ValueError):
            self.app.reverse_batch([{"original_reference": "OUT-001", "reference": "R", "extra": 1}])
        record = self.app.reverse_batch([{"original_reference": "  OUT-001  ", "reference": "  REV OUT  "}])
        self.assertEqual((record[0]["original_reference"], record[0]["reference"]), ("OUT-001", "REV OUT"))
        with self.assertRaises(ValueError):
            self.app.reverse_batch([{"original_reference": "out-001", "reference": "LOWER"}])
        with self.assertRaises(ValueError):
            self.app.reverse_batch([{"original_reference": "IN-001", "reference": "REV OUT"}])

    def test_batch_rejects_invalid_originals_and_duplicates(self):
        self.app.count("PAPER", 14, "CNT-ZERO")
        self.app.count("PAPER", 10, "CNT-001")
        cases = [
            [{"original_reference": "MISSING", "reference": "R1"}],
            [{"original_reference": "CNT-ZERO", "reference": "R1"}],
            [{"original_reference": "CNT-001", "reference": "R1"}],
            [
                {"original_reference": "OUT-001", "reference": "REV-OUT"},
                {"original_reference": "REV-OUT", "reference": "R2"},
            ],
            [
                {"original_reference": "OUT-001", "reference": "A"},
                {"original_reference": "OUT-001", "reference": "B"},
            ],
            [{"original_reference": "IN-001", "reference": "IN-001"}],
            [{"original_reference": "IN-001", "reference": "CNT-ZERO"}],
            [
                {"original_reference": "IN-001", "reference": "DUP"},
                {"original_reference": "BIN", "reference": "DUP"},
            ],
        ]
        for rows in cases:
            with self.subTest(rows=rows):
                with self.assertRaises(ValueError):
                    self.app.reverse_batch(rows)
        self.app.reverse("OUT-001", "REV-OUT")
        with self.assertRaises(ValueError):
            self.app.reverse_batch([{"original_reference": "OUT-001", "reference": "R3"}])
        with self.assertRaises(ValueError):
            self.app.reverse_batch([{"original_reference": "REV-OUT", "reference": "R4"}])
        self.assertEqual([row["reference"] for row in self.app.reversals("PAPER")], ["REV-OUT"])

    def test_batch_rejects_purchase_receipt_and_return_originals(self):
        self.app.register("TAPE", "胶带", "卷")
        self.app.create_purchase("PO-1", "supplier", [{"code": "TAPE", "quantity": 5}])
        self.app.receive_purchase("PO-1", [{"code": "TAPE", "quantity": 5, "reference": "RCV-1"}])
        self.app.return_purchase("RCV-1", 2, "RET-1")
        with self.assertRaises(ValueError):
            self.app.reverse_batch([{"original_reference": "RCV-1", "reference": "RR"}])
        with self.assertRaises(ValueError):
            self.app.reverse_batch([{"original_reference": "RET-1", "reference": "RR2"}])
        # Purchase references keep their own namespace.
        self.app.create_purchase("RCV-1", "supplier", [{"code": "TAPE", "quantity": 1}])

    def test_batch_allows_inactive_material(self):
        self.app.set_active("PAPER", False)
        record = self.app.reverse_batch([{"original_reference": "OUT-001", "reference": "REV-OUT"}])
        self.assertEqual(record[0]["balance"], 20)
        self.assertEqual(self.app.material_status("PAPER")["active"], False)

    def test_batch_failure_creates_no_file_and_reuses_references(self):
        empty_root = Path(self.temp.name) / "empty"
        app = StockRoom(empty_root)
        with self.assertRaises(ValueError):
            app.reverse_batch([{"original_reference": "X", "reference": "Y"}])
        self.assertFalse((empty_root / "data.json").exists())

    def test_batch_reversed_originals_cannot_be_reversed_singly_afterwards(self):
        self.app.reverse_batch([
            {"original_reference": "OUT-001", "reference": "REV-OUT"},
            {"original_reference": "IN-001", "reference": "REV-IN"},
        ])
        with self.assertRaises(ValueError):
            self.app.reverse("OUT-001", "REV-OUT-2")
        with self.assertRaises(ValueError):
            self.app.reverse("IN-001", "REV-IN-2")

    def test_cli_reverse_batch_success_failure_and_independent_batches(self):
        payload = self.root / "reverse-batch.json"
        payload.write_text(json.dumps({"rows": [
            {"original_reference": "OUT-001", "reference": "REV-OUT"},
            {"original_reference": "IN-001", "reference": "REV-IN"},
        ]}), encoding="utf-8")
        result = subprocess.run([sys.executable, "-m", "stock_room", "--root", str(self.root), "reverse-batch", str(payload)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([row["balance"] for row in json.loads(result.stdout)], [20, 0])
        payload.write_text(json.dumps({"rows": [{"original_reference": "OUT-001", "reference": "AGAIN"}]}), encoding="utf-8")
        result = subprocess.run([sys.executable, "-m", "stock_room", "--root", str(self.root), "reverse-batch", str(payload)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("error", result.stderr)
        payload.write_text(json.dumps([
            {"rows": [{"original_reference": "BIN", "reference": "REV-BIN"}]},
            {"rows": [{"original_reference": "OUT-001", "reference": "REV-AGAIN"}]},
        ]), encoding="utf-8")
        result = subprocess.run([sys.executable, "-m", "stock_room", "--root", str(self.root), "reverse-batch", str(payload)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.app.stock("BOX")["quantity"], 0)
        self.assertEqual([row["reference"] for row in self.app.reversals("PAPER")], ["REV-OUT", "REV-IN"])


class PreviewReversalsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = StockRoom(self.root)
        self.app.register("PAPER", "包装纸", "张")
        self.app.register("BOX", "纸箱", "个")
        self.app.movement("PAPER", 20, "IN-001")
        self.app.movement("PAPER", -6, "OUT-001")
        self.app.movement("BOX", 3, "BIN")

    def run_cli(self, *args):
        return subprocess.run([sys.executable, "-m", "stock_room", "--root", str(self.root), *args],
                              text=True, capture_output=True)

    def write_payload(self, name, body):
        payload = self.root / name
        payload.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
        return str(payload)

    def rows(self):
        return [
            {"original_reference": "OUT-001", "reference": "REV-OUT"},
            {"original_reference": "IN-001", "reference": "REV-IN"},
        ]

    def test_preview_fields_order_and_running_balances(self):
        result = self.app.preview_reversals(self.rows())
        self.assertEqual(result, [
            {"code": "PAPER", "original_reference": "OUT-001", "reference": "REV-OUT",
             "quantity": 6, "before": 14, "balance": 20},
            {"code": "PAPER", "original_reference": "IN-001", "reference": "REV-IN",
             "quantity": -20, "before": 20, "balance": 0},
        ])
        self.assertEqual([set(row) for row in result], [
            {"code", "original_reference", "reference", "quantity", "before", "balance"},
            {"code", "original_reference", "reference", "quantity", "before", "balance"},
        ])
        self.assertEqual(len(result), len(self.rows()))
        # Preview leaves stock and history untouched.
        self.assertEqual(self.app.stock("PAPER")["quantity"], 14)
        self.assertEqual([(row["quantity"], row["reference"]) for row in self.app.history("PAPER")],
                         [(20, "IN-001"), (-6, "OUT-001")])

    def test_preview_spans_materials(self):
        result = self.app.preview_reversals([
            {"original_reference": "BIN", "reference": "REV-BIN"},
            {"original_reference": "OUT-001", "reference": "REV-OUT"},
        ])
        self.assertEqual([(row["code"], row["before"], row["balance"]) for row in result],
                         [("BOX", 3, 0), ("PAPER", 14, 20)])
        self.assertEqual(self.app.stock("BOX")["quantity"], 3)
        self.assertEqual(self.app.stock("PAPER")["quantity"], 14)

    def test_preview_allows_inactive_material(self):
        self.app.set_active("PAPER", False)
        result = self.app.preview_reversals(self.rows())
        self.assertEqual([row["balance"] for row in result], [20, 0])
        self.assertEqual(self.app.material_status("PAPER")["active"], False)

    def test_swapped_order_raises_and_returns_nothing(self):
        with self.assertRaises(ValueError):
            self.app.preview_reversals([
                {"original_reference": "IN-001", "reference": "REV-IN"},
                {"original_reference": "OUT-001", "reference": "REV-OUT"},
            ])
        self.assertEqual(self.app.stock("PAPER")["quantity"], 14)
        self.assertEqual(self.app.reversals("PAPER"), [])
        # Failed request's references are reusable.
        result = self.app.preview_reversals([{"original_reference": "OUT-001", "reference": "REV-IN"}])
        self.assertEqual((result[0]["before"], result[0]["balance"]), (14, 20))

    def test_preview_changes_nothing_keeps_input_and_reopens(self):
        before = self.app.path.read_bytes()
        rows = [
            {"original_reference": "  OUT-001  ", "reference": "  REV OUT  "},
            {"original_reference": "IN-001", "reference": "REV-IN"},
        ]
        snapshot = json.loads(json.dumps(rows))
        result = self.app.preview_reversals(rows)
        self.assertEqual(rows, snapshot)
        self.assertEqual((result[0]["original_reference"], result[0]["reference"]), ("OUT-001", "REV OUT"))
        self.assertEqual(self.app.path.read_bytes(), before)
        self.assertEqual(self.app.stock("PAPER")["quantity"], 14)
        self.assertEqual(self.app.stock("BOX")["quantity"], 3)
        self.assertEqual(self.app.reversals("PAPER"), [])
        self.assertEqual([row["reference"] for row in self.app.history("PAPER")], ["IN-001", "OUT-001"])
        reopened = StockRoom(self.root)
        self.assertEqual(reopened.stock("PAPER")["quantity"], 14)
        self.assertEqual(reopened.reversals("PAPER"), [])
        self.assertEqual([row["reference"] for row in reopened.history("PAPER")], ["IN-001", "OUT-001"])

    def test_failed_preview_does_not_create_missing_file(self):
        empty = Path(self.temp.name) / "empty"
        app = StockRoom(empty)
        for rows in ([], None, "rows", 1, [None], ["x"], [{}],
                     [{"original_reference": "X", "reference": "Y"}]):
            with self.subTest(rows=rows):
                with self.assertRaises(ValueError):
                    app.preview_reversals(rows)
        self.assertFalse((empty / "data.json").exists())

    def test_repeated_preview_is_identical(self):
        first = self.app.preview_reversals(self.rows())
        second = self.app.preview_reversals(json.loads(json.dumps(self.rows())))
        self.assertEqual(first, second)
        self.assertEqual(self.app.stock("PAPER")["quantity"], 14)

    def test_preview_then_commit_matches_without_before(self):
        preview = self.app.preview_reversals(self.rows())
        committed = self.app.reverse_batch(self.rows())
        self.assertEqual(len(preview), len(committed))
        for previewed, actual in zip(preview, committed):
            self.assertEqual(set(actual), {"code", "original_reference", "reference", "quantity", "balance"})
            self.assertEqual({key: value for key, value in previewed.items() if key != "before"}, actual)
        reopened = StockRoom(self.root)
        self.assertEqual(reopened.stock("PAPER")["quantity"], 0)
        self.assertEqual([row["reference"] for row in reopened.reversals("PAPER")], ["REV-OUT", "REV-IN"])
        self.assertEqual([(row["quantity"], row["reference"]) for row in reopened.history("PAPER")],
                         [(20, "IN-001"), (-6, "OUT-001"), (6, "REV-OUT"), (-20, "REV-IN")])

    def test_business_change_between_preview_and_commit_uses_latest_ledger(self):
        preview = self.app.preview_reversals(self.rows())
        self.assertEqual([(row["before"], row["balance"]) for row in preview], [(14, 20), (20, 0)])
        self.app.movement("PAPER", 2, "IN-002")
        committed = self.app.reverse_batch(self.rows())
        self.assertEqual([(row["balance"]) for row in committed], [22, 2])
        self.assertEqual(self.app.stock("PAPER")["quantity"], 2)

    def test_identifiers_stripped_internal_whitespace_kept_and_case_sensitive(self):
        result = self.app.preview_reversals([
            {"original_reference": "  OUT-001  ", "reference": "  REV A  "},
        ])
        self.assertEqual((result[0]["original_reference"], result[0]["reference"]), ("OUT-001", "REV A"))
        with self.assertRaises(ValueError):
            self.app.preview_reversals([{"original_reference": "out-001", "reference": "LOWER"}])
        # A committed reference is matched exactly and case-sensitively.
        self.app.reverse("OUT-001", "REV OUT")
        with self.assertRaises(ValueError):
            self.app.preview_reversals([{"original_reference": "IN-001", "reference": "REV OUT"}])
        different_case = self.app.preview_reversals([
            {"original_reference": "IN-001", "reference": "rev out"},
        ])
        self.assertEqual(different_case[0]["reference"], "rev out")

    def test_invalid_rows_shape_and_values_rejected(self):
        bad_rows = [
            [], None, "rows", 5, ["not-an-object"], [None], [{}],
            [{"original_reference": "OUT-001"}],
            [{"reference": "REV-OUT"}],
            [{"original_reference": "OUT-001", "reference": "REV-OUT", "extra": 1}],
        ]
        for rows in bad_rows:
            with self.subTest(rows=rows):
                with self.assertRaises(ValueError):
                    self.app.preview_reversals(rows)
        for value in ("", "   ", 7, None):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    self.app.preview_reversals([{"original_reference": value, "reference": "R"}])
                with self.assertRaises(ValueError):
                    self.app.preview_reversals([{"original_reference": "OUT-001", "reference": value}])

    def test_invalid_originals_and_duplicates_rejected(self):
        self.app.count("PAPER", 14, "CNT-ZERO")
        self.app.count("PAPER", 10, "CNT-001")
        cases = [
            [{"original_reference": "MISSING", "reference": "R1"}],
            [{"original_reference": "CNT-ZERO", "reference": "R1"}],
            [{"original_reference": "CNT-001", "reference": "R1"}],
            [
                {"original_reference": "OUT-001", "reference": "REV-OUT"},
                {"original_reference": "REV-OUT", "reference": "R2"},
            ],
            [
                {"original_reference": "OUT-001", "reference": "A"},
                {"original_reference": "OUT-001", "reference": "B"},
            ],
            [{"original_reference": "IN-001", "reference": "IN-001"}],
            [{"original_reference": "IN-001", "reference": "CNT-ZERO"}],
            [
                {"original_reference": "IN-001", "reference": "DUP"},
                {"original_reference": "BIN", "reference": "DUP"},
            ],
        ]
        for rows in cases:
            with self.subTest(rows=rows):
                with self.assertRaises(ValueError):
                    self.app.preview_reversals(rows)
        self.app.reverse("OUT-001", "REV-OUT")
        with self.assertRaises(ValueError):
            self.app.preview_reversals([{"original_reference": "OUT-001", "reference": "R3"}])
        with self.assertRaises(ValueError):
            self.app.preview_reversals([{"original_reference": "REV-OUT", "reference": "R4"}])
        self.assertEqual([row["reference"] for row in self.app.reversals("PAPER")], ["REV-OUT"])
        # The count set PAPER to 10 before the single reversal returned +6.
        self.assertEqual(self.app.stock("PAPER")["quantity"], 16)

    def test_purchase_originals_rejected_and_purchase_namespace_independent(self):
        self.app.register("TAPE", "胶带", "卷")
        self.app.create_purchase("PO-1", "supplier", [{"code": "TAPE", "quantity": 5}])
        self.app.receive_purchase("PO-1", [{"code": "TAPE", "quantity": 5, "reference": "RCV-1"}])
        self.app.return_purchase("RCV-1", 2, "RET-1")
        with self.assertRaises(ValueError):
            self.app.preview_reversals([{"original_reference": "RCV-1", "reference": "RR"}])
        with self.assertRaises(ValueError):
            self.app.preview_reversals([{"original_reference": "RET-1", "reference": "RR2"}])
        result = self.app.preview_reversals([{"original_reference": "OUT-001", "reference": "PO-1"}])
        self.assertEqual(result[0]["reference"], "PO-1")

    def test_hypothetical_batch_reversals_cannot_serve_as_originals(self):
        with self.assertRaises(ValueError):
            self.app.preview_reversals([
                {"original_reference": "OUT-001", "reference": "REV-OUT"},
                {"original_reference": "REV-OUT", "reference": "REV-REV"},
            ])
        self.assertEqual(self.app.reversals("PAPER"), [])
        self.assertEqual(self.app.stock("PAPER")["quantity"], 14)

    def test_rejected_preview_keeps_file_bytes(self):
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.preview_reversals([
                {"original_reference": "IN-001", "reference": "REV-IN"},
                {"original_reference": "OUT-001", "reference": "REV-OUT"},
            ])
        self.assertEqual(before, self.app.path.read_bytes())
        self.assertEqual(self.app.stock("PAPER")["quantity"], 14)
        self.assertEqual(self.app.reversals("PAPER"), [])

    def test_legacy_data_without_associations_treats_them_as_absent(self):
        legacy = self.root / "legacy"
        legacy.mkdir()
        (legacy / "data.json").write_text(json.dumps({
            "materials": {"PAPER": {"code": "PAPER", "name": "包装纸", "unit": "张"}},
            "movements": [
                {"code": "PAPER", "quantity": 5, "reference": "PLAIN-IN"},
                {"code": "PAPER", "quantity": 3, "reference": "LOOKS-LIKE-RECEIPT"},
            ],
        }), encoding="utf-8")
        app = StockRoom(legacy)
        result = app.preview_reversals([
            {"original_reference": "PLAIN-IN", "reference": "REV-1"},
            {"original_reference": "LOOKS-LIKE-RECEIPT", "reference": "REV-2"},
        ])
        self.assertEqual([(row["before"], row["quantity"], row["balance"]) for row in result],
                         [(8, -5, 3), (3, -3, 0)])

    def test_cli_preview_success_failure_and_array_independence(self):
        payload = self.write_payload("preview.json", {"rows": self.rows()})
        result = self.run_cli("preview-reversals", payload)
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertEqual([(row["before"], row["balance"]) for row in output], [(14, 20), (20, 0)])
        # Failure: error JSON on stderr, exit code 2, ledger untouched.
        bad = self.write_payload("bad.json", {"rows": [
            {"original_reference": "IN-001", "reference": "REV-IN"},
            {"original_reference": "OUT-001", "reference": "REV-OUT"},
        ]})
        failed = self.run_cli("preview-reversals", bad)
        self.assertEqual(failed.returncode, 2)
        self.assertIn("error", json.loads(failed.stderr))
        self.assertEqual(self.app.stock("PAPER")["quantity"], 14)
        # JSON array: every preview reads the real ledger instead of chaining.
        batch = self.write_payload("previews.json", [
            {"rows": [{"original_reference": "OUT-001", "reference": "REV-OUT"}]},
            {"rows": [{"original_reference": "OUT-001", "reference": "REV-OUT"}]},
        ])
        repeated = self.run_cli("preview-reversals", batch)
        self.assertEqual(repeated.returncode, 0, repeated.stderr)
        groups = json.loads(repeated.stdout)
        self.assertEqual([group[0]["before"] for group in groups], [14, 14])
        self.assertEqual([group[0]["balance"] for group in groups], [20, 20])
        self.assertEqual(self.app.stock("PAPER")["quantity"], 14)
        self.assertEqual(self.app.reversals("PAPER"), [])


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

    def test_fixed_sample_one_adjustment_one_zero_count_survives_reopen(self):
        result = self.app.import_counts_csv("code,counted,reference\nPAPER,12,CNT-P\nBOX,0,CNT-B\n")
        self.assertEqual(result, [
            {"code": "PAPER", "reference": "CNT-P", "before": 14, "counted": 12, "difference": -2},
            {"code": "BOX", "reference": "CNT-B", "before": 0, "counted": 0, "difference": 0},
        ])
        self.assertEqual(self.app.stock("PAPER")["quantity"], 12)
        self.assertEqual(self.app.stock("BOX")["quantity"], 0)
        self.assertEqual([row["reference"] for row in self.app.counts("PAPER")], ["CNT-P"])
        self.assertEqual([row["reference"] for row in self.app.counts("BOX")], ["CNT-B"])
        self.assertEqual([(row["quantity"], row["reference"]) for row in self.app.history("PAPER")], [(20, "IN-001"), (-6, "OUT-001"), (-2, "CNT-P")])
        self.assertEqual(self.app.history("BOX"), [])
        reopened = StockRoom(self.root)
        self.assertEqual([row["difference"] for row in reopened.counts("PAPER")], [-2])
        self.assertEqual([row["difference"] for row in reopened.counts("BOX")], [0])
        self.assertEqual(reopened.stock("PAPER")["quantity"], 12)
        self.assertEqual(reopened.stock("BOX")["quantity"], 0)

    def test_reimport_same_references_rejects_whole_file(self):
        content = "code,counted,reference\nPAPER,12,CNT-P\nBOX,0,CNT-B\n"
        self.assertEqual(len(self.app.import_counts_csv(content)), 2)
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.import_counts_csv(content)
        self.assertEqual(before, self.app.path.read_bytes())
        self.assertEqual(len(self.app.counts("PAPER")), 1)
        self.assertEqual(len(self.app.counts("BOX")), 1)
        self.assertEqual(self.app.stock("PAPER")["quantity"], 12)

    def test_bom_crlf_reordered_header_and_quoted_fields(self):
        content = "﻿reference,code,counted\r\n\"CNT-G\", PAPER ,012\r\n\r\nCNT-B,BOX,0\r\n"
        result = self.app.import_counts_csv(content)
        self.assertEqual([(row["code"], row["counted"], row["reference"], row["difference"]) for row in result], [
            ("PAPER", 12, "CNT-G", -2),
            ("BOX", 0, "CNT-B", 0),
        ])
        self.assertEqual(self.app.stock("PAPER")["quantity"], 12)

    def test_quoted_fields_may_contain_commas_newlines_and_doubled_quotes(self):
        self.app.register('PA"PER', '特殊纸', '张')
        result = self.app.import_counts_csv('code,counted,reference\n"PA""PER",12,"CNT,X"\n')
        self.assertEqual([(row["code"], row["reference"], row["difference"]) for row in result], [('PA"PER', "CNT,X", 12)])
        content = 'code,counted,reference\nPAPER,12,"CNT\r\nLINE"\n'
        self.assertEqual(self.app.import_counts_csv(content)[0]["reference"], "CNT\r\nLINE")

    def test_header_only_returns_empty_without_writing(self):
        empty = Path(self.temp.name) / "empty"
        app = StockRoom(empty)
        self.assertEqual(app.import_counts_csv("code,counted,reference\n"), [])
        self.assertFalse((empty / "data.json").exists())
        before = self.app.path.read_bytes()
        self.assertEqual(self.app.import_counts_csv("reference,code,counted\r\n\r\n"), [])
        self.assertEqual(before, self.app.path.read_bytes())

    def test_invalid_content_and_header_rejected(self):
        for content in ("", "﻿", None, 11, ["code"], {"c": "x"}):
            with self.subTest(content=content):
                with self.assertRaises(ValueError):
                    self.app.import_counts_csv(content)
        for content in (
            "code,counted\n",
            "code,counted,reference,extra\n",
            "code,code,reference\n",
            "code,counted\nPAPER,12\n",
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
            'code,counted,reference\n"unclosed,12,R\n',
            'code,counted,reference\nPAPER,1"2,R\n',
            'code,counted,reference\nPAPER,"1"2,R\n',
            'code,counted,reference\nPAPER,"1" 2,R\n',
            'code,counted,reference\nPAPER, "12",R\n',
        ):
            with self.subTest(content=content):
                with self.assertRaises(ValueError):
                    self.app.import_counts_csv(content)

    def test_wrong_column_count_blank_fields_and_blank_like_records_rejected(self):
        for content in (
            "code,counted,reference\nPAPER,12\n",
            "code,counted,reference\nPAPER,12,R,多\n",
            "code,counted,reference\n,12,R\n",
            "code,counted,reference\nPAPER,,R\n",
            "code,counted,reference\nPAPER,12,\n",
            "code,counted,reference\n   \n",
            "code,counted,reference\n,,\n",
            "code,counted,reference\nPAPER,12,R,\n",
        ):
            with self.subTest(content=content):
                with self.assertRaises(ValueError):
                    self.app.import_counts_csv(content)

    def test_counted_accepts_digits_only_with_zero_and_leading_zeros(self):
        content = "code,counted,reference\nPAPER,007,CNT-007\n"
        record = self.app.import_counts_csv(content)[0]
        self.assertEqual((record["counted"], record["difference"]), (7, -7))
        for counted in ("-1", "+1", "1.0", "1e3", "1 2", "一", "0x1", "1."):
            with self.subTest(counted=counted):
                with self.assertRaises(ValueError):
                    self.app.import_counts_csv("code,counted,reference\nPAPER," + counted + ",CNT-X\n")

    def test_identifiers_are_stripped_internal_whitespace_kept_and_case_sensitive(self):
        result = self.app.import_counts_csv('code,counted,reference\n PAPER ,12," CNT A "\n')
        self.assertEqual(result[0]["code"], "PAPER")
        self.assertEqual(result[0]["reference"], "CNT A")
        with self.assertRaises(ValueError):
            self.app.import_counts_csv("code,counted,reference\npaper,12,CNT-LOWER\n")

    def test_duplicate_codes_and_references_in_file_rejected(self):
        for content in (
            "code,counted,reference\nPAPER,12,R1\nPAPER,11,R2\n",
            "code,counted,reference\nPAPER,12,R1\n PAPER ,11,R2\n",
            "code,counted,reference\nPAPER,12,R1\nBOX,0,R1\n",
            "code,counted,reference\nPAPER,12,R1\nBOX,0, R1 \n",
        ):
            with self.subTest(content=content):
                with self.assertRaises(ValueError):
                    self.app.import_counts_csv(content)

    def test_reference_conflicts_with_existing_movements_counts_and_reversals(self):
        self.app.reverse("OUT-001", "REV-OUT")
        self.app.count("BOX", 1, "CNT-EXIST")
        for content in (
            "code,counted,reference\nPAPER,12,IN-001\n",
            "code,counted,reference\nPAPER,12,CNT-EXIST\n",
            "code,counted,reference\nPAPER,12,REV-OUT\n",
        ):
            with self.subTest(content=content):
                with self.assertRaises(ValueError):
                    self.app.import_counts_csv(content)

    def test_unknown_material_rejects_even_when_later_rows_are_valid(self):
        with self.assertRaises(ValueError):
            self.app.import_counts_csv("code,counted,reference\nPAPER,12,CNT-P\nOTHER,1,CNT-O\n")
        with self.assertRaises(ValueError):
            self.app.import_counts_csv("code,counted,reference\nOTHER,1,CNT-O\nPAPER,12,CNT-P\n")
        self.assertEqual(self.app.counts("PAPER"), [])
        self.assertEqual(self.app.stock("PAPER")["quantity"], 14)

    def test_inactive_material_can_be_counted(self):
        self.app.set_active("BOX", False)
        result = self.app.import_counts_csv("code,counted,reference\nBOX,3,CNT-B\n")
        self.assertEqual((result[0]["before"], result[0]["difference"]), (0, 3))
        self.assertEqual(self.app.stock("BOX")["quantity"], 3)
        self.assertEqual(self.app.material_status("BOX")["active"], False)

    def test_zero_difference_count_occupies_reference(self):
        self.app.import_counts_csv("code,counted,reference\nBOX,0,CNT-ZERO\n")
        with self.assertRaises(ValueError):
            self.app.movement("BOX", 1, "CNT-ZERO")
        with self.assertRaises(ValueError):
            self.app.count("BOX", 0, "CNT-ZERO")

    def test_imported_counts_lock_unit_and_cannot_be_reversed(self):
        self.app.import_counts_csv("code,counted,reference\nBOX,0,CNT-B\nPAPER,12,CNT-P\n")
        with self.assertRaises(ValueError):
            self.app.update_material("BOX", "纸箱", "只")
        with self.assertRaises(ValueError):
            self.app.reverse("CNT-P", "REV-CNT")

    def test_failed_import_preserves_file_and_all_state(self):
        self.app.set_minimum("PAPER", 15)
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.import_counts_csv("code,counted,reference\nPAPER,12,CNT-OK\nPAPER,11,CNT-DUP\n")
        with self.assertRaises(ValueError):
            self.app.import_counts_csv("code,counted,reference\nPAPER,12,IN-001\n")
        with self.assertRaises(ValueError):
            self.app.import_counts_csv("code,counted,reference\nUNKNOWN,1,CNT-U\n")
        with self.assertRaises(ValueError):
            self.app.import_counts_csv(11)
        self.assertEqual(before, self.app.path.read_bytes())
        self.assertEqual(self.app.stock("PAPER")["quantity"], 14)
        self.assertEqual(self.app.counts("PAPER"), [])
        self.assertEqual(self.app.counts("BOX"), [])
        self.assertEqual(len(self.app.history("PAPER")), 2)
        self.assertEqual([item["code"] for item in self.app.shortages()], ["PAPER"])

    def test_failed_import_on_empty_directory_creates_no_file(self):
        empty = Path(self.temp.name) / "empty"
        app = StockRoom(empty)
        with self.assertRaises(ValueError):
            app.import_counts_csv("code,counted,reference\nPAPER,12\n")
        with self.assertRaises(ValueError):
            app.import_counts_csv("")
        with self.assertRaises(ValueError):
            app.import_counts_csv("code,counted,reference\nPAPER,12,R\n")
        self.assertFalse((empty / "data.json").exists())

    def run_cli(self, *args):
        return subprocess.run([sys.executable, "-m", "stock_room", "--root", str(self.root), *args], text=True, capture_output=True)

    def test_cli_import_success_and_failure(self):
        payload = self.root / "import.json"
        payload.write_text(json.dumps({"content": "code,counted,reference\nPAPER,12,CNT-P\nBOX,0,CNT-B\n"}), encoding="utf-8")
        result = self.run_cli("import-counts-csv", str(payload))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([row["difference"] for row in json.loads(result.stdout)], [-2, 0])
        bad = self.root / "bad-import.json"
        bad.write_text(json.dumps({"content": "code,counted,reference\nPAPER,12,CNT-P\n"}), encoding="utf-8")
        failed = self.run_cli("import-counts-csv", str(bad))
        self.assertEqual(failed.returncode, 2)
        self.assertIn("error", json.loads(failed.stderr))
        self.assertEqual(self.app.stock("PAPER")["quantity"], 12)
        self.assertEqual(len(self.app.counts("PAPER")), 1)

    def test_cli_import_array_keeps_earlier_success(self):
        payload = self.root / "imports.json"
        payload.write_text(json.dumps([
            {"content": "code,counted,reference\nBOX,2,CNT-B\n"},
            {"content": "code,counted,reference\nPAPER,12,CNT-B\n"},
        ]), encoding="utf-8")
        result = self.run_cli("import-counts-csv", str(payload))
        self.assertEqual(result.returncode, 2)
        self.assertIn("error", result.stderr)
        self.assertEqual(self.app.stock("BOX")["quantity"], 2)
        self.assertEqual(len(self.app.counts("BOX")), 1)
        self.assertEqual(self.app.stock("PAPER")["quantity"], 14)
        self.assertEqual(self.app.counts("PAPER"), [])


class PreviewCountsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = StockRoom(self.root)
        self.app.register("PAPER", "包装纸", "张")
        self.app.register("BOX", "纸箱", "个")
        self.app.set_minimum("PAPER", 10)
        self.app.set_minimum("BOX", 5)
        self.app.movement("PAPER", 12, "IN-P")
        self.app.movement("BOX", 3, "IN-B")
        self.app.set_active("BOX", False)

    def run_cli(self, *args):
        return subprocess.run([sys.executable, "-m", "stock_room", "--root", str(self.root), *args],
                              text=True, capture_output=True)

    def write_payload(self, name, body):
        payload = self.root / name
        payload.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
        return str(payload)

    def rows(self):
        return [
            {"code": "PAPER", "counted": 8, "reference": "CNT-P"},
            {"code": "BOX", "counted": 3, "reference": "CNT-B"},
        ]

    def test_preview_fields_shortages_order_and_zero_difference_inactive(self):
        result = self.app.preview_counts(self.rows())
        self.assertEqual(result, [
            {"code": "PAPER", "reference": "CNT-P", "before": 12, "counted": 8, "difference": -4,
             "before_shortage": 0, "after_shortage": 2},
            {"code": "BOX", "reference": "CNT-B", "before": 3, "counted": 3, "difference": 0,
             "before_shortage": 2, "after_shortage": 2},
        ])
        self.assertEqual([set(row) for row in result], [
            {"code", "reference", "before", "counted", "difference", "before_shortage", "after_shortage"},
            {"code", "reference", "before", "counted", "difference", "before_shortage", "after_shortage"},
        ])
        self.assertEqual(len(result), len(self.rows()))

    def test_incoming_purchase_does_not_reduce_shortage(self):
        self.app.create_purchase("PO-1", "供应商", [{"code": "PAPER", "quantity": 100}])
        result = self.app.preview_counts(self.rows())
        self.assertEqual((result[0]["before_shortage"], result[0]["after_shortage"]), (0, 2))

    def test_missing_minimum_counts_as_zero(self):
        self.app.register("TAPE", "胶带", "卷")
        self.app.movement("TAPE", 1, "IN-T")
        result = self.app.preview_counts([{"code": "TAPE", "counted": 0, "reference": "CNT-T"}])
        self.assertEqual((result[0]["before_shortage"], result[0]["after_shortage"]), (0, 0))

    def test_preview_changes_nothing_and_keeps_input(self):
        before = self.app.path.read_bytes()
        rows = [
            {"code": "  PAPER  ", "counted": 8, "reference": "  CNT P  "},
            {"code": "BOX", "counted": 3, "reference": "CNT-B"},
        ]
        snapshot = json.loads(json.dumps(rows))
        result = self.app.preview_counts(rows)
        self.assertEqual(rows, snapshot)
        self.assertEqual(result[0]["code"], "PAPER")
        self.assertEqual(result[0]["reference"], "CNT P")
        # Preview changes nothing on disk, in stock or in history.
        self.assertEqual(self.app.path.read_bytes(), before)
        self.assertEqual(self.app.stock("PAPER")["quantity"], 12)
        self.assertEqual(self.app.stock("BOX")["quantity"], 3)
        self.assertEqual(self.app.counts("PAPER"), [])
        self.assertEqual(self.app.counts("BOX"), [])
        self.assertEqual([row["reference"] for row in self.app.history("PAPER")], ["IN-P"])
        self.assertEqual([row["reference"] for row in self.app.history("BOX")], ["IN-B"])
        # Reopening the directory still shows no new records.
        reopened = StockRoom(self.root)
        self.assertEqual(reopened.counts("PAPER"), [])
        self.assertEqual(reopened.counts("BOX"), [])
        self.assertEqual(reopened.stock("PAPER")["quantity"], 12)
        self.assertEqual(reopened.stock("BOX")["quantity"], 3)

    def test_failed_preview_does_not_create_missing_file(self):
        empty = Path(self.temp.name) / "empty"
        app = StockRoom(empty)
        for rows in ([], None, "x", [{"code": "PAPER", "counted": 8, "reference": "CNT-P"}]):
            with self.subTest(rows=rows):
                with self.assertRaises(ValueError):
                    app.preview_counts(rows)
        self.assertFalse((empty / "data.json").exists())

    def test_repeated_preview_is_identical(self):
        first = self.app.preview_counts(self.rows())
        second = self.app.preview_counts(json.loads(json.dumps(self.rows())))
        self.assertEqual(first, second)
        self.assertEqual(self.app.stock("PAPER")["quantity"], 12)

    def test_preview_then_commit_matches_without_extra_fields(self):
        preview = self.app.preview_counts(self.rows())
        committed = self.app.count_batch(self.rows())
        self.assertEqual(len(preview), len(committed))
        for previewed, actual in zip(preview, committed):
            self.assertEqual(set(actual), {"code", "reference", "before", "counted", "difference"})
            self.assertEqual({key: value for key, value in previewed.items()
                              if key not in ("before_shortage", "after_shortage")}, actual)
        reopened = StockRoom(self.root)
        self.assertEqual(reopened.stock("PAPER")["quantity"], 8)
        self.assertEqual(reopened.stock("BOX")["quantity"], 3)
        self.assertEqual([row["reference"] for row in reopened.counts("PAPER")], ["CNT-P"])
        self.assertEqual([row["reference"] for row in reopened.counts("BOX")], ["CNT-B"])
        # Only the nonzero PAPER count appends an adjustment movement.
        self.assertEqual([(row["quantity"], row["reference"]) for row in reopened.history("PAPER")],
                         [(12, "IN-P"), (-4, "CNT-P")])
        self.assertEqual([(row["quantity"], row["reference"]) for row in reopened.history("BOX")],
                         [(3, "IN-B")])

    def test_movement_between_preview_and_commit_uses_latest_ledger(self):
        preview = self.app.preview_counts([{"code": "PAPER", "counted": 8, "reference": "CNT-P"}])
        self.assertEqual((preview[0]["before"], preview[0]["difference"]), (12, -4))
        self.app.movement("PAPER", -2, "OUT-P")
        committed = self.app.count_batch([{"code": "PAPER", "counted": 8, "reference": "CNT-P"}])
        self.assertEqual((committed[0]["before"], committed[0]["difference"]), (10, -2))
        self.assertEqual(self.app.stock("PAPER")["quantity"], 8)

    def test_identifiers_stripped_internal_whitespace_kept_and_case_sensitive(self):
        result = self.app.preview_counts([
            {"code": "  PAPER  ", "counted": 8, "reference": "  CNT A  "},
        ])
        self.assertEqual(result[0]["code"], "PAPER")
        self.assertEqual(result[0]["reference"], "CNT A")
        with self.assertRaises(ValueError):
            self.app.preview_counts([{"code": "paper", "counted": 8, "reference": "CNT-LOWER"}])

    def test_invalid_rows_shape_and_values_rejected(self):
        bad_rows = [
            [],
            None,
            "rows",
            5,
            ["not-an-object"],
            [{"code": "PAPER", "counted": 8}],
            [{"code": "PAPER", "reference": "CNT-P"}],
            [{"counted": 8, "reference": "CNT-P"}],
            [{"code": "PAPER", "counted": 8, "reference": "CNT-P", "extra": 1}],
        ]
        for rows in bad_rows:
            with self.subTest(rows=rows):
                with self.assertRaises(ValueError):
                    self.app.preview_counts(rows)
        for code in (None, 5, "", "   "):
            with self.subTest(code=code):
                with self.assertRaises(ValueError):
                    self.app.preview_counts([{"code": code, "counted": 8, "reference": "CNT-X"}])
        for reference in (None, 5, "", "   "):
            with self.subTest(reference=reference):
                with self.assertRaises(ValueError):
                    self.app.preview_counts([{"code": "PAPER", "counted": 8, "reference": reference}])
        for counted in (-1, True, False, 1.5, "8", None, [8], 8.0):
            with self.subTest(counted=counted):
                with self.assertRaises(ValueError):
                    self.app.preview_counts([{"code": "PAPER", "counted": counted, "reference": "CNT-X"}])

    def test_unknown_material_rejected(self):
        with self.assertRaises(ValueError):
            self.app.preview_counts([{"code": "OTHER", "counted": 1, "reference": "CNT-X"}])

    def test_duplicate_codes_and_references_in_batch_rejected(self):
        with self.assertRaises(ValueError):
            self.app.preview_counts([
                {"code": "PAPER", "counted": 8, "reference": "CNT-1"},
                {"code": " PAPER ", "counted": 7, "reference": "CNT-2"},
            ])
        with self.assertRaises(ValueError):
            self.app.preview_counts([
                {"code": "PAPER", "counted": 8, "reference": "CNT-1"},
                {"code": "BOX", "counted": 3, "reference": " CNT-1 "},
            ])

    def test_reference_conflicts_with_existing_history_rejected(self):
        # Existing movement reference.
        with self.assertRaises(ValueError):
            self.app.preview_counts([{"code": "PAPER", "counted": 8, "reference": "IN-P"}])
        # Existing zero-difference count reference still occupies the namespace.
        self.app.count("PAPER", 12, "CNT-ZERO")
        with self.assertRaises(ValueError):
            self.app.preview_counts([{"code": "BOX", "counted": 3, "reference": "CNT-ZERO"}])
        # Existing reversal reference.
        self.app.movement("PAPER", 1, "IN-REV")
        self.app.reverse("IN-REV", "REV-1")
        with self.assertRaises(ValueError):
            self.app.preview_counts([{"code": "BOX", "counted": 3, "reference": "REV-1"}])

    def test_purchase_reference_stays_independent(self):
        self.app.create_purchase("PO-1", "供应商", [{"code": "PAPER", "quantity": 4}])
        result = self.app.preview_counts([{"code": "PAPER", "counted": 8, "reference": "PO-1"}])
        self.assertEqual(result[0]["reference"], "PO-1")

    def test_rejected_preview_keeps_file_bytes(self):
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.preview_counts([
                {"code": "PAPER", "counted": 8, "reference": "CNT-1"},
                {"code": "OTHER", "counted": 1, "reference": "CNT-2"},
            ])
        self.assertEqual(before, self.app.path.read_bytes())
        self.assertEqual(self.app.stock("PAPER")["quantity"], 12)
        self.assertEqual(self.app.counts("PAPER"), [])

    def test_cli_preview_success_failure_and_array_independence(self):
        payload = self.write_payload("preview.json", {"rows": self.rows()})
        result = self.run_cli("preview-counts", payload)
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertEqual([(row["difference"], row["before_shortage"], row["after_shortage"]) for row in output],
                         [(-4, 0, 2), (0, 2, 2)])
        # Failure: error JSON on stderr, exit code 2, ledger untouched.
        bad = self.write_payload("bad.json", {"rows": [{"code": "PAPER", "counted": -1, "reference": "CNT-X"}]})
        failed = self.run_cli("preview-counts", bad)
        self.assertEqual(failed.returncode, 2)
        self.assertIn("error", json.loads(failed.stderr))
        self.assertEqual(self.app.stock("PAPER")["quantity"], 12)
        # JSON array: every preview reads the real ledger instead of chaining.
        batch = self.write_payload("previews.json", [
            {"rows": [{"code": "PAPER", "counted": 8, "reference": "CNT-P"}]},
            {"rows": [{"code": "PAPER", "counted": 5, "reference": "CNT-P"}]},
        ])
        repeated = self.run_cli("preview-counts", batch)
        self.assertEqual(repeated.returncode, 0, repeated.stderr)
        groups = json.loads(repeated.stdout)
        self.assertEqual([group[0]["before"] for group in groups], [12, 12])
        self.assertEqual([group[0]["after_shortage"] for group in groups], [2, 5])
        self.assertEqual(self.app.stock("PAPER")["quantity"], 12)
        self.assertEqual(self.app.counts("PAPER"), [])


class ConfirmCountsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = StockRoom(self.root)
        self.app.register("PAPER", "包装纸", "张")
        self.app.register("BOX", "纸箱", "个")
        self.app.movement("PAPER", 12, "IN-P")
        self.app.movement("BOX", 3, "IN-B")
        self.app.set_active("BOX", False)

    def run_cli(self, *args):
        return subprocess.run([sys.executable, "-m", "stock_room", "--root", str(self.root), *args],
                              text=True, capture_output=True)

    def write_payload(self, name, body):
        payload = self.root / name
        payload.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
        return str(payload)

    def rows(self):
        return [
            {"code": "PAPER", "counted": 8, "reference": "CNT-P", "expected_before": 12},
            {"code": "BOX", "counted": 3, "reference": "CNT-B", "expected_before": 3},
        ]

    def test_success_fields_order_and_zero_difference_inactive(self):
        rows = self.rows()
        snapshot = json.loads(json.dumps(rows))
        result = self.app.confirm_counts(rows)
        self.assertEqual(result, [
            {"code": "PAPER", "reference": "CNT-P", "before": 12, "counted": 8, "difference": -4},
            {"code": "BOX", "reference": "CNT-B", "before": 3, "counted": 3, "difference": 0},
        ])
        self.assertEqual([set(row) for row in result],
                         [{"code", "reference", "before", "counted", "difference"}] * 2)
        # Input rows are not modified.
        self.assertEqual(rows, snapshot)
        reopened = StockRoom(self.root)
        self.assertEqual(reopened.stock("PAPER")["quantity"], 8)
        self.assertEqual(reopened.stock("BOX")["quantity"], 3)
        self.assertEqual([row["reference"] for row in reopened.counts("PAPER")], ["CNT-P"])
        self.assertEqual([row["reference"] for row in reopened.counts("BOX")], ["CNT-B"])
        # Only the nonzero PAPER count appends an adjustment movement.
        self.assertEqual([(row["quantity"], row["reference"]) for row in reopened.history("PAPER")],
                         [(12, "IN-P"), (-4, "CNT-P")])
        self.assertEqual([(row["quantity"], row["reference"]) for row in reopened.history("BOX")],
                         [(3, "IN-B")])

    def test_movement_before_submit_rejects_whole_batch_then_fixed_expected_succeeds(self):
        self.app.movement("PAPER", 2, "IN-P2")
        before = self.app.path.read_bytes()
        rows = self.rows()
        with self.assertRaises(ValueError):
            self.app.confirm_counts(rows)
        # Whole batch rejected: file bytes, stock and counts untouched.
        self.assertEqual(self.app.path.read_bytes(), before)
        self.assertEqual(self.app.stock("PAPER")["quantity"], 14)
        self.assertEqual(self.app.stock("BOX")["quantity"], 3)
        self.assertEqual(self.app.counts("PAPER"), [])
        self.assertEqual(self.app.counts("BOX"), [])
        # Failed references stay reusable after fixing the expectation.
        rows[0]["expected_before"] = 14
        result = self.app.confirm_counts(rows)
        self.assertEqual([(row["before"], row["difference"]) for row in result], [(14, -6), (3, 0)])
        reopened = StockRoom(self.root)
        self.assertEqual(reopened.stock("PAPER")["quantity"], 8)
        self.assertEqual(reopened.stock("BOX")["quantity"], 3)
        self.assertEqual(len(reopened.counts("PAPER")), 1)
        self.assertEqual(len(reopened.counts("BOX")), 1)
        self.assertEqual([(row["quantity"], row["reference"]) for row in reopened.history("PAPER")],
                         [(12, "IN-P"), (2, "IN-P2"), (-6, "CNT-P")])
        self.assertEqual([(row["quantity"], row["reference"]) for row in reopened.history("BOX")],
                         [(3, "IN-B")])

    def test_quantity_restored_by_movements_still_confirms(self):
        self.app.movement("PAPER", -2, "OUT-P")
        self.app.movement("PAPER", 2, "IN-P2")
        result = self.app.confirm_counts([
            {"code": "PAPER", "counted": 8, "reference": "CNT-P", "expected_before": 12},
        ])
        self.assertEqual((result[0]["before"], result[0]["difference"]), (12, -4))

    def test_profile_location_minimum_and_status_changes_do_not_block(self):
        self.app.update_material("PAPER", "牛皮纸", "张")
        self.app.set_minimum("PAPER", 10)
        self.app.assign_locations([{"code": "PAPER", "location": "A-1"}])
        self.app.set_active("PAPER", False)
        result = self.app.confirm_counts([
            {"code": "PAPER", "counted": 8, "reference": "CNT-P", "expected_before": 12},
        ])
        self.assertEqual(result[0]["difference"], -4)

    def test_expected_before_must_be_nonnegative_integer(self):
        for expected in (-1, True, False, 1.5, 12.0, "12", None, [12]):
            with self.subTest(expected_before=expected):
                with self.assertRaises(ValueError):
                    self.app.confirm_counts([
                        {"code": "PAPER", "counted": 8, "reference": "CNT-X", "expected_before": expected},
                    ])
        with self.assertRaises(ValueError):
            self.app.confirm_counts([{"code": "PAPER", "counted": 8, "reference": "CNT-X"}])

    def test_invalid_rows_shape_and_values_rejected(self):
        bad_rows = [
            [],
            None,
            "rows",
            5,
            ["not-an-object"],
            [{"code": "PAPER", "counted": 8, "reference": "CNT-P"}],
            [{"code": "PAPER", "counted": 8, "reference": "CNT-P", "expected_before": 12, "extra": 1}],
        ]
        for rows in bad_rows:
            with self.subTest(rows=rows):
                with self.assertRaises(ValueError):
                    self.app.confirm_counts(rows)
        for counted in (-1, True, 1.5, "8", None):
            with self.subTest(counted=counted):
                with self.assertRaises(ValueError):
                    self.app.confirm_counts([
                        {"code": "PAPER", "counted": counted, "reference": "CNT-X", "expected_before": 12},
                    ])

    def test_unknown_material_duplicates_and_reference_conflicts_rejected(self):
        with self.assertRaises(ValueError):
            self.app.confirm_counts([
                {"code": "OTHER", "counted": 1, "reference": "CNT-X", "expected_before": 0},
            ])
        with self.assertRaises(ValueError):
            self.app.confirm_counts([
                {"code": "PAPER", "counted": 8, "reference": "CNT-1", "expected_before": 12},
                {"code": " PAPER ", "counted": 7, "reference": "CNT-2", "expected_before": 12},
            ])
        with self.assertRaises(ValueError):
            self.app.confirm_counts([
                {"code": "PAPER", "counted": 8, "reference": "CNT-1", "expected_before": 12},
                {"code": "BOX", "counted": 3, "reference": " CNT-1 ", "expected_before": 3},
            ])
        # Existing movement, count and reversal references all conflict.
        with self.assertRaises(ValueError):
            self.app.confirm_counts([
                {"code": "PAPER", "counted": 8, "reference": "IN-P", "expected_before": 12},
            ])
        self.app.count("PAPER", 12, "CNT-ZERO")
        with self.assertRaises(ValueError):
            self.app.confirm_counts([
                {"code": "BOX", "counted": 3, "reference": "CNT-ZERO", "expected_before": 3},
            ])
        self.app.movement("PAPER", 1, "IN-REV")
        self.app.reverse("IN-REV", "REV-1")
        with self.assertRaises(ValueError):
            self.app.confirm_counts([
                {"code": "BOX", "counted": 3, "reference": "REV-1", "expected_before": 3},
            ])

    def test_rejection_keeps_file_bytes_and_missing_file_not_created(self):
        empty = Path(self.temp.name) / "empty"
        app = StockRoom(empty)
        with self.assertRaises(ValueError):
            app.confirm_counts([{"code": "PAPER", "counted": 8, "reference": "CNT-P", "expected_before": 0}])
        self.assertFalse((empty / "data.json").exists())
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.confirm_counts([
                {"code": "PAPER", "counted": 8, "reference": "CNT-1", "expected_before": 12},
                {"code": "BOX", "counted": 3, "reference": "CNT-2", "expected_before": 99},
            ])
        self.assertEqual(self.app.path.read_bytes(), before)
        self.assertEqual(self.app.counts("PAPER"), [])
        self.assertEqual(self.app.counts("BOX"), [])

    def test_confirmed_counts_lock_unit_and_cannot_be_reversed(self):
        self.app.confirm_counts(self.rows())
        with self.assertRaises(ValueError):
            self.app.update_material("PAPER", "包装纸", "箱")
        with self.assertRaises(ValueError):
            self.app.reverse("CNT-P", "REV-1")

    def test_count_batch_still_uses_latest_ledger(self):
        result = self.app.count_batch([{"code": "PAPER", "counted": 8, "reference": "CNT-P"}])
        self.assertEqual((result[0]["before"], result[0]["difference"]), (12, -4))

    def test_legacy_data_without_movements_counts_stock_as_zero(self):
        legacy = Path(self.temp.name) / "legacy"
        legacy.mkdir()
        (legacy / "data.json").write_text(
            json.dumps({"materials": {"PAPER": {"code": "PAPER", "name": "包装纸", "unit": "张"}}}),
            encoding="utf-8")
        app = StockRoom(legacy)
        result = app.confirm_counts([{"code": "PAPER", "counted": 5, "reference": "CNT-P", "expected_before": 0}])
        self.assertEqual((result[0]["before"], result[0]["difference"]), (0, 5))

    def test_cli_success_failure_and_array_independence(self):
        payload = self.write_payload("confirm.json", {"rows": self.rows()})
        result = self.run_cli("confirm-counts", payload)
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertEqual([(row["before"], row["difference"]) for row in output], [(12, -4), (3, 0)])
        # Failure: error JSON on stderr, exit code 2, ledger untouched.
        before = self.app.path.read_bytes()
        bad = self.write_payload("bad.json", {"rows": [
            {"code": "PAPER", "counted": 8, "reference": "CNT-X", "expected_before": 99},
        ]})
        failed = self.run_cli("confirm-counts", bad)
        self.assertEqual(failed.returncode, 2)
        self.assertIn("error", json.loads(failed.stderr))
        self.assertEqual(self.app.path.read_bytes(), before)
        # JSON array: each item commits independently, later failure keeps earlier success.
        batch = self.write_payload("confirms.json", [
            {"rows": [{"code": "PAPER", "counted": 8, "reference": "CNT-1", "expected_before": 8}]},
            {"rows": [{"code": "BOX", "counted": 2, "reference": "CNT-2", "expected_before": 99}]},
        ])
        mixed = self.run_cli("confirm-counts", batch)
        self.assertEqual(mixed.returncode, 2)
        self.assertIn("error", json.loads(mixed.stderr))
        self.assertEqual(self.app.stock("PAPER")["quantity"], 8)
        self.assertEqual(len(self.app.counts("PAPER")), 2)
        self.assertEqual(self.app.stock("BOX")["quantity"], 3)
        self.assertEqual(len(self.app.counts("BOX")), 1)


class ImportMovementsCsvTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = StockRoom(self.root)
        self.app.register("PAPER", "包装纸", "张")
        self.app.register("BOX", "纸箱", "个")
        self.app.movement("PAPER", 20, "IN-001")
        self.app.movement("PAPER", -6, "OUT-001")

    def test_fixed_sample_balances_survive_reopen_and_ledger(self):
        result = self.app.import_movements_csv("code,quantity,reference\nPAPER,-4,MV-1\nPAPER,2,MV-2\nBOX,3,MV-3\n")
        self.assertEqual([(row["code"], row["quantity"], row["reference"], row["balance"]) for row in result], [
            ("PAPER", -4, "MV-1", 10),
            ("PAPER", 2, "MV-2", 12),
            ("BOX", 3, "MV-3", 3),
        ])
        self.assertEqual(self.app.stock("PAPER")["quantity"], 12)
        self.assertEqual(self.app.stock("BOX")["quantity"], 3)
        self.assertEqual([(row["quantity"], row["reference"]) for row in self.app.history("PAPER")], [
            (20, "IN-001"), (-6, "OUT-001"), (-4, "MV-1"), (2, "MV-2"),
        ])
        reopened = StockRoom(self.root)
        self.assertEqual(reopened.stock("PAPER")["quantity"], 12)
        self.assertEqual(reopened.stock("BOX")["quantity"], 3)
        ledger = reopened.movement_ledger("PAPER")
        self.assertEqual([row["reference"] for row in ledger], ["IN-001", "OUT-001", "MV-1", "MV-2"])
        self.assertTrue(all(row["kind"] == "movement" for row in ledger[2:]))

    def test_fixed_sample_negative_after_rescue_still_rejects_whole_file(self):
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.import_movements_csv("code,quantity,reference\nBOX,-1,BAD-1\nBOX,4,BAD-2\n")
        self.assertEqual(before, self.app.path.read_bytes())
        self.assertEqual(self.app.stock("BOX")["quantity"], 0)
        self.assertEqual(self.app.history("BOX"), [])

    def test_imported_movements_can_be_reversed(self):
        self.app.import_movements_csv("code,quantity,reference\nBOX,3,MV-3\n")
        record = self.app.reverse("MV-3", "REV-MV3")
        self.assertEqual((record["code"], record["quantity"], record["balance"]), ("BOX", -3, 0))
        self.assertEqual(self.app.movement_ledger("BOX", kind="reversal")[0]["reference"], "REV-MV3")

    def test_bom_crlf_reordered_header_quoted_fields_and_leading_zeros(self):
        content = '﻿reference,quantity,code\r\n"M,1", 007 , PAPER \r\n\r\nM2,-004,PAPER\r\n'
        result = self.app.import_movements_csv(content)
        self.assertEqual([(row["code"], row["quantity"], row["reference"], row["balance"]) for row in result], [
            ("PAPER", 7, "M,1", 21),
            ("PAPER", -4, "M2", 17),
        ])

    def test_quoted_fields_may_contain_commas_newlines_and_doubled_quotes(self):
        self.app.register('PA"PER', '特殊纸', '张')
        result = self.app.import_movements_csv('code,quantity,reference\n"PA""PER",12,"MV,X"\n')
        self.assertEqual([(row["code"], row["reference"], row["balance"]) for row in result], [('PA"PER', "MV,X", 12)])
        content = 'code,quantity,reference\nPAPER,12,"MV\r\nLINE"\n'
        self.assertEqual(self.app.import_movements_csv(content)[0]["reference"], "MV\r\nLINE")

    def test_header_only_returns_empty_without_writing(self):
        empty = Path(self.temp.name) / "empty"
        app = StockRoom(empty)
        self.assertEqual(app.import_movements_csv("code,quantity,reference\n"), [])
        self.assertFalse((empty / "data.json").exists())
        before = self.app.path.read_bytes()
        self.assertEqual(self.app.import_movements_csv("reference,code,quantity\r\n\r\n"), [])
        self.assertEqual(before, self.app.path.read_bytes())

    def test_invalid_content_and_header_rejected(self):
        for content in ("", "﻿", None, 11, ["code"], {"c": "x"}):
            with self.subTest(content=content):
                with self.assertRaises(ValueError):
                    self.app.import_movements_csv(content)
        for content in (
            "code,quantity\n",
            "code,quantity,reference,extra\n",
            "code,code,reference\n",
            "code,quantity\nPAPER,1\n",
            " code,quantity,reference\n",
            "code ,quantity,reference\n",
            "CODE,quantity,reference\n",
            "code,qty,reference\n",
        ):
            with self.subTest(content=content):
                with self.assertRaises(ValueError):
                    self.app.import_movements_csv(content)

    def test_invalid_quotes_rejected(self):
        for content in (
            'code,quantity,reference\n"unclosed,1,R\n',
            'code,quantity,reference\nPAPER,1"2,R\n',
            'code,quantity,reference\nPAPER,"1"2,R\n',
            'code,quantity,reference\nPAPER,"1" 2,R\n',
            'code,quantity,reference\nPAPER, "1",R\n',
        ):
            with self.subTest(content=content):
                with self.assertRaises(ValueError):
                    self.app.import_movements_csv(content)

    def test_wrong_column_count_blank_fields_and_blank_like_records_rejected(self):
        for content in (
            "code,quantity,reference\nPAPER,1\n",
            "code,quantity,reference\nPAPER,1,R,多\n",
            "code,quantity,reference\n,1,R\n",
            "code,quantity,reference\nPAPER,,R\n",
            "code,quantity,reference\nPAPER,1,\n",
            "code,quantity,reference\n   \n",
            "code,quantity,reference\n,,\n",
            "code,quantity,reference\n,  ,\n",
            "code,quantity,reference\nPAPER,1,R,\n",
        ):
            with self.subTest(content=content):
                with self.assertRaises(ValueError):
                    self.app.import_movements_csv(content)

    def test_quantity_accepts_signed_digits_with_leading_zeros_but_not_zero(self):
        result = self.app.import_movements_csv("code,quantity,reference\nPAPER,007,Q1\nPAPER,-004,Q2\n")
        self.assertEqual([(row["quantity"], row["balance"]) for row in result], [(7, 21), (-4, 17)])
        for quantity in ("0", "-0", "-00", "+1", "1.0", "1e3", "1 2", "一", "0x1", "--1", "-", "1."):
            with self.subTest(quantity=quantity):
                with self.assertRaises(ValueError):
                    self.app.import_movements_csv("code,quantity,reference\nPAPER," + quantity + ",QX\n")

    def test_identifiers_are_stripped_internal_whitespace_kept_and_case_sensitive(self):
        result = self.app.import_movements_csv('code,quantity,reference\n PAPER ,1," MV A "\n')
        self.assertEqual(result[0]["code"], "PAPER")
        self.assertEqual(result[0]["reference"], "MV A")
        with self.assertRaises(ValueError):
            self.app.import_movements_csv("code,quantity,reference\npaper,1,MV-LOWER\n")

    def test_same_material_may_repeat_but_references_must_not(self):
        result = self.app.import_movements_csv("code,quantity,reference\nPAPER,1,R1\nPAPER,2,R2\n")
        self.assertEqual([row["balance"] for row in result], [15, 17])
        for content in (
            "code,quantity,reference\nPAPER,1,R1\nBOX,2,R1\n",
            "code,quantity,reference\nPAPER,1,R1\nBOX,2, R1 \n",
        ):
            with self.subTest(content=content):
                with self.assertRaises(ValueError):
                    self.app.import_movements_csv(content)

    def test_reference_conflicts_with_existing_movements_counts_and_reversals(self):
        self.app.reverse("OUT-001", "REV-OUT")
        self.app.count("BOX", 1, "CNT-EXIST")
        for content in (
            "code,quantity,reference\nPAPER,1,IN-001\n",
            "code,quantity,reference\nPAPER,1,CNT-EXIST\n",
            "code,quantity,reference\nPAPER,1,REV-OUT\n",
        ):
            with self.subTest(content=content):
                with self.assertRaises(ValueError):
                    self.app.import_movements_csv(content)

    def test_purchase_reference_namespace_is_independent(self):
        self.app.create_purchase("PR-1", "供应商", [{"code": "BOX", "quantity": 2}])
        result = self.app.import_movements_csv("code,quantity,reference\nBOX,1,PR-1\n")
        self.assertEqual(result[0]["balance"], 1)
        self.assertEqual([row["code"] for row in self.app.purchase_order("PR-1")["rows"]], ["BOX"])

    def test_unknown_or_inactive_material_rejects_even_when_other_rows_valid(self):
        with self.assertRaises(ValueError):
            self.app.import_movements_csv("code,quantity,reference\nPAPER,1,MV-P\nOTHER,1,MV-O\n")
        with self.assertRaises(ValueError):
            self.app.import_movements_csv("code,quantity,reference\nOTHER,1,MV-O\nPAPER,1,MV-P\n")
        self.app.set_active("BOX", False)
        with self.assertRaises(ValueError):
            self.app.import_movements_csv("code,quantity,reference\nBOX,1,MV-B\n")
        self.assertEqual(self.app.stock("PAPER")["quantity"], 14)
        self.assertEqual(self.app.stock("BOX")["quantity"], 0)

    def test_failed_import_preserves_file_and_all_state(self):
        self.app.set_minimum("PAPER", 15)
        self.app.set_active("BOX", False)
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.import_movements_csv("code,quantity,reference\nPAPER,1,MV-OK\nPAPER,1,MV-OK\n")
        with self.assertRaises(ValueError):
            self.app.import_movements_csv("code,quantity,reference\nPAPER,1,IN-001\n")
        with self.assertRaises(ValueError):
            self.app.import_movements_csv("code,quantity,reference\nUNKNOWN,1,MV-U\n")
        with self.assertRaises(ValueError):
            self.app.import_movements_csv("code,quantity,reference\nBOX,1,MV-B\n")
        with self.assertRaises(ValueError):
            self.app.import_movements_csv(11)
        self.assertEqual(before, self.app.path.read_bytes())
        self.assertEqual(self.app.stock("PAPER")["quantity"], 14)
        self.assertEqual(len(self.app.history("PAPER")), 2)
        self.assertEqual(self.app.material_status("BOX")["active"], False)
        self.assertEqual([item["code"] for item in self.app.shortages()], ["PAPER"])

    def test_failed_import_on_empty_directory_creates_no_file(self):
        empty = Path(self.temp.name) / "empty"
        app = StockRoom(empty)
        with self.assertRaises(ValueError):
            app.import_movements_csv("code,quantity,reference\nPAPER,1\n")
        with self.assertRaises(ValueError):
            app.import_movements_csv("")
        with self.assertRaises(ValueError):
            app.import_movements_csv("code,quantity,reference\nPAPER,1,MV\n")
        self.assertFalse((empty / "data.json").exists())

    def run_cli(self, *args):
        return subprocess.run([sys.executable, "-m", "stock_room", "--root", str(self.root), *args], text=True, capture_output=True)

    def test_cli_import_success_and_failure(self):
        payload = self.root / "import.json"
        payload.write_text(json.dumps({"content": "code,quantity,reference\nPAPER,-4,MV-1\nPAPER,2,MV-2\nBOX,3,MV-3\n"}), encoding="utf-8")
        result = self.run_cli("import-movements-csv", str(payload))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([row["balance"] for row in json.loads(result.stdout)], [10, 12, 3])
        bad = self.root / "bad-import.json"
        bad.write_text(json.dumps({"content": "code,quantity,reference\nBOX,-4,BAD-1\nBOX,4,BAD-2\n"}), encoding="utf-8")
        failed = self.run_cli("import-movements-csv", str(bad))
        self.assertEqual(failed.returncode, 2)
        self.assertIn("error", json.loads(failed.stderr))
        self.assertEqual(self.app.stock("BOX")["quantity"], 3)

    def test_cli_import_array_keeps_earlier_success(self):
        self.app.import_movements_csv("code,quantity,reference\nBOX,3,MV-3\n")
        payload = self.root / "imports.json"
        payload.write_text(json.dumps([
            {"content": "code,quantity,reference\nBOX,1,LATER-1\n"},
            {"content": "code,quantity,reference\nBOX,-99,LATER-2\n"},
        ]), encoding="utf-8")
        result = self.run_cli("import-movements-csv", str(payload))
        self.assertEqual(result.returncode, 2)
        self.assertIn("error", result.stderr)
        self.assertEqual(self.app.stock("BOX")["quantity"], 4)
        self.assertEqual([row["reference"] for row in self.app.history("BOX")], ["MV-3", "LATER-1"])


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


class ReopenPurchasesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = StockRoom(self.root)
        self.app.register("PAPER", "包装纸", "张")
        self.app.register("BOX", "纸箱", "个")

    def run_cli(self, *args):
        return subprocess.run([sys.executable, "-m", "stock_room", "--root", str(self.root), *args],
                              text=True, capture_output=True)

    def write_payload(self, name, body):
        payload = self.root / name
        payload.write_text(json.dumps(body), encoding="utf-8")
        return str(payload)

    def test_fixed_scenario_receive_return_cancel_reopen_then_complete(self):
        self.app.create_purchase("PO-1", "供应商", [{"code": "PAPER", "quantity": 10}])
        self.app.receive_purchase("PO-1", [{"code": "PAPER", "quantity": 6, "reference": "RCV-1"}])
        self.app.return_purchase("RCV-1", 2, "RET-1")
        self.assertEqual(self.app.stock("PAPER")["quantity"], 4)
        self.app.cancel_purchase("PO-1")
        result = self.app.reopen_purchases([" PO-1 "])
        self.assertEqual(result, [{
            "reference": "PO-1",
            "supplier": "供应商",
            "status": "open",
            "rows": [{"code": "PAPER", "name": "包装纸", "unit": "张", "quantity": 10}],
        }])
        self.assertEqual(self.app.stock("PAPER")["quantity"], 4)
        progress = self.app.purchase_progress("PO-1")
        self.assertEqual(progress["rows"][0]["received"], 6)
        self.assertEqual(progress["rows"][0]["returned"], 2)
        self.assertEqual(progress["rows"][0]["remaining"], 4)
        # 退货不恢复额度：只能再收四张
        with self.assertRaises(ValueError):
            self.app.receive_purchase("PO-1", [{"code": "PAPER", "quantity": 5, "reference": "RCV-2"}])
        self.app.receive_purchase("PO-1", [{"code": "PAPER", "quantity": 4, "reference": "RCV-2"}])
        self.assertEqual(self.app.purchase_progress("PO-1")["progress"], "complete")
        self.assertEqual(self.app.stock("PAPER")["quantity"], 8)
        reopened = StockRoom(self.root)
        self.assertEqual(reopened.purchase_order("PO-1")["status"], "open")
        self.assertEqual(reopened.purchase_progress("PO-1")["progress"], "complete")

    def test_invalid_references_rejected(self):
        before = self.app.path.read_bytes()
        for body in ([], "PO-1", {"reference": "PO-1"}, 5, None,
                     ["PO-1", 3],
                     ["PO-1", "  "],
                     ["PO-1", ""],
                     [" PO-1 ", "PO-1"]):
            with self.subTest(body=body):
                with self.assertRaises(ValueError):
                    self.app.reopen_purchases(body)
        self.assertEqual(self.app.path.read_bytes(), before)

    def test_unknown_reference_rejected_and_case_sensitive_exact_match(self):
        self.app.create_purchase("PO 1", "供应商", [{"code": "PAPER", "quantity": 1}])
        self.app.cancel_purchase("PO 1")
        before = self.app.path.read_bytes()
        for body in (["MISSING"], ["po 1"], ["PO  1"], ["PAPER"]):
            with self.subTest(body=body):
                with self.assertRaises(ValueError):
                    self.app.reopen_purchases(body)
        self.assertEqual(self.app.path.read_bytes(), before)
        # 内部空白保留，精确匹配成功
        self.assertEqual(self.app.reopen_purchases(["PO 1"])[0]["status"], "open")

    def test_cancelled_order_without_outstanding_lines_rejected(self):
        self.app.create_purchase("PO-1", "供应商", [{"code": "PAPER", "quantity": 3}])
        self.app.receive_purchase("PO-1", [{"code": "PAPER", "quantity": 3, "reference": "RCV-1"}])
        self.app.cancel_purchase("PO-1")
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.reopen_purchases(["PO-1"])
        self.assertEqual(self.app.path.read_bytes(), before)
        self.assertEqual(self.app.purchase_order("PO-1")["status"], "cancelled")

    def test_outstanding_material_missing_inactive_or_unit_mismatch_rejected(self):
        # 物料已删除
        self.app.create_purchase("PO-1", "供应商", [{"code": "PAPER", "quantity": 1}])
        self.app.cancel_purchase("PO-1")
        data = json.loads(self.app.path.read_text(encoding="utf-8"))
        del data["materials"]["PAPER"]
        self.app.path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            StockRoom(self.root).reopen_purchases(["PO-1"])
        self.assertEqual(self.app.path.read_bytes(), before)
        # 物料已停用
        data = json.loads(self.app.path.read_text(encoding="utf-8"))
        data["materials"]["PAPER"] = {"code": "PAPER", "name": "包装纸", "unit": "张"}
        data["status"] = {"PAPER": False}
        self.app.path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            StockRoom(self.root).reopen_purchases(["PO-1"])
        self.assertEqual(self.app.path.read_bytes(), before)
        # 单位与快照不一致
        data = json.loads(self.app.path.read_text(encoding="utf-8"))
        data["materials"]["PAPER"]["unit"] = "包"
        data["status"] = {}
        self.app.path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            StockRoom(self.root).reopen_purchases(["PO-1"])
        self.assertEqual(self.app.path.read_bytes(), before)

    def test_fully_received_line_does_not_block_even_if_inactive_or_renamed(self):
        self.app.create_purchase("PO-1", "供应商", [
            {"code": "PAPER", "quantity": 3},
            {"code": "BOX", "quantity": 2},
        ])
        self.app.receive_purchase("PO-1", [{"code": "PAPER", "quantity": 3, "reference": "RCV-1"}])
        self.app.cancel_purchase("PO-1")
        self.app.set_active("PAPER", False)
        result = self.app.reopen_purchases(["PO-1"])
        self.assertEqual(result[0]["status"], "open")
        self.assertEqual(result[0]["rows"][0]["name"], "包装纸")

    def test_open_orders_pass_without_condition_checks(self):
        self.app.create_purchase("PO-1", "供应商", [{"code": "PAPER", "quantity": 1}])
        self.app.create_purchase("PO-2", "供应商", [{"code": "BOX", "quantity": 1}])
        self.app.cancel_purchase("PO-2")
        self.app.set_active("PAPER", False)
        data = json.loads(self.app.path.read_text(encoding="utf-8"))
        del data["materials"]["BOX"]
        self.app.path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        before = self.app.path.read_bytes()
        # PO-1 是 open：物料停用也原样返回；PO-2 物料已删除故整批失败
        with self.assertRaises(ValueError):
            StockRoom(self.root).reopen_purchases(["PO-1", "PO-2"])
        self.assertEqual(self.app.path.read_bytes(), before)

    def test_batch_rejected_atomically_when_any_order_fails(self):
        self.app.create_purchase("PO-1", "供应商", [{"code": "PAPER", "quantity": 10}])
        self.app.receive_purchase("PO-1", [{"code": "PAPER", "quantity": 6, "reference": "RCV-1"}])
        self.app.return_purchase("RCV-1", 2, "RET-1")
        self.app.create_purchase("PO-2", "供应商", [{"code": "BOX", "quantity": 4}])
        self.app.cancel_purchase("PO-1")
        self.app.cancel_purchase("PO-2")
        self.app.set_active("BOX", False)
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.reopen_purchases(["PO-1", "PO-2"])
        self.assertEqual(self.app.path.read_bytes(), before)
        app = StockRoom(self.root)
        self.assertEqual(app.purchase_order("PO-1")["status"], "cancelled")
        self.assertEqual(app.purchase_order("PO-2")["status"], "cancelled")
        self.assertEqual(app.stock("PAPER")["quantity"], 4)
        self.assertEqual(app.purchase_changes("PO-1")[-1]["action"], "cancel_purchase")
        self.assertEqual(app.purchase_changes("PO-2")[-1]["action"], "cancel_purchase")

    def test_mixed_open_and_cancelled_batch_only_reopens_cancelled(self):
        self.app.create_purchase("PO-1", "供应商", [{"code": "PAPER", "quantity": 1}])
        self.app.create_purchase("PO-2", "供应商", [{"code": "BOX", "quantity": 2}])
        self.app.cancel_purchase("PO-2")
        result = self.app.reopen_purchases(["PO-2", "PO-1"])
        self.assertEqual([order["reference"] for order in result], ["PO-2", "PO-1"])
        self.assertEqual([order["status"] for order in result], ["open", "open"])
        changes_1 = self.app.purchase_changes("PO-1")
        changes_2 = self.app.purchase_changes("PO-2")
        self.assertEqual(changes_1, [])
        self.assertEqual([row["action"] for row in changes_2], ["cancel_purchase", "reopen_purchases"])
        reopen = changes_2[-1]
        self.assertEqual(reopen["sequence"], 2)
        self.assertEqual(reopen["before"]["status"], "cancelled")
        self.assertEqual(reopen["after"]["status"], "open")
        self.assertEqual(reopen["before"]["rows"], reopen["after"]["rows"])
        self.assertEqual(reopen["before"]["supplier"], reopen["after"]["supplier"])

    def test_all_open_batch_does_not_write_or_add_history(self):
        self.app.create_purchase("PO-1", "供应商", [{"code": "PAPER", "quantity": 1}])
        self.app.create_purchase("PO-2", "供应商", [{"code": "BOX", "quantity": 1}])
        before = self.app.path.read_bytes()
        result = self.app.reopen_purchases([" PO-2 ", "PO-1"])
        self.assertEqual(self.app.path.read_bytes(), before)
        self.assertEqual([order["reference"] for order in result], ["PO-2", "PO-1"])
        self.assertEqual(self.app.purchase_changes("PO-1"), [])
        self.assertEqual(self.app.purchase_changes("PO-2"), [])

    def test_failure_creates_no_file(self):
        empty = self.root / "empty"
        app = StockRoom(empty)
        with self.assertRaises(ValueError):
            app.reopen_purchases(["PO-1"])
        self.assertFalse((empty / "data.json").exists())

    def test_legacy_missing_receipts_and_status_defaults(self):
        self.app.create_purchase("PO-1", "供应商", [{"code": "PAPER", "quantity": 2}])
        self.app.cancel_purchase("PO-1")
        data = json.loads(self.app.path.read_text(encoding="utf-8"))
        data.pop("purchase_receipts", None)
        data.pop("status", None)
        self.app.path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        result = StockRoom(self.root).reopen_purchases(["PO-1"])
        self.assertEqual(result[0]["status"], "open")

    def test_reopened_order_counts_in_supplier_outstanding_and_replenishment(self):
        self.app.set_minimum("PAPER", 10)
        self.app.create_purchase("PO-1", "供应商", [{"code": "PAPER", "quantity": 10}])
        self.app.cancel_purchase("PO-1")
        self.assertEqual(self.app.supplier_outstanding("供应商"), [])
        plan = {item["code"]: item for item in self.app.replenishment_plan()}
        self.assertEqual(plan["PAPER"]["incoming"], 0)
        self.assertEqual(plan["PAPER"]["suggested"], 10)
        self.app.reopen_purchases(["PO-1"])
        outstanding = self.app.supplier_outstanding("供应商")
        self.assertEqual(len(outstanding), 1)
        self.assertEqual(outstanding[0]["code"], "PAPER")
        self.assertEqual(outstanding[0]["remaining"], 10)
        plan = {item["code"]: item for item in self.app.replenishment_plan()}
        self.assertEqual(plan["PAPER"]["incoming"], 10)
        self.assertEqual(plan["PAPER"]["suggested"], 0)
        orders = self.app.purchase_orders(status="open")
        self.assertEqual([order["reference"] for order in orders], ["PO-1"])
        self.assertIn("open", self.app.export_purchases_csv())

    def test_reopen_persists_across_reopen_with_single_history_record(self):
        self.app.create_purchase("PO-1", "供应商", [{"code": "PAPER", "quantity": 1}])
        self.app.cancel_purchase("PO-1")
        self.app.reopen_purchases(["PO-1"])
        app = StockRoom(self.root)
        self.assertEqual(app.purchase_order("PO-1")["status"], "open")
        actions = [row["action"] for row in app.purchase_changes("PO-1")]
        self.assertEqual(actions, ["cancel_purchase", "reopen_purchases"])

    def test_cli_reopen_success_failure_and_array_partial_commit(self):
        self.app.create_purchase("PO-1", "供应商", [{"code": "PAPER", "quantity": 10}])
        self.app.receive_purchase("PO-1", [{"code": "PAPER", "quantity": 6, "reference": "RCV-1"}])
        self.app.return_purchase("RCV-1", 2, "RET-1")
        self.app.create_purchase("PO-2", "供应商", [{"code": "BOX", "quantity": 4}])
        self.app.cancel_purchase("PO-1")
        self.app.cancel_purchase("PO-2")
        payload = self.write_payload("reopen.json", {"references": [" PO-1 "]})
        result = self.run_cli("reopen-purchases", payload)
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual([order["reference"] for order in value], ["PO-1"])
        self.assertEqual(value[0]["status"], "open")
        bad = self.write_payload("bad.json", {"references": ["PO-2", "MISSING"]})
        result = self.run_cli("reopen-purchases", bad)
        self.assertEqual(result.returncode, 2)
        self.assertIn("error", json.loads(result.stderr))
        self.assertEqual(StockRoom(self.root).purchase_order("PO-2")["status"], "cancelled")
        # JSON 数组逐项独立提交：第一项恢复 PO-2，第二项失败不回滚
        array_payload = self.write_payload("array.json", [
            {"references": ["PO-2"]},
            {"references": ["MISSING"]},
        ])
        result = self.run_cli("reopen-purchases", array_payload)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(StockRoom(self.root).purchase_order("PO-2")["status"], "open")


class ReplenishmentPurchaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = StockRoom(self.root)
        self.app.register("PAPER", "包装纸", "张")
        self.app.register("BOX", "纸箱", "个")
        self.app.set_minimum("PAPER", 10)
        self.app.set_minimum("BOX", 5)
        self.app.create_purchase("PO-1", "供应商", [{"code": "PAPER", "quantity": 7}])
        self.app.receive_purchase("PO-1", [{"code": "PAPER", "quantity": 3, "reference": "RCV-1"}])
        self.app.return_purchase("RCV-1", 1, "RET-1")
        self.app.create_purchase("PO-2", "供应商", [{"code": "BOX", "quantity": 5}])

    def run_cli(self, *args):
        return subprocess.run([sys.executable, "-m", "stock_room", "--root", str(self.root), *args],
                              text=True, capture_output=True)

    def write_payload(self, name, body):
        payload = self.root / name
        payload.write_text(json.dumps(body), encoding="utf-8")
        return str(payload)

    def test_fixed_sample_mixed_selection_fails_single_succeeds(self):
        # PAPER: 库存 2（收 3 退 1），在途 4（订 7 收 3，退货不恢复未收量），建议 4。
        # BOX: 库存 0，在途 5，建议 0，因此混合选择整次失败。
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.create_replenishment_purchase("PO-3", "新供应商", ["PAPER", "BOX"])
        self.assertEqual(self.app.path.read_bytes(), before)
        with self.assertRaises(ValueError):
            self.app.purchase_order("PO-3")

        order = self.app.create_replenishment_purchase("PO-3", " 新 供应商 ", ["PAPER"])
        self.assertEqual(order, {
            "reference": "PO-3",
            "supplier": "新 供应商",
            "status": "open",
            "rows": [{"code": "PAPER", "name": "包装纸", "unit": "张", "quantity": 4}],
        })

        reopened = StockRoom(self.root)
        self.assertEqual(reopened.stock("PAPER")["quantity"], 2)
        self.assertEqual(reopened.purchase_order("PO-3"), order)
        plan = {item["code"]: item for item in reopened.replenishment_plan()}
        self.assertEqual(plan["PAPER"]["incoming"], 8)
        self.assertEqual(plan["PAPER"]["suggested"], 0)
        self.assertEqual(
            [(item["reference"], item["remaining"]) for item in plan["PAPER"]["purchases"]],
            [("PO-1", 4), ("PO-3", 4)],
        )
        # 新单按既有规则收货。
        received = reopened.receive_purchase("PO-3", [{"code": "PAPER", "quantity": 4, "reference": "RCV-2"}])
        self.assertEqual(received[0]["balance"], 6)

    def test_rows_follow_codes_order_and_snapshot_current_profile(self):
        self.app.register("INK", "油墨", "瓶")
        self.app.set_minimum("INK", 3)
        order = self.app.create_replenishment_purchase("PO-3", "供应商", ["INK", "PAPER"])
        self.assertEqual([row["code"] for row in order["rows"]], ["INK", "PAPER"])
        self.assertEqual(order["rows"][0], {"code": "INK", "name": "油墨", "unit": "瓶", "quantity": 3})
        self.assertEqual(order["rows"][1]["quantity"], 4)
        # 名称与单位为下单时快照，后续修改物料资料不影响单据。
        self.app.update_material("INK", "高级油墨", "桶")
        saved = self.app.purchase_order("PO-3")
        self.assertEqual(saved["rows"][0]["name"], "油墨")
        self.assertEqual(saved["rows"][0]["unit"], "瓶")

    def test_cancelled_and_fully_received_lines_do_not_count(self):
        self.app.cancel_purchase("PO-2")
        order = self.app.create_replenishment_purchase("PO-3", "供应商", ["BOX"])
        self.assertEqual(order["rows"][0]["quantity"], 5)
        self.app.receive_purchase("PO-3", [{"code": "BOX", "quantity": 5, "reference": "RCV-2"}])
        self.app.set_minimum("PAPER", 2)
        # PAPER 建议量降为零（最低库存 2，库存 2），BOX 在途已收齐。
        with self.assertRaises(ValueError):
            self.app.create_replenishment_purchase("PO-4", "供应商", ["PAPER", "BOX"])

    def test_validation_failures_preserve_file(self):
        before = self.app.path.read_bytes()
        bad = [
            {"reference": "PO-9", "supplier": "供应商", "codes": []},
            {"reference": "PO-9", "supplier": "供应商", "codes": "PAPER"},
            {"reference": "PO-9", "supplier": "供应商", "codes": {"code": "PAPER"}},
            {"reference": "PO-9", "supplier": "供应商", "codes": ["PAPER", " PAPER "]},
            {"reference": "PO-9", "supplier": "供应商", "codes": ["UNKNOWN"]},
            {"reference": "PO-9", "supplier": "供应商", "codes": [""]},
            {"reference": "PO-9", "supplier": "供应商", "codes": ["  "]},
            {"reference": "PO-9", "supplier": "供应商", "codes": [1]},
            {"reference": "PO-9", "supplier": "供应商", "codes": [None]},
            {"reference": "PO-9", "supplier": "供应商", "codes": [True]},
            {"reference": "PO-9", "supplier": "供应商", "codes": [{"code": "PAPER"}]},
            {"reference": "PO-9", "supplier": "供应商", "codes": ["BOX"]},
            {"reference": "", "supplier": "供应商", "codes": ["PAPER"]},
            {"reference": "  ", "supplier": "供应商", "codes": ["PAPER"]},
            {"reference": 1, "supplier": "供应商", "codes": ["PAPER"]},
            {"reference": "PO-9", "supplier": "", "codes": ["PAPER"]},
            {"reference": "PO-9", "supplier": "  ", "codes": ["PAPER"]},
            {"reference": "PO-9", "supplier": 1, "codes": ["PAPER"]},
            {"reference": "PO-1", "supplier": "供应商", "codes": ["PAPER"]},
            {"reference": " PO-1 ", "supplier": "供应商", "codes": ["PAPER"]},
        ]
        for body in bad:
            with self.subTest(body=body):
                with self.assertRaises(ValueError):
                    self.app.create_replenishment_purchase(**body)
        self.assertEqual(self.app.path.read_bytes(), before)
        with self.assertRaises(ValueError):
            self.app.purchase_order("PO-9")

    def test_inactive_material_rejected(self):
        self.app.set_active("PAPER", False)
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.create_replenishment_purchase("PO-9", "供应商", ["PAPER"])
        self.assertEqual(self.app.path.read_bytes(), before)

    def test_unit_mismatch_checked_only_for_selected_materials(self):
        self.app.register("INK", "油墨", "瓶")
        self.app.set_minimum("INK", 3)
        self.app.create_purchase("PO-8", "供应商", [{"code": "INK", "quantity": 2}])
        self.app.update_material("INK", "油墨", "桶")
        # INK 的在途单位与当前单位不一致，但不影响只为 PAPER 下单。
        order = self.app.create_replenishment_purchase("PO-9", "供应商", ["PAPER"])
        self.assertEqual(order["rows"][0]["quantity"], 4)
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.create_replenishment_purchase("PO-10", "供应商", ["INK"])
        with self.assertRaises(ValueError):
            self.app.create_replenishment_purchase("PO-10", "供应商", ["PAPER", "INK"])
        self.assertEqual(self.app.path.read_bytes(), before)

    def test_reference_unique_only_among_purchases(self):
        order = self.app.create_replenishment_purchase("RCV-1", "供应商", ["PAPER"])
        self.assertEqual(order["reference"], "RCV-1")
        with self.assertRaises(ValueError):
            self.app.create_replenishment_purchase("RCV-1", "供应商", ["PAPER"])
        moved = self.app.movement("PAPER", 1, "PO-1")
        self.assertEqual(moved["balance"], 3)

    def test_creation_keeps_stock_suppliers_and_history_unchanged(self):
        before_movements = self.app.history("PAPER")
        self.app.create_replenishment_purchase("PO-9", "新供应商", ["PAPER"])
        self.assertEqual(self.app.stock("PAPER")["quantity"], 2)
        self.assertEqual(self.app.history("PAPER"), before_movements)
        self.assertEqual(self.app.inventory()[0]["minimum"], 5)
        data = json.loads(self.app.path.read_text(encoding="utf-8"))
        self.assertNotIn("新供应商", data.get("suppliers", {}))
        self.assertEqual(self.app.supplier_changes("新供应商"), [])
        self.assertEqual(self.app.purchase_changes("PO-9"), [])

    def test_failure_creates_no_file(self):
        empty = self.root / "empty"
        app = StockRoom(empty)
        with self.assertRaises(ValueError):
            app.create_replenishment_purchase("PO-1", "供应商", ["PAPER"])
        with self.assertRaises(ValueError):
            app.create_replenishment_purchase("PO-1", "供应商", [])
        self.assertFalse((empty / "data.json").exists())

    def test_legacy_data_defaults_apply(self):
        data = json.loads(self.app.path.read_text(encoding="utf-8"))
        data.pop("status", None)
        data.pop("purchase_receipts", None)
        self.app.path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        reopened = StockRoom(self.root)
        # 缺少收货记录时在途按订购量全额计算：PAPER 库存 2、在途 7，建议 1。
        order = reopened.create_replenishment_purchase("PO-9", "供应商", ["PAPER"])
        self.assertEqual(order["rows"][0]["quantity"], 1)
        # 旧数据缺少最低库存时按零，建议量为零则拒绝。
        data = json.loads(self.app.path.read_text(encoding="utf-8"))
        data.pop("minimums", None)
        self.app.path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            StockRoom(self.root).create_replenishment_purchase("PO-10", "供应商", ["PAPER"])
        self.assertEqual(self.app.path.read_bytes(), before)

    def test_cli_success_failure_and_array_partial_success(self):
        payload = self.write_payload("replen.json", {
            "reference": "PO-3", "supplier": "供应商", "codes": ["PAPER"],
        })
        result = self.run_cli("create-replenishment-purchase", payload)
        self.assertEqual(result.returncode, 0, result.stderr)
        created = json.loads(result.stdout)
        self.assertEqual(created["status"], "open")
        self.assertEqual(created["rows"], [{"code": "PAPER", "name": "包装纸", "unit": "张", "quantity": 4}])

        before = self.app.path.read_bytes()
        bad = self.write_payload("bad.json", {"reference": "PO-4", "supplier": "供应商", "codes": ["BOX"]})
        result = self.run_cli("create-replenishment-purchase", bad)
        self.assertEqual(result.returncode, 2)
        self.assertIn("error", json.loads(result.stderr))
        self.assertEqual(self.app.path.read_bytes(), before)

        array = self.write_payload("array.json", [
            {"reference": "PO-5", "supplier": "供应商", "codes": ["INK"]},
            {"reference": "PO-6", "supplier": "供应商", "codes": ["UNKNOWN"]},
        ])
        self.app.register("INK", "油墨", "瓶")
        self.app.set_minimum("INK", 3)
        result = self.run_cli("create-replenishment-purchase", array)
        self.assertEqual(result.returncode, 2)
        self.assertIn("error", json.loads(result.stderr))
        self.assertEqual(StockRoom(self.root).purchase_order("PO-5")["rows"],
                         [{"code": "INK", "name": "油墨", "unit": "瓶", "quantity": 3}])
        with self.assertRaises(ValueError):
            StockRoom(self.root).purchase_order("PO-6")


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


class ReceivePurchaseBatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = StockRoom(self.root)
        self.app.register("BOX", "纸箱", "个")
        self.app.create_purchase("P1", "甲供应商", [{"code": "BOX", "quantity": 5}])
        self.app.create_purchase("P2", "乙供应商", [{"code": "BOX", "quantity": 4}])

    def run_cli(self, *args):
        return subprocess.run([sys.executable, "-m", "stock_room", "--root", str(self.root), *args],
                              text=True, capture_output=True)

    def write_payload(self, name, body):
        payload = self.root / name
        payload.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
        return str(payload)

    def test_fixed_sample_cross_purchase_batch_survives_reopen(self):
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.receive_purchase_batch([
                {"purchase_reference": "P1", "code": "BOX", "quantity": 2, "reference": "RCV-1"},
                {"purchase_reference": "P2", "code": "BOX", "quantity": 5, "reference": "RCV-2"},
            ])
        self.assertEqual(self.app.path.read_bytes(), before)
        self.assertEqual(self.app.stock("BOX")["quantity"], 0)
        # Rejected references stay reusable once the offending row is fixed.
        result = self.app.receive_purchase_batch([
            {"purchase_reference": " P1 ", "code": " BOX ", "quantity": 2, "reference": " RCV-1 "},
            {"purchase_reference": "P2", "code": "BOX", "quantity": 3, "reference": "RCV-2"},
        ])
        self.assertEqual(result, [
            {"code": "BOX", "quantity": 2, "reference": "RCV-1", "balance": 2, "purchase_reference": "P1"},
            {"code": "BOX", "quantity": 3, "reference": "RCV-2", "balance": 5, "purchase_reference": "P2"},
        ])
        self.assertEqual(self.app.stock("BOX")["quantity"], 5)
        progress1 = self.app.purchase_progress("P1")
        progress2 = self.app.purchase_progress("P2")
        self.assertEqual(progress1["rows"][0]["received"], 2)
        self.assertEqual(progress1["rows"][0]["remaining"], 3)
        self.assertEqual(progress2["rows"][0]["received"], 3)
        self.assertEqual(progress2["rows"][0]["remaining"], 1)
        self.assertEqual([row["reference"] for row in self.app.history("BOX")], ["RCV-1", "RCV-2"])
        self.assertEqual(self.app.purchase_receipts("P1"),
                         [{"code": "BOX", "quantity": 2, "reference": "RCV-1", "balance": 2}])
        self.assertEqual(self.app.purchase_receipts("P2"),
                         [{"code": "BOX", "quantity": 3, "reference": "RCV-2", "balance": 5}])
        reopened = StockRoom(self.root)
        self.assertEqual(reopened.stock("BOX")["quantity"], 5)
        self.assertEqual([row["reference"] for row in reopened.purchase_receipts("P1")], ["RCV-1"])
        self.assertEqual([row["reference"] for row in reopened.purchase_receipts("P2")], ["RCV-2"])
        with self.assertRaises(ValueError):
            reopened.receive_purchase_batch([
                {"purchase_reference": "P2", "code": "BOX", "quantity": 2, "reference": "RCV-3"},
            ])
        reopened.receive_purchase_batch([
            {"purchase_reference": "P1", "code": "BOX", "quantity": 3, "reference": "RCV-3"},
            {"purchase_reference": "P2", "code": "BOX", "quantity": 1, "reference": "RCV-4"},
        ])
        self.assertEqual(reopened.stock("BOX")["quantity"], 9)

    def test_invalid_rows_and_row_shape_rejected(self):
        for rows in (None, [], {}, "x", 1):
            with self.subTest(rows=rows):
                with self.assertRaises(ValueError):
                    self.app.receive_purchase_batch(rows)
        good = {"purchase_reference": "P1", "code": "BOX", "quantity": 1, "reference": "R"}
        for rows in (
            [None],
            ["P1"],
            [[]],
            [{"code": "BOX", "quantity": 1, "reference": "R"}],
            [{"purchase_reference": "P1", "quantity": 1, "reference": "R"}],
            [{"purchase_reference": "P1", "code": "BOX", "reference": "R"}],
            [{"purchase_reference": "P1", "code": "BOX", "quantity": 1}],
            [{**good, "extra": 1}],
        ):
            with self.subTest(rows=rows):
                with self.assertRaises(ValueError):
                    self.app.receive_purchase_batch(rows)

    def test_invalid_identifiers_and_quantity_rejected(self):
        for field in ("purchase_reference", "code", "reference"):
            for value in ("", "   ", 11, None):
                with self.subTest(field=field, value=value):
                    row = {"purchase_reference": "P1", "code": "BOX", "quantity": 1, "reference": "R"}
                    row[field] = value
                    with self.assertRaises(ValueError):
                        self.app.receive_purchase_batch([row])
        for quantity in (0, -1, True, False, 1.5, "1", None, [1]):
            with self.subTest(quantity=quantity):
                with self.assertRaises(ValueError):
                    self.app.receive_purchase_batch([
                        {"purchase_reference": "P1", "code": "BOX", "quantity": quantity, "reference": "R"},
                    ])

    def test_business_rules_rejected(self):
        self.app.register("INK", "油墨", "瓶")
        self.app.set_active("INK", False)
        self.app.create_purchase("P3", "丙供应商", [{"code": "BOX", "quantity": 2}])
        self.app.cancel_purchase("P3")
        self.app.create_purchase("P4", "丁供应商", [{"code": "BOX", "quantity": 2}])
        self.app.update_material("BOX", "纸箱", "箱")
        before = self.app.path.read_bytes()
        for row in (
            {"purchase_reference": "MISSING", "code": "BOX", "quantity": 1, "reference": "R"},
            {"purchase_reference": "P3", "code": "BOX", "quantity": 1, "reference": "R"},
            {"purchase_reference": "P1", "code": "UNKNOWN", "quantity": 1, "reference": "R"},
            {"purchase_reference": "P1", "code": "INK", "quantity": 1, "reference": "R"},
            {"purchase_reference": "P4", "code": "BOX", "quantity": 1, "reference": "R"},
        ):
            with self.subTest(row=row):
                with self.assertRaises(ValueError):
                    self.app.receive_purchase_batch([row])
        # Unit snapshot mismatch: P1/P2 were created with unit 个, current is 箱.
        with self.assertRaises(ValueError):
            self.app.receive_purchase_batch([
                {"purchase_reference": "P1", "code": "BOX", "quantity": 1, "reference": "R"},
            ])
        self.assertEqual(self.app.path.read_bytes(), before)
        self.assertEqual(self.app.stock("BOX")["quantity"], 0)

    def test_same_material_across_purchases_but_pair_repeat_rejected(self):
        result = self.app.receive_purchase_batch([
            {"purchase_reference": "P1", "code": "BOX", "quantity": 1, "reference": "RCV-1"},
            {"purchase_reference": "P2", "code": "BOX", "quantity": 1, "reference": "RCV-2"},
        ])
        self.assertEqual([row["balance"] for row in result], [1, 2])
        with self.assertRaises(ValueError):
            self.app.receive_purchase_batch([
                {"purchase_reference": "P1", "code": "BOX", "quantity": 1, "reference": "RCV-3"},
                {"purchase_reference": " P1 ", "code": " BOX ", "quantity": 1, "reference": "RCV-4"},
            ])

    def test_cumulative_quota_and_returns_do_not_restore_capacity(self):
        self.app.receive_purchase_batch([
            {"purchase_reference": "P1", "code": "BOX", "quantity": 5, "reference": "RCV-1"},
        ])
        self.app.return_purchase("RCV-1", 2, "RET-1")
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.receive_purchase_batch([
                {"purchase_reference": "P1", "code": "BOX", "quantity": 1, "reference": "RCV-2"},
            ])
        self.assertEqual(self.app.path.read_bytes(), before)
        self.assertEqual(self.app.purchase_progress("P1")["rows"][0]["remaining"], 0)
        # Other purchases of the same material keep their own quota.
        self.app.receive_purchase_batch([
            {"purchase_reference": "P2", "code": "BOX", "quantity": 4, "reference": "RCV-3"},
        ])

    def test_reference_scope_shared_with_all_histories(self):
        self.app.movement("BOX", 1, "IN-1")
        self.app.count("BOX", 1, "CNT-1")
        self.app.reverse("IN-1", "REV-1")
        for reference in ("IN-1", "CNT-1", "REV-1"):
            with self.subTest(reference=reference):
                with self.assertRaises(ValueError):
                    self.app.receive_purchase_batch([
                        {"purchase_reference": "P1", "code": "BOX", "quantity": 1, "reference": reference},
                    ])
        with self.assertRaises(ValueError):
            self.app.receive_purchase_batch([
                {"purchase_reference": "P1", "code": "BOX", "quantity": 1, "reference": "DUP"},
                {"purchase_reference": "P2", "code": "BOX", "quantity": 1, "reference": " DUP "},
            ])

    def test_purchase_reference_stays_independent_of_receipt_reference(self):
        self.app.create_purchase("RCV-9", "供应商", [{"code": "BOX", "quantity": 1}])
        result = self.app.receive_purchase_batch([
            {"purchase_reference": "P1", "code": "BOX", "quantity": 1, "reference": "RCV-9"},
        ])
        self.assertEqual(result[0]["reference"], "RCV-9")

    def test_failed_batch_on_empty_directory_creates_no_file(self):
        empty = self.root / "empty"
        app = StockRoom(empty)
        with self.assertRaises(ValueError):
            app.receive_purchase_batch([])
        with self.assertRaises(ValueError):
            app.receive_purchase_batch([
                {"purchase_reference": "P1", "code": "BOX", "quantity": 1, "reference": "R"},
            ])
        self.assertFalse((empty / "data.json").exists())

    def test_purchase_snapshot_minimums_and_status_unchanged(self):
        self.app.set_minimum("BOX", 6)
        self.app.receive_purchase_batch([
            {"purchase_reference": "P1", "code": "BOX", "quantity": 2, "reference": "RCV-1"},
            {"purchase_reference": "P2", "code": "BOX", "quantity": 3, "reference": "RCV-2"},
        ])
        for reference in ("P1", "P2"):
            order = self.app.purchase_order(reference)
            self.assertEqual(order["status"], "open")
            self.assertEqual(order["rows"][0], {"code": "BOX", "name": "纸箱", "unit": "个", "quantity": 5 if reference == "P1" else 4})
        self.assertEqual(self.app.material_status("BOX"), {"code": "BOX", "active": True})
        self.assertEqual(self.app.inventory()[0]["minimum"], 6)

    def test_existing_queries_recognize_receipts_and_reversal_rejected(self):
        self.app.set_minimum("BOX", 10)
        self.app.receive_purchase_batch([
            {"purchase_reference": "P1", "code": "BOX", "quantity": 2, "reference": "RCV-1"},
            {"purchase_reference": "P2", "code": "BOX", "quantity": 3, "reference": "RCV-2"},
        ])
        ledger = self.app.movement_ledger("BOX", "purchase_receipt")
        self.assertEqual([(row["reference"], row["purchase_reference"]) for row in ledger],
                         [("RCV-1", "P1"), ("RCV-2", "P2")])
        plan = self.app.replenishment_plan()
        self.assertEqual(plan[0]["quantity"], 5)
        self.assertEqual(plan[0]["incoming"], 4)
        self.assertEqual([item["reference"] for item in plan[0]["purchases"]], ["P1", "P2"])
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.reverse("RCV-1", "REV-RCV")
        self.assertEqual(self.app.path.read_bytes(), before)

    def test_legacy_data_defaults_apply(self):
        data = json.loads(self.app.path.read_text(encoding="utf-8"))
        data.pop("purchase_receipts", None)
        data.pop("status", None)
        self.app.path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        reopened = StockRoom(self.root)
        result = reopened.receive_purchase_batch([
            {"purchase_reference": "P1", "code": "BOX", "quantity": 5, "reference": "RCV-1"},
            {"purchase_reference": "P2", "code": "BOX", "quantity": 4, "reference": "RCV-2"},
        ])
        self.assertEqual([row["balance"] for row in result], [5, 9])

    def test_cli_receive_purchase_batch_success_and_failure(self):
        payload = self.write_payload("batch.json", {"rows": [
            {"purchase_reference": "P1", "code": "BOX", "quantity": 2, "reference": "RCV-1"},
            {"purchase_reference": "P2", "code": "BOX", "quantity": 3, "reference": "RCV-2"},
        ]})
        result = self.run_cli("receive-purchase-batch", payload)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [
            {"balance": 2, "code": "BOX", "purchase_reference": "P1", "quantity": 2, "reference": "RCV-1"},
            {"balance": 5, "code": "BOX", "purchase_reference": "P2", "quantity": 3, "reference": "RCV-2"},
        ])
        before = self.app.path.read_bytes()
        bad = self.write_payload("bad.json", {"rows": [
            {"purchase_reference": "P1", "code": "BOX", "quantity": 4, "reference": "RCV-3"},
        ]})
        failed = self.run_cli("receive-purchase-batch", bad)
        self.assertEqual(failed.returncode, 2)
        self.assertIn("error", json.loads(failed.stderr))
        self.assertEqual(self.app.path.read_bytes(), before)

    def test_cli_array_keeps_earlier_batches(self):
        payload = self.write_payload("batches.json", [
            {"rows": [{"purchase_reference": "P1", "code": "BOX", "quantity": 1, "reference": "RCV-1"}]},
            {"rows": [{"purchase_reference": "P2", "code": "BOX", "quantity": 9, "reference": "RCV-2"}]},
        ])
        result = self.run_cli("receive-purchase-batch", payload)
        self.assertEqual(result.returncode, 2)
        self.assertIn("error", json.loads(result.stderr))
        self.assertEqual(self.app.stock("BOX")["quantity"], 1)
        self.assertEqual([row["reference"] for row in self.app.purchase_receipts("P1")], ["RCV-1"])
        self.assertEqual(self.app.purchase_receipts("P2"), [])


def seed_beichen_merge(app):
    # Fixed sample for supplier merge regression: BOX (纸箱/个, minimum 10),
    # two supplier profiles and four purchases with mixed receipt/cancel state.
    app.register("BOX", "纸箱", "个")
    app.set_minimum("BOX", 10)
    app.save_supplier("北辰旧名", contact="张", phone="123", note="旧档")
    app.save_supplier("北辰", contact="李", phone="", note="保留")
    app.create_purchase("P1", "北辰旧名", [{"code": "BOX", "quantity": 7}])
    app.receive_purchase("P1", [{"code": "BOX", "quantity": 3, "reference": "RCV-1"}])
    app.return_purchase("RCV-1", 1, "RET-1")
    app.create_purchase("P3", "北辰旧名", [{"code": "BOX", "quantity": 2}])
    app.cancel_purchase("P3")
    app.create_purchase("P2", "北辰", [{"code": "BOX", "quantity": 5}])
    app.create_purchase("P4", "北辰包装", [{"code": "BOX", "quantity": 1}])


class MergeSupplierTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = StockRoom(self.root)
        seed_beichen_merge(self.app)

    def order_refs(self, record):
        return [order["reference"] for order in record["purchases"]]

    def test_fixed_sample_merge_contacts_orders_stats_and_reopen(self):
        before_suppliers = {item["supplier"]: item for item in self.app.suppliers()}
        self.assertEqual(list(before_suppliers), ["北辰", "北辰包装", "北辰旧名"])
        self.assertEqual([item["reference"] for item in self.app.purchase_orders(supplier="北辰旧名")], ["P1", "P3"])
        plan_before = self.app.replenishment_plan()[0]
        self.assertEqual(
            [(item["reference"], item["supplier"], item["remaining"]) for item in plan_before["purchases"]],
            [("P1", "北辰旧名", 4), ("P2", "北辰", 5), ("P4", "北辰包装", 1)],
        )

        # Names are located after trimming surrounding whitespace.
        merged = self.app.merge_supplier("  北辰旧名  ", "  北辰  ")
        record = self.app.supplier_record("北辰")
        self.assertEqual(merged, record)
        self.assertEqual(list(merged), ["supplier", "contact", "phone", "note", "purchases"])
        self.assertEqual(
            {key: merged[key] for key in ("supplier", "contact", "phone", "note")},
            {"supplier": "北辰", "contact": "李", "phone": "123", "note": "保留"},
        )
        self.assertEqual(self.order_refs(merged), ["P1", "P2", "P3"])
        by_ref = {order["reference"]: order for order in merged["purchases"]}
        p1, p2, p3 = by_ref["P1"], by_ref["P2"], by_ref["P3"]
        self.assertEqual(p1["supplier"], "北辰")
        self.assertEqual(p1["status"], "open")
        self.assertEqual(p1["progress"], "partial")
        self.assertEqual(p1["rows"], [{
            "code": "BOX", "name": "纸箱", "unit": "个", "quantity": 7,
            "received": 3, "returned": 1, "net_received": 2, "remaining": 4,
        }])
        self.assertEqual(p2["status"], "open")
        self.assertEqual(p2["progress"], "pending")
        self.assertEqual(p2["rows"][0]["received"], 0)
        self.assertEqual(p2["rows"][0]["remaining"], 5)
        self.assertEqual(p3["status"], "cancelled")
        self.assertEqual(p3["progress"], "pending")
        self.assertEqual(p3["rows"][0]["remaining"], 2)

        # The similar supplier and its purchase stay untouched.
        packaging = self.app.supplier_record("北辰包装")
        self.assertEqual(
            {key: packaging[key] for key in ("contact", "phone", "note")},
            {"contact": "", "phone": "", "note": ""},
        )
        self.assertEqual(self.order_refs(packaging), ["P4"])
        self.assertEqual(self.app.purchase_order("P4")["supplier"], "北辰包装")
        self.assertEqual(packaging["purchases"][0]["progress"], "pending")
        self.assertEqual(packaging["purchases"][0]["rows"][0], {
            "code": "BOX", "name": "纸箱", "unit": "个", "quantity": 1,
            "received": 0, "returned": 0, "net_received": 0, "remaining": 1,
        })

        # Stock, material profile, minimum, snapshot, row order and every
        # receipt/return record are unchanged.
        self.assertEqual(self.app.stock("BOX"), {"code": "BOX", "name": "纸箱", "unit": "个", "quantity": 2})
        self.assertEqual(self.app.material_status("BOX"), {"code": "BOX", "active": True})
        self.assertEqual(self.app.shortages(), [
            {"code": "BOX", "name": "纸箱", "unit": "个", "quantity": 2, "minimum": 10, "shortage": 8},
        ])
        self.assertEqual(self.app.purchase_order("P1")["rows"], [
            {"code": "BOX", "name": "纸箱", "unit": "个", "quantity": 7},
        ])
        self.assertEqual(self.app.purchase_receipts("P1"), [
            {"code": "BOX", "quantity": 3, "reference": "RCV-1", "balance": 3},
        ])
        self.assertEqual(self.app.purchase_returns("P1"), [
            {"purchase_reference": "P1", "receipt_reference": "RCV-1", "code": "BOX",
             "quantity": 1, "reference": "RET-1", "balance": 2},
        ])
        self.assertEqual(self.app.movement_ledger("BOX"), [
            {"code": "BOX", "quantity": 3, "reference": "RCV-1", "before": 0, "balance": 3,
             "kind": "purchase_receipt", "purchase_reference": "P1", "related_reference": None},
            {"code": "BOX", "quantity": -1, "reference": "RET-1", "before": 3, "balance": 2,
             "kind": "purchase_return", "purchase_reference": "P1", "related_reference": "RCV-1"},
        ])

        # Purchase filters and replenishment sources show migrated orders under the new name.
        self.assertEqual(self.app.purchase_orders(supplier="北辰旧名"), [])
        self.assertEqual(
            [item["reference"] for item in self.app.purchase_orders(supplier="北辰", status="cancelled")],
            ["P3"],
        )
        self.assertEqual(
            [item["reference"] for item in self.app.purchase_orders(supplier="北辰", progress="partial")],
            ["P1"],
        )
        plan_after = self.app.replenishment_plan()[0]
        self.assertEqual((plan_after["quantity"], plan_after["minimum"], plan_after["shortage"]), (2, 10, 8))
        self.assertEqual((plan_after["incoming"], plan_after["suggested"]), (10, 0))
        self.assertEqual(
            [(item["reference"], item["supplier"], item["remaining"]) for item in plan_after["purchases"]],
            [("P1", "北辰", 4), ("P2", "北辰", 5), ("P4", "北辰包装", 1)],
        )

        # The old name is gone from the list and exact lookup.
        self.assertEqual([item["supplier"] for item in self.app.suppliers()], ["北辰", "北辰包装"])
        self.assertEqual([item["supplier"] for item in self.app.suppliers(keyword="北辰")], ["北辰", "北辰包装"])
        with self.assertRaises(ValueError):
            self.app.supplier_record("北辰旧名")

        # Results survive reopening the same directory.
        reopened = StockRoom(self.root)
        self.assertEqual(reopened.supplier_record("北辰"), record)
        self.assertEqual([item["supplier"] for item in reopened.suppliers()], ["北辰", "北辰包装"])
        with self.assertRaises(ValueError):
            reopened.supplier_record("北辰旧名")
        self.assertEqual(reopened.stock("BOX")["quantity"], 2)
        self.assertEqual(reopened.purchase_progress("P1")["rows"][0]["net_received"], 2)

    def test_merge_again_with_gone_source_fails_then_name_can_be_reused(self):
        self.app.merge_supplier("北辰旧名", "北辰")
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.merge_supplier("北辰旧名", "北辰包装")
        self.assertEqual(self.app.path.read_bytes(), before)
        # The old name may be re-established through the existing entries.
        self.app.save_supplier("北辰旧名", contact="周", phone="", note="重建")
        record = self.app.supplier_record("北辰旧名")
        self.assertEqual((record["contact"], record["phone"], record["note"]), ("周", "", "重建"))
        self.assertEqual(record["purchases"], [])
        self.app.create_purchase("P5", "北辰旧名", [{"code": "BOX", "quantity": 6}])
        self.assertEqual(
            [order["reference"] for order in self.app.supplier_record("北辰旧名")["purchases"]],
            ["P5"],
        )
        self.assertIn("北辰旧名", [item["supplier"] for item in self.app.suppliers()])

    def test_only_one_side_has_profile(self):
        self.app = StockRoom(self.root / "sided")
        self.app.register("BOX", "纸箱", "个")
        # Source profile only: target exists just through a purchase.
        self.app.save_supplier("有档案", contact="赵", phone="9", note="N")
        self.app.create_purchase("Q1", "仅采购", [{"code": "BOX", "quantity": 1}])
        result = self.app.merge_supplier("有档案", "仅采购")
        self.assertEqual(
            {key: result[key] for key in ("contact", "phone", "note")},
            {"contact": "赵", "phone": "9", "note": "N"},
        )
        self.assertEqual(self.order_refs(result), ["Q1"])
        with self.assertRaises(ValueError):
            self.app.supplier_record("有档案")

        # Target profile only: its values win and the source's empty fields
        # never overwrite them.
        self.app.save_supplier("留档方", contact="钱", phone="8", note="留")
        self.app.create_purchase("Q2", "流动方", [{"code": "BOX", "quantity": 1}])
        result = self.app.merge_supplier("流动方", "留档方")
        self.assertEqual(
            {key: result[key] for key in ("contact", "phone", "note")},
            {"contact": "钱", "phone": "8", "note": "留"},
        )
        self.assertEqual(self.order_refs(result), ["Q2"])
        with self.assertRaises(ValueError):
            self.app.supplier_record("流动方")

    def test_neither_side_has_profile_creates_no_empty_profile(self):
        app = StockRoom(self.root / "profileless")
        app.register("BOX", "纸箱", "个")
        app.create_purchase("R1", "无档甲", [{"code": "BOX", "quantity": 1}])
        app.create_purchase("R2", "无档乙", [{"code": "BOX", "quantity": 2}])
        result = app.merge_supplier("无档甲", "无档乙")
        self.assertEqual(
            {key: result[key] for key in ("contact", "phone", "note")},
            {"contact": "", "phone": "", "note": ""},
        )
        self.assertEqual(self.order_refs(result), ["R1", "R2"])
        data = json.loads(app.path.read_text(encoding="utf-8"))
        self.assertEqual(data.get("suppliers", {}), {})
        with self.assertRaises(ValueError):
            app.supplier_record("无档甲")

    def test_legacy_data_without_receipts_or_returns_counts_stats_as_zero(self):
        # Old ledger with no receipts/returns collections: merge still migrates
        # the order and progress stats are all computed as zero.
        legacy = self.root / "legacy"
        legacy.mkdir()
        payload = {
            "materials": {"BOX": {"code": "BOX", "name": "纸箱", "unit": "个"}},
            "minimums": {"BOX": 10},
            "purchases": [
                {"reference": "L1", "supplier": "旧供应商", "status": "open",
                 "rows": [{"code": "BOX", "name": "纸箱", "unit": "个", "quantity": 7}]},
            ],
        }
        (legacy / "data.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        old = StockRoom(legacy)
        old.save_supplier("新供应商", contact="李", phone="123", note="保留")
        merged = old.merge_supplier("旧供应商", "新供应商")
        order = merged["purchases"][0]
        self.assertEqual(order["reference"], "L1")
        self.assertEqual(order["progress"], "pending")
        self.assertEqual(order["rows"][0], {
            "code": "BOX", "name": "纸箱", "unit": "个", "quantity": 7,
            "received": 0, "returned": 0, "net_received": 0, "remaining": 7,
        })
        self.assertEqual((merged["contact"], merged["phone"], merged["note"]), ("李", "123", "保留"))
        self.assertEqual(StockRoom(legacy).supplier_record("新供应商")["purchases"][0]["rows"][0]["net_received"], 0)


class MergeSupplierValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = StockRoom(self.root)
        seed_beichen_merge(self.app)

    def test_invalid_arguments_rejected_with_bytes_and_business_intact(self):
        before = self.app.path.read_bytes()
        invalid_pairs = [
            (None, "北辰"), (123, "北辰"), ([], "北辰"), ({}, "北辰"),
            ("北辰旧名", None), ("北辰旧名", 123), ("北辰旧名", []), ("北辰旧名", {}),
            ("", "北辰"), ("   ", "北辰"), ("\t\n", "北辰"),
            ("北辰旧名", ""), ("北辰旧名", "   "),
            ("幽灵", "北辰"), ("北辰旧名", "幽灵"),
            ("北辰", "  北辰  "), ("北辰旧名", "北辰旧名"),
        ]
        for source, target in invalid_pairs:
            with self.subTest(source=source, target=target):
                with self.assertRaises(ValueError):
                    self.app.merge_supplier(source, target)
        self.assertEqual(self.app.path.read_bytes(), before)
        # All business results stay exactly as seeded after every rejection.
        self.assertEqual([item["supplier"] for item in self.app.suppliers()],
                         ["北辰", "北辰包装", "北辰旧名"])
        self.assertEqual(self.app.stock("BOX")["quantity"], 2)
        self.assertEqual(self.app.purchase_order("P1")["supplier"], "北辰旧名")
        self.assertEqual(self.app.purchase_receipts("P1")[0]["quantity"], 3)
        self.assertEqual(self.app.purchase_returns("P1")[0]["quantity"], 1)
        self.assertEqual(self.app.purchase_progress("P3")["status"], "cancelled")

    def test_failed_merge_on_empty_directory_creates_no_file(self):
        empty = self.root / "empty"
        app = StockRoom(empty)
        for source, target in [
            (None, "A"), ("A", None), ("", "A"), ("A", "  "),
            ("A", "A"), ("  A ", "A"), ("A", "B"),
        ]:
            with self.subTest(source=source, target=target):
                with self.assertRaises(ValueError):
                    app.merge_supplier(source, target)
        self.assertFalse((empty / "data.json").exists())

    def test_names_trimmed_internal_whitespace_kept_and_case_sensitive(self):
        self.app.create_purchase("W1", "North", [{"code": "BOX", "quantity": 1}])
        self.app.create_purchase("W2", "north", [{"code": "BOX", "quantity": 1}])
        self.app.create_purchase("W3", "北 辰", [{"code": "BOX", "quantity": 1}])
        before = self.app.path.read_bytes()
        # Unknown mixed-case name: lookup is case sensitive.
        with self.assertRaises(ValueError):
            self.app.merge_supplier("NORTH", "North")
        # Internal whitespace is part of the name: the double-space name does not exist.
        with self.assertRaises(ValueError):
            self.app.merge_supplier("北  辰", "北辰包装")
        # After trimming surrounding whitespace these normalize to the same name.
        with self.assertRaises(ValueError):
            self.app.merge_supplier(" 北 辰 ", "北 辰")
        self.assertEqual(self.app.path.read_bytes(), before)
        # The no-space name 北辰 is a different supplier and keeps P2.
        self.assertEqual(
            [order["reference"] for order in self.app.supplier_record("北辰")["purchases"]],
            ["P2"],
        )

        # Surrounding whitespace is trimmed and the single internal space preserved.
        self.app.merge_supplier(" 北 辰 ", "  North ")
        self.assertEqual(
            [order["reference"] for order in self.app.supplier_record("North")["purchases"]],
            ["W1", "W3"],
        )
        with self.assertRaises(ValueError):
            self.app.supplier_record("北 辰")
        # Distinct casings are different names and may merge into each other.
        self.app.merge_supplier("north", "North")
        self.assertEqual(
            [order["reference"] for order in self.app.supplier_record("North")["purchases"]],
            ["W1", "W2", "W3"],
        )
        with self.assertRaises(ValueError):
            self.app.supplier_record("north")
        # The unrelated supplier and the no-space 北辰 keep their own orders.
        self.assertEqual(self.app.purchase_order("P4")["supplier"], "北辰包装")
        self.assertEqual(
            [order["reference"] for order in self.app.supplier_record("北辰")["purchases"]],
            ["P2"],
        )


class MergeSupplierCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = StockRoom(self.root)
        seed_beichen_merge(self.app)

    def run_cli(self, *args):
        return subprocess.run([sys.executable, "-m", "stock_room", "--root", str(self.root), *args],
                              text=True, capture_output=True)

    def write_payload(self, name, body):
        payload = self.root / name
        payload.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
        return str(payload)

    def test_cli_success_prints_target_record_json_and_exit_0(self):
        payload = self.write_payload("merge.json", {"source": " 北辰旧名 ", "target": " 北辰 "})
        result = self.run_cli("merge-supplier", payload)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        value = json.loads(result.stdout)
        self.assertEqual(value, StockRoom(self.root).supplier_record("北辰"))
        self.assertEqual(
            {key: value[key] for key in ("supplier", "contact", "phone", "note")},
            {"supplier": "北辰", "contact": "李", "phone": "123", "note": "保留"},
        )
        self.assertEqual([order["reference"] for order in value["purchases"]], ["P1", "P2", "P3"])

    def test_cli_failure_prints_error_json_to_stderr_and_exit_2(self):
        before = self.app.path.read_bytes()
        payload = self.write_payload("bad.json", {"source": "幽灵", "target": "北辰"})
        result = self.run_cli("merge-supplier", payload)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("error", json.loads(result.stderr))
        self.assertEqual(self.app.path.read_bytes(), before)
        self.assertEqual(self.app.purchase_order("P1")["supplier"], "北辰旧名")
        # No data file is created when the directory starts empty.
        empty = self.root / "cli-empty"
        empty_payload = self.root / "empty-input.json"
        empty_payload.write_text(json.dumps({"source": "A", "target": "B"}), encoding="utf-8")
        empty_result = subprocess.run(
            [sys.executable, "-m", "stock_room", "--root", str(empty), "merge-supplier", str(empty_payload)],
            text=True, capture_output=True)
        self.assertEqual(empty_result.returncode, 2)
        self.assertIn("error", json.loads(empty_result.stderr))
        self.assertFalse((empty / "data.json").exists())

    def test_cli_array_first_success_persists_when_second_fails(self):
        payload = self.write_payload("merges.json", [
            {"source": "北辰旧名", "target": "北辰"},
            {"source": "北辰旧名", "target": "北辰包装"},
        ])
        result = self.run_cli("merge-supplier", payload)
        self.assertEqual(result.returncode, 2)
        self.assertIn("error", json.loads(result.stderr))
        # The first merge survived; the failed second item changed nothing else.
        reopened = StockRoom(self.root)
        self.assertEqual([item["supplier"] for item in reopened.suppliers()], ["北辰", "北辰包装"])
        record = reopened.supplier_record("北辰")
        self.assertEqual(
            {key: record[key] for key in ("contact", "phone", "note")},
            {"contact": "李", "phone": "123", "note": "保留"},
        )
        self.assertEqual([order["reference"] for order in record["purchases"]], ["P1", "P2", "P3"])
        packaging = reopened.supplier_record("北辰包装")
        self.assertEqual(
            {key: packaging[key] for key in ("contact", "phone", "note")},
            {"contact": "", "phone": "", "note": ""},
        )
        self.assertEqual([order["reference"] for order in packaging["purchases"]], ["P4"])
        self.assertEqual(reopened.purchase_order("P4")["supplier"], "北辰包装")
        with self.assertRaises(ValueError):
            reopened.supplier_record("北辰旧名")

class SupplierChangeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = StockRoom(self.root)

    def profile(self, supplier, contact="", phone="", note=""):
        return {"supplier": supplier, "contact": contact, "phone": phone, "note": note}

    def test_save_repeat_merge_and_recreate_sequence(self):
        self.app.save_supplier("甲", contact="张三")
        self.app.save_supplier("乙")
        # Repeat save with identical data succeeds without growing history.
        self.app.save_supplier("甲", contact="张三")
        self.assertEqual(
            self.app.supplier_changes("甲"),
            [{"supplier": "甲", "sequence": 1, "action": "save_supplier", "related_supplier": None,
              "before": None, "after": self.profile("甲", contact="张三")}],
        )
        self.assertEqual(
            self.app.supplier_changes("乙"),
            [{"supplier": "乙", "sequence": 1, "action": "save_supplier", "related_supplier": None,
              "before": None, "after": self.profile("乙")}],
        )
        self.app.merge_supplier("甲", "乙")
        source_history = self.app.supplier_changes("甲")
        self.assertEqual(len(source_history), 2)
        self.assertEqual(source_history[1], {
            "supplier": "甲", "sequence": 2, "action": "merge_supplier", "related_supplier": "乙",
            "before": self.profile("甲", contact="张三"), "after": None,
        })
        target_history = self.app.supplier_changes("乙")
        self.assertEqual(len(target_history), 2)
        self.assertEqual(target_history[1], {
            "supplier": "乙", "sequence": 2, "action": "merge_supplier", "related_supplier": "甲",
            "before": self.profile("乙"), "after": self.profile("乙", contact="张三"),
        })
        # Re-establishing the old name continues its original sequence.
        self.app.save_supplier("甲", contact="李四")
        self.assertEqual(
            self.app.supplier_changes("甲")[2],
            {"supplier": "甲", "sequence": 3, "action": "save_supplier", "related_supplier": None,
             "before": None, "after": self.profile("甲", contact="李四")},
        )

    def test_history_survives_reopen_and_later_business_keeps_old_snapshots(self):
        self.app.save_supplier("甲", contact="张三")
        self.app.save_supplier("乙")
        self.app.merge_supplier("甲", "乙")
        history = self.app.supplier_changes("甲")
        self.assertEqual(StockRoom(self.root).supplier_changes("甲"), history)
        self.app.save_supplier("乙", contact="王五")
        self.app.save_supplier("甲", contact="李四")
        self.assertEqual(self.app.supplier_changes("甲")[:2], history)
        target = self.app.supplier_changes("乙")
        self.assertEqual(target[1]["after"], self.profile("乙", contact="张三"))
        self.assertEqual(target[2]["after"], self.profile("乙", contact="王五"))

    def test_merge_records_only_changed_sides(self):
        # Neither side has a profile: no records at all.
        self.app.register("BOX", "纸箱", "个")
        self.app.create_purchase("P1", "无档甲", [{"code": "BOX", "quantity": 1}])
        self.app.create_purchase("P2", "无档乙", [{"code": "BOX", "quantity": 1}])
        self.app.merge_supplier("无档甲", "无档乙")
        self.assertEqual(self.app.supplier_changes("无档乙"), [])
        with self.assertRaises(ValueError):
            self.app.supplier_changes("无档甲")
        # Source profile only: source deletion and target creation recorded.
        self.app.save_supplier("仅有源", contact="赵")
        self.app.create_purchase("P3", "仅采购", [{"code": "BOX", "quantity": 1}])
        self.app.merge_supplier("仅有源", "仅采购")
        self.assertEqual(self.app.supplier_changes("仅有源")[-1]["after"], None)
        target = self.app.supplier_changes("仅采购")
        self.assertEqual(len(target), 1)
        self.assertEqual(target[0]["before"], None)
        self.assertEqual(target[0]["after"], self.profile("仅采购", contact="赵"))
        # Target profile unchanged by the merge: only the source is recorded.
        self.app.save_supplier("满档", contact="钱", phone="8", note="留")
        self.app.save_supplier("空档")
        before_count = len(self.app.supplier_changes("满档"))
        self.app.merge_supplier("空档", "满档")
        self.assertEqual(len(self.app.supplier_changes("满档")), before_count)
        self.assertEqual(self.app.supplier_changes("空档")[-1]["after"], None)

    def test_gone_name_queryable_but_not_listed_or_mergeable(self):
        self.app.save_supplier("旧名", contact="张")
        self.app.save_supplier("新名")
        self.app.merge_supplier("旧名", "新名")
        self.assertEqual(len(self.app.supplier_changes("旧名")), 2)
        self.assertEqual([item["supplier"] for item in self.app.suppliers()], ["新名"])
        with self.assertRaises(ValueError):
            self.app.merge_supplier("旧名", "新名")
        with self.assertRaises(ValueError):
            self.app.merge_supplier("新名", "旧名")

    def test_invalid_and_unknown_names_rejected_without_writes(self):
        self.app.save_supplier("甲", contact="张三")
        before = self.app.path.read_bytes()
        for bad in ("", "   ", 1, None, True, [], {}, "幽灵"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    self.app.supplier_changes(bad)
        self.assertEqual(self.app.path.read_bytes(), before)
        empty = StockRoom(self.root / "empty")
        with self.assertRaises(ValueError):
            empty.supplier_changes("甲")
        self.assertFalse((self.root / "empty" / "data.json").exists())

    def test_query_does_not_write_and_returns_independent_copies(self):
        self.app.save_supplier("甲", contact="张三")
        before = self.app.path.read_bytes()
        rows = self.app.supplier_changes("甲")
        self.assertEqual(self.app.path.read_bytes(), before)
        rows[0]["after"]["contact"] = "篡改"
        rows[0]["action"] = "tampered"
        fresh = self.app.supplier_changes("甲")
        self.assertEqual(fresh[0]["action"], "save_supplier")
        self.assertEqual(fresh[0]["after"]["contact"], "张三")

    def test_legacy_data_first_change_uses_actual_old_profile(self):
        legacy = self.root / "legacy"
        legacy.mkdir()
        (legacy / "data.json").write_text(json.dumps({
            "suppliers": {"旧": {"supplier": "旧", "contact": "老", "phone": "1", "note": ""}},
        }, ensure_ascii=False), encoding="utf-8")
        app = StockRoom(legacy)
        self.assertEqual(app.supplier_changes("旧"), [])
        app.save_supplier("旧", contact="新")
        changes = StockRoom(legacy).supplier_changes("旧")
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["sequence"], 1)
        self.assertEqual(changes[0]["before"], self.profile("旧", contact="老", phone="1"))
        self.assertEqual(changes[0]["after"], self.profile("旧", contact="新"))

    def test_failed_save_and_merge_append_nothing(self):
        self.app.save_supplier("甲", contact="张三")
        self.app.save_supplier("乙")
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.save_supplier("  ", contact="x")
        with self.assertRaises(ValueError):
            self.app.save_supplier("甲", contact=1)
        with self.assertRaises(ValueError):
            self.app.merge_supplier("幽灵", "乙")
        with self.assertRaises(ValueError):
            self.app.merge_supplier("甲", "甲")
        self.assertEqual(self.app.path.read_bytes(), before)
        self.assertEqual(len(self.app.supplier_changes("甲")), 1)

    def test_cli_success_failure_and_array_processing(self):
        self.app.save_supplier("甲", contact="张三")
        query = self.root / "query.json"
        query.write_text(json.dumps({"supplier": "甲"}), encoding="utf-8")
        ok = subprocess.run([sys.executable, "-m", "stock_room", "--root", str(self.root), "supplier-changes", str(query)],
                            text=True, capture_output=True)
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertEqual(len(json.loads(ok.stdout)), 1)
        bad = self.root / "bad.json"
        bad.write_text(json.dumps({"supplier": "幽灵"}), encoding="utf-8")
        failed = subprocess.run([sys.executable, "-m", "stock_room", "--root", str(self.root), "supplier-changes", str(bad)],
                                text=True, capture_output=True)
        self.assertEqual(failed.returncode, 2)
        self.assertIn("error", json.loads(failed.stderr))
        batch = self.root / "batch.json"
        batch.write_text(json.dumps([{"supplier": "甲"}, {"supplier": "幽灵"}]), encoding="utf-8")
        result = subprocess.run([sys.executable, "-m", "stock_room", "--root", str(self.root), "supplier-changes", str(batch)],
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("error", json.loads(result.stderr))


class MaterialChangeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = StockRoom(self.root)
        self.app.register("BOX", "纸箱", "个")

    def profile(self, name="纸箱", unit="个", minimum=0, active=True):
        return {"name": name, "unit": unit, "minimum": minimum, "active": active}

    def test_fixed_sample_rename_minimum_repeated_deactivate_survives_reopen(self):
        self.assertEqual(self.app.update_material("  BOX  ", "周转箱", "  个  "), {"code": "BOX", "name": "周转箱", "unit": "个"})
        self.assertEqual(self.app.set_minimum("BOX", 5), {"code": "BOX", "minimum": 5})
        self.assertEqual(self.app.set_active("BOX", False), {"code": "BOX", "active": False})
        self.assertEqual(self.app.set_active("BOX", False), {"code": "BOX", "active": False})
        changes = StockRoom(self.root).material_changes("BOX")
        self.assertEqual([row["sequence"] for row in changes], [1, 2, 3])
        self.assertEqual([row["action"] for row in changes], ["update_material", "set_minimum", "set_active"])
        expected = [
            (self.profile(), self.profile(name="周转箱")),
            (self.profile(name="周转箱"), self.profile(name="周转箱", minimum=5)),
            (self.profile(name="周转箱", minimum=5), self.profile(name="周转箱", minimum=5, active=False)),
        ]
        for row, (before, after) in zip(changes, expected):
            self.assertEqual(set(row), {"code", "sequence", "action", "before", "after"})
            self.assertEqual(row["code"], "BOX")
            self.assertEqual(row["before"], before)
            self.assertEqual(row["after"], after)
            self.assertEqual(set(row["before"]), {"name", "unit", "minimum", "active"})
            self.assertEqual(set(row["after"]), {"name", "unit", "minimum", "active"})

    def test_empty_history_for_registered_material_and_inactive_query(self):
        self.assertEqual(self.app.material_changes("BOX"), [])
        self.app.set_active("BOX", False)
        reopened = StockRoom(self.root)
        changes = reopened.material_changes("  BOX  ")
        self.assertEqual([(row["sequence"], row["action"]) for row in changes], [(1, "set_active")])
        self.assertEqual(changes[0]["after"]["active"], False)

    def test_invalid_code_arguments_rejected(self):
        for code in ("", "   ", 11, None, True, ["BOX"]):
            with self.subTest(code=code):
                with self.assertRaises(ValueError):
                    self.app.material_changes(code)
        with self.assertRaises(ValueError):
            self.app.material_changes("box")
        with self.assertRaises(ValueError):
            self.app.material_changes("UNKNOWN")

    def test_case_sensitive_code_with_inner_whitespace(self):
        self.app.register("A B", "物料", "个")
        self.app.set_minimum("A B", 2)
        self.assertEqual(self.app.material_changes("A B")[0]["code"], "A B")
        with self.assertRaises(ValueError):
            self.app.material_changes("AB")
        with self.assertRaises(ValueError):
            self.app.material_changes("a b")

    def test_repeat_values_add_no_records_and_combined_profile_one_record(self):
        self.app.update_material("BOX", "纸箱", "个")
        self.app.set_minimum("BOX", 0)
        self.app.set_active("BOX", True)
        self.assertEqual(self.app.material_changes("BOX"), [])
        self.app.update_material("BOX", "新 纸箱", "只")
        changes = self.app.material_changes("BOX")
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["before"], self.profile())
        self.assertEqual(changes[0]["after"], self.profile(name="新 纸箱", unit="只"))

    def test_register_and_import_create_no_records_legacy_defaults_in_snapshot(self):
        self.app.import_materials_csv("code,name,unit\nTAPE,胶带,卷\n")
        self.assertEqual(self.app.material_changes("BOX"), [])
        self.assertEqual(self.app.material_changes("TAPE"), [])
        self.app.set_minimum("TAPE", 1)
        change = StockRoom(self.root).material_changes("TAPE")[0]
        self.assertEqual(change["sequence"], 1)
        self.assertEqual(change["before"], {"name": "胶带", "unit": "卷", "minimum": 0, "active": True})
        self.assertEqual(change["after"], {"name": "胶带", "unit": "卷", "minimum": 1, "active": True})

    def test_failed_modifications_append_nothing_and_keep_bytes(self):
        self.app.set_minimum("BOX", 5)
        self.app.register("TAPE", "胶带", "卷")
        self.app.movement("TAPE", 1, "TAPE-IN")
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.update_material("TAPE", "新胶带", "包")
        with self.assertRaises(ValueError):
            self.app.update_material("BOX", "", "个")
        with self.assertRaises(ValueError):
            self.app.update_material("UNKNOWN", "纸", "个")
        with self.assertRaises(ValueError):
            self.app.set_minimum("BOX", True)
        with self.assertRaises(ValueError):
            self.app.set_minimum("UNKNOWN", 1)
        with self.assertRaises(ValueError):
            self.app.set_active("BOX", 1)
        with self.assertRaises(ValueError):
            self.app.set_active("UNKNOWN", False)
        self.assertEqual(before, self.app.path.read_bytes())
        self.assertEqual(len(self.app.material_changes("BOX")), 1)
        self.assertEqual(self.app.material_changes("TAPE"), [])

    def test_query_on_empty_directory_creates_no_file(self):
        empty = Path(self.temp.name) / "empty"
        app = StockRoom(empty)
        with self.assertRaises(ValueError):
            app.material_changes("BOX")
        with self.assertRaises(ValueError):
            app.material_changes(11)
        self.assertFalse((empty / "data.json").exists())

    def test_query_success_and_failure_do_not_modify_file(self):
        self.app.set_minimum("BOX", 5)
        before = self.app.path.read_bytes()
        self.app.material_changes("BOX")
        with self.assertRaises(ValueError):
            self.app.material_changes("UNKNOWN")
        self.assertEqual(before, self.app.path.read_bytes())

    def test_returned_snapshots_are_independent_copies(self):
        self.app.set_minimum("BOX", 5)
        changes = self.app.material_changes("BOX")
        changes[0]["after"]["minimum"] = 99
        changes[0]["action"] = "tampered"
        fresh = self.app.material_changes("BOX")
        self.assertEqual(fresh[0]["after"]["minimum"], 5)
        self.assertEqual(fresh[0]["action"], "set_minimum")

    def test_changes_keep_out_of_other_histories_and_do_not_lock_unit(self):
        self.app.update_material("BOX", "周转箱", "个")
        self.app.set_minimum("BOX", 5)
        self.app.set_active("BOX", False)
        self.app.set_active("BOX", True)
        self.assertEqual(self.app.history("BOX"), [])
        self.assertEqual(self.app.counts("BOX"), [])
        self.assertEqual(self.app.reversals("BOX"), [])
        self.assertEqual(self.app.update_material("BOX", "周转箱", "只")["unit"], "只")
        changes = self.app.material_changes("BOX")
        self.assertEqual([row["sequence"] for row in changes], [1, 2, 3, 4, 5])
        self.assertEqual([row["action"] for row in changes], ["update_material", "set_minimum", "set_active", "set_active", "update_material"])

    def test_sequences_are_independent_between_materials(self):
        self.app.register("TAPE", "胶带", "卷")
        self.app.set_minimum("BOX", 1)
        self.app.set_minimum("TAPE", 2)
        self.app.set_active("BOX", False)
        self.app.set_minimum("TAPE", 3)
        self.assertEqual([(row["sequence"], row["action"]) for row in self.app.material_changes("BOX")], [(1, "set_minimum"), (2, "set_active")])
        self.assertEqual([(row["sequence"], row["action"]) for row in self.app.material_changes("TAPE")], [(1, "set_minimum"), (2, "set_minimum")])

    def test_cli_query_success_failure_and_no_file_side_effects(self):
        query = self.root / "query.json"
        query.write_text(json.dumps({"code": "BOX"}), encoding="utf-8")
        result = subprocess.run([sys.executable, "-m", "stock_room", "--root", str(self.root), "material-changes", str(query)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [])
        bad = self.root / "bad-query.json"
        bad.write_text(json.dumps({"code": "UNKNOWN"}), encoding="utf-8")
        failed = subprocess.run([sys.executable, "-m", "stock_room", "--root", str(self.root), "material-changes", str(bad)], text=True, capture_output=True)
        self.assertEqual(failed.returncode, 2)
        self.assertIn("error", json.loads(failed.stderr))

    def test_cli_array_independent_commit_keeps_earlier_change(self):
        payload = self.root / "active-batch.json"
        payload.write_text(json.dumps([
            {"code": "BOX", "active": False},
            {"code": "BOX", "active": 1},
        ]), encoding="utf-8")
        result = subprocess.run([sys.executable, "-m", "stock_room", "--root", str(self.root), "set-active", str(payload)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("error", result.stderr)
        changes = self.app.material_changes("BOX")
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["after"]["active"], False)
        self.assertEqual(self.app.material_status("BOX")["active"], False)

class PurchaseChangeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = StockRoom(self.root)
        self.app.register("PAPER", "纸张", "包")
        self.app.register("INK", "墨水", "瓶")
        self.app.save_supplier("乙", "李", "123", "备注")
        self.app.create_purchase("P1", "甲", [{"code": "PAPER", "quantity": 10}])

    def order(self, supplier="甲", status="open", rows=None):
        if rows is None:
            rows = [{"code": "PAPER", "name": "纸张", "unit": "包", "quantity": 10}]
        return {"reference": "P1", "supplier": supplier, "status": status, "rows": rows}

    def test_fixed_scenario_update_repeat_cancel_repeat_merge_yields_three_records(self):
        self.app.update_purchase(" P1 ", "甲", [{"code": "PAPER", "quantity": 8}])
        self.app.update_purchase("P1", "甲", [{"code": "PAPER", "quantity": 8}])
        self.app.cancel_purchase("P1")
        self.app.cancel_purchase("P1")
        changes = StockRoom(self.root).purchase_changes("P1")
        self.assertEqual([c["sequence"] for c in changes], [1, 2])
        self.assertEqual([c["action"] for c in changes], ["update_purchase", "cancel_purchase"])
        self.app.merge_supplier("甲", "乙")
        changes = StockRoom(self.root).purchase_changes("P1")
        self.assertEqual([c["sequence"] for c in changes], [1, 2, 3])
        self.assertEqual([c["action"] for c in changes], ["update_purchase", "cancel_purchase", "merge_supplier"])
        self.assertEqual(changes[0]["before"], self.order(rows=[{"code": "PAPER", "name": "纸张", "unit": "包", "quantity": 10}]))
        self.assertEqual(changes[0]["after"], self.order(rows=[{"code": "PAPER", "name": "纸张", "unit": "包", "quantity": 8}]))
        eight = self.order(status="cancelled", rows=[{"code": "PAPER", "name": "纸张", "unit": "包", "quantity": 8}])
        self.assertEqual(changes[1]["before"], self.order(rows=[{"code": "PAPER", "name": "纸张", "unit": "包", "quantity": 8}]))
        self.assertEqual(changes[1]["after"], eight)
        self.assertEqual(changes[2]["before"], eight)
        self.assertEqual(changes[2]["after"], self.order(supplier="乙", status="cancelled", rows=[{"code": "PAPER", "name": "纸张", "unit": "包", "quantity": 8}]))
        for change in changes:
            self.assertEqual(set(change), {"reference", "sequence", "action", "before", "after"})
            self.assertEqual(change["reference"], "P1")

    def test_empty_history_for_existing_order_including_cancelled(self):
        self.assertEqual(self.app.purchase_changes("P1"), [])
        self.app.cancel_purchase("P1")
        self.assertEqual(len(StockRoom(self.root).purchase_changes("P1")), 1)
        self.assertEqual(len(StockRoom(self.root).purchase_changes(" P1 ")), 1)

    def test_invalid_reference_arguments_rejected(self):
        for reference in ("", "   ", 11, None, True, ["P1"]):
            with self.subTest(reference=reference):
                with self.assertRaises(ValueError):
                    self.app.purchase_changes(reference)
        with self.assertRaises(ValueError):
            self.app.purchase_changes("p1")
        with self.assertRaises(ValueError):
            self.app.purchase_changes("P 1")
        with self.assertRaises(ValueError):
            self.app.purchase_changes("UNKNOWN")

    def test_case_sensitive_reference_with_inner_whitespace(self):
        self.app.create_purchase("A 1", "甲", [{"code": "PAPER", "quantity": 1}])
        self.app.update_purchase("A 1", "甲", [{"code": "PAPER", "quantity": 2}])
        self.assertEqual(self.app.purchase_changes("A 1")[0]["reference"], "A 1")
        # Leading/trailing whitespace is trimmed before matching; inner
        # whitespace and letter case are part of the identity.
        self.assertEqual(self.app.purchase_changes("  A 1  ")[0]["reference"], "A 1")
        for bad in ("A1", "a 1", "A  1"):
            with self.assertRaises(ValueError):
                self.app.purchase_changes(bad)

    def test_row_count_order_and_refreshed_snapshot_all_recorded_once(self):
        self.app.create_purchase("M1", "甲", [
            {"code": "PAPER", "quantity": 1}, {"code": "INK", "quantity": 2},
        ])
        # Quantity + supplier + row reorder in one call: a single record.
        self.app.update_purchase("M1", "乙", [
            {"code": "INK", "quantity": 4}, {"code": "PAPER", "quantity": 1},
        ])
        changes = self.app.purchase_changes("M1")
        self.assertEqual(len(changes), 1)
        self.assertEqual([r["code"] for r in changes[0]["before"]["rows"]], ["PAPER", "INK"])
        self.assertEqual([r["code"] for r in changes[0]["after"]["rows"]], ["INK", "PAPER"])
        self.assertEqual(changes[0]["before"]["supplier"], "甲")
        self.assertEqual(changes[0]["after"]["supplier"], "乙")
        self.assertEqual(changes[0]["after"]["rows"][0]["quantity"], 4)
        # A material rename refreshes the name snapshot on an identical re-submission.
        self.app.update_material("PAPER", "复印纸", "包")
        self.app.update_purchase("M1", "乙", [
            {"code": "INK", "quantity": 4}, {"code": "PAPER", "quantity": 1},
        ])
        changes = self.app.purchase_changes("M1")
        self.assertEqual(len(changes), 2)
        self.assertEqual(changes[1]["before"]["rows"][1]["name"], "纸张")
        self.assertEqual(changes[1]["after"]["rows"][1]["name"], "复印纸")
        # Unit snapshot refresh counts too.
        self.app.register("TAPE", "胶带", "卷")
        self.app.create_purchase("M2", "甲", [{"code": "TAPE", "quantity": 1}])
        self.app.update_material("TAPE", "胶带", "筒")
        self.app.update_purchase("M2", "甲", [{"code": "TAPE", "quantity": 1}])
        unit_change = self.app.purchase_changes("M2")
        self.assertEqual(len(unit_change), 1)
        self.assertEqual(unit_change[0]["before"]["rows"][0]["unit"], "卷")
        self.assertEqual(unit_change[0]["after"]["rows"][0]["unit"], "筒")

    def test_merge_records_each_migrated_order_including_cancelled_and_received(self):
        self.app.create_purchase("R1", "甲", [{"code": "PAPER", "quantity": 5}])
        self.app.receive_purchase("R1", [{"code": "PAPER", "quantity": 5, "reference": "RCV1"}])
        self.app.return_purchase("RCV1", 2, "RET1")
        self.app.create_purchase("R2", "甲", [{"code": "INK", "quantity": 3}])
        self.app.cancel_purchase("R2")
        self.app.create_purchase("T1", "乙", [{"code": "INK", "quantity": 1}])
        self.app.merge_supplier("甲", "乙")
        r1 = StockRoom(self.root).purchase_changes("R1")
        self.assertEqual([c["action"] for c in r1], ["merge_supplier"])
        self.assertEqual(r1[0]["before"]["supplier"], "甲")
        self.assertEqual(r1[0]["after"]["supplier"], "乙")
        r2 = StockRoom(self.root).purchase_changes("R2")
        self.assertEqual([(c["sequence"], c["action"]) for c in r2], [(1, "cancel_purchase"), (2, "merge_supplier")])
        self.assertEqual(r2[1]["before"]["status"], "cancelled")
        # Target's pre-existing order is not recorded.
        self.assertEqual(StockRoom(self.root).purchase_changes("T1"), [])

    def test_create_import_receive_return_save_supplier_add_no_records(self):
        self.app.import_purchases_csv("reference,supplier,code,quantity\nI1,甲,PAPER,3\n")
        self.app.receive_purchase("I1", [{"code": "PAPER", "quantity": 2, "reference": "RCV2"}])
        self.app.return_purchase("RCV2", 1, "RET2")
        self.app.save_supplier("甲", "联系人", "9", "x")
        self.assertEqual(self.app.purchase_changes("I1"), [])
        self.assertEqual(self.app.purchase_changes("P1"), [])

    def test_failed_modifications_append_nothing_and_keep_bytes(self):
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.update_purchase("UNKNOWN", "甲", [{"code": "PAPER", "quantity": 1}])
        with self.assertRaises(ValueError):
            self.app.update_purchase("P1", "甲", [{"code": "GHOST", "quantity": 1}])
        with self.assertRaises(ValueError):
            self.app.cancel_purchase("UNKNOWN")
        with self.assertRaises(ValueError):
            self.app.merge_supplier("幽灵", "乙")
        self.assertEqual(self.app.path.read_bytes(), before)
        self.assertEqual(self.app.purchase_changes("P1"), [])

    def test_query_success_and_failure_do_not_modify_file_and_no_file_created(self):
        self.app.update_purchase("P1", "甲", [{"code": "PAPER", "quantity": 8}])
        before = self.app.path.read_bytes()
        self.app.purchase_changes("P1")
        with self.assertRaises(ValueError):
            self.app.purchase_changes("UNKNOWN")
        self.assertEqual(before, self.app.path.read_bytes())
        empty = Path(self.temp.name) / "empty"
        empty_app = StockRoom(empty)
        with self.assertRaises(ValueError):
            empty_app.purchase_changes("P1")
        with self.assertRaises(ValueError):
            empty_app.purchase_changes(11)
        self.assertFalse((empty / "data.json").exists())

    def test_legacy_data_without_history_treated_as_empty_first_before_is_actual(self):
        legacy = Path(self.temp.name) / "legacy"
        legacy.mkdir()
        payload = {"materials": {"PAPER": {"code": "PAPER", "name": "纸张", "unit": "包"}},
                   "purchases": [{
            "reference": "L1", "supplier": "s", "status": "open",
            "rows": [{"code": "PAPER", "name": "纸张", "unit": "包", "quantity": 7}],
        }]}
        (legacy / "data.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        app = StockRoom(legacy)
        self.assertEqual(app.purchase_changes("L1"), [])
        app.update_purchase("L1", "s", [{"code": "PAPER", "quantity": 6}])
        changes = StockRoom(legacy).purchase_changes("L1")
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["sequence"], 1)
        self.assertEqual(changes[0]["before"]["rows"][0]["quantity"], 7)
        self.assertEqual(changes[0]["after"]["rows"][0]["quantity"], 6)

    def test_returned_snapshots_are_independent_copies(self):
        self.app.update_purchase("P1", "甲", [{"code": "PAPER", "quantity": 8}])
        changes = self.app.purchase_changes("P1")
        changes[0]["action"] = "tampered"
        changes[0]["after"]["supplier"] = "ZZ"
        changes[0]["after"]["rows"][0]["quantity"] = 999
        fresh = self.app.purchase_changes("P1")
        self.assertEqual(fresh[0]["action"], "update_purchase")
        self.assertEqual(fresh[0]["after"]["supplier"], "甲")
        self.assertEqual(fresh[0]["after"]["rows"][0]["quantity"], 8)

    def test_later_business_does_not_rewrite_saved_history(self):
        self.app.update_purchase("P1", "甲", [{"code": "PAPER", "quantity": 8}])
        first = json.dumps(self.app.purchase_changes("P1"), ensure_ascii=False)
        self.app.cancel_purchase("P1")
        self.app.merge_supplier("甲", "乙")
        history = self.app.purchase_changes("P1")
        self.assertEqual(json.dumps(history[:1], ensure_ascii=False), first)
        self.assertEqual(history[1]["after"], history[2]["before"])

    def test_cli_query_success_failure_and_array_processing(self):
        self.app.update_purchase("P1", "甲", [{"code": "PAPER", "quantity": 8}])
        query = self.root / "query.json"
        query.write_text(json.dumps({"reference": "P1"}), encoding="utf-8")
        ok = subprocess.run([sys.executable, "-m", "stock_room", "--root", str(self.root), "purchase-changes", str(query)],
                            text=True, capture_output=True)
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertEqual(len(json.loads(ok.stdout)), 1)
        bad = self.root / "bad.json"
        bad.write_text(json.dumps({"reference": "UNKNOWN"}), encoding="utf-8")
        failed = subprocess.run([sys.executable, "-m", "stock_room", "--root", str(self.root), "purchase-changes", str(bad)],
                                text=True, capture_output=True)
        self.assertEqual(failed.returncode, 2)
        self.assertIn("error", json.loads(failed.stderr))
        # JSON array: items handled independently.
        batch = self.root / "batch.json"
        batch.write_text(json.dumps([{"reference": "P1"}, {"reference": "UNKNOWN"}]), encoding="utf-8")
        result = subprocess.run([sys.executable, "-m", "stock_room", "--root", str(self.root), "purchase-changes", str(batch)],
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("error", json.loads(result.stderr))


class AdjustPurchaseQuantitiesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = StockRoom(self.root)
        self.app.register("PAPER", "包装纸", "张")
        self.app.register("BOX", "纸箱", "个")
        self.app.create_purchase("P1", "甲", [
            {"code": "PAPER", "quantity": 10},
            {"code": "BOX", "quantity": 5},
        ])

    def run_cli(self, *args):
        return subprocess.run([sys.executable, "-m", "stock_room", "--root", str(self.root), *args],
                              text=True, capture_output=True)

    def write_payload(self, name, body):
        payload = self.root / name
        payload.write_text(json.dumps(body), encoding="utf-8")
        return str(payload)

    def order(self, paper=10, box=5):
        return {
            "reference": "P1",
            "supplier": "甲",
            "status": "open",
            "rows": [
                {"code": "PAPER", "name": "包装纸", "unit": "张", "quantity": paper},
                {"code": "BOX", "name": "纸箱", "unit": "个", "quantity": box},
            ],
        }

    def test_partial_adjust_returns_full_order_and_preserves_everything_else(self):
        result = self.app.adjust_purchase_quantities(" P1 ", [{"code": " PAPER ", "quantity": 8}])
        self.assertEqual(result, self.order(paper=8))
        reopened = StockRoom(self.root)
        self.assertEqual(reopened.purchase_order("P1"), self.order(paper=8))
        # 库存、收退货记录与业务编号不受影响
        self.assertEqual(reopened.stock("PAPER")["quantity"], 0)
        self.assertEqual(reopened.purchase_receipts("P1"), [])
        self.assertEqual(reopened.purchase_returns("P1"), [])

    def test_fixed_scenario_receive_return_adjust_then_atomic_failure(self):
        self.app.receive_purchase("P1", [{"code": "PAPER", "quantity": 6, "reference": "RCV1"}])
        self.app.return_purchase("RCV1", 2, "RET1")
        adjusted = self.app.adjust_purchase_quantities("P1", [{"code": "PAPER", "quantity": 8}])
        self.assertEqual(adjusted, self.order(paper=8))
        progress = StockRoom(self.root).purchase_progress("P1")
        paper = progress["rows"][0]
        self.assertEqual((paper["quantity"], paper["received"], paper["returned"],
                          paper["net_received"], paper["remaining"]), (8, 6, 2, 4, 2))
        history = self.app.purchase_changes("P1")
        self.assertEqual(len(history), 1)
        before_bytes = self.app.path.read_bytes()
        # 同次请求 BOX 调为三个、PAPER 调为五张：PAPER 低于累计收货六，整次失败
        with self.assertRaises(ValueError):
            self.app.adjust_purchase_quantities("P1", [
                {"code": "BOX", "quantity": 3},
                {"code": "PAPER", "quantity": 5},
            ])
        self.assertEqual(self.app.path.read_bytes(), before_bytes)
        self.assertEqual(self.app.purchase_order("P1"), self.order(paper=8))
        self.assertEqual(self.app.purchase_changes("P1"), history)

    def test_received_floor_ignores_returns_movements_counts_and_reversals(self):
        self.app.receive_purchase("P1", [{"code": "PAPER", "quantity": 6, "reference": "RCV1"}])
        self.app.return_purchase("RCV1", 2, "RET1")
        self.app.movement("PAPER", -3, "OUT-1")
        self.app.count("PAPER", 1, "CNT-1")
        self.app.reverse("OUT-1", "REV-1")
        # 退货不恢复下限：累计收货六仍是下限
        with self.assertRaises(ValueError):
            self.app.adjust_purchase_quantities("P1", [{"code": "PAPER", "quantity": 5}])
        adjusted = self.app.adjust_purchase_quantities("P1", [{"code": "PAPER", "quantity": 6}])
        self.assertEqual(adjusted["rows"][0]["quantity"], 6)
        # 未收货的 BOX 下限为零，可任意调整
        self.assertEqual(self.app.adjust_purchase_quantities("P1", [{"code": "BOX", "quantity": 1}])["rows"][1]["quantity"], 1)

    def test_unreceived_order_adjustable_and_missing_receipts_count_as_zero(self):
        legacy = Path(self.temp.name) / "legacy"
        legacy.mkdir()
        payload = {
            "materials": {"PAPER": {"code": "PAPER", "name": "包装纸", "unit": "张"}},
            "purchases": [{
                "reference": "L1", "supplier": "s", "status": "open",
                "rows": [{"code": "PAPER", "name": "包装纸", "unit": "张", "quantity": 7}],
            }],
        }
        (legacy / "data.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        app = StockRoom(legacy)
        adjusted = app.adjust_purchase_quantities("L1", [{"code": "PAPER", "quantity": 3}])
        self.assertEqual(adjusted["rows"][0]["quantity"], 3)
        changes = StockRoom(legacy).purchase_changes("L1")
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["sequence"], 1)
        self.assertEqual(changes[0]["action"], "adjust_purchase_quantities")
        self.assertEqual(changes[0]["before"]["rows"][0]["quantity"], 7)
        self.assertEqual(changes[0]["after"]["rows"][0]["quantity"], 3)

    def test_inactive_or_updated_material_does_not_block_adjustment(self):
        self.app.set_active("BOX", False)
        self.app.update_material("PAPER", "高级包装纸", "张")
        result = self.app.adjust_purchase_quantities("P1", [
            {"code": "PAPER", "quantity": 12},
            {"code": "BOX", "quantity": 3},
        ])
        # 名称与单位快照保持原样
        self.assertEqual(result, self.order(paper=12, box=3))
        # 后续收货仍按既有状态与单位校验
        with self.assertRaises(ValueError):
            self.app.receive_purchase("P1", [{"code": "BOX", "quantity": 1, "reference": "RCV2"}])
        received = self.app.receive_purchase("P1", [{"code": "PAPER", "quantity": 12, "reference": "RCV3"}])
        self.assertEqual(received[0]["quantity"], 12)

    def test_new_quantity_becomes_receipt_limit_after_reopen(self):
        self.app.adjust_purchase_quantities("P1", [{"code": "PAPER", "quantity": 4}])
        reopened = StockRoom(self.root)
        with self.assertRaises(ValueError):
            reopened.receive_purchase("P1", [{"code": "PAPER", "quantity": 5, "reference": "RCV1"}])
        reopened.receive_purchase("P1", [{"code": "PAPER", "quantity": 4, "reference": "RCV1"}])
        self.assertEqual(reopened.purchase_progress("P1")["rows"][0]["remaining"], 0)

    def test_adjustment_visible_in_progress_list_supplier_csv_and_replenishment(self):
        self.app.set_minimum("PAPER", 20)
        self.app.adjust_purchase_quantities("P1", [{"code": "PAPER", "quantity": 15}])
        reopened = StockRoom(self.root)
        self.assertEqual(reopened.purchase_progress("P1")["rows"][0]["quantity"], 15)
        listed = reopened.purchase_orders(supplier="甲")
        self.assertEqual(listed[0]["rows"][0]["quantity"], 15)
        record = reopened.supplier_record("甲")
        self.assertEqual(record["purchases"][0]["rows"][0]["quantity"], 15)
        exported = reopened.export_purchases_csv()
        self.assertIn("P1,甲,open,pending,PAPER,包装纸,张,15,0,0,0,15", exported)
        plan = reopened.replenishment_plan()
        self.assertEqual(plan[0]["incoming"], 15)
        self.assertEqual(plan[0]["purchases"][0]["remaining"], 15)

    def test_repeat_submission_succeeds_without_write_or_history(self):
        first = self.app.adjust_purchase_quantities("P1", [{"code": "PAPER", "quantity": 8}])
        before = self.app.path.read_bytes()
        again = self.app.adjust_purchase_quantities("P1", [{"code": "PAPER", "quantity": 8}])
        self.assertEqual(again, first)
        self.assertEqual(self.app.path.read_bytes(), before)
        changes = self.app.purchase_changes("P1")
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["action"], "adjust_purchase_quantities")
        self.assertEqual(changes[0]["before"], self.order())
        self.assertEqual(changes[0]["after"], self.order(paper=8))

    def test_history_shares_sequence_and_full_snapshots_with_other_actions(self):
        self.app.adjust_purchase_quantities("P1", [{"code": "BOX", "quantity": 9}])
        self.app.cancel_purchase("P1")
        changes = StockRoom(self.root).purchase_changes("P1")
        self.assertEqual([c["sequence"] for c in changes], [1, 2])
        self.assertEqual([c["action"] for c in changes], ["adjust_purchase_quantities", "cancel_purchase"])
        self.assertEqual(changes[0]["before"], self.order())
        self.assertEqual(changes[0]["after"], self.order(box=9))
        self.assertEqual(changes[1]["before"], changes[0]["after"])
        self.assertEqual(changes[1]["after"]["status"], "cancelled")
        self.assertEqual(changes[1]["after"]["rows"], self.order(box=9)["rows"])

    def test_validation_failures_preserve_file_bytes(self):
        self.app.receive_purchase("P1", [{"code": "PAPER", "quantity": 6, "reference": "RCV1"}])
        before = self.app.path.read_bytes()
        bad_calls = [
            ("", [{"code": "PAPER", "quantity": 8}]),
            ("   ", [{"code": "PAPER", "quantity": 8}]),
            (11, [{"code": "PAPER", "quantity": 8}]),
            (None, [{"code": "PAPER", "quantity": 8}]),
            ("UNKNOWN", [{"code": "PAPER", "quantity": 8}]),
            ("p1", [{"code": "PAPER", "quantity": 8}]),
            ("P1", []),
            ("P1", "PAPER"),
            ("P1", None),
            ("P1", ["PAPER"]),
            ("P1", [{"code": "PAPER"}]),
            ("P1", [{"quantity": 8}]),
            ("P1", [{"code": "PAPER", "quantity": 8, "name": "x"}]),
            ("P1", [{"code": "  ", "quantity": 8}]),
            ("P1", [{"code": 11, "quantity": 8}]),
            ("P1", [{"code": "GHOST", "quantity": 8}]),
            ("P1", [{"code": "PAPER", "quantity": True}]),
            ("P1", [{"code": "PAPER", "quantity": 0}]),
            ("P1", [{"code": "PAPER", "quantity": -1}]),
            ("P1", [{"code": "PAPER", "quantity": 1.5}]),
            ("P1", [{"code": "PAPER", "quantity": "8"}]),
            ("P1", [{"code": "PAPER", "quantity": 8}, {"code": " PAPER ", "quantity": 9}]),
            ("P1", [{"code": "PAPER", "quantity": 5}]),
        ]
        for reference, rows in bad_calls:
            with self.subTest(reference=reference, rows=rows):
                with self.assertRaises(ValueError):
                    self.app.adjust_purchase_quantities(reference, rows)
        self.assertEqual(self.app.path.read_bytes(), before)
        self.assertEqual(self.app.purchase_changes("P1"), [])

    def test_cancelled_purchase_rejected(self):
        self.app.cancel_purchase("P1")
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.adjust_purchase_quantities("P1", [{"code": "PAPER", "quantity": 8}])
        self.assertEqual(self.app.path.read_bytes(), before)

    def test_failure_creates_no_file(self):
        empty = self.root / "empty"
        app = StockRoom(empty)
        with self.assertRaises(ValueError):
            app.adjust_purchase_quantities("P1", [{"code": "PAPER", "quantity": 8}])
        self.assertFalse((empty / "data.json").exists())

    def test_adjustment_does_not_change_stock_or_records(self):
        self.app.receive_purchase("P1", [{"code": "PAPER", "quantity": 6, "reference": "RCV1"}])
        self.app.return_purchase("RCV1", 2, "RET1")
        movements_before = self.app.history("PAPER")
        stock_before = self.app.stock("PAPER")["quantity"]
        self.app.adjust_purchase_quantities("P1", [{"code": "PAPER", "quantity": 8}])
        reopened = StockRoom(self.root)
        self.assertEqual(reopened.stock("PAPER")["quantity"], stock_before)
        self.assertEqual(reopened.history("PAPER"), movements_before)
        self.assertEqual(len(reopened.purchase_receipts("P1")), 1)
        self.assertEqual(len(reopened.purchase_returns("P1")), 1)

    def test_cli_success_failure_and_idempotent_repeat(self):
        payload = self.write_payload("adjust.json", {
            "reference": "P1", "rows": [{"code": "PAPER", "quantity": 8}, {"code": "BOX", "quantity": 3}],
        })
        result = self.run_cli("adjust-purchase-quantities", payload)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), self.order(paper=8, box=3))
        before = self.app.path.read_bytes()
        result = self.run_cli("adjust-purchase-quantities", payload)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.app.path.read_bytes(), before)
        self.assertEqual(len(StockRoom(self.root).purchase_changes("P1")), 1)
        bad = self.write_payload("bad-adjust.json", {"reference": "P1", "rows": [{"code": "PAPER", "quantity": 0}]})
        result = self.run_cli("adjust-purchase-quantities", bad)
        self.assertEqual(result.returncode, 2)
        self.assertIn("error", json.loads(result.stderr))
        self.assertEqual(self.app.path.read_bytes(), before)

class ImportSuppliersCsvTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = StockRoom(self.root)
        self.app.register("PAPER", "包装纸", "张")
        self.app.create_purchase("PO-1", "北辰包装", [{"code": "PAPER", "quantity": 5}])
        self.app.save_supplier("北辰", contact="李", phone="001", note="老档")

    def profile(self, supplier, contact="", phone="", note=""):
        return {"supplier": supplier, "contact": contact, "phone": phone, "note": note}

    def test_fixed_sample_replace_create_and_history(self):
        result = self.app.import_suppliers_csv("supplier,contact,phone,note\n北辰,李,,老档\n北辰包装,王,002,新档\n")
        self.assertEqual(result, [
            self.profile("北辰", contact="李", note="老档"),
            self.profile("北辰包装", contact="王", phone="002", note="新档"),
        ])
        reopened = StockRoom(self.root)
        self.assertEqual(reopened.supplier_record("北辰")["phone"], "")
        self.assertEqual(reopened.supplier_record("北辰包装")["phone"], "002")
        self.assertEqual(reopened.supplier_changes("北辰"), [
            {"supplier": "北辰", "sequence": 1, "action": "save_supplier", "related_supplier": None,
             "before": None, "after": self.profile("北辰", contact="李", phone="001", note="老档")},
            {"supplier": "北辰", "sequence": 2, "action": "save_supplier", "related_supplier": None,
             "before": self.profile("北辰", contact="李", phone="001", note="老档"),
             "after": self.profile("北辰", contact="李", note="老档")},
        ])
        self.assertEqual(reopened.supplier_changes("北辰包装"), [
            {"supplier": "北辰包装", "sequence": 1, "action": "save_supplier", "related_supplier": None,
             "before": None, "after": self.profile("北辰包装", contact="王", phone="002", note="新档")},
        ])
        # The purchase keeps its own supplier snapshot and other business is intact.
        self.assertEqual(reopened.purchase_order("PO-1")["supplier"], "北辰包装")
        self.assertEqual(reopened.stock("PAPER")["quantity"], 0)

    def test_fixed_sample_whitespace_duplicate_rejects_whole_file(self):
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.import_suppliers_csv("supplier,contact,phone,note\n北辰,李,,老档\n 北辰 ,王,002,新档\n")
        self.assertEqual(before, self.app.path.read_bytes())
        self.assertEqual(self.app.supplier_record("北辰")["phone"], "001")
        self.assertEqual(len(self.app.supplier_changes("北辰")), 1)
        self.assertEqual(self.app.supplier_changes("北辰包装"), [])

    def test_bom_crlf_reordered_header_and_quoted_fields(self):
        content = "﻿note,supplier,phone,contact\r\n\"备\r\n注\"\"一\"\"\", 南商 , 003 , 钱 \r\n\r\n,东商,,\r\n"
        result = self.app.import_suppliers_csv(content)
        self.assertEqual(result, [
            self.profile("南商", contact="钱", phone="003", note="备\r\n注\"一\""),
            self.profile("东商"),
        ])
        reopened = StockRoom(self.root)
        self.assertEqual(reopened.supplier_record("南商")["note"], "备\r\n注\"一\"")
        self.assertEqual(reopened.supplier_record("东商"), {**self.profile("东商"), "purchases": []})

    def test_quoted_fields_may_contain_commas(self):
        result = self.app.import_suppliers_csv('supplier,contact,phone,note\n"西,商","联,系",004,"注,记"\n')
        self.assertEqual(result, [self.profile("西,商", contact="联,系", phone="004", note="注,记")])

    def test_header_only_returns_empty_without_writing(self):
        empty = Path(self.temp.name) / "empty"
        app = StockRoom(empty)
        self.assertEqual(app.import_suppliers_csv("supplier,contact,phone,note\n"), [])
        self.assertFalse((empty / "data.json").exists())
        before = self.app.path.read_bytes()
        self.assertEqual(self.app.import_suppliers_csv("note,phone,contact,supplier\r\n\r\n"), [])
        self.assertEqual(before, self.app.path.read_bytes())

    def test_invalid_content_and_header_rejected(self):
        for content in ("", "﻿", None, 11, ["supplier"], {"c": "x"}):
            with self.subTest(content=content):
                with self.assertRaises(ValueError):
                    self.app.import_suppliers_csv(content)
        for content in (
            "supplier,contact,phone\n",
            "supplier,contact,phone,note,extra\n",
            "supplier,contact,phone,supplier\n",
            "supplier,contact,phone\n甲,乙,1\n",
            " supplier,contact,phone,note\n",
            "supplier ,contact,phone,note\n",
            "SUPPLIER,contact,phone,note\n",
        ):
            with self.subTest(content=content):
                with self.assertRaises(ValueError):
                    self.app.import_suppliers_csv(content)

    def test_invalid_quotes_rejected(self):
        for content in (
            'supplier,contact,phone,note\n"unclosed,乙,1,注\n',
            'supplier,contact,phone,note\n甲"x,乙,1,注\n',
            'supplier,contact,phone,note\n"x"y,乙,1,注\n',
            'supplier,contact,phone,note\n"x" y,乙,1,注\n',
            'supplier,contact,phone,note\n "x",乙,1,注\n',
        ):
            with self.subTest(content=content):
                with self.assertRaises(ValueError):
                    self.app.import_suppliers_csv(content)

    def test_wrong_column_count_and_empty_name_rejected(self):
        for content in (
            "supplier,contact,phone,note\n甲,乙,1\n",
            "supplier,contact,phone,note\n甲,乙,1,注,多\n",
            "supplier,contact,phone,note\n,乙,1,注\n",
            "supplier,contact,phone,note\n  ,乙,1,注\n",
            "supplier,contact,phone,note\n,,,\n",
            "supplier,contact,phone,note\n   \n",
            "supplier,contact,phone,note\n甲,乙,1,注,\n",
        ):
            with self.subTest(content=content):
                with self.assertRaises(ValueError):
                    self.app.import_suppliers_csv(content)

    def test_duplicate_names_rejected_even_with_identical_data(self):
        for content in (
            "supplier,contact,phone,note\n甲,乙,1,注\n甲,乙,1,注\n",
            "supplier,contact,phone,note\n甲,乙,1,注\n甲,丙,2,注\n",
            "supplier,contact,phone,note\n甲,乙,1,注\n 甲 ,乙,1,注\n",
        ):
            with self.subTest(content=content):
                with self.assertRaises(ValueError):
                    self.app.import_suppliers_csv(content)

    def test_names_are_case_sensitive_and_inner_whitespace_kept(self):
        result = self.app.import_suppliers_csv("supplier,contact,phone,note\nABC,一,1,\nabc,二,2,\n北 辰,三,3,\n")
        self.assertEqual([row["supplier"] for row in result], ["ABC", "abc", "北 辰"])
        reopened = StockRoom(self.root)
        self.assertEqual(reopened.supplier_record("ABC")["contact"], "一")
        self.assertEqual(reopened.supplier_record("abc")["contact"], "二")
        self.assertEqual(reopened.supplier_record("北 辰")["contact"], "三")
        self.assertEqual(reopened.supplier_record("北辰")["phone"], "001")

    def test_unmentioned_names_keep_profiles_and_purchases(self):
        self.app.save_supplier("不动", contact="孙", phone="9", note="留")
        before_purchase = self.app.purchase_order("PO-1")
        self.app.import_suppliers_csv("supplier,contact,phone,note\n北辰,李,008,新\n")
        reopened = StockRoom(self.root)
        self.assertEqual(reopened.supplier_record("不动"), {**self.profile("不动", contact="孙", phone="9", note="留"), "purchases": []})
        self.assertEqual(reopened.purchase_order("PO-1"), before_purchase)
        self.assertEqual(reopened.supplier_changes("不动"), [
            {"supplier": "不动", "sequence": 1, "action": "save_supplier", "related_supplier": None,
             "before": None, "after": self.profile("不动", contact="孙", phone="9", note="留")},
        ])

    def test_repeat_import_adds_no_history_and_does_not_write(self):
        content = "supplier,contact,phone,note\n北辰,李,,老档\n北辰包装,王,002,新档\n"
        self.app.import_suppliers_csv(content)
        before = self.app.path.read_bytes()
        result = self.app.import_suppliers_csv(content)
        self.assertEqual(result, [
            self.profile("北辰", contact="李", note="老档"),
            self.profile("北辰包装", contact="王", phone="002", note="新档"),
        ])
        self.assertEqual(before, self.app.path.read_bytes())
        self.assertEqual(len(self.app.supplier_changes("北辰")), 2)
        self.assertEqual(len(self.app.supplier_changes("北辰包装")), 1)

    def test_partial_change_writes_once_and_records_only_changed(self):
        self.app.import_suppliers_csv("supplier,contact,phone,note\n北辰,李,001,老档\n新商,周,005,\n")
        self.assertEqual(len(self.app.supplier_changes("北辰")), 1)
        self.assertEqual(self.app.supplier_changes("新商"), [
            {"supplier": "新商", "sequence": 1, "action": "save_supplier", "related_supplier": None,
             "before": None, "after": self.profile("新商", contact="周", phone="005")},
        ])

    def test_failed_import_preserves_file_and_all_state(self):
        before = self.app.path.read_bytes()
        for content in (
            "supplier,contact,phone,note\n新商,周,005,\n新商,周,005,\n",
            "supplier,contact,phone,note\n新商,周,005\n",
            "supplier,contact,phone,note\n,周,005,\n",
            "supplier,contact,phone\n新商,周,005\n",
        ):
            with self.subTest(content=content):
                with self.assertRaises(ValueError):
                    self.app.import_suppliers_csv(content)
        with self.assertRaises(ValueError):
            self.app.import_suppliers_csv(11)
        self.assertEqual(before, self.app.path.read_bytes())
        self.assertEqual(self.app.supplier_record("北辰")["phone"], "001")
        self.assertEqual(len(self.app.supplier_changes("北辰")), 1)
        with self.assertRaises(ValueError):
            self.app.supplier_record("新商")

    def test_failed_import_on_empty_directory_creates_no_file(self):
        empty = Path(self.temp.name) / "empty"
        app = StockRoom(empty)
        with self.assertRaises(ValueError):
            app.import_suppliers_csv("supplier,contact,phone,note\n甲,乙\n")
        with self.assertRaises(ValueError):
            app.import_suppliers_csv("")
        self.assertFalse((empty / "data.json").exists())

    def test_legacy_profile_missing_fields_defaults_to_empty(self):
        legacy = Path(self.temp.name) / "legacy"
        legacy.mkdir()
        (legacy / "data.json").write_text(json.dumps(
            {"suppliers": {"旧商": {"supplier": "旧商", "contact": "赵"}}}, ensure_ascii=False), encoding="utf-8")
        app = StockRoom(legacy)
        # Import matching the normalized legacy profile: no change, no write.
        before = app.path.read_bytes()
        self.assertEqual(app.import_suppliers_csv("supplier,contact,phone,note\n旧商,赵,,\n"),
                         [self.profile("旧商", contact="赵")])
        self.assertEqual(app.path.read_bytes(), before)
        self.assertEqual(app.supplier_changes("旧商"), [])
        # First real change snapshots the actual legacy values as before.
        app.import_suppliers_csv("supplier,contact,phone,note\n旧商,赵,007,\n")
        self.assertEqual(app.supplier_changes("旧商"), [
            {"supplier": "旧商", "sequence": 1, "action": "save_supplier", "related_supplier": None,
             "before": self.profile("旧商", contact="赵"), "after": self.profile("旧商", contact="赵", phone="007")},
        ])

    def run_cli(self, *args):
        return subprocess.run([sys.executable, "-m", "stock_room", "--root", str(self.root), *args], text=True, capture_output=True)

    def test_cli_import_success_and_failure(self):
        payload = self.root / "import.json"
        payload.write_text(json.dumps({"content": "supplier,contact,phone,note\n北辰,李,,老档\n北辰包装,王,002,新档\n"}), encoding="utf-8")
        result = self.run_cli("import-suppliers-csv", str(payload))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([row["supplier"] for row in json.loads(result.stdout)], ["北辰", "北辰包装"])
        bad = self.root / "bad-import.json"
        bad.write_text(json.dumps({"content": "supplier,contact,phone,note\n新商,周,005,\n新商,周,005,\n"}), encoding="utf-8")
        failed = self.run_cli("import-suppliers-csv", str(bad))
        self.assertEqual(failed.returncode, 2)
        self.assertIn("error", json.loads(failed.stderr))
        with self.assertRaises(ValueError):
            self.app.supplier_record("新商")
        self.assertEqual(self.app.supplier_record("北辰")["phone"], "")

    def test_cli_import_array_keeps_earlier_success(self):
        payload = self.root / "imports.json"
        payload.write_text(json.dumps([
            {"content": "supplier,contact,phone,note\n新商,周,005,\n"},
            {"content": "supplier,contact,phone,note\n北辰,李,,老档\n 北辰 ,王,002,\n"},
        ]), encoding="utf-8")
        result = self.run_cli("import-suppliers-csv", str(payload))
        self.assertEqual(result.returncode, 2)
        self.assertIn("error", result.stderr)
        reopened = StockRoom(self.root)
        self.assertEqual(reopened.supplier_record("新商")["phone"], "005")
        self.assertEqual(reopened.supplier_record("北辰")["phone"], "001")


class SupplierOutstandingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = StockRoom(self.root)
        self.app.register("PAPER", "包装纸", "张")
        self.app.register("BOX", "纸箱", "个")

    def run_cli(self, *args):
        return subprocess.run([sys.executable, "-m", "stock_room", "--root", str(self.root), *args],
                              text=True, capture_output=True)

    def write_payload(self, name, body):
        payload = self.root / name
        payload.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
        return str(payload)

    def seed_fixed_sample(self):
        self.app.create_purchase("P1", "北辰", [{"code": "PAPER", "quantity": 10}])
        self.app.receive_purchase("P1", [{"code": "PAPER", "quantity": 6, "reference": "RCV-1"}])
        self.app.return_purchase("RCV-1", 2, "RET-1")
        self.app.create_purchase("P2", "北辰", [{"code": "PAPER", "quantity": 3}])
        self.app.create_purchase("P3", "北辰", [{"code": "PAPER", "quantity": 20}])
        self.app.cancel_purchase("P3")
        self.app.create_purchase("P4", "北辰包装", [{"code": "PAPER", "quantity": 100}])

    def test_fixed_sample_return_does_not_restore_and_cancelled_excluded(self):
        self.seed_fixed_sample()
        result = StockRoom(self.root).supplier_outstanding("  北辰  ")
        self.assertEqual(result, [{
            "code": "PAPER",
            "unit": "张",
            "remaining": 7,
            "purchases": [
                {"reference": "P1", "name": "包装纸", "remaining": 4},
                {"reference": "P2", "name": "包装纸", "remaining": 3},
            ],
        }])
        self.assertEqual(StockRoom(self.root).supplier_outstanding("北辰包装"), [{
            "code": "PAPER",
            "unit": "张",
            "remaining": 100,
            "purchases": [{"reference": "P4", "name": "包装纸", "remaining": 100}],
        }])

    def test_known_supplier_without_outstanding_returns_empty_list(self):
        self.app.save_supplier("仅档案", contact="李")
        self.assertEqual(self.app.supplier_outstanding("仅档案"), [])
        self.app.create_purchase("P1", "北辰", [{"code": "PAPER", "quantity": 5}])
        self.app.receive_purchase("P1", [{"code": "PAPER", "quantity": 5, "reference": "RCV-1"}])
        self.app.create_purchase("P2", "北辰", [{"code": "BOX", "quantity": 2}])
        self.app.cancel_purchase("P2")
        self.assertEqual(StockRoom(self.root).supplier_outstanding("北辰"), [])

    def test_unknown_and_history_only_names_rejected(self):
        with self.assertRaises(ValueError):
            self.app.supplier_outstanding("北辰")
        self.app.create_purchase("P1", "北辰", [{"code": "PAPER", "quantity": 5}])
        self.app.save_supplier("新名", contact="李")
        self.app.merge_supplier("北辰", "新名")
        with self.assertRaises(ValueError):
            self.app.supplier_outstanding("北辰")
        self.assertEqual(len(self.app.supplier_outstanding("新名")), 1)

    def test_invalid_arguments_rejected(self):
        self.app.create_purchase("P1", "北辰", [{"code": "PAPER", "quantity": 5}])
        for supplier in (None, 123, "", "   ", [], {}):
            with self.assertRaises(ValueError):
                self.app.supplier_outstanding(supplier)

    def test_exact_match_case_and_inner_whitespace(self):
        self.app.create_purchase("P1", "北 辰", [{"code": "PAPER", "quantity": 5}])
        self.assertEqual(len(self.app.supplier_outstanding(" 北 辰 ")), 1)
        for supplier in ("北辰", "北  辰", "北 辰包装"):
            with self.assertRaises(ValueError):
                self.app.supplier_outstanding(supplier)

    def test_grouping_by_code_and_unit_snapshot_sorted(self):
        self.app.create_purchase("P2", "北辰", [{"code": "PAPER", "quantity": 5}])
        self.app.update_material("PAPER", "包装纸", "包")
        self.app.create_purchase("P1", "北辰", [
            {"code": "PAPER", "quantity": 2},
            {"code": "BOX", "quantity": 4},
        ])
        result = self.app.supplier_outstanding("北辰")
        self.assertEqual([(item["code"], item["unit"], item["remaining"]) for item in result],
                         [("BOX", "个", 4), ("PAPER", "包", 2), ("PAPER", "张", 5)])
        self.assertEqual(result[2]["purchases"], [{"reference": "P2", "name": "包装纸", "remaining": 5}])

    def test_movements_counts_reversals_and_material_changes_do_not_affect(self):
        self.app.create_purchase("P1", "北辰", [{"code": "PAPER", "quantity": 10}])
        self.app.receive_purchase("P1", [{"code": "PAPER", "quantity": 6, "reference": "RCV-1"}])
        self.app.movement("PAPER", 5, "IN-1")
        self.app.movement("PAPER", -3, "OUT-1")
        self.app.count("PAPER", 8, "CNT-1")
        self.app.reverse("IN-1", "REV-1")
        self.app.update_material("PAPER", "新名称", "张")
        self.app.set_active("PAPER", False)
        result = StockRoom(self.root).supplier_outstanding("北辰")
        self.assertEqual(result, [{
            "code": "PAPER",
            "unit": "张",
            "remaining": 4,
            "purchases": [{"reference": "P1", "name": "包装纸", "remaining": 4}],
        }])

    def test_latest_saved_orders_after_adjust_update_and_merge(self):
        self.app.create_purchase("P1", "北辰", [{"code": "PAPER", "quantity": 10}])
        self.app.receive_purchase("P1", [{"code": "PAPER", "quantity": 6, "reference": "RCV-1"}])
        self.app.adjust_purchase_quantities("P1", [{"code": "PAPER", "quantity": 8}])
        self.assertEqual(self.app.supplier_outstanding("北辰")[0]["remaining"], 2)
        self.app.create_purchase("P2", "北辰", [{"code": "BOX", "quantity": 5}])
        self.app.update_purchase("P2", "北辰", [{"code": "BOX", "quantity": 7}])
        self.app.save_supplier("新名")
        self.app.merge_supplier("北辰", "新名")
        result = StockRoom(self.root).supplier_outstanding("新名")
        self.assertEqual([(item["code"], item["remaining"]) for item in result],
                         [("BOX", 7), ("PAPER", 2)])
        with self.assertRaises(ValueError):
            self.app.supplier_outstanding("北辰")

    def test_query_is_read_only_and_creates_no_file(self):
        self.seed_fixed_sample()
        before = self.app.path.read_bytes()
        self.app.supplier_outstanding("北辰")
        with self.assertRaises(ValueError):
            self.app.supplier_outstanding("未知")
        self.assertEqual(self.app.path.read_bytes(), before)
        empty = self.root / "empty"
        app = StockRoom(empty)
        with self.assertRaises(ValueError):
            app.supplier_outstanding("北辰")
        self.assertFalse((empty / "data.json").exists())

    def test_legacy_data_missing_purchases_or_receipts_treated_as_empty(self):
        self.app.save_supplier("旧商")
        data = json.loads(self.app.path.read_text(encoding="utf-8"))
        data.pop("purchases", None)
        self.app.path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        self.assertEqual(StockRoom(self.root).supplier_outstanding("旧商"), [])
        self.app.create_purchase("P1", "旧商", [{"code": "PAPER", "quantity": 5}])
        self.app.receive_purchase("P1", [{"code": "PAPER", "quantity": 2, "reference": "RCV-1"}])
        data = json.loads(self.app.path.read_text(encoding="utf-8"))
        data.pop("purchase_receipts", None)
        self.app.path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        result = StockRoom(self.root).supplier_outstanding("旧商")
        self.assertEqual(result[0]["remaining"], 5)
        self.assertEqual(result[0]["purchases"][0]["remaining"], 5)

    def test_cli_object_and_array_success_and_failure(self):
        self.seed_fixed_sample()
        payload = self.write_payload("outstanding.json", {"supplier": "北辰"})
        result = self.run_cli("supplier-outstanding", payload)
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual(value, [{
            "code": "PAPER",
            "unit": "张",
            "remaining": 7,
            "purchases": [
                {"reference": "P1", "name": "包装纸", "remaining": 4},
                {"reference": "P2", "name": "包装纸", "remaining": 3},
            ],
        }])
        array_payload = self.write_payload("outstanding-array.json", [
            {"supplier": "北辰"}, {"supplier": "北辰包装"},
        ])
        result = self.run_cli("supplier-outstanding", array_payload)
        self.assertEqual(result.returncode, 0, result.stderr)
        values = json.loads(result.stdout)
        self.assertEqual([item[0]["remaining"] for item in values], [7, 100])
        bad = self.write_payload("bad.json", {"supplier": "未知"})
        before = self.app.path.read_bytes()
        failed = self.run_cli("supplier-outstanding", bad)
        self.assertEqual(failed.returncode, 2)
        self.assertIn("error", json.loads(failed.stderr))
        self.assertEqual(self.app.path.read_bytes(), before)


class SupplierLedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = StockRoom(self.root)
        self.app.register("PAPER", "包装纸", "张")
        self.app.register("BOX", "纸箱", "个")

    def run_cli(self, *args):
        return subprocess.run([sys.executable, "-m", "stock_room", "--root", str(self.root), *args],
                              text=True, capture_output=True)

    def write_payload(self, name, body):
        payload = self.root / name
        payload.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
        return str(payload)

    def seed_fixed_sample(self):
        self.app.create_purchase("P2", "甲", [{"code": "PAPER", "quantity": 10}])
        self.app.create_purchase("P1", "甲", [{"code": "PAPER", "quantity": 10}])
        self.app.receive_purchase("P2", [{"code": "PAPER", "quantity": 6, "reference": "RCV-2"}])
        self.app.receive_purchase("P1", [{"code": "PAPER", "quantity": 3, "reference": "RCV-1"}])
        self.app.return_purchase("RCV-2", 2, "RET-1")

    def test_fixed_sample_quantities_and_net_received(self):
        self.seed_fixed_sample()
        expected = [
            {"code": "PAPER", "name": "包装纸", "unit": "张", "quantity": 6, "reference": "RCV-2",
             "kind": "purchase_receipt", "purchase_reference": "P2", "related_reference": None, "net_received": 6},
            {"code": "PAPER", "name": "包装纸", "unit": "张", "quantity": 3, "reference": "RCV-1",
             "kind": "purchase_receipt", "purchase_reference": "P1", "related_reference": None, "net_received": 9},
            {"code": "PAPER", "name": "包装纸", "unit": "张", "quantity": -2, "reference": "RET-1",
             "kind": "purchase_return", "purchase_reference": "P2", "related_reference": "RCV-2", "net_received": 7},
        ]
        self.assertEqual(self.app.supplier_ledger("甲"), expected)
        self.assertEqual(StockRoom(self.root).supplier_ledger(" 甲 "), expected)

    def test_kind_filter_hides_rows_without_recomputing_net(self):
        self.seed_fixed_sample()
        rows = self.app.supplier_ledger("甲", kind="purchase_return")
        self.assertEqual([(row["reference"], row["quantity"], row["net_received"]) for row in rows],
                         [("RET-1", -2, 7)])
        rows = self.app.supplier_ledger("甲", kind="purchase_receipt")
        self.assertEqual([(row["reference"], row["net_received"]) for row in rows],
                         [("RCV-2", 6), ("RCV-1", 9)])
        self.assertEqual(self.app.supplier_ledger("甲"), self.app.supplier_ledger("甲", kind=None))

    def test_registration_order_not_purchase_reference_order(self):
        self.seed_fixed_sample()
        self.assertEqual([row["purchase_reference"] for row in self.app.supplier_ledger("甲")],
                         ["P2", "P1", "P2"])

    def test_invalid_arguments_and_unknown_names_rejected(self):
        self.seed_fixed_sample()
        for supplier in (None, 123, "", "   ", [], {}):
            with self.assertRaises(ValueError):
                self.app.supplier_ledger(supplier)
        for kind in ("", "receipt", "purchase", "movement", 1, True):
            with self.assertRaises(ValueError):
                self.app.supplier_ledger("甲", kind=kind)
        for name in ("未知", "甲包装", "甲  公司"):
            with self.assertRaises(ValueError):
                self.app.supplier_ledger(name)
        self.app.save_supplier("新名")
        self.app.merge_supplier("甲", "新名")
        with self.assertRaises(ValueError):
            self.app.supplier_ledger("甲")
        self.assertEqual(len(self.app.supplier_ledger("新名")), 3)

    def test_known_supplier_without_flows_returns_empty_list(self):
        self.app.save_supplier("仅档案", contact="李")
        self.assertEqual(self.app.supplier_ledger("仅档案"), [])
        self.app.create_purchase("P1", "甲", [{"code": "PAPER", "quantity": 5}])
        self.assertEqual(StockRoom(self.root).supplier_ledger("甲"), [])

    def test_other_flows_excluded_cancelled_and_inactive_included(self):
        self.app.create_purchase("P1", "甲", [{"code": "PAPER", "quantity": 10}])
        self.app.receive_purchase("P1", [{"code": "PAPER", "quantity": 6, "reference": "RCV-1"}])
        self.app.movement("PAPER", 5, "IN-1")
        self.app.movement("PAPER", -3, "OUT-1")
        self.app.count("PAPER", 8, "CNT-1")
        self.app.reverse("IN-1", "REV-1")
        self.app.create_purchase("P2", "甲", [{"code": "BOX", "quantity": 4}])
        self.app.receive_purchase("P2", [{"code": "BOX", "quantity": 4, "reference": "RCV-2"}])
        self.app.cancel_purchase("P2")
        self.app.set_active("BOX", False)
        self.app.update_material("PAPER", "新名称", "张")
        rows = StockRoom(self.root).supplier_ledger("甲")
        self.assertEqual([(row["reference"], row["kind"]) for row in rows],
                         [("RCV-1", "purchase_receipt"), ("RCV-2", "purchase_receipt")])
        self.assertEqual(rows[0]["name"], "包装纸")
        self.assertEqual(rows[1]["unit"], "个")

    def test_net_received_grouped_by_code_and_unit_snapshot(self):
        data = {
            "materials": {"PAPER": {"code": "PAPER", "name": "包装纸", "unit": "包"}},
            "purchases": [
                {"reference": "P1", "supplier": "甲", "status": "open",
                 "rows": [{"code": "PAPER", "name": "包装纸", "unit": "张", "quantity": 10}]},
                {"reference": "P2", "supplier": "甲", "status": "open",
                 "rows": [{"code": "PAPER", "name": "包装纸", "unit": "包", "quantity": 5}]},
            ],
            "purchase_receipts": {
                "P1": [{"code": "PAPER", "quantity": 6, "reference": "RCV-1", "balance": 6}],
                "P2": [{"code": "PAPER", "quantity": 2, "reference": "RCV-2", "balance": 8}],
            },
            "movements": [
                {"code": "PAPER", "quantity": 6, "reference": "RCV-1"},
                {"code": "PAPER", "quantity": 2, "reference": "RCV-2"},
            ],
        }
        self.app.path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        rows = StockRoom(self.root).supplier_ledger("甲")
        self.assertEqual([(row["unit"], row["net_received"]) for row in rows],
                         [("张", 6), ("包", 2)])

    def test_merge_moves_records_to_target_only(self):
        self.seed_fixed_sample()
        self.app.create_purchase("P9", "甲包装", [{"code": "BOX", "quantity": 3}])
        self.app.receive_purchase("P9", [{"code": "BOX", "quantity": 3, "reference": "RCV-9"}])
        self.app.save_supplier("乙")
        self.app.merge_supplier("甲", "乙")
        rows = StockRoom(self.root).supplier_ledger("乙")
        self.assertEqual([row["reference"] for row in rows], ["RCV-2", "RCV-1", "RET-1"])
        self.assertEqual([row["reference"] for row in self.app.supplier_ledger("甲包装")], ["RCV-9"])

    def test_query_is_read_only_and_creates_no_file(self):
        self.seed_fixed_sample()
        before = self.app.path.read_bytes()
        self.app.supplier_ledger("甲")
        self.app.supplier_ledger("甲", kind="purchase_return")
        with self.assertRaises(ValueError):
            self.app.supplier_ledger("未知")
        with self.assertRaises(ValueError):
            self.app.supplier_ledger("甲", kind="bad")
        self.assertEqual(self.app.path.read_bytes(), before)
        empty = self.root / "empty"
        app = StockRoom(empty)
        with self.assertRaises(ValueError):
            app.supplier_ledger("甲")
        self.assertFalse((empty / "data.json").exists())

    def test_legacy_data_missing_links_treated_as_no_records(self):
        self.app.create_purchase("P1", "甲", [{"code": "PAPER", "quantity": 5}])
        self.app.receive_purchase("P1", [{"code": "PAPER", "quantity": 2, "reference": "RCV-1"}])
        data = json.loads(self.app.path.read_text(encoding="utf-8"))
        data.pop("purchase_receipts", None)
        self.app.path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        self.assertEqual(StockRoom(self.root).supplier_ledger("甲"), [])
        data = json.loads(self.app.path.read_text(encoding="utf-8"))
        data.pop("purchases", None)
        data["suppliers"] = {"甲": {"supplier": "甲", "contact": "", "phone": "", "note": ""}}
        self.app.path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        self.assertEqual(StockRoom(self.root).supplier_ledger("甲"), [])

    def test_cli_object_and_array_success_and_failure(self):
        self.seed_fixed_sample()
        payload = self.write_payload("ledger.json", {"supplier": "甲"})
        result = self.run_cli("supplier-ledger", payload)
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual([(row["quantity"], row["net_received"]) for row in value],
                         [(6, 6), (3, 9), (-2, 7)])
        array_payload = self.write_payload("ledger-array.json", [
            {"supplier": "甲", "kind": "purchase_return"}, {"supplier": "甲"},
        ])
        result = self.run_cli("supplier-ledger", array_payload)
        self.assertEqual(result.returncode, 0, result.stderr)
        values = json.loads(result.stdout)
        self.assertEqual([len(rows) for rows in values], [1, 3])
        bad = self.write_payload("bad.json", {"supplier": "未知"})
        before = self.app.path.read_bytes()
        failed = self.run_cli("supplier-ledger", bad)
        self.assertEqual(failed.returncode, 2)
        self.assertIn("error", json.loads(failed.stderr))
        self.assertEqual(self.app.path.read_bytes(), before)


class LocationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = StockRoom(self.root)
        self.app.register("PAPER", "包装纸", "张")
        self.app.register("BOX", "纸箱", "个")

    def run_cli(self, *args):
        return subprocess.run([sys.executable, "-m", "stock_room", "--root", str(self.root), *args],
                              text=True, capture_output=True)

    def write_payload(self, name, body):
        payload = self.root / name
        payload.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
        return str(payload)

    def seed_fixed_sample(self):
        self.app.movement("PAPER", 12, "IN-1")

    def test_fixed_sample_assign_move_cancel(self):
        self.seed_fixed_sample()
        result = self.app.assign_locations([
            {"code": " PAPER ", "location": " A-01 "},
            {"code": "BOX", "location": "A-01"},
        ])
        self.assertEqual(result, [
            {"code": "PAPER", "before": "", "location": "A-01"},
            {"code": "BOX", "before": "", "location": "A-01"},
        ])
        self.assertEqual(self.app.assign_locations([{"code": "PAPER", "location": "B-02"}]),
                         [{"code": "PAPER", "before": "A-01", "location": "B-02"}])
        self.assertEqual(self.app.assign_locations([{"code": "BOX", "location": "  "}]),
                         [{"code": "BOX", "before": "A-01", "location": ""}])
        reopened = StockRoom(self.root)
        self.assertEqual(reopened.stock("PAPER")["quantity"], 12)
        self.assertEqual(reopened.stock("BOX")["quantity"], 0)
        self.assertEqual(reopened.location_changes("PAPER"), [
            {"code": "PAPER", "sequence": 1, "before": "", "after": "A-01"},
            {"code": "PAPER", "sequence": 2, "before": "A-01", "after": "B-02"},
        ])
        self.assertEqual(reopened.location_changes("BOX"), [
            {"code": "BOX", "sequence": 1, "before": "", "after": "A-01"},
            {"code": "BOX", "sequence": 2, "before": "A-01", "after": ""},
        ])
        self.assertEqual([item["code"] for item in reopened.location_inventory("A-01")], [])
        self.assertEqual([item["code"] for item in reopened.location_inventory("B-02")], ["PAPER"])
        self.assertEqual([item["code"] for item in reopened.location_inventory("")], ["BOX"])

    def test_repeat_assign_is_noop_and_writes_nothing(self):
        self.app.assign_locations([{"code": "PAPER", "location": "A-01"}])
        before = self.app.path.read_bytes()
        result = self.app.assign_locations([{"code": "PAPER", "location": " A-01 "}])
        self.assertEqual(result, [{"code": "PAPER", "before": "A-01", "location": "A-01"}])
        self.assertEqual(self.app.assign_locations([{"code": "BOX", "location": ""}]),
                         [{"code": "BOX", "before": "", "location": ""}])
        self.assertEqual(self.app.path.read_bytes(), before)
        self.assertEqual(len(self.app.location_changes("PAPER")), 1)

    def test_mixed_batch_writes_only_actual_changes(self):
        self.app.assign_locations([{"code": "PAPER", "location": "A-01"}])
        result = self.app.assign_locations([
            {"code": "PAPER", "location": "A-01"},
            {"code": "BOX", "location": "A-01"},
        ])
        self.assertEqual([row["before"] for row in result], ["A-01", ""])
        self.assertEqual(len(self.app.location_changes("PAPER")), 1)
        self.assertEqual(len(self.app.location_changes("BOX")), 1)

    def test_invalid_batches_rejected_and_file_preserved(self):
        self.seed_fixed_sample()
        before = self.app.path.read_bytes()
        bad_rows = [
            None,
            [],
            "PAPER",
            [{"code": "PAPER"}],
            [{"code": "PAPER", "location": "A-01", "extra": 1}],
            ["PAPER"],
            [{"code": "", "location": "A-01"}],
            [{"code": "  ", "location": "A-01"}],
            [{"code": 1, "location": "A-01"}],
            [{"code": "PAPER", "location": 1}],
            [{"code": "PAPER", "location": None}],
            [{"code": "GHOST", "location": "A-01"}],
            [{"code": "PAPER", "location": "A-01"}, {"code": " PAPER", "location": "B-02"}],
        ]
        for rows in bad_rows:
            with self.assertRaises(ValueError, msg=repr(rows)):
                self.app.assign_locations(rows)
        self.assertEqual(self.app.path.read_bytes(), before)
        self.assertEqual([item["code"] for item in self.app.location_inventory("")], ["BOX", "PAPER"])

    def test_failure_creates_no_file(self):
        empty = self.root / "empty"
        app = StockRoom(empty)
        with self.assertRaises(ValueError):
            app.assign_locations([{"code": "PAPER", "location": "A-01"}])
        with self.assertRaises(ValueError):
            app.location_changes("PAPER")
        self.assertEqual(app.location_inventory("A-01"), [])
        self.assertFalse((empty / "data.json").exists())

    def test_location_inventory_fields_and_sorting(self):
        self.app.register("Ä", "特殊", "件")
        self.app.register("A-2", "二号", "件")
        self.app.movement("PAPER", 5, "IN-1")
        self.app.set_minimum("PAPER", 3)
        self.app.set_active("BOX", False)
        self.app.assign_locations([
            {"code": "PAPER", "location": "A-01"},
            {"code": "BOX", "location": "A-01"},
            {"code": "Ä", "location": "A-01"},
            {"code": "A-2", "location": "A-01"},
        ])
        items = self.app.location_inventory(" A-01 ")
        self.assertEqual([item["code"] for item in items], ["A-2", "BOX", "PAPER", "Ä"])
        paper = next(item for item in items if item["code"] == "PAPER")
        self.assertEqual(paper, {"code": "PAPER", "name": "包装纸", "unit": "张", "quantity": 5,
                                 "minimum": 3, "active": True, "location": "A-01"})
        box = next(item for item in items if item["code"] == "BOX")
        self.assertEqual(box["active"], False)
        self.assertEqual(box["quantity"], 0)
        self.assertEqual(self.app.location_inventory("a-01"), [])
        with self.assertRaises(ValueError):
            self.app.location_inventory(None)
        with self.assertRaises(ValueError):
            self.app.location_inventory(1)

    def test_location_changes_validation_and_empty_history(self):
        self.assertEqual(self.app.location_changes("PAPER"), [])
        for code in ("", "  ", None, 1, "GHOST"):
            with self.assertRaises(ValueError, msg=repr(code)):
                self.app.location_changes(code)
        self.app.set_active("BOX", False)
        self.assertEqual(self.app.location_changes("BOX"), [])

    def test_assignment_has_no_business_side_effects(self):
        self.seed_fixed_sample()
        self.app.assign_locations([{"code": "PAPER", "location": "A-01"}])
        reopened = StockRoom(self.root)
        self.assertEqual(reopened.history("PAPER"), [{"code": "PAPER", "quantity": 12, "reference": "IN-1"}])
        self.assertEqual(reopened.material_changes("PAPER"), [])
        self.assertEqual(reopened.stock("PAPER")["quantity"], 12)
        inventory = reopened.inventory()
        self.assertEqual([set(item) for item in inventory],
                         [{"code", "name", "unit", "quantity", "minimum", "active"}] * 2)
        self.assertEqual(reopened.export_inventory_csv().splitlines()[0], HEADER)
        # 归位历史不锁定单位变更（无流水之外的锁定来源；此处有流水故仍锁定）。
        self.app.register("EMPTY", "空料", "件")
        self.app.assign_locations([{"code": "EMPTY", "location": "Z-9"}])
        updated = self.app.update_material("EMPTY", "空料", "箱")
        self.assertEqual(updated["unit"], "箱")

    def test_queries_are_read_only(self):
        self.seed_fixed_sample()
        self.app.assign_locations([{"code": "PAPER", "location": "A-01"}])
        before = self.app.path.read_bytes()
        self.app.location_inventory("A-01")
        self.app.location_inventory("")
        self.app.location_changes("PAPER")
        with self.assertRaises(ValueError):
            self.app.location_inventory(None)
        with self.assertRaises(ValueError):
            self.app.location_changes("GHOST")
        self.assertEqual(self.app.path.read_bytes(), before)

    def test_legacy_data_treated_as_unassigned(self):
        self.seed_fixed_sample()
        data = json.loads(self.app.path.read_text(encoding="utf-8"))
        data.pop("locations", None)
        data.pop("location_changes", None)
        self.app.path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        reopened = StockRoom(self.root)
        self.assertEqual([item["code"] for item in reopened.location_inventory("")], ["BOX", "PAPER"])
        self.assertEqual(reopened.location_changes("PAPER"), [])
        result = reopened.assign_locations([{"code": "PAPER", "location": "A-01"}])
        self.assertEqual(result, [{"code": "PAPER", "before": "", "location": "A-01"}])
        self.assertEqual(reopened.location_changes("PAPER"),
                         [{"code": "PAPER", "sequence": 1, "before": "", "after": "A-01"}])

    def test_cli_object_and_array_success_and_failure(self):
        self.seed_fixed_sample()
        payload = self.write_payload("assign.json", {"rows": [
            {"code": "PAPER", "location": "A-01"},
            {"code": "BOX", "location": "A-01"},
        ]})
        result = self.run_cli("assign-locations", payload)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([row["location"] for row in json.loads(result.stdout)], ["A-01", "A-01"])
        query = self.write_payload("query.json", {"location": "A-01"})
        result = self.run_cli("location-inventory", query)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([item["code"] for item in json.loads(result.stdout)], ["BOX", "PAPER"])
        array_payload = self.write_payload("changes.json", [{"code": "PAPER"}, {"code": "BOX"}])
        result = self.run_cli("location-changes", array_payload)
        self.assertEqual(result.returncode, 0, result.stderr)
        values = json.loads(result.stdout)
        self.assertEqual([len(rows) for rows in values], [1, 1])
        bad = self.write_payload("bad.json", {"rows": [{"code": "GHOST", "location": "A-01"}]})
        before = self.app.path.read_bytes()
        failed = self.run_cli("assign-locations", bad)
        self.assertEqual(failed.returncode, 2)
        self.assertIn("error", json.loads(failed.stderr))
        self.assertEqual(self.app.path.read_bytes(), before)
