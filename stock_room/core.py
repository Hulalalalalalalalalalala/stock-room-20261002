from .storage import JsonStore, text

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

    def movement(self, code, quantity, reference):
        reference = text(reference, "reference")
        if type(quantity) is not int or quantity == 0:
            raise ValueError("quantity must be a nonzero integer")
        data = self._read()
        if code not in data.get("materials", {}):
            raise ValueError("unknown material")
        rows = data.setdefault("movements", [])
        self._require_unique_reference(data, reference)
        stock = sum(row["quantity"] for row in rows if row["code"] == code)
        if stock + quantity < 0:
            raise ValueError("insufficient stock")
        row = {"code": code, "quantity": quantity, "reference": reference}
        rows.append(row)
        self._write(data)
        return {**row, "balance": stock + quantity}

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

    @staticmethod
    def _require_unique_reference(data, reference):
        if any(row["reference"] == reference for row in data.get("movements", [])):
            raise ValueError("reference already exists")
        if any(row["reference"] == reference for row in data.get("counts", [])):
            raise ValueError("reference already exists")
