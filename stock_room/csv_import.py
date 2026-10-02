"""严格 RFC 4180 风格的 CSV 解析（仅逗号分隔、双引号包裹）。"""

HEADERS = ("code", "name", "unit")


def parse_catalog(content):
    """解析整份物料目录 CSV，返回按记录顺序排列的 (code, name, unit) 列表。

    兼容开头的一个可选 BOM、LF 与 CRLF 换行，以及引号内换行和连续双引号转义。
    表头必须恰好是 code、name、unit 三列（顺序可变，列名原文匹配）；
    数据区域只有完全空白的行忽略，仅含空白或分隔符的记录仍参与校验。
    任何格式或字段问题均抛出 ValueError。
    """
    records = _parse_records(content)
    if not records:
        raise ValueError("CSV must contain a header row")
    header = records[0]
    if len(header) != len(HEADERS) or set(header) != set(HEADERS) or len(set(header)) != len(header):
        raise ValueError("CSV header must contain exactly code, name and unit columns")
    index = {name: header.index(name) for name in HEADERS}
    rows = []
    for record in records[1:]:
        # 仅真正的空行（一个空字段、无任何字符或分隔符）忽略；空白行与分隔符行继续校验。
        if record == [""]:
            continue
        if len(record) != len(HEADERS):
            raise ValueError("each CSV record must contain exactly three fields")
        values = []
        for name in HEADERS:
            value = record[index[name]].strip()
            if not value:
                raise ValueError(name + " must be a nonempty string")
            values.append(value)
        rows.append(tuple(values))
    return rows


def _parse_records(content):
    if not isinstance(content, str):
        raise ValueError("content must be a string")
    if content.startswith("﻿"):
        content = content[1:]
    if content == "":
        raise ValueError("content must not be empty")
    records = []
    fields = [""]
    in_quotes = False
    field_quoted = False
    at_field_start = True
    i = 0
    n = len(content)
    while i < n:
        char = content[i]
        if in_quotes:
            if char == '"':
                if i + 1 < n and content[i + 1] == '"':
                    fields[-1] += '"'
                    i += 2
                    continue
                in_quotes = False
                i += 1
                continue
            fields[-1] += char
            i += 1
            continue
        if char == '"':
            if not at_field_start:
                raise ValueError("quoted field must start at the beginning of a field")
            in_quotes = True
            field_quoted = True
            at_field_start = False
        elif char == ",":
            if field_quoted:
                field_quoted = False
            fields.append("")
            at_field_start = True
        elif char == "\n":
            records.append(fields)
            fields = [""]
            field_quoted = False
            at_field_start = True
        elif char == "\r":
            if i + 1 < n and content[i + 1] == "\n":
                i += 1
            records.append(fields)
            fields = [""]
            field_quoted = False
            at_field_start = True
        else:
            if field_quoted:
                raise ValueError("quoted field must end before unquoted text")
            fields[-1] += char
            at_field_start = False
        i += 1
    if in_quotes:
        raise ValueError("unterminated quoted field")
    records.append(fields)
    return records
