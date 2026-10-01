from .storage import JsonStore, text, positive

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

    def _reference_taken(self, data, reference):
        rows = data.get("movements", [])
        counts = data.get("counts", [])
        return any(row["reference"] == reference for row in rows + counts)

    def movement(self, code, quantity, reference):
        reference = text(reference, "reference")
        if type(quantity) is not int or quantity == 0:
            raise ValueError("quantity must be a nonzero integer")
        data = self._read()
        if code not in data.get("materials", {}):
            raise ValueError("unknown material")
        rows = data.setdefault("movements", [])
        if self._reference_taken(data, reference):
            raise ValueError("reference already exists")
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

    def count(self, code, counted, reference):
        code, reference = text(code, "code"), text(reference, "reference")
        if type(counted) is not int or counted < 0:
            raise ValueError("counted must be a nonnegative integer")
        data = self._read()
        if code not in data.get("materials", {}):
            raise ValueError("unknown material")
        if self._reference_taken(data, reference):
            raise ValueError("reference already exists")
        rows = data.setdefault("movements", [])
        before = sum(row["quantity"] for row in rows if row["code"] == code)
        record = {"code": code, "reference": reference, "before": before,
                  "counted": counted, "difference": counted - before}
        data.setdefault("counts", []).append(record)
        if record["difference"]:
            rows.append({"code": code, "quantity": record["difference"], "reference": reference})
        self._write(data)
        return dict(record)

    def counts(self, code):
        self.stock(code)
        return [row for row in self._read().get("counts", []) if row["code"] == code]

    def history(self, code):
        self.stock(code)
        return [row for row in self._read().get("movements", []) if row["code"] == code]
