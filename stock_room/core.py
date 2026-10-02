from .storage import JsonStore, text
from .csv_import import parse_catalog

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
        rows = parse_catalog(content)
        data = self._read()
        materials = data.setdefault("materials", {})
        parsed = []
        seen = set()
        for code, name, unit in rows:
            if code in seen or code in materials:
                raise ValueError("material already exists")
            seen.add(code)
            parsed.append({"code": code, "name": name, "unit": unit})
        if not parsed:
            return []
        for material in parsed:
            materials[material["code"]] = dict(material)
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
        materials[code]["name"] = name
        materials[code]["unit"] = unit
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
        materials = data.get("materials", {})
        status = data.get("status", {})
        existing = data.get("movements", [])
        balances = {}
        seen = set()
        parsed = []
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
            balances[code] += quantity
            if balances[code] < 0:
                raise ValueError("insufficient stock")
            seen.add(reference)
            parsed.append({"code": code, "quantity": quantity, "reference": reference, "balance": balances[code]})
        data.setdefault("movements", []).extend(
            {"code": row["code"], "quantity": row["quantity"], "reference": row["reference"]} for row in parsed
        )
        self._write(data)
        return parsed

    def stock(self, code):
        data = self._read()
        if code not in data.get("materials", {}):
            raise ValueError("unknown material")
        return {**data["materials"][code], "quantity": sum(row["quantity"] for row in data.get("movements", []) if row["code"] == code)}

    def history(self, code):
        self.stock(code)
        return [row for row in self._read().get("movements", []) if row["code"] == code]

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
            parsed.append({"code": code, "reference": reference, "before": before, "counted": counted, "difference": counted - before})
        counts = data.setdefault("counts", [])
        movements = data.setdefault("movements", [])
        for record in parsed:
            counts.append(dict(record))
            if record["difference"] != 0:
                movements.append({"code": record["code"], "quantity": record["difference"], "reference": record["reference"]})
        self._write(data)
        return [dict(record) for record in parsed]

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

    def reversals(self, code):
        code = text(code, "code")
        data = self._read()
        if code not in data.get("materials", {}):
            raise ValueError("unknown material")
        return [dict(row) for row in data.get("reversals", []) if row["code"] == code]

    def set_active(self, code, active):
        code = text(code, "code")
        if type(active) is not bool:
            raise ValueError("active must be a boolean")
        data = self._read()
        if code not in data.get("materials", {}):
            raise ValueError("unknown material")
        data.setdefault("status", {})[code] = active
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
        data.setdefault("minimums", {})[code] = minimum
        self._write(data)
        return {"code": code, "minimum": minimum}

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

    @staticmethod
    def _require_unique_reference(data, reference):
        if any(row["reference"] == reference for row in data.get("movements", [])):
            raise ValueError("reference already exists")
        if any(row["reference"] == reference for row in data.get("counts", [])):
            raise ValueError("reference already exists")
