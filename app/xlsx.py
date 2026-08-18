"""A small read-only .xlsx reader built on zipfile + ElementTree.

openpyxl is not installable here, and an .xlsx is just a zip of XML, so the
subset we need (cell values, shared strings, date formats) is cheap to read
directly. Everything not needed for reading an order list — formulas, styles,
charts, merged cells — is ignored on purpose.

Not supported: the old binary .xls format. That is a completely different
container; callers should detect it and tell the user to re-save.
"""

from __future__ import annotations

import datetime as _dt
import io
import re
import zipfile
from xml.etree import ElementTree as ET

MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"

# Built-in number format ids that mean "this number is a date/time".
_BUILTIN_DATE_FORMATS = set(range(14, 23)) | set(range(45, 48))

_CELL_REF_RE = re.compile(r"^([A-Z]+)")

# Excel's epoch is 1899-12-30 (the 1900 leap-year bug is baked in above day 60).
_EXCEL_EPOCH = _dt.datetime(1899, 12, 30)


class XlsxError(ValueError):
    pass


def _q(tag: str, ns: str = MAIN_NS) -> str:
    return f"{{{ns}}}{tag}"


def column_index(cell_ref: str) -> int:
    """'C7' -> 2 (zero-based). Cells are sparse, so this is how we place them."""
    match = _CELL_REF_RE.match(cell_ref or "")
    if not match:
        return 0
    index = 0
    for char in match.group(1):
        index = index * 26 + (ord(char) - ord("A") + 1)
    return index - 1


def _text_of(element) -> str:
    """Concatenate every <t> under an element (shared strings use <r> runs)."""
    return "".join(node.text or "" for node in element.iter(_q("t")))


def _serial_to_value(serial: float):
    """Excel date serial -> date/datetime. Whole numbers come back as dates."""
    try:
        value = _EXCEL_EPOCH + _dt.timedelta(days=float(serial))
    except (OverflowError, ValueError):
        return serial
    if value.time() == _dt.time(0, 0):
        return value.date()
    return value


def _read_shared_strings(zf: zipfile.ZipFile) -> list[str]:
    try:
        raw = zf.read("xl/sharedStrings.xml")
    except KeyError:
        return []
    root = ET.fromstring(raw)
    return [_text_of(si) for si in root.findall(_q("si"))]


def _read_date_styles(zf: zipfile.ZipFile) -> set[int]:
    """Indices into cellXfs whose number format renders as a date."""
    try:
        root = ET.fromstring(zf.read("xl/styles.xml"))
    except KeyError:
        return set()

    custom_date_ids = set()
    for fmt in root.iter(_q("numFmt")):
        code = (fmt.get("formatCode") or "").lower()
        # Strip literals like "yyyy" inside quotes before sniffing for d/m/y.
        code = re.sub(r'"[^"]*"', "", code)
        code = re.sub(r"\[[^\]]*\]", "", code)
        if any(ch in code for ch in "dmyhs"):
            try:
                custom_date_ids.add(int(fmt.get("numFmtId")))
            except (TypeError, ValueError):
                continue

    date_styles = set()
    cell_xfs = root.find(_q("cellXfs"))
    if cell_xfs is None:
        return date_styles
    for index, xf in enumerate(cell_xfs.findall(_q("xf"))):
        try:
            fmt_id = int(xf.get("numFmtId", "0"))
        except ValueError:
            continue
        if fmt_id in _BUILTIN_DATE_FORMATS or fmt_id in custom_date_ids:
            date_styles.add(index)
    return date_styles


def _sheet_targets(zf: zipfile.ZipFile) -> list[tuple[str, str]]:
    """[(sheet name, zip path)] in the workbook's own tab order."""
    try:
        workbook = ET.fromstring(zf.read("xl/workbook.xml"))
    except KeyError as exc:
        raise XlsxError("not a workbook: xl/workbook.xml is missing") from exc

    rels: dict[str, str] = {}
    try:
        rel_root = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
    except KeyError:
        rel_root = None
    if rel_root is not None:
        for rel in rel_root.findall(_q("Relationship", PKG_REL_NS)):
            target = rel.get("Target", "")
            if target.startswith("/"):
                target = target.lstrip("/")
            elif not target.startswith("xl/"):
                target = "xl/" + target
            rels[rel.get("Id", "")] = target

    sheets = []
    for index, sheet in enumerate(workbook.iter(_q("sheet")), start=1):
        name = sheet.get("name") or f"Sheet{index}"
        path = rels.get(sheet.get(_q("id", REL_NS), ""))
        if not path:
            path = f"xl/worksheets/sheet{index}.xml"
        if path in zf.namelist():
            sheets.append((name, path))
    return sheets


def _read_sheet(zf: zipfile.ZipFile, path: str, shared, date_styles, max_rows) -> list[list]:
    root = ET.fromstring(zf.read(path))
    rows: list[list] = []

    for row in root.iter(_q("row")):
        values: list = []
        for cell in row.findall(_q("c")):
            index = column_index(cell.get("r", ""))
            while len(values) < index:
                values.append("")
            values.append(_cell_value(cell, shared, date_styles))
        rows.append(values)
        if max_rows and len(rows) >= max_rows:
            break

    while rows and not any(str(v).strip() for v in rows[-1]):
        rows.pop()
    return rows


def _cell_value(cell, shared: list[str], date_styles: set[int]):
    cell_type = cell.get("t", "n")

    if cell_type == "inlineStr":
        return _text_of(cell)

    value_node = cell.find(_q("v"))
    if value_node is None or value_node.text is None:
        # A formula cell with no cached value; nothing to recover.
        return ""
    raw = value_node.text

    if cell_type == "s":
        try:
            return shared[int(raw)]
        except (ValueError, IndexError):
            return ""
    if cell_type in ("str", "e"):
        return raw
    if cell_type == "b":
        return raw == "1"

    try:
        number = float(raw)
    except ValueError:
        return raw

    try:
        style = int(cell.get("s", "-1"))
    except ValueError:
        style = -1
    if style in date_styles:
        return _serial_to_value(number)

    return int(number) if number.is_integer() else number


def read_sheets(data: bytes, max_rows: int = 5000) -> list[tuple[str, list[list]]]:
    """Read every worksheet as [(name, rows)], rows being lists of cell values."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise XlsxError("this file is not a readable .xlsx workbook") from exc

    with zf:
        shared = _read_shared_strings(zf)
        date_styles = _read_date_styles(zf)
        targets = _sheet_targets(zf)
        if not targets:
            raise XlsxError("the workbook has no worksheets")
        return [
            (name, _read_sheet(zf, path, shared, date_styles, max_rows))
            for name, path in targets
        ]
