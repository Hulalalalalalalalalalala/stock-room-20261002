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

    def export_inventory_csv(self, keyword="", active=None):
        items = self.inventory(keyword=keyword, active=active)
        records = ["code,name,unit,quantity,minimum,active"]
        for item in items:
            fields = [
                item["code"],
                item["name"],
                item["unit"],
                str(item["quantity"]),
                str(item["minimum"]),
                "true" if item["active"] else "false",
            ]
            records.append(",".join(_csv_quote(field) for field in fields))
        return "\n".join(records) + "\n"

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

def _csv_quote(field):
    if any(char in field for char in (',', '"', '\r', '\n')):
        return '"' + field.replace('"', '""') + '"'
    return field

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
