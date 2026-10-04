from .storage import JsonStore, text, optional_text

class StockRoom(JsonStore):
    def register(self, code, name, unit):
        code, name, unit = text(code, "code"), text(name, "name"), text(unit, "unit")
        data = self._read()
        materials = data.setdefault("materials", {})
        if code in materials:
            raise ValueError("material already exists")
        materials[code] = {"code": code, "name": name, "unit": unit}
        self._write(data)
        return materials[code]

    def import_materials_csv(self, content):
        if not isinstance(content, str):
            raise ValueError("content must be a string")
        if content.startswith("\ufeff"):
            content = content[1:]
        if not content:
            raise ValueError("content must be a nonempty CSV document")
        rows = _parse_csv(content)
        if not rows:
            raise ValueError("content must be a nonempty CSV document")
        header = rows[0]
        if len(header) != 3 or set(header) != {"code", "name", "unit"}:
            raise ValueError("header must contain exactly the code, name and unit columns")
        positions = {name: header.index(name) for name in ("code", "name", "unit")}
        records = []
        seen = set()
        for row in rows[1:]:
            if not row:
                continue
            if len(row) != 3:
                raise ValueError("each record must have exactly three columns")
            fields = {}
            for name, index in positions.items():
                value = row[index].strip()
                if not value:
                    raise ValueError(name + " must be a nonempty string")
                fields[name] = value
            if fields["code"] in seen:
                raise ValueError("material already exists")
            seen.add(fields["code"])
            records.append(fields)
        if not records:
            return []
        data = self._read()
        materials = data.setdefault("materials", {})
        for fields in records:
            if fields["code"] in materials:
                raise ValueError("material already exists")
        for fields in records:
            materials[fields["code"]] = dict(fields)
        self._write(data)
        return [dict(fields) for fields in records]

    def import_counts_csv(self, content):
        if not isinstance(content, str):
            raise ValueError("content must be a string")
        if content.startswith("\ufeff"):
            content = content[1:]
        if not content:
            raise ValueError("content must be a nonempty CSV document")
        rows = _parse_csv(content)
        if not rows:
            raise ValueError("content must be a nonempty CSV document")
        header = rows[0]
        if len(header) != 3 or set(header) != {"code", "counted", "reference"}:
            raise ValueError("header must contain exactly the code, counted and reference columns")
        positions = {name: header.index(name) for name in ("code", "counted", "reference")}
        entries = []
        seen_codes = set()
        seen_references = set()
        for row in rows[1:]:
            if not row:
                continue
            if len(row) != 3:
                raise ValueError("each record must have exactly three columns")
            code = row[positions["code"]].strip()
            reference = row[positions["reference"]].strip()
            counted_text = row[positions["counted"]].strip()
            if not code:
                raise ValueError("code must be a nonempty string")
            if not reference:
                raise ValueError("reference must be a nonempty string")
            if not counted_text or any(char < "0" or char > "9" for char in counted_text):
                raise ValueError("counted must be a nonnegative integer")
            if code in seen_codes:
                raise ValueError("material already exists in batch")
            if reference in seen_references:
                raise ValueError("reference already exists")
            seen_codes.add(code)
            seen_references.add(reference)
            entries.append({"code": code, "reference": reference, "counted": int(counted_text)})
        if not entries:
            return []
        data = self._read()
        materials = data.get("materials", {})
        existing = data.get("movements", [])
        parsed = []
        for entry in entries:
            code = entry["code"]
            reference = entry["reference"]
            counted = entry["counted"]
            if code not in materials:
                raise ValueError("unknown material")
            self._require_unique_reference(data, reference)
            before = sum(row["quantity"] for row in existing if row["code"] == code)
            parsed.append({"code": code, "reference": reference, "before": before, "counted": counted, "difference": counted - before})
        counts = data.setdefault("counts", [])
        movements = data.setdefault("movements", [])
        for record in parsed:
            counts.append(dict(record))
            if record["difference"] != 0:
                movements.append({"code": record["code"], "quantity": record["difference"], "reference": record["reference"]})
        self._write(data)
        return [dict(record) for record in parsed]

    def import_movements_csv(self, content):
        if not isinstance(content, str):
            raise ValueError("content must be a string")
        if content.startswith("﻿"):
            content = content[1:]
        if not content:
            raise ValueError("content must be a nonempty CSV document")
        rows = _parse_csv(content)
        if not rows:
            raise ValueError("content must be a nonempty CSV document")
        header = rows[0]
        if len(header) != 3 or set(header) != {"code", "quantity", "reference"}:
            raise ValueError("header must contain exactly the code, quantity and reference columns")
        positions = {name: header.index(name) for name in ("code", "quantity", "reference")}
        entries = []
        seen_references = set()
        for row in rows[1:]:
            if not row:
                continue
            if len(row) != 3:
                raise ValueError("each record must have exactly three columns")
            code = row[positions["code"]].strip()
            reference = row[positions["reference"]].strip()
            quantity_text = row[positions["quantity"]].strip()
            if not code:
                raise ValueError("code must be a nonempty string")
            if not reference:
                raise ValueError("reference must be a nonempty string")
            digits = quantity_text[1:] if quantity_text.startswith("-") else quantity_text
            if not digits or any(char < "0" or char > "9" for char in digits):
                raise ValueError("quantity must be a nonzero integer with an optional leading minus sign")
            quantity = int(quantity_text)
            if quantity == 0:
                raise ValueError("quantity must be a nonzero integer")
            if reference in seen_references:
                raise ValueError("reference already exists")
            seen_references.add(reference)
            entries.append({"code": code, "quantity": quantity, "reference": reference})
        if not entries:
            return []
        data = self._read()
        materials = data.get("materials", {})
        status = data.get("status", {})
        existing = data.get("movements", [])
        balances = {}
        parsed = []
        for entry in entries:
            code = entry["code"]
            reference = entry["reference"]
            quantity = entry["quantity"]
            if code not in materials:
                raise ValueError("unknown material")
            if not status.get(code, True):
                raise ValueError("material is inactive")
            self._require_unique_reference(data, reference)
            if code not in balances:
                balances[code] = sum(row["quantity"] for row in existing if row["code"] == code)
            balances[code] += quantity
            if balances[code] < 0:
                raise ValueError("insufficient stock")
            parsed.append({"code": code, "quantity": quantity, "reference": reference, "balance": balances[code]})
        data.setdefault("movements", []).extend(
            {"code": row["code"], "quantity": row["quantity"], "reference": row["reference"]} for row in parsed
        )
        self._write(data)
        return parsed

    def update_material(self, code, name, unit):
        code, name, unit = text(code, "code"), text(name, "name"), text(unit, "unit")
        data = self._read()
        materials = data.get("materials", {})
        if code not in materials:
            raise ValueError("unknown material")
        if unit != materials[code]["unit"]:
            if any(row["code"] == code for row in data.get("movements", [])):
                raise ValueError("unit cannot change after stock history exists")
            if any(row["code"] == code for row in data.get("counts", [])):
                raise ValueError("unit cannot change after stock history exists")
            if any(row["code"] == code for row in data.get("reversals", [])):
                raise ValueError("unit cannot change after stock history exists")
        before = self._material_profile(data, code)
        materials[code]["name"] = name
        materials[code]["unit"] = unit
        self._record_material_change(data, code, "update_material", before)
        self._write(data)
        return {"code": code, "name": name, "unit": unit}

    def movement(self, code, quantity, reference):
        reference = text(reference, "reference")
        if type(quantity) is not int or quantity == 0:
            raise ValueError("quantity must be a nonzero integer")
        data = self._read()
        if code not in data.get("materials", {}):
            raise ValueError("unknown material")
        if not data.get("status", {}).get(code, True):
            raise ValueError("material is inactive")
        rows = data.setdefault("movements", [])
        self._require_unique_reference(data, reference)
        stock = sum(row["quantity"] for row in rows if row["code"] == code)
        if stock + quantity < 0:
            raise ValueError("insufficient stock")
        row = {"code": code, "quantity": quantity, "reference": reference}
        rows.append(row)
        self._write(data)
        return {**row, "balance": stock + quantity}

    def movement_batch(self, rows):
        if not isinstance(rows, list) or not rows:
            raise ValueError("rows must be a nonempty list")
        data = self._read()
        planned = self._plan_movements(data, rows)
        data.setdefault("movements", []).extend(
            {"code": row["code"], "quantity": row["quantity"], "reference": row["reference"]} for row in planned
        )
        self._write(data)
        return [{key: row[key] for key in ("code", "quantity", "reference", "balance")} for row in planned]

    def preview_movements(self, rows):
        if not isinstance(rows, list) or not rows:
            raise ValueError("rows must be a nonempty list")
        data = self._read()
        return self._plan_movements(data, rows)

    def _plan_movements(self, data, rows):
        # Shared batch movement rules for the commit and preview paths: each
        # row is validated in input order against the material registry, the
        # reference namespace and the running per-material balance. Nothing
        # is written here; callers decide whether to commit the plan or just
        # report it.
        materials = data.get("materials", {})
        status = data.get("status", {})
        existing = data.get("movements", [])
        balances = {}
        seen = set()
        planned = []
        for entry in rows:
            if not isinstance(entry, dict) or set(entry) != {"code", "quantity", "reference"}:
                raise ValueError("each row must be an object with code, quantity and reference")
            code = text(entry["code"], "code")
            reference = text(entry["reference"], "reference")
            quantity = entry["quantity"]
            if type(quantity) is not int or quantity == 0:
                raise ValueError("quantity must be a nonzero integer")
            if code not in materials:
                raise ValueError("unknown material")
            if not status.get(code, True):
                raise ValueError("material is inactive")
            if reference in seen:
                raise ValueError("reference already exists")
            self._require_unique_reference(data, reference)
            if code not in balances:
                balances[code] = sum(row["quantity"] for row in existing if row["code"] == code)
            before = balances[code]
            balances[code] = before + quantity
            if balances[code] < 0:
                raise ValueError("insufficient stock")
            seen.add(reference)
            planned.append({"code": code, "quantity": quantity, "reference": reference, "before": before, "balance": balances[code]})
        return planned

    def stock(self, code):
        data = self._read()
        if code not in data.get("materials", {}):
            raise ValueError("unknown material")
        return {**data["materials"][code], "quantity": sum(row["quantity"] for row in data.get("movements", []) if row["code"] == code)}

    def history(self, code):
        self.stock(code)
        return [row for row in self._read().get("movements", []) if row["code"] == code]

    def movement_ledger(self, code, kind=None):
        code = text(code, "code")
        if kind is not None and kind not in ("movement", "count", "reversal", "purchase_receipt", "purchase_return"):
            raise ValueError("kind must be movement, count, reversal, purchase_receipt, purchase_return or None")
        data = self._read()
        if code not in data.get("materials", {}):
            raise ValueError("unknown material")
        receipts = {}
        for purchase_reference, rows in data.get("purchase_receipts", {}).items():
            for row in rows:
                receipts[row["reference"]] = purchase_reference
        returns = {}
        for purchase_reference, rows in data.get("purchase_returns", {}).items():
            for row in rows:
                returns[row["reference"]] = (purchase_reference, row["receipt_reference"])
        reversals = {row["reference"]: row["original_reference"] for row in data.get("reversals", [])}
        counts = {row["reference"] for row in data.get("counts", [])}
        entries = []
        before = 0
        for row in data.get("movements", []):
            if row["code"] != code:
                continue
            reference = row["reference"]
            purchase_reference = None
            related_reference = None
            if reference in receipts:
                entry_kind = "purchase_receipt"
                purchase_reference = receipts[reference]
            elif reference in returns:
                entry_kind = "purchase_return"
                purchase_reference, related_reference = returns[reference]
            elif reference in reversals:
                entry_kind = "reversal"
                related_reference = reversals[reference]
            elif reference in counts:
                entry_kind = "count"
            else:
                entry_kind = "movement"
            balance = before + row["quantity"]
            if kind is None or kind == entry_kind:
                entries.append({
                    "code": code,
                    "quantity": row["quantity"],
                    "reference": reference,
                    "before": before,
                    "balance": balance,
                    "kind": entry_kind,
                    "purchase_reference": purchase_reference,
                    "related_reference": related_reference,
                })
            before = balance
        return entries

    def stock_summary(self, start_reference=None, end_reference=None, keyword=""):
        # Read-only receipts/issues summary over the whole-ledger interval
        # defined by two movement references. The interval endpoints are
        # located by movements registration order (never by reference
        # sorting) and are inclusive; a null endpoint means the very first
        # or last movement. Validation runs even when the keyword matches
        # nothing, so bad boundaries are still reported.
        if start_reference is not None:
            start_reference = text(start_reference, "start_reference")
        if end_reference is not None:
            end_reference = text(end_reference, "end_reference")
        if not isinstance(keyword, str):
            raise ValueError("keyword must be a string")
        keyword = keyword.strip()
        data = self._read()
        movements = data.get("movements", [])
        if start_reference is not None or end_reference is not None:
            positions = {}
            for index, row in enumerate(movements):
                positions.setdefault(row["reference"], index)
            if start_reference is not None and start_reference not in positions:
                raise ValueError("unknown start reference")
            if end_reference is not None and end_reference not in positions:
                raise ValueError("unknown end reference")
            start_index = positions[start_reference] if start_reference is not None else 0
            end_index = positions[end_reference] if end_reference is not None else len(movements) - 1
            if start_index > end_index:
                raise ValueError("start reference must not come after end reference")
        else:
            start_index, end_index = 0, len(movements) - 1
        materials = data.get("materials", {})
        totals = {code: [0, 0, 0] for code in materials}
        for index, row in enumerate(movements):
            code = row["code"]
            if code not in totals:
                continue
            quantity = row["quantity"]
            if index < start_index:
                totals[code][0] += quantity
            elif index <= end_index:
                if quantity > 0:
                    totals[code][1] += quantity
                else:
                    totals[code][2] -= quantity
        items = []
        for code, material in materials.items():
            if keyword and keyword not in code and keyword not in material["name"]:
                continue
            opening, incoming, outgoing = totals[code]
            items.append({
                "code": code,
                "name": material["name"],
                "unit": material["unit"],
                "opening": opening,
                "incoming": incoming,
                "outgoing": outgoing,
                "closing": opening + incoming - outgoing,
            })
        items.sort(key=lambda item: item["code"])
        return items

    def count(self, code, counted, reference):
        code, reference = text(code, "code"), text(reference, "reference")
        if type(counted) is not int or counted < 0:
            raise ValueError("counted must be a nonnegative integer")
        data = self._read()
        if code not in data.get("materials", {}):
            raise ValueError("unknown material")
        rows = data.setdefault("movements", [])
        self._require_unique_reference(data, reference)
        before = sum(row["quantity"] for row in rows if row["code"] == code)
        difference = counted - before
        record = {"code": code, "reference": reference, "before": before, "counted": counted, "difference": difference}
        data.setdefault("counts", []).append(record)
        if difference != 0:
            rows.append({"code": code, "quantity": difference, "reference": reference})
        self._write(data)
        return dict(record)

    def count_batch(self, rows):
        if not isinstance(rows, list) or not rows:
            raise ValueError("rows must be a nonempty list")
        data = self._read()
        parsed = self._plan_counts(data, rows)
        counts = data.setdefault("counts", [])
        movements = data.setdefault("movements", [])
        for record in parsed:
            counts.append(dict(record))
            if record["difference"] != 0:
                movements.append({"code": record["code"], "quantity": record["difference"], "reference": record["reference"]})
        self._write(data)
        return [dict(record) for record in parsed]

    def confirm_counts(self, rows):
        if not isinstance(rows, list) or not rows:
            raise ValueError("rows must be a nonempty list")
        data = self._read()
        parsed = self._plan_confirmed_counts(data, rows)
        counts = data.setdefault("counts", [])
        movements = data.setdefault("movements", [])
        for record in parsed:
            counts.append(dict(record))
            if record["difference"] != 0:
                movements.append({"code": record["code"], "quantity": record["difference"], "reference": record["reference"]})
        self._write(data)
        return [dict(record) for record in parsed]

    def preview_counts(self, rows):
        if not isinstance(rows, list) or not rows:
            raise ValueError("rows must be a nonempty list")
        data = self._read()
        minimums = data.get("minimums", {})
        planned = self._plan_counts(data, rows)
        return [
            {
                **record,
                "before_shortage": max(minimums.get(record["code"], 0) - record["before"], 0),
                "after_shortage": max(minimums.get(record["code"], 0) - record["counted"], 0),
            }
            for record in planned
        ]

    def _plan_counts(self, data, rows):
        # Shared batch count rules for the commit and preview paths: each row
        # is validated in input order against the material registry, the
        # reference namespace and the current ledger. Counts never act on
        # each other within a batch, so every before value is read from the
        # pre-batch movements. Nothing is written here; callers decide
        # whether to commit the plan or just report it.
        materials = data.get("materials", {})
        existing = data.get("movements", [])
        parsed = []
        seen_codes = set()
        seen_references = set()
        for entry in rows:
            if not isinstance(entry, dict) or set(entry) != {"code", "counted", "reference"}:
                raise ValueError("each row must be an object with code, counted and reference")
            code = text(entry["code"], "code")
            reference = text(entry["reference"], "reference")
            counted = entry["counted"]
            if type(counted) is not int or counted < 0:
                raise ValueError("counted must be a nonnegative integer")
            if code not in materials:
                raise ValueError("unknown material")
            if code in seen_codes:
                raise ValueError("material already exists in batch")
            if reference in seen_references:
                raise ValueError("reference already exists")
            self._require_unique_reference(data, reference)
            before = sum(row["quantity"] for row in existing if row["code"] == code)
            seen_codes.add(code)
            seen_references.add(reference)
            parsed.append({"code": code, "reference": reference, "before": before, "counted": counted,
                           "difference": counted - before})
        return parsed

    def _plan_confirmed_counts(self, data, rows):
        # Batch count rules plus a confirmed-baseline check: each row carries
        # the stock the operator verified from a query or preview, and the
        # whole batch is rejected when any row's current ledger stock
        # differs. Only the quantity is compared — movements that restore the
        # quantity still allow confirmation, and profile, location, minimum
        # or active changes never block one. Nothing is written here; the
        # caller commits the plan atomically.
        materials = data.get("materials", {})
        existing = data.get("movements", [])
        parsed = []
        seen_codes = set()
        seen_references = set()
        for entry in rows:
            if not isinstance(entry, dict) or set(entry) != {"code", "counted", "reference", "expected_before"}:
                raise ValueError("each row must be an object with code, counted, reference and expected_before")
            code = text(entry["code"], "code")
            reference = text(entry["reference"], "reference")
            counted = entry["counted"]
            if type(counted) is not int or counted < 0:
                raise ValueError("counted must be a nonnegative integer")
            expected_before = entry["expected_before"]
            if type(expected_before) is not int or expected_before < 0:
                raise ValueError("expected_before must be a nonnegative integer")
            if code not in materials:
                raise ValueError("unknown material")
            if code in seen_codes:
                raise ValueError("material already exists in batch")
            if reference in seen_references:
                raise ValueError("reference already exists")
            self._require_unique_reference(data, reference)
            before = sum(row["quantity"] for row in existing if row["code"] == code)
            if before != expected_before:
                raise ValueError("stock does not match the confirmed quantity")
            seen_codes.add(code)
            seen_references.add(reference)
            parsed.append({"code": code, "reference": reference, "before": before, "counted": counted,
                           "difference": counted - before})
        return parsed

    def counts(self, code):
        code = text(code, "code")
        data = self._read()
        if code not in data.get("materials", {}):
            raise ValueError("unknown material")
        return [dict(row) for row in data.get("counts", []) if row["code"] == code]

    def reverse(self, original_reference, reference):
        original_reference = text(original_reference, "original_reference")
        reference = text(reference, "reference")
        data = self._read()
        counts = data.get("counts", [])
        reversals = data.get("reversals", [])
        if any(row["reference"] == original_reference for row in counts):
            raise ValueError("cannot reverse a count")
        if any(row["reference"] == original_reference for row in reversals):
            raise ValueError("cannot reverse a reversal")
        if any(row["original_reference"] == original_reference for row in reversals):
            raise ValueError("movement already reversed")
        receipt_references = {row["reference"] for receipts in data.get("purchase_receipts", {}).values() for row in receipts}
        if original_reference in receipt_references:
            raise ValueError("cannot reverse a purchase receipt")
        return_references = {row["reference"] for rows in data.get("purchase_returns", {}).values() for row in rows}
        if original_reference in return_references:
            raise ValueError("cannot reverse a purchase return")
        original = next((row for row in data.get("movements", []) if row["reference"] == original_reference), None)
        if original is None:
            raise ValueError("unknown original reference")
        code = original["code"]
        quantity = -original["quantity"]
        self._require_unique_reference(data, reference)
        stock = sum(row["quantity"] for row in data["movements"] if row["code"] == code)
        if stock + quantity < 0:
            raise ValueError("insufficient stock")
        balance = stock + quantity
        data["movements"].append({"code": code, "quantity": quantity, "reference": reference})
        record = {"code": code, "original_reference": original_reference, "reference": reference, "quantity": quantity, "balance": balance}
        data.setdefault("reversals", []).append(record)
        self._write(data)
        return dict(record)

    def reverse_batch(self, rows):
        if not isinstance(rows, list) or not rows:
            raise ValueError("rows must be a nonempty list")
        data = self._read()
        planned = self._plan_reversals(data, rows)
        movements = data.setdefault("movements", [])
        records = []
        for row in planned:
            movements.append({"code": row["code"], "quantity": row["quantity"], "reference": row["reference"]})
            records.append({key: row[key] for key in ("code", "original_reference", "reference", "quantity", "balance")})
        data.setdefault("reversals", []).extend(records)
        self._write(data)
        return [dict(record) for record in records]

    def preview_reversals(self, rows):
        if not isinstance(rows, list) or not rows:
            raise ValueError("rows must be a nonempty list")
        data = self._read()
        planned = self._plan_reversals(data, rows)
        return [
            {key: row[key] for key in ("code", "original_reference", "reference", "quantity", "before", "balance")}
            for row in planned
        ]

    def _plan_reversals(self, data, rows):
        # Shared batch reversal rules for the commit and preview paths: each
        # row is validated in input order against the pre-batch ledger (new
        # reversals never act as originals within the same batch), the
        # reference namespace and the running per-material balance. Nothing
        # is written here; callers decide whether to commit the plan or just
        # report it.
        counts = data.get("counts", [])
        reversals = data.get("reversals", [])
        movements = data.get("movements", [])
        count_references = {row["reference"] for row in counts}
        reversal_references = {row["reference"] for row in reversals}
        reversed_originals = {row["original_reference"] for row in reversals}
        receipt_references = {row["reference"] for receipts in data.get("purchase_receipts", {}).values() for row in receipts}
        return_references = {row["reference"] for records in data.get("purchase_returns", {}).values() for row in records}
        movement_index = {row["reference"]: row for row in movements}
        balances = {}
        seen_originals = set()
        seen_references = set()
        planned = []
        for entry in rows:
            if not isinstance(entry, dict) or set(entry) != {"original_reference", "reference"}:
                raise ValueError("each row must be an object with original_reference and reference")
            original_reference = text(entry["original_reference"], "original_reference")
            reference = text(entry["reference"], "reference")
            if original_reference in count_references:
                raise ValueError("cannot reverse a count")
            if original_reference in reversal_references:
                raise ValueError("cannot reverse a reversal")
            if original_reference in reversed_originals:
                raise ValueError("movement already reversed")
            if original_reference in receipt_references:
                raise ValueError("cannot reverse a purchase receipt")
            if original_reference in return_references:
                raise ValueError("cannot reverse a purchase return")
            original = movement_index.get(original_reference)
            if original is None:
                raise ValueError("unknown original reference")
            if original_reference in seen_originals:
                raise ValueError("movement already reversed")
            if reference in seen_references:
                raise ValueError("reference already exists")
            self._require_unique_reference(data, reference)
            if reference in reversal_references:
                raise ValueError("reference already exists")
            code = original["code"]
            quantity = -original["quantity"]
            if code not in balances:
                balances[code] = sum(row["quantity"] for row in movements if row["code"] == code)
            before = balances[code]
            balances[code] = before + quantity
            if balances[code] < 0:
                raise ValueError("insufficient stock")
            seen_originals.add(original_reference)
            seen_references.add(reference)
            planned.append({
                "code": code,
                "original_reference": original_reference,
                "reference": reference,
                "quantity": quantity,
                "before": before,
                "balance": balances[code],
            })
        return planned

    def reversals(self, code):
        code = text(code, "code")
        data = self._read()
        if code not in data.get("materials", {}):
            raise ValueError("unknown material")
        return [dict(row) for row in data.get("reversals", []) if row["code"] == code]

    def create_purchase(self, reference, supplier, rows):
        reference = text(reference, "reference")
        supplier = text(supplier, "supplier")
        if not isinstance(rows, list) or not rows:
            raise ValueError("rows must be a nonempty list")
        data = self._read()
        materials = data.get("materials", {})
        status = data.get("status", {})
        if any(order["reference"] == reference for order in data.get("purchases", [])):
            raise ValueError("reference already exists")
        seen = set()
        parsed = []
        for entry in rows:
            if not isinstance(entry, dict) or set(entry) != {"code", "quantity"}:
                raise ValueError("each row must be an object with code and quantity")
            code = text(entry["code"], "code")
            quantity = entry["quantity"]
            if type(quantity) is not int or quantity <= 0:
                raise ValueError("quantity must be a positive integer")
            if code not in materials:
                raise ValueError("unknown material")
            if not status.get(code, True):
                raise ValueError("material is inactive")
            if code in seen:
                raise ValueError("material already exists in purchase")
            seen.add(code)
            material = materials[code]
            parsed.append({"code": code, "name": material["name"], "unit": material["unit"], "quantity": quantity})
        order = {"reference": reference, "supplier": supplier, "status": "open", "rows": parsed}
        data.setdefault("purchases", []).append(order)
        self._write(data)
        return self._purchase_snapshot(order)

    def create_replenishment_purchase(self, reference, supplier, codes):
        # Order the current replenishment suggestion for the selected
        # materials as one purchase. The suggested quantity per material is
        # max(minimum - stock - incoming, 0) computed at submission time,
        # where incoming sums open-order lines with ordered minus received
        # greater than zero (cancelled orders and fully received lines never
        # contribute, returns do not restore the outstanding quantity, and
        # movements, counts and reversals only act through the stock). Only
        # the selected materials are checked, so a unit mismatch on any
        # other material's open line never blocks this order. The whole
        # request is validated before anything is written.
        reference = text(reference, "reference")
        supplier = text(supplier, "supplier")
        if not isinstance(codes, list) or not codes:
            raise ValueError("codes must be a nonempty list")
        data = self._read()
        materials = data.get("materials", {})
        status = data.get("status", {})
        if any(order["reference"] == reference for order in data.get("purchases", [])):
            raise ValueError("reference already exists")
        seen = set()
        selected = {}
        for entry in codes:
            code = text(entry, "code")
            if code not in materials:
                raise ValueError("unknown material")
            if not status.get(code, True):
                raise ValueError("material is inactive")
            if code in seen:
                raise ValueError("material already exists in purchase")
            seen.add(code)
            selected[code] = materials[code]
        incoming = {code: 0 for code in selected}
        for order in data.get("purchases", []):
            if order.get("status") != "open":
                continue
            received = self._received_totals(data, order["reference"])
            for line in order.get("rows", []):
                code = line["code"]
                if code not in selected:
                    continue
                remaining = line["quantity"] - received.get(code, 0)
                if remaining <= 0:
                    continue
                if line["unit"] != selected[code]["unit"]:
                    raise ValueError("purchase unit differs from the current material unit")
                incoming[code] += remaining
        movements = data.get("movements", [])
        minimums = data.get("minimums", {})
        rows = []
        for code, material in selected.items():
            quantity = sum(row["quantity"] for row in movements if row["code"] == code)
            suggested = minimums.get(code, 0) - quantity - incoming[code]
            if suggested <= 0:
                raise ValueError("suggested quantity is zero")
            rows.append({"code": code, "name": material["name"], "unit": material["unit"], "quantity": suggested})
        order = {"reference": reference, "supplier": supplier, "status": "open", "rows": rows}
        data.setdefault("purchases", []).append(order)
        self._write(data)
        return self._purchase_snapshot(order)

    def import_purchases_csv(self, content):
        if not isinstance(content, str):
            raise ValueError("content must be a string")
        if content.startswith("﻿"):
            content = content[1:]
        if not content:
            raise ValueError("content must be a nonempty CSV document")
        rows = _parse_csv(content)
        if not rows:
            raise ValueError("content must be a nonempty CSV document")
        header = rows[0]
        if len(header) != 4 or set(header) != {"reference", "supplier", "code", "quantity"}:
            raise ValueError("header must contain exactly the reference, supplier, code and quantity columns")
        positions = {name: header.index(name) for name in ("reference", "supplier", "code", "quantity")}
        entries = []
        orders = {}
        for row in rows[1:]:
            if not row:
                continue
            if len(row) != 4:
                raise ValueError("each record must have exactly four columns")
            reference = row[positions["reference"]].strip()
            supplier = row[positions["supplier"]].strip()
            code = row[positions["code"]].strip()
            quantity_text = row[positions["quantity"]].strip()
            if not reference:
                raise ValueError("reference must be a nonempty string")
            if not supplier:
                raise ValueError("supplier must be a nonempty string")
            if not code:
                raise ValueError("code must be a nonempty string")
            if not quantity_text or any(char < "0" or char > "9" for char in quantity_text):
                raise ValueError("quantity must be a positive integer")
            quantity = int(quantity_text)
            if quantity == 0:
                raise ValueError("quantity must be a positive integer")
            order = orders.get(reference)
            if order is None:
                order = {"reference": reference, "supplier": supplier, "rows": []}
                orders[reference] = order
                entries.append(order)
            elif order["supplier"] != supplier:
                raise ValueError("supplier must be consistent within a purchase")
            if any(line["code"] == code for line in order["rows"]):
                raise ValueError("material already exists in purchase")
            order["rows"].append({"code": code, "quantity": quantity})
        if not entries:
            return []
        data = self._read()
        materials = data.get("materials", {})
        status = data.get("status", {})
        existing = {order["reference"] for order in data.get("purchases", [])}
        parsed_orders = []
        for entry in entries:
            if entry["reference"] in existing:
                raise ValueError("reference already exists")
            parsed_rows = []
            for line in entry["rows"]:
                code = line["code"]
                if code not in materials:
                    raise ValueError("unknown material")
                if not status.get(code, True):
                    raise ValueError("material is inactive")
                material = materials[code]
                parsed_rows.append({"code": code, "name": material["name"], "unit": material["unit"], "quantity": line["quantity"]})
            parsed_orders.append({"reference": entry["reference"], "supplier": entry["supplier"], "status": "open", "rows": parsed_rows})
        data.setdefault("purchases", []).extend(parsed_orders)
        self._write(data)
        return [self._purchase_snapshot(order) for order in parsed_orders]

    def purchase_order(self, reference):
        reference = text(reference, "reference")
        data = self._read()
        order = next((row for row in data.get("purchases", []) if row["reference"] == reference), None)
        if order is None:
            raise ValueError("unknown purchase reference")
        return self._purchase_snapshot(order)

    def purchase_changes(self, reference):
        reference = text(reference, "reference")
        data = self._read()
        if not any(order["reference"] == reference for order in data.get("purchases", [])):
            raise ValueError("unknown purchase reference")
        return [
            {
                "reference": row["reference"],
                "sequence": row["sequence"],
                "action": row["action"],
                "before": self._purchase_snapshot(row["before"]),
                "after": self._purchase_snapshot(row["after"]),
            }
            for row in data.get("purchase_changes", {}).get(reference, [])
        ]

    def cancel_purchase(self, reference):
        reference = text(reference, "reference")
        data = self._read()
        order = next((row for row in data.get("purchases", []) if row["reference"] == reference), None)
        if order is None:
            raise ValueError("unknown purchase reference")
        if order["status"] != "cancelled":
            before = self._purchase_snapshot(order)
            order["status"] = "cancelled"
            self._record_purchase_change(data, order, "cancel_purchase", before)
            self._write(data)
        return self._purchase_snapshot(order)

    def update_purchase(self, reference, supplier, rows):
        reference = text(reference, "reference")
        supplier = text(supplier, "supplier")
        if not isinstance(rows, list) or not rows:
            raise ValueError("rows must be a nonempty list")
        data = self._read()
        order = next((row for row in data.get("purchases", []) if row["reference"] == reference), None)
        if order is None:
            raise ValueError("unknown purchase reference")
        if order["status"] != "open":
            raise ValueError("purchase is not open")
        if data.get("purchase_receipts", {}).get(reference):
            raise ValueError("purchase already has receipts")
        materials = data.get("materials", {})
        status = data.get("status", {})
        seen = set()
        parsed = []
        for entry in rows:
            if not isinstance(entry, dict) or set(entry) != {"code", "quantity"}:
                raise ValueError("each row must be an object with code and quantity")
            code = text(entry["code"], "code")
            quantity = entry["quantity"]
            if type(quantity) is not int or quantity <= 0:
                raise ValueError("quantity must be a positive integer")
            if code not in materials:
                raise ValueError("unknown material")
            if not status.get(code, True):
                raise ValueError("material is inactive")
            if code in seen:
                raise ValueError("material already exists in purchase")
            seen.add(code)
            material = materials[code]
            parsed.append({"code": code, "name": material["name"], "unit": material["unit"], "quantity": quantity})
        before = self._purchase_snapshot(order)
        order["supplier"] = supplier
        order["rows"] = parsed
        self._record_purchase_change(data, order, "update_purchase", before)
        self._write(data)
        return self._purchase_snapshot(order)

    def adjust_purchase_quantities(self, reference, rows):
        reference = text(reference, "reference")
        if not isinstance(rows, list) or not rows:
            raise ValueError("rows must be a nonempty list")
        data = self._read()
        order = next((row for row in data.get("purchases", []) if row["reference"] == reference), None)
        if order is None:
            raise ValueError("unknown purchase reference")
        if order["status"] == "cancelled":
            raise ValueError("purchase is cancelled")
        received = self._received_totals(data, reference)
        lines = {row["code"]: row for row in order["rows"]}
        seen = set()
        planned = []
        for entry in rows:
            if not isinstance(entry, dict) or set(entry) != {"code", "quantity"}:
                raise ValueError("each row must be an object with code and quantity")
            code = text(entry["code"], "code")
            quantity = entry["quantity"]
            if type(quantity) is not int or quantity <= 0:
                raise ValueError("quantity must be a positive integer")
            if code not in lines:
                raise ValueError("material is not part of the purchase")
            if code in seen:
                raise ValueError("material already exists in purchase")
            seen.add(code)
            if quantity < received.get(code, 0):
                raise ValueError("quantity is below the received quantity")
            planned.append((code, quantity))
        before = self._purchase_snapshot(order)
        for code, quantity in planned:
            lines[code]["quantity"] = quantity
        after = self._purchase_snapshot(order)
        if after != before:
            self._record_purchase_change(data, order, "adjust_purchase_quantities", before)
            self._write(data)
        return after

    def receive_purchase(self, purchase_reference, rows):
        purchase_reference = text(purchase_reference, "purchase_reference")
        if not isinstance(rows, list) or not rows:
            raise ValueError("rows must be a nonempty list")
        data = self._read()
        planned = self._plan_purchase_receipts(data, purchase_reference, rows)
        data.setdefault("movements", []).extend(
            {"code": row["code"], "quantity": row["quantity"], "reference": row["reference"]} for row in planned
        )
        data.setdefault("purchase_receipts", {}).setdefault(purchase_reference, []).extend(
            self._receipt_record(row) for row in planned
        )
        self._write(data)
        return [self._receipt_record(row) for row in planned]

    def preview_purchase_receipts(self, purchase_reference, rows):
        purchase_reference = text(purchase_reference, "purchase_reference")
        if not isinstance(rows, list) or not rows:
            raise ValueError("rows must be a nonempty list")
        data = self._read()
        planned = self._plan_purchase_receipts(data, purchase_reference, rows)
        return [{
            **self._receipt_record(row),
            "before": row["before"],
            "remaining": row["remaining"],
        } for row in planned]

    def receive_purchase_batch(self, rows):
        if not isinstance(rows, list) or not rows:
            raise ValueError("rows must be a nonempty list")
        data = self._read()
        planned = self._plan_purchase_receipt_batch(data, rows)
        movements = data.setdefault("movements", [])
        receipts = data.setdefault("purchase_receipts", {})
        for row in planned:
            movements.append({"code": row["code"], "quantity": row["quantity"], "reference": row["reference"]})
            receipts.setdefault(row["purchase_reference"], []).append(self._receipt_record(row))
        self._write(data)
        return [{**self._receipt_record(row), "purchase_reference": row["purchase_reference"]} for row in planned]

    def import_purchase_receipts_csv(self, content):
        if not isinstance(content, str):
            raise ValueError("content must be a string")
        if content.startswith("﻿"):
            content = content[1:]
        if not content:
            raise ValueError("content must be a nonempty CSV document")
        rows = _parse_csv(content)
        if not rows:
            raise ValueError("content must be a nonempty CSV document")
        header = rows[0]
        if len(header) != 4 or set(header) != {"purchase_reference", "code", "quantity", "reference"}:
            raise ValueError("header must contain exactly the purchase_reference, code, quantity and reference columns")
        positions = {name: header.index(name) for name in ("purchase_reference", "code", "quantity", "reference")}
        entries = []
        for row in rows[1:]:
            if not row:
                continue
            if len(row) != 4:
                raise ValueError("each record must have exactly four columns")
            purchase_reference = row[positions["purchase_reference"]].strip()
            code = row[positions["code"]].strip()
            reference = row[positions["reference"]].strip()
            quantity_text = row[positions["quantity"]].strip()
            if not purchase_reference:
                raise ValueError("purchase_reference must be a nonempty string")
            if not code:
                raise ValueError("code must be a nonempty string")
            if not reference:
                raise ValueError("reference must be a nonempty string")
            if not quantity_text or any(char < "0" or char > "9" for char in quantity_text):
                raise ValueError("quantity must be a positive integer")
            quantity = int(quantity_text)
            if quantity == 0:
                raise ValueError("quantity must be a positive integer")
            entries.append({"purchase_reference": purchase_reference, "code": code, "quantity": quantity, "reference": reference})
        if not entries:
            return []
        data = self._read()
        planned = self._plan_purchase_receipt_batch(data, entries)
        movements = data.setdefault("movements", [])
        receipts = data.setdefault("purchase_receipts", {})
        for row in planned:
            movements.append({"code": row["code"], "quantity": row["quantity"], "reference": row["reference"]})
            receipts.setdefault(row["purchase_reference"], []).append(self._receipt_record(row))
        self._write(data)
        return [{**self._receipt_record(row), "purchase_reference": row["purchase_reference"]} for row in planned]

    def _plan_purchase_receipt_batch(self, data, rows):
        # Cross-purchase counterpart of _plan_purchase_receipts: each row
        # carries its own purchase reference and is validated in input order
        # against that order's snapshot and quota, while stock balances and
        # the reference namespace are shared across the whole batch. Nothing
        # is written here; the caller commits the plan atomically.
        purchases = {order["reference"]: order for order in data.get("purchases", [])}
        materials = data.get("materials", {})
        status = data.get("status", {})
        existing = data.get("movements", [])
        ordered_cache = {}
        received_cache = {}
        balances = {}
        seen_pairs = set()
        seen_references = set()
        planned = []
        for entry in rows:
            if not isinstance(entry, dict) or set(entry) != {"purchase_reference", "code", "quantity", "reference"}:
                raise ValueError("each row must be an object with purchase_reference, code, quantity and reference")
            purchase_reference = text(entry["purchase_reference"], "purchase_reference")
            code = text(entry["code"], "code")
            reference = text(entry["reference"], "reference")
            quantity = entry["quantity"]
            if type(quantity) is not int or quantity <= 0:
                raise ValueError("quantity must be a positive integer")
            order = purchases.get(purchase_reference)
            if order is None:
                raise ValueError("unknown purchase reference")
            if order["status"] == "cancelled":
                raise ValueError("purchase is cancelled")
            if code not in materials:
                raise ValueError("unknown material")
            if not status.get(code, True):
                raise ValueError("material is inactive")
            if purchase_reference not in ordered_cache:
                ordered_cache[purchase_reference] = {row["code"]: row for row in order["rows"]}
                received_cache[purchase_reference] = self._received_totals(data, purchase_reference)
            ordered = ordered_cache[purchase_reference]
            if code not in ordered:
                raise ValueError("material is not part of the purchase")
            if materials[code]["unit"] != ordered[code]["unit"]:
                raise ValueError("material unit differs from the purchase snapshot")
            pair = (purchase_reference, code)
            if pair in seen_pairs:
                raise ValueError("material already exists in batch for the purchase")
            if received_cache[purchase_reference].get(code, 0) + quantity > ordered[code]["quantity"]:
                raise ValueError("received quantity exceeds ordered quantity")
            if reference in seen_references:
                raise ValueError("reference already exists")
            self._require_unique_reference(data, reference)
            if any(row["reference"] == reference for row in data.get("reversals", [])):
                raise ValueError("reference already exists")
            if code not in balances:
                balances[code] = sum(row["quantity"] for row in existing if row["code"] == code)
            balances[code] += quantity
            seen_pairs.add(pair)
            seen_references.add(reference)
            planned.append({
                "purchase_reference": purchase_reference,
                "code": code,
                "quantity": quantity,
                "reference": reference,
                "balance": balances[code],
            })
        return planned

    def _plan_purchase_receipts(self, data, purchase_reference, rows):
        # Shared receipt rules for the commit and preview paths: each row is
        # validated in input order against the purchase snapshot, the receipt
        # quota (history plus this batch) and the reference namespace.
        # Nothing is written here; callers decide whether to commit the plan
        # or just report it.
        order = next((row for row in data.get("purchases", []) if row["reference"] == purchase_reference), None)
        if order is None:
            raise ValueError("unknown purchase reference")
        if order["status"] == "cancelled":
            raise ValueError("purchase is cancelled")
        materials = data.get("materials", {})
        status = data.get("status", {})
        existing = data.get("movements", [])
        ordered = {row["code"]: row for row in order["rows"]}
        received = self._received_totals(data, purchase_reference)
        balances = {}
        batch_received = {}
        seen_codes = set()
        seen_references = set()
        planned = []
        for entry in rows:
            if not isinstance(entry, dict) or set(entry) != {"code", "quantity", "reference"}:
                raise ValueError("each row must be an object with code, quantity and reference")
            code = text(entry["code"], "code")
            reference = text(entry["reference"], "reference")
            quantity = entry["quantity"]
            if type(quantity) is not int or quantity <= 0:
                raise ValueError("quantity must be a positive integer")
            if code not in materials:
                raise ValueError("unknown material")
            if not status.get(code, True):
                raise ValueError("material is inactive")
            if code not in ordered:
                raise ValueError("material is not part of the purchase")
            if materials[code]["unit"] != ordered[code]["unit"]:
                raise ValueError("material unit differs from the purchase snapshot")
            if code in seen_codes:
                raise ValueError("material already exists in batch")
            batch_received[code] = batch_received.get(code, 0) + quantity
            if received.get(code, 0) + batch_received[code] > ordered[code]["quantity"]:
                raise ValueError("received quantity exceeds ordered quantity")
            if reference in seen_references:
                raise ValueError("reference already exists")
            self._require_unique_reference(data, reference)
            if code not in balances:
                balances[code] = sum(row["quantity"] for row in existing if row["code"] == code)
            before = balances[code]
            balances[code] += quantity
            seen_codes.add(code)
            seen_references.add(reference)
            planned.append({
                "code": code,
                "quantity": quantity,
                "reference": reference,
                "before": before,
                "balance": balances[code],
                "remaining": ordered[code]["quantity"] - received.get(code, 0) - batch_received[code],
            })
        return planned

    @staticmethod
    def _receipt_record(row):
        return {
            "code": row["code"],
            "quantity": row["quantity"],
            "reference": row["reference"],
            "balance": row["balance"],
        }

    def purchase_receipts(self, purchase_reference):
        purchase_reference = text(purchase_reference, "purchase_reference")
        data = self._read()
        order = next((row for row in data.get("purchases", []) if row["reference"] == purchase_reference), None)
        if order is None:
            raise ValueError("unknown purchase reference")
        return [dict(row) for row in data.get("purchase_receipts", {}).get(purchase_reference, [])]

    def return_purchase(self, receipt_reference, quantity, reference):
        rows = [{"receipt_reference": receipt_reference, "quantity": quantity, "reference": reference}]
        data = self._read()
        planned = self._plan_purchase_returns(data, rows)
        self._commit_purchase_returns(data, planned)
        return self._return_record(planned[0])

    def return_purchase_batch(self, rows):
        if not isinstance(rows, list) or not rows:
            raise ValueError("rows must be a nonempty list")
        data = self._read()
        planned = self._plan_purchase_returns(data, rows)
        self._commit_purchase_returns(data, planned)
        return [self._return_record(record) for record in planned]

    def import_purchase_returns_csv(self, content):
        if not isinstance(content, str):
            raise ValueError("content must be a string")
        if content.startswith("﻿"):
            content = content[1:]
        if not content:
            raise ValueError("content must be a nonempty CSV document")
        rows = _parse_csv(content)
        if not rows:
            raise ValueError("content must be a nonempty CSV document")
        header = rows[0]
        if len(header) != 3 or set(header) != {"receipt_reference", "quantity", "reference"}:
            raise ValueError("header must contain exactly the receipt_reference, quantity and reference columns")
        positions = {name: header.index(name) for name in ("receipt_reference", "quantity", "reference")}
        entries = []
        for row in rows[1:]:
            if not row:
                continue
            if len(row) != 3:
                raise ValueError("each record must have exactly three columns")
            receipt_reference = row[positions["receipt_reference"]].strip()
            reference = row[positions["reference"]].strip()
            quantity_text = row[positions["quantity"]].strip()
            if not receipt_reference:
                raise ValueError("receipt_reference must be a nonempty string")
            if not reference:
                raise ValueError("reference must be a nonempty string")
            if not quantity_text or any(char < "0" or char > "9" for char in quantity_text):
                raise ValueError("quantity must be a positive integer")
            quantity = int(quantity_text)
            if quantity == 0:
                raise ValueError("quantity must be a positive integer")
            entries.append({"receipt_reference": receipt_reference, "quantity": quantity, "reference": reference})
        if not entries:
            return []
        data = self._read()
        planned = self._plan_purchase_returns(data, entries)
        self._commit_purchase_returns(data, planned)
        return [self._return_record(record) for record in planned]

    def preview_purchase_returns(self, rows):
        if not isinstance(rows, list) or not rows:
            raise ValueError("rows must be a nonempty list")
        data = self._read()
        planned = self._plan_purchase_returns(data, rows)
        return [{
            "purchase_reference": record["purchase_reference"],
            "receipt_reference": record["receipt_reference"],
            "code": record["code"],
            "quantity": record["quantity"],
            "reference": record["reference"],
            "before": record["before"],
            "balance": record["balance"],
            "receipt_remaining": record["receipt_remaining"],
        } for record in planned]

    def _plan_purchase_returns(self, data, rows):
        # Shared return rules for the single, batch and preview paths: each
        # row is validated in input order against the receipt quota (history
        # plus this batch), the shared per-material stock and the reference
        # namespace. Nothing is written here; callers decide whether to
        # commit the plan or just report it.
        receipts_index = {}
        for purchase_reference, receipts in data.get("purchase_receipts", {}).items():
            for receipt in receipts:
                receipts_index[receipt["reference"]] = (purchase_reference, receipt)
        returned = {}
        for records in data.get("purchase_returns", {}).values():
            for row in records:
                returned[row["receipt_reference"]] = returned.get(row["receipt_reference"], 0) + row["quantity"]
        balances = {}
        seen = set()
        planned = []
        for entry in rows:
            if not isinstance(entry, dict) or set(entry) != {"receipt_reference", "quantity", "reference"}:
                raise ValueError("each row must be an object with receipt_reference, quantity and reference")
            receipt_reference = text(entry["receipt_reference"], "receipt_reference")
            reference = text(entry["reference"], "reference")
            quantity = entry["quantity"]
            if type(quantity) is not int or quantity <= 0:
                raise ValueError("quantity must be a positive integer")
            found = receipts_index.get(receipt_reference)
            if found is None:
                raise ValueError("unknown receipt reference")
            purchase_reference, receipt = found
            code = receipt["code"]
            total = returned.get(receipt_reference, 0) + quantity
            if total > receipt["quantity"]:
                raise ValueError("returned quantity exceeds received quantity")
            returned[receipt_reference] = total
            if reference in seen:
                raise ValueError("reference already exists")
            self._require_unique_reference(data, reference)
            if code not in balances:
                balances[code] = sum(row["quantity"] for row in data.get("movements", []) if row["code"] == code)
            before = balances[code]
            balance = before - quantity
            if balance < 0:
                raise ValueError("insufficient stock")
            balances[code] = balance
            seen.add(reference)
            planned.append({
                "purchase_reference": purchase_reference,
                "receipt_reference": receipt_reference,
                "code": code,
                "quantity": quantity,
                "reference": reference,
                "before": before,
                "balance": balance,
                "receipt_remaining": receipt["quantity"] - total,
            })
        return planned

    @staticmethod
    def _return_record(record):
        return {
            "purchase_reference": record["purchase_reference"],
            "receipt_reference": record["receipt_reference"],
            "code": record["code"],
            "quantity": record["quantity"],
            "reference": record["reference"],
            "balance": record["balance"],
        }

    def _commit_purchase_returns(self, data, planned):
        movements = data.setdefault("movements", [])
        returns = data.setdefault("purchase_returns", {})
        for record in planned:
            movements.append({"code": record["code"], "quantity": -record["quantity"], "reference": record["reference"]})
            returns.setdefault(record["purchase_reference"], []).append(self._return_record(record))
        self._write(data)

    def purchase_returns(self, purchase_reference):
        purchase_reference = text(purchase_reference, "purchase_reference")
        data = self._read()
        order = next((row for row in data.get("purchases", []) if row["reference"] == purchase_reference), None)
        if order is None:
            raise ValueError("unknown purchase reference")
        return [dict(row) for row in data.get("purchase_returns", {}).get(purchase_reference, [])]

    def purchase_progress(self, purchase_reference):
        purchase_reference = text(purchase_reference, "purchase_reference")
        data = self._read()
        order = next((row for row in data.get("purchases", []) if row["reference"] == purchase_reference), None)
        if order is None:
            raise ValueError("unknown purchase reference")
        return self._purchase_progress(data, order)

    def purchase_orders(self, supplier="", status=None, progress=None):
        if not isinstance(supplier, str):
            raise ValueError("supplier must be a string")
        if status is not None and status not in ("open", "cancelled"):
            raise ValueError("status must be open, cancelled or None")
        if progress is not None and progress not in ("pending", "partial", "complete"):
            raise ValueError("progress must be pending, partial, complete or None")
        supplier = supplier.strip()
        data = self._read()
        items = []
        for order in data.get("purchases", []):
            if supplier and supplier not in order["supplier"]:
                continue
            if status is not None and order["status"] != status:
                continue
            item = self._purchase_progress(data, order)
            if progress is not None and item["progress"] != progress:
                continue
            items.append(item)
        items.sort(key=lambda item: item["reference"])
        return items

    def export_purchases_csv(self, supplier="", status=None, progress=None):
        orders = self.purchase_orders(supplier=supplier, status=status, progress=progress)
        lines = ["reference,supplier,status,progress,code,name,unit,quantity,received,returned,net_received,remaining"]
        for order in orders:
            head = [order["reference"], order["supplier"], order["status"], order["progress"]]
            for row in order["rows"]:
                fields = head + [
                    row["code"],
                    row["name"],
                    row["unit"],
                    str(row["quantity"]),
                    str(row["received"]),
                    str(row["returned"]),
                    str(row["net_received"]),
                    str(row["remaining"]),
                ]
                lines.append(",".join(_csv_field(value) for value in fields))
        return "\n".join(lines) + "\n"

    def save_supplier(self, supplier, contact="", phone="", note=""):
        supplier = text(supplier, "supplier")
        contact = optional_text(contact, "contact")
        phone = optional_text(phone, "phone")
        note = optional_text(note, "note")
        record = {"supplier": supplier, "contact": contact, "phone": phone, "note": note}
        data = self._read()
        profiles = data.setdefault("suppliers", {})
        before = self._supplier_snapshot(profiles.get(supplier), supplier)
        profiles[supplier] = record
        self._record_supplier_change(data, supplier, "save_supplier", None, before, record)
        self._write(data)
        return dict(record)

    def import_suppliers_csv(self, content):
        if not isinstance(content, str):
            raise ValueError("content must be a string")
        if content.startswith("﻿"):
            content = content[1:]
        if not content:
            raise ValueError("content must be a nonempty CSV document")
        rows = _parse_csv(content)
        if not rows:
            raise ValueError("content must be a nonempty CSV document")
        header = rows[0]
        if len(header) != 4 or set(header) != {"supplier", "contact", "phone", "note"}:
            raise ValueError("header must contain exactly the supplier, contact, phone and note columns")
        positions = {name: header.index(name) for name in ("supplier", "contact", "phone", "note")}
        records = []
        seen = set()
        for row in rows[1:]:
            if not row:
                continue
            if len(row) != 4:
                raise ValueError("each record must have exactly four columns")
            fields = {name: row[index].strip() for name, index in positions.items()}
            if not fields["supplier"]:
                raise ValueError("supplier must be a nonempty string")
            if fields["supplier"] in seen:
                raise ValueError("supplier already exists in batch")
            seen.add(fields["supplier"])
            records.append(fields)
        if not records:
            return []
        data = self._read()
        profiles = data.setdefault("suppliers", {})
        changed = False
        for fields in records:
            name = fields["supplier"]
            before = self._supplier_snapshot(profiles.get(name), name)
            record = dict(fields)
            if before == record:
                continue
            profiles[name] = record
            self._record_supplier_change(data, name, "save_supplier", None, before, record)
            changed = True
        if changed:
            self._write(data)
        return [dict(fields) for fields in records]

    def suppliers(self, keyword=""):
        keyword = optional_text(keyword, "keyword")
        data = self._read()
        profiles = data.get("suppliers", {})
        names = set(profiles)
        for order in data.get("purchases", []):
            names.add(order["supplier"])
        items = []
        for name in names:
            if keyword and keyword not in name:
                continue
            profile = profiles.get(name)
            if profile is None:
                items.append({"supplier": name, "contact": "", "phone": "", "note": ""})
            else:
                items.append({field: profile.get(field, "") for field in ("supplier", "contact", "phone", "note")})
        items.sort(key=lambda item: item["supplier"])
        return items

    def supplier_record(self, supplier):
        supplier = text(supplier, "supplier")
        data = self._read()
        profiles = data.get("suppliers", {})
        orders = [order for order in data.get("purchases", []) if order["supplier"] == supplier]
        profile = profiles.get(supplier)
        if profile is None and not orders:
            raise ValueError("unknown supplier")
        if profile is None:
            record = {"supplier": supplier, "contact": "", "phone": "", "note": ""}
        else:
            record = {field: profile.get(field, "") for field in ("supplier", "contact", "phone", "note")}
            record["supplier"] = supplier
        purchases = [self._purchase_progress(data, order) for order in orders]
        purchases.sort(key=lambda item: item["reference"])
        return {**record, "purchases": purchases}

    def supplier_changes(self, supplier):
        supplier = text(supplier, "supplier")
        data = self._read()
        history = data.get("supplier_changes", {}).get(supplier, [])
        if not history and supplier not in data.get("suppliers", {}):
            if not any(order["supplier"] == supplier for order in data.get("purchases", [])):
                raise ValueError("unknown supplier")
        return [
            {
                "supplier": row["supplier"],
                "sequence": row["sequence"],
                "action": row["action"],
                "related_supplier": row["related_supplier"],
                "before": None if row["before"] is None else dict(row["before"]),
                "after": None if row["after"] is None else dict(row["after"]),
            }
            for row in history
        ]

    def supplier_outstanding(self, supplier):
        supplier = text(supplier, "supplier")
        data = self._read()
        profiles = data.get("suppliers", {})
        orders = [order for order in data.get("purchases", []) if order["supplier"] == supplier]
        if supplier not in profiles and not orders:
            raise ValueError("unknown supplier")
        groups = {}
        for order in orders:
            if order.get("status") != "open":
                continue
            received = self._received_totals(data, order["reference"])
            for line in order.get("rows", []):
                remaining = line["quantity"] - received.get(line["code"], 0)
                if remaining <= 0:
                    continue
                key = (line["code"], line["unit"])
                group = groups.setdefault(key, {"code": line["code"], "unit": line["unit"], "remaining": 0, "purchases": []})
                group["remaining"] += remaining
                group["purchases"].append({"reference": order["reference"], "name": line["name"], "remaining": remaining})
        items = sorted(groups.values(), key=lambda item: (item["code"], item["unit"]))
        for item in items:
            item["purchases"].sort(key=lambda purchase: purchase["reference"])
        return items

    def supplier_ledger(self, supplier, kind=None):
        # Read-only receipt/return ledger for one supplier. Entries follow
        # whole-ledger movement registration order (never purchase reference
        # sorting); only movements linked to a purchase receipt or return of
        # an order currently owned by the supplier are included. The running
        # net_received accumulates per (code, unit snapshot) over every
        # matching entry, so a kind filter hides rows without recomputing
        # the cumulative values. Nothing is written here.
        supplier = text(supplier, "supplier")
        if kind is not None and kind not in ("purchase_receipt", "purchase_return"):
            raise ValueError("kind must be purchase_receipt, purchase_return or None")
        data = self._read()
        orders = {order["reference"]: order for order in data.get("purchases", [])}
        if supplier not in data.get("suppliers", {}):
            if not any(order["supplier"] == supplier for order in orders.values()):
                raise ValueError("unknown supplier")
        receipts = {}
        for purchase_reference, rows in data.get("purchase_receipts", {}).items():
            for row in rows:
                receipts[row["reference"]] = purchase_reference
        returns = {}
        for purchase_reference, rows in data.get("purchase_returns", {}).items():
            for row in rows:
                returns[row["reference"]] = (purchase_reference, row["receipt_reference"])
        entries = []
        nets = {}
        for row in data.get("movements", []):
            reference = row["reference"]
            related_reference = None
            if reference in receipts:
                entry_kind = "purchase_receipt"
                purchase_reference = receipts[reference]
            elif reference in returns:
                entry_kind = "purchase_return"
                purchase_reference, related_reference = returns[reference]
            else:
                continue
            order = orders.get(purchase_reference)
            if order is None or order["supplier"] != supplier:
                continue
            line = next((item for item in order.get("rows", []) if item["code"] == row["code"]), None)
            if line is None:
                continue
            key = (row["code"], line["unit"])
            nets[key] = nets.get(key, 0) + row["quantity"]
            if kind is None or kind == entry_kind:
                entries.append({
                    "code": row["code"],
                    "name": line["name"],
                    "unit": line["unit"],
                    "quantity": row["quantity"],
                    "reference": reference,
                    "kind": entry_kind,
                    "purchase_reference": purchase_reference,
                    "related_reference": related_reference,
                    "net_received": nets[key],
                })
        return entries

    @staticmethod
    def _supplier_snapshot(profile, name):
        # Normalized contact profile used for change snapshots; a missing
        # profile is represented by None. Legacy profiles default absent
        # fields to empty strings, matching the query paths.
        if profile is None:
            return None
        return {
            "supplier": name,
            "contact": profile.get("contact", ""),
            "phone": profile.get("phone", ""),
            "note": profile.get("note", ""),
        }

    def _record_supplier_change(self, data, name, action, related, before, after):
        # Append a change only when the normalized profile snapshot actually
        # differs (None marks "no profile"); repeat saves and no-op merge
        # sides succeed without growing the history. The sequence is per
        # supplier name and starts at 1, surviving deletion and re-creation
        # of the profile. Snapshots are fresh dicts so later business can
        # never rewrite a saved one.
        before = None if before is None else dict(before)
        after = None if after is None else dict(after)
        if before == after:
            return
        records = data.setdefault("supplier_changes", {}).setdefault(name, [])
        records.append({
            "supplier": name,
            "sequence": len(records) + 1,
            "action": action,
            "related_supplier": related,
            "before": before,
            "after": after,
        })

    def merge_supplier(self, source, target):
        source = text(source, "source")
        target = text(target, "target")
        data = self._read()
        profiles = data.get("suppliers", {})
        orders = data.get("purchases", [])

        def has_supplier(name):
            if name in profiles:
                return True
            return any(order["supplier"] == name for order in orders)

        if source == target:
            raise ValueError("source and target must be different suppliers")
        if not has_supplier(source):
            raise ValueError("unknown supplier")
        if not has_supplier(target):
            raise ValueError("unknown supplier")
        source_profile = profiles.get(source)
        target_profile = profiles.get(target)
        migrated = []
        for order in orders:
            if order["supplier"] == source:
                migrated.append((self._purchase_snapshot(order), order))
                order["supplier"] = target
        if source_profile is not None or target_profile is not None:
            source_before = self._supplier_snapshot(source_profile, source)
            target_before = self._supplier_snapshot(target_profile, target)
            merged = {"supplier": target}
            for field in ("contact", "phone", "note"):
                target_value = target_profile.get(field, "") if target_profile is not None else ""
                source_value = source_profile.get(field, "") if source_profile is not None else ""
                merged[field] = target_value if target_value else source_value
            profiles[target] = merged
            profiles.pop(source, None)
            self._record_supplier_change(data, source, "merge_supplier", target, source_before, None)
            self._record_supplier_change(data, target, "merge_supplier", source, target_before, merged)
        for before, order in migrated:
            self._record_purchase_change(data, order, "merge_supplier", before)
        self._write(data)
        return self.supplier_record(target)

    def _purchase_progress(self, data, order):
        purchase_reference = order["reference"]
        received = self._received_totals(data, purchase_reference)
        returned = self._returned_totals(data, purchase_reference)
        rows = []
        all_pending = True
        all_complete = True
        for line in order["rows"]:
            code = line["code"]
            received_qty = received.get(code, 0)
            returned_qty = returned.get(code, 0)
            if received_qty != 0:
                all_pending = False
            if received_qty != line["quantity"]:
                all_complete = False
            rows.append({
                **dict(line),
                "received": received_qty,
                "returned": returned_qty,
                "net_received": received_qty - returned_qty,
                "remaining": line["quantity"] - received_qty,
            })
        state = "pending" if all_pending else "complete" if all_complete else "partial"
        return {
            "reference": order["reference"],
            "supplier": order["supplier"],
            "status": order["status"],
            "rows": rows,
            "progress": state,
        }

    @staticmethod
    def _received_totals(data, purchase_reference):
        totals = {}
        for row in data.get("purchase_receipts", {}).get(purchase_reference, []):
            totals[row["code"]] = totals.get(row["code"], 0) + row["quantity"]
        return totals

    @staticmethod
    def _returned_totals(data, purchase_reference):
        totals = {}
        for row in data.get("purchase_returns", {}).get(purchase_reference, []):
            totals[row["code"]] = totals.get(row["code"], 0) + row["quantity"]
        return totals

    @staticmethod
    def _purchase_snapshot(order):
        return {
            "reference": order["reference"],
            "supplier": order["supplier"],
            "status": order["status"],
            "rows": [dict(row) for row in order["rows"]],
        }

    def _record_purchase_change(self, data, order, action, before):
        # Append a change only when the normalized purchase snapshot actually
        # differs; repeat submissions and repeat cancellations succeed without
        # growing the history. The sequence is per purchase and starts at 1.
        # Snapshots are fresh dicts so later business can never rewrite one.
        after = self._purchase_snapshot(order)
        if before == after:
            return
        records = data.setdefault("purchase_changes", {}).setdefault(order["reference"], [])
        records.append({
            "reference": order["reference"],
            "sequence": len(records) + 1,
            "action": action,
            "before": before,
            "after": after,
        })

    def set_active(self, code, active):
        code = text(code, "code")
        if type(active) is not bool:
            raise ValueError("active must be a boolean")
        data = self._read()
        if code not in data.get("materials", {}):
            raise ValueError("unknown material")
        before = self._material_profile(data, code)
        data.setdefault("status", {})[code] = active
        self._record_material_change(data, code, "set_active", before)
        self._write(data)
        return {"code": code, "active": active}

    def material_status(self, code):
        code = text(code, "code")
        data = self._read()
        if code not in data.get("materials", {}):
            raise ValueError("unknown material")
        return {"code": code, "active": data.get("status", {}).get(code, True)}

    def set_minimum(self, code, minimum):
        code = text(code, "code")
        if type(minimum) is not int or minimum < 0:
            raise ValueError("minimum must be a nonnegative integer")
        data = self._read()
        if code not in data.get("materials", {}):
            raise ValueError("unknown material")
        before = self._material_profile(data, code)
        data.setdefault("minimums", {})[code] = minimum
        self._record_material_change(data, code, "set_minimum", before)
        self._write(data)
        return {"code": code, "minimum": minimum}

    def material_changes(self, code):
        code = text(code, "code")
        data = self._read()
        if code not in data.get("materials", {}):
            raise ValueError("unknown material")
        return [
            {
                "code": row["code"],
                "sequence": row["sequence"],
                "action": row["action"],
                "before": dict(row["before"]),
                "after": dict(row["after"]),
            }
            for row in data.get("material_changes", {}).get(code, [])
        ]

    @staticmethod
    def _material_profile(data, code):
        # Complete effective material profile used for change snapshots.
        # Legacy data without minimum or active defaults to 0 and True.
        material = data["materials"][code]
        return {
            "name": material["name"],
            "unit": material["unit"],
            "minimum": data.get("minimums", {}).get(code, 0),
            "active": data.get("status", {}).get(code, True),
        }

    def _record_material_change(self, data, code, action, before):
        # Append a change only when the normalized effective profile actually
        # differs; repeat submissions succeed without growing the history.
        # The sequence is per material and starts at 1. Snapshots are fresh
        # dicts so later business can never rewrite a saved one.
        after = self._material_profile(data, code)
        if before == after:
            return
        records = data.setdefault("material_changes", {}).setdefault(code, [])
        records.append({
            "code": code,
            "sequence": len(records) + 1,
            "action": action,
            "before": dict(before),
            "after": dict(after),
        })

    def inventory(self, keyword="", active=None):
        if not isinstance(keyword, str):
            raise ValueError("keyword must be a string")
        if active is not None and type(active) is not bool:
            raise ValueError("active must be a boolean or None")
        keyword = keyword.strip()
        data = self._read()
        rows = data.get("movements", [])
        minimums = data.get("minimums", {})
        status = data.get("status", {})
        items = []
        for code, material in data.get("materials", {}).items():
            enabled = status.get(code, True)
            if active is not None and enabled != active:
                continue
            if keyword and keyword not in code and keyword not in material["name"]:
                continue
            quantity = sum(row["quantity"] for row in rows if row["code"] == code)
            items.append({"code": code, "name": material["name"], "unit": material["unit"], "quantity": quantity, "minimum": minimums.get(code, 0), "active": enabled})
        items.sort(key=lambda item: item["code"])
        return items

    def export_inventory_csv(self, keyword="", active=None):
        items = self.inventory(keyword=keyword, active=active)
        lines = ["code,name,unit,quantity,minimum,active"]
        for item in items:
            fields = [
                item["code"],
                item["name"],
                item["unit"],
                str(item["quantity"]),
                str(item["minimum"]),
                "true" if item["active"] else "false",
            ]
            lines.append(",".join(_csv_field(value) for value in fields))
        return "\n".join(lines) + "\n"

    def shortages(self):
        data = self._read()
        rows = data.get("movements", [])
        minimums = data.get("minimums", {})
        items = []
        for code, material in data.get("materials", {}).items():
            quantity = sum(row["quantity"] for row in rows if row["code"] == code)
            minimum = minimums.get(code, 0)
            if quantity < minimum:
                items.append({"code": code, "name": material["name"], "unit": material["unit"], "quantity": quantity, "minimum": minimum, "shortage": minimum - quantity})
        items.sort(key=lambda item: item["code"])
        return items

    def replenishment_plan(self, keyword=""):
        if not isinstance(keyword, str):
            raise ValueError("keyword must be a string")
        keyword = keyword.strip()
        data = self._read()
        rows = data.get("movements", [])
        minimums = data.get("minimums", {})
        status = data.get("status", {})
        materials = data.get("materials", {})
        selected = {}
        for code, material in materials.items():
            if not status.get(code, True):
                continue
            if keyword and keyword not in code and keyword not in material["name"]:
                continue
            quantity = sum(row["quantity"] for row in rows if row["code"] == code)
            minimum = minimums.get(code, 0)
            if quantity < minimum:
                selected[code] = (material, quantity, minimum)
        sources = {code: [] for code in selected}
        for order in data.get("purchases", []):
            if order.get("status") != "open":
                continue
            received = self._received_totals(data, order["reference"])
            for line in order.get("rows", []):
                code = line["code"]
                if code not in selected:
                    continue
                remaining = line["quantity"] - received.get(code, 0)
                if remaining <= 0:
                    continue
                if line["unit"] != selected[code][0]["unit"]:
                    raise ValueError("purchase unit differs from the current material unit")
                sources[code].append({"reference": order["reference"], "supplier": order["supplier"], "remaining": remaining})
        items = []
        for code, (material, quantity, minimum) in selected.items():
            purchases = sorted(sources[code], key=lambda item: item["reference"])
            incoming = sum(item["remaining"] for item in purchases)
            shortage = minimum - quantity
            items.append({
                "code": code,
                "name": material["name"],
                "unit": material["unit"],
                "quantity": quantity,
                "minimum": minimum,
                "shortage": shortage,
                "incoming": incoming,
                "suggested": max(shortage - incoming, 0),
                "purchases": purchases,
            })
        items.sort(key=lambda item: item["code"])
        return items

    def assign_locations(self, rows):
        if not isinstance(rows, list) or not rows:
            raise ValueError("rows must be a nonempty list")
        data = self._read()
        materials = data.get("materials", {})
        locations = data.get("locations", {})
        seen = set()
        planned = []
        for entry in rows:
            if not isinstance(entry, dict) or set(entry) != {"code", "location"}:
                raise ValueError("each row must be an object with code and location")
            code = text(entry["code"], "code")
            location = entry["location"]
            if not isinstance(location, str):
                raise ValueError("location must be a string")
            location = location.strip()
            if code not in materials:
                raise ValueError("unknown material")
            if code in seen:
                raise ValueError("material already exists in batch")
            seen.add(code)
            planned.append({"code": code, "before": locations.get(code, ""), "location": location})
        if any(row["before"] != row["location"] for row in planned):
            updated = data.setdefault("locations", {})
            histories = data.setdefault("location_changes", {})
            for row in planned:
                if row["before"] == row["location"]:
                    continue
                code = row["code"]
                if row["location"]:
                    updated[code] = row["location"]
                else:
                    updated.pop(code, None)
                records = histories.setdefault(code, [])
                records.append({
                    "code": code,
                    "sequence": len(records) + 1,
                    "before": row["before"],
                    "after": row["location"],
                })
            self._write(data)
        return planned

    def location_inventory(self, location):
        if not isinstance(location, str):
            raise ValueError("location must be a string")
        location = location.strip()
        data = self._read()
        rows = data.get("movements", [])
        minimums = data.get("minimums", {})
        status = data.get("status", {})
        locations = data.get("locations", {})
        items = []
        for code, material in data.get("materials", {}).items():
            if locations.get(code, "") != location:
                continue
            quantity = sum(row["quantity"] for row in rows if row["code"] == code)
            items.append({
                "code": code,
                "name": material["name"],
                "unit": material["unit"],
                "quantity": quantity,
                "minimum": minimums.get(code, 0),
                "active": status.get(code, True),
                "location": location,
            })
        items.sort(key=lambda item: item["code"])
        return items

    def location_changes(self, code):
        code = text(code, "code")
        data = self._read()
        if code not in data.get("materials", {}):
            raise ValueError("unknown material")
        return [
            {
                "code": row["code"],
                "sequence": row["sequence"],
                "before": row["before"],
                "after": row["after"],
            }
            for row in data.get("location_changes", {}).get(code, [])
        ]

    @staticmethod
    def _require_unique_reference(data, reference):
        if any(row["reference"] == reference for row in data.get("movements", [])):
            raise ValueError("reference already exists")
        if any(row["reference"] == reference for row in data.get("counts", [])):
            raise ValueError("reference already exists")

