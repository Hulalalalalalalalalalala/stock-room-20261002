import csv
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from stock_room import StockRoom

HEADER = "code,name,unit,quantity,minimum,active\n"


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
        self.app.count("PAPER", 11, "CNT-001")
        self.app.reverse("OUT-001", "REV-OUT")
        self.expected = (
            HEADER
            + "BOX,纸箱,个,0,3,false\n"
            + "PAPER,包装纸,张,17,15,true\n"
        )

    def run_cli(self, *args):
        return subprocess.run([sys.executable, "-m", "stock_room", "--root", str(self.root), *args], text=True, capture_output=True)

    def test_fixed_sample_full_export_exact_text(self):
        self.assertEqual(self.app.stock("PAPER")["quantity"], 17)
        self.assertEqual(self.app.export_inventory_csv(), self.expected)

    def test_header_columns_and_order(self):
        rows = list(csv.reader(io.StringIO(self.app.export_inventory_csv())))
        self.assertEqual(rows[0], ["code", "name", "unit", "quantity", "minimum", "active"])
        self.assertEqual(self.app.export_inventory_csv().split("\n")[0], "code,name,unit,quantity,minimum,active")
        for row in rows[1:]:
            self.assertEqual(len(row), 6)

    def test_sorted_by_unicode_code_points(self):
        self.app.register("paper", "复印纸", "张")
        self.app.register("纸张", "A4纸", "张")
        rows = list(csv.reader(io.StringIO(self.app.export_inventory_csv())))
        self.assertEqual([row[0] for row in rows[1:]], ["BOX", "PAPER", "paper", "纸张"])

    def test_integers_have_no_grouping_separators(self):
        self.app.register("BIG", "大宗料", "批")
        self.app.movement("BIG", 1234567, "BIG-IN")
        self.app.set_minimum("BIG", 1000)
        text = self.app.export_inventory_csv()
        self.assertIn("BIG,大宗料,批,1234567,1000,true\n", text)
        self.assertNotIn("1,234,567", text)

    def test_status_uses_lowercase_true_and_false(self):
        text = self.app.export_inventory_csv()
        rows = list(csv.reader(io.StringIO(text)))
        self.assertEqual([row[5] for row in rows[1:]], ["false", "true"])
        self.assertNotIn("True", text)
        self.assertNotIn("False", text)

    def test_rename_shows_new_name_and_history_keeps_original(self):
        self.app.update_material("PAPER", "加厚包装纸", "张")
        text = self.app.export_inventory_csv()
        self.assertIn("PAPER,加厚包装纸,张,17,15,true\n", text)
        self.assertNotIn("PAPER,包装纸,", text)
        history = self.app.history("PAPER")
        self.assertEqual([row["reference"] for row in history], ["IN-001", "OUT-001", "CNT-001", "REV-OUT"])
        self.assertEqual([row["quantity"] for row in history], [20, -6, -3, 6])
        self.assertEqual([row["counted"] for row in self.app.counts("PAPER")], [11])
        self.assertEqual([row["quantity"] for row in self.app.reversals("PAPER")], [6])

    def test_escaping_round_trip_and_exact_layout(self):
        empty = Path(self.temp.name) / "escaping"
        app = StockRoom(empty)
        samples = [
            ("Q1", "强力\r\n胶\"水\"", "卷"),
            ("Q2", "胶,水", "瓶"),
            ("Q3", " 纸 箱 ", "个"),
            ("Q4", "包装纸", "张"),
        ]
        for code, name, unit in samples:
            app.register(code, name, unit)
        text = app.export_inventory_csv()
        self.assertFalse(text.startswith("\ufeff"))
        self.assertTrue(text.endswith("\n"))
        self.assertFalse(text.endswith("\n\n"))
        expected = (
            HEADER
            + "Q1,\"强力\r\n胶\"\"水\"\"\",卷,0,0,true\n"
            + "Q2,\"胶,水\",瓶,0,0,true\n"
            + "Q3,纸 箱,个,0,0,true\n"
            + "Q4,包装纸,张,0,0,true\n"
        )
        self.assertEqual(text, expected)
        rows = list(csv.reader(io.StringIO(text)))
        self.assertEqual(rows[0], ["code", "name", "unit", "quantity", "minimum", "active"])
        parsed = {row[0]: row for row in rows[1:]}
        self.assertEqual(parsed["Q1"][1], "强力\r\n胶\"水\"")
        self.assertEqual(parsed["Q1"][2], "卷")
        self.assertEqual(parsed["Q2"][1], "胶,水")
        self.assertEqual(parsed["Q3"][1], "纸 箱")
        self.assertEqual(parsed["Q4"][1], "包装纸")

    def test_keyword_is_stripped_and_matches_code_or_name_literally(self):
        self.assertEqual(self.app.export_inventory_csv(keyword="  纸  "), self.expected)
        self.assertEqual(self.app.export_inventory_csv(keyword="纸"), self.expected)
        self.assertEqual(self.app.export_inventory_csv(keyword="BOX"), HEADER + "BOX,纸箱,个,0,3,false\n")
        self.assertEqual(self.app.export_inventory_csv(keyword="包装"), HEADER + "PAPER,包装纸,张,17,15,true\n")
        for keyword in ("paper", "box", ".*", ".", "装 纸"):
            with self.subTest(keyword=keyword):
                self.assertEqual(self.app.export_inventory_csv(keyword=keyword), HEADER)

    def test_keyword_and_active_filters_combine(self):
        self.assertEqual(
            self.app.export_inventory_csv(keyword="纸", active=True),
            HEADER + "PAPER,包装纸,张,17,15,true\n",
        )
        self.assertEqual(
            self.app.export_inventory_csv(keyword="纸", active=False),
            HEADER + "BOX,纸箱,个,0,3,false\n",
        )
        self.assertEqual(
            self.app.export_inventory_csv(active=False),
            HEADER + "BOX,纸箱,个,0,3,false\n",
        )
        self.assertEqual(
            self.app.export_inventory_csv(active=True),
            HEADER + "PAPER,包装纸,张,17,15,true\n",
        )
        self.assertEqual(self.app.export_inventory_csv(keyword="包装", active=False), HEADER)

    def test_no_match_or_no_materials_returns_header_only(self):
        self.assertEqual(self.app.export_inventory_csv(keyword="不存在"), HEADER)
        empty = Path(self.temp.name) / "empty"
        app = StockRoom(empty)
        self.assertEqual(app.export_inventory_csv(), HEADER)
        self.assertEqual(app.export_inventory_csv(keyword="纸", active=True), HEADER)
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

    def test_export_is_read_only_and_repeatable(self):
        before = self.app.path.read_bytes()
        first = self.app.export_inventory_csv()
        second = self.app.export_inventory_csv()
        self.assertEqual(first, second)
        self.assertEqual(self.app.export_inventory_csv(keyword="纸", active=True), self.app.export_inventory_csv(keyword="纸", active=True))
        with self.assertRaises(ValueError):
            self.app.export_inventory_csv(keyword=11)
        with self.assertRaises(ValueError):
            self.app.export_inventory_csv(active=1)
        self.assertEqual(before, self.app.path.read_bytes())
        self.assertEqual(self.app.stock("PAPER")["quantity"], 17)
        self.assertEqual(self.app.stock("BOX")["quantity"], 0)
        self.assertEqual([row["reference"] for row in self.app.history("PAPER")], ["IN-001", "OUT-001", "CNT-001", "REV-OUT"])
        self.assertEqual(len(self.app.counts("PAPER")), 1)
        self.assertEqual(len(self.app.reversals("PAPER")), 1)
        self.assertEqual(self.app.material_status("BOX"), {"code": "BOX", "active": False})
        self.assertEqual([item["code"] for item in self.app.shortages()], ["BOX"])
        self.assertEqual(StockRoom(self.root).export_inventory_csv(), first)

    def test_failed_export_on_empty_directory_creates_no_file(self):
        empty = Path(self.temp.name) / "empty"
        app = StockRoom(empty)
        with self.assertRaises(ValueError):
            app.export_inventory_csv(keyword=11)
        with self.assertRaises(ValueError):
            app.export_inventory_csv(active=0)
        self.assertFalse((empty / "data.json").exists())

    def test_legacy_data_without_minimum_or_status_outputs_defaults(self):
        legacy = Path(self.temp.name) / "legacy"
        legacy.mkdir()
        document = {
            "materials": {
                "PAPER": {"code": "PAPER", "name": "包装纸", "unit": "张"},
                "BOX": {"code": "BOX", "name": "纸箱", "unit": "个"},
            },
            "movements": [{"code": "PAPER", "quantity": 5, "reference": "IN-1"}],
            "minimums": {"BOX": 3},
            "status": {"BOX": False},
        }
        (legacy / "data.json").write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
        expected = (
            HEADER
            + "BOX,纸箱,个,0,3,false\n"
            + "PAPER,包装纸,张,5,0,true\n"
        )
        self.assertEqual(StockRoom(legacy).export_inventory_csv(), expected)
        self.assertEqual(StockRoom(legacy).export_inventory_csv(), expected)

    def test_cli_export_success_outputs_json_string(self):
        before = self.app.path.read_bytes()
        result = self.run_cli("export-inventory-csv")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout), self.expected)
        self.assertIsInstance(json.loads(result.stdout), str)
        payload = self.root / "query.json"
        payload.write_text(json.dumps({"keyword": "纸", "active": True}), encoding="utf-8")
        result = self.run_cli("export-inventory-csv", str(payload))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), HEADER + "PAPER,包装纸,张,17,15,true\n")
        self.assertEqual(before, self.app.path.read_bytes())

    def test_cli_export_array_returns_strings_in_order(self):
        payload = self.root / "queries.json"
        payload.write_text(json.dumps([
            {"keyword": "纸", "active": True},
            {"active": False},
            {},
        ]), encoding="utf-8")
        result = self.run_cli("export-inventory-csv", str(payload))
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual(value, [
            HEADER + "PAPER,包装纸,张,17,15,true\n",
            HEADER + "BOX,纸箱,个,0,3,false\n",
            self.expected,
        ])

    def test_cli_export_invalid_filter_returns_2_without_writes(self):
        before = self.app.path.read_bytes()
        for payload_value in ({"keyword": 11}, {"active": "true"}, {"active": 1}, {"active": 0}):
            with self.subTest(payload=payload_value):
                payload = self.root / "bad.json"
                payload.write_text(json.dumps(payload_value), encoding="utf-8")
                result = self.run_cli("export-inventory-csv", str(payload))
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
                self.assertIn("error", json.loads(result.stderr))
        self.assertEqual(before, self.app.path.read_bytes())


if __name__ == "__main__":
    unittest.main()
