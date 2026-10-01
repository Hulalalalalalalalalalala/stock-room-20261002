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

    def test_count_sample_scenario(self):
        self.app.register("PAPER", "Paper", "sheet")
        self.app.movement("PAPER", 20, "IN-001")
        self.app.movement("PAPER", -6, "OUT-001")
        first = self.app.count("PAPER", 11, "COUNT-001")
        self.assertEqual(first, {"code": "PAPER", "reference": "COUNT-001",
                                 "before": 14, "counted": 11, "difference": -3})
        self.assertEqual(self.app.stock("PAPER")["quantity"], 11)
        second = self.app.count("PAPER", 11, "COUNT-002")
        self.assertEqual(second["difference"], 0)
        self.assertEqual(len(self.app.counts("PAPER")), 2)
        self.assertEqual(len(self.app.history("PAPER")), 3)
        self.app.movement("PAPER", 2, "IN-002")
        self.assertEqual(self.app.stock("PAPER")["quantity"], 13)
        reopened = StockRoom(self.root)
        self.assertEqual(reopened.counts("PAPER"), [first, second])

    def test_count_rejects_invalid_input_and_preserves_state(self):
        self.app.register("P", "Paper", "sheet")
        self.app.movement("P", 3, "IN")
        before = self.app.path.read_bytes()
        for counted in (-1, True, 1.5, "3", None):
            with self.assertRaises(ValueError):
                self.app.count("P", counted, "C-1")
        for code in ("", "  ", None, 3):
            with self.assertRaises(ValueError):
                self.app.count(code, 1, "C-1")
        for reference in ("", "  ", None, 3):
            with self.assertRaises(ValueError):
                self.app.count("P", 1, reference)
        with self.assertRaises(ValueError):
            self.app.count("UNKNOWN", 1, "C-1")
        with self.assertRaises(ValueError):
            self.app.count("P", 1, "IN")
        self.assertEqual(before, self.app.path.read_bytes())
        self.assertEqual(self.app.counts("P"), [])

    def test_zero_difference_count_blocks_reference(self):
        self.app.register("P", "Paper", "sheet")
        self.app.movement("P", 5, "IN")
        self.app.count("P", 5, "C-1")
        self.assertEqual(len(self.app.history("P")), 1)
        with self.assertRaises(ValueError):
            self.app.movement("P", 1, "C-1")
        with self.assertRaises(ValueError):
            self.app.count("P", 5, "IN")

    def test_counts_unknown_material_and_trimmed_code(self):
        self.app.register("P", "Paper", "sheet")
        self.app.count(" P ", 2, " C-1 ")
        self.assertEqual(self.app.counts("P")[0]["reference"], "C-1")
        with self.assertRaises(ValueError):
            self.app.counts("UNKNOWN")

    def test_cli_count_and_counts(self):
        self.app.register("PAPER", "Paper", "sheet")
        self.app.movement("PAPER", 20, "IN-001")
        self.app.movement("PAPER", -6, "OUT-001")
        payload = self.root / "count.json"
        payload.write_text(json.dumps([
            {"code": "PAPER", "counted": 11, "reference": "COUNT-001"},
            {"code": "PAPER", "counted": 11, "reference": "COUNT-002"},
        ]), encoding="utf-8")
        result = subprocess.run([sys.executable, "-m", "stock_room", "--root", str(self.root),
                                 "count", str(payload)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = json.loads(result.stdout)
        self.assertEqual([row["difference"] for row in rows], [-3, 0])
        query = self.root / "query.json"
        query.write_text(json.dumps({"code": "PAPER"}), encoding="utf-8")
        result = subprocess.run([sys.executable, "-m", "stock_room", "--root", str(self.root),
                                 "counts", str(query)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(json.loads(result.stdout)), 2)
        bad = self.root / "bad.json"
        bad.write_text(json.dumps({"code": "PAPER", "counted": -1, "reference": "COUNT-003"}), encoding="utf-8")
        failed = subprocess.run([sys.executable, "-m", "stock_room", "--root", str(self.root),
                                 "count", str(bad)], text=True, capture_output=True)
        self.assertEqual(failed.returncode, 2)
        self.assertIn("error", json.loads(failed.stderr))

    def test_cli_demo_and_invalid_action(self):
        result = subprocess.run([sys.executable, "-m", "stock_room", "--root", str(self.root), "demo"], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual(value["quantity"], 14)
        failed = subprocess.run([sys.executable, "-m", "stock_room", "--root", str(self.root), "not-an-action"], text=True, capture_output=True)
        self.assertEqual(failed.returncode, 2)

if __name__ == "__main__":
    unittest.main()