def _csv_field(value):
    # Strict CSV output: quote fields containing commas, quotes or line
    # breaks; escape embedded quotes by doubling them. Other whitespace is
    # preserved verbatim.
    if any(char in value for char in (",", '"', "\r", "\n")):
        return '"' + value.replace('"', '""') + '"'
    return value

def _parse_csv(content):
    # Strict CSV: comma-separated fields, double-quoted fields may contain
    # commas, quotes (escaped as "") and newlines. Blank lines parse to [].
    records = []
    index = 0
    length = len(content)
    while index < length:
        if content[index] in "\r\n":
            records.append([])
            index += 1
            if content[index - 1] == "\r" and index < length and content[index] == "\n":
                index += 1
            continue
        fields = []
        while True:
            if content[index] == '"':
                index += 1
                chars = []
                while True:
                    if index >= length:
                        raise ValueError("unterminated quoted field")
                    if content[index] == '"':
                        if index + 1 < length and content[index + 1] == '"':
                            chars.append('"')
                            index += 2
                        else:
                            index += 1
                            break
                    else:
                        chars.append(content[index])
                        index += 1
                if index < length and content[index] not in ",\r\n":
                    raise ValueError("unexpected character after closing quote")
                fields.append("".join(chars))
            else:
                start = index
                while index < length and content[index] not in ",\r\n":
                    if content[index] == '"':
                        raise ValueError("unexpected quote in unquoted field")
                    index += 1
                fields.append(content[start:index])
            if index < length and content[index] == ",":
                index += 1
                if index >= length or content[index] in "\r\n":
                    fields.append("")
                    break
                continue
            break
        if index < length and content[index] == "\r":
            index += 1
            if index < length and content[index] == "\n":
                index += 1
        elif index < length and content[index] == "\n":
            index += 1
        records.append(fields)
    return records
