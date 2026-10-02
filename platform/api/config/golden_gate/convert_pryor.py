"""Reproduce release matrices from unchanged primary Pryor XLSX files (stdlib only).

Run: python config/golden_gate/convert_pryor.py
No downloads. Source axes are retained independently, never positionally aligned.
"""
from __future__ import annotations

import gzip
import hashlib
import itertools
import json
from pathlib import Path
import xml.etree.ElementTree as ET
from zipfile import ZipFile

VERSION = "pryor-xlsx-labels-v1"
NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def convert(path: Path, length: int) -> dict:
    with ZipFile(path) as archive:
        strings = ["".join(x.itertext()) for x in ET.fromstring(
            archive.read("xl/sharedStrings.xml")).findall("s:si", NS)]
        sheets = ET.fromstring(archive.read("xl/workbook.xml")).findall("s:sheets/s:sheet", NS)
        if len(sheets) != 1:
            raise ValueError("Expected one worksheet")
        rows = []
        for row in ET.fromstring(archive.read("xl/worksheets/sheet1.xml")).findall("s:sheetData/s:row", NS):
            cells = []
            for cell in row.findall("s:c", NS):
                value = cell.find("s:v", NS)
                if value is None:
                    continue
                if cell.find("s:f", NS) is not None:
                    raise ValueError("Formula in observation matrix")
                text = value.text
                if text is None:
                    raise ValueError("Empty observation cell")
                cells.append(strings[int(text)] if cell.get("t") == "s" else int(text))
            rows.append(cells)
    size = 4 ** length
    assert len(rows) == size + 1 and all(len(row) == size + 1 for row in rows)
    columns = rows[0][1:]
    labels = [row[0] for row in rows[1:]]
    expected = {"".join(x) for x in itertools.product("ACGT", repeat=length)}
    assert set(columns) == set(labels) == expected
    assert len(set(columns)) == len(set(labels)) == size
    values = [row[1:] for row in rows[1:]]
    assert all(type(v) is int and v >= 0 for row in values for v in row)
    return {"conversion_version": VERSION, "worksheet": sheets[0].get("name"),
            "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "row_labels": labels, "column_labels": columns, "observations": values}


def main() -> None:
    root = Path(__file__).resolve().parent
    manifest = json.loads((root / "manifest.json").read_text())
    for item in manifest:
        parsed = convert(root / item["file_name"], item["end_length"])
        assert parsed["source_sha256"] == item["sha256"]
        assert parsed["worksheet"] == item["worksheet"]
        payload = gzip.compress(json.dumps(parsed, separators=(",", ":"), ensure_ascii=True).encode(), mtime=0)
        assert hashlib.sha256(payload).hexdigest() == item["parsed_sha256"]
        (root / item["parsed_file"]).write_bytes(payload)
        print(item["id"], parsed["worksheet"], item["parsed_sha256"])


if __name__ == "__main__":
    main()
