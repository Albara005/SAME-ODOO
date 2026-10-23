"""CSV reading and writing for import/export.

* Output is UTF-8 with a BOM so spreadsheet apps show Arabic text correctly.
* Cells that a spreadsheet would run as a formula (=, +, -, @, tab, CR) are
  prefixed with an apostrophe on export (CSV injection, OWASP).
"""

from __future__ import annotations

import csv
import io
from typing import Iterable

from fastapi import Response

_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")
MAX_IMPORT_ROWS = 5000


class CsvError(ValueError):
    pass


def safe_cell(value: object) -> str:
    text = "" if value is None else str(value)
    if text.startswith(_FORMULA_START):
        # Plain negative numbers are data, not formulas.
        try:
            float(text)
            return text
        except ValueError:
            return "'" + text
    return text


def csv_response(filename: str, header: list[str], rows: Iterable[Iterable[object]]) -> Response:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(header)
    for row in rows:
        writer.writerow([safe_cell(v) for v in row])
    return Response(
        content="﻿" + buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def read_csv(raw: bytes, required: set[str], allowed: set[str]) -> list[dict[str, str]]:
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise CsvError("file must be UTF-8 encoded")
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        raise CsvError("file is empty")
    headers = {h.strip() for h in reader.fieldnames}
    missing = required - headers
    if missing:
        raise CsvError(f"missing columns: {sorted(missing)}")
    unknown = headers - allowed
    if unknown:
        raise CsvError(f"unknown columns: {sorted(unknown)}")
    rows = []
    for row in reader:
        if len(rows) >= MAX_IMPORT_ROWS:
            raise CsvError(f"at most {MAX_IMPORT_ROWS} rows per import")
        rows.append({(k or "").strip(): (v or "").strip() for k, v in row.items() if k is not None})
    return rows
