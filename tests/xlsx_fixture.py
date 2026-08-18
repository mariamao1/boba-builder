"""Build a minimal .xlsx in memory, so the reader can be tested without Excel.

Writes only what app.xlsx reads: workbook, rels, one or more sheets, optional
shared strings, and an optional date-formatted style.
"""

from __future__ import annotations

import io
import zipfile
from xml.sax.saxutils import escape

CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
 <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
 <Default Extension="xml" ContentType="application/xml"/>
 <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
</Types>"""

ROOT_RELS = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""

STYLES = """<?xml version="1.0" encoding="UTF-8"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
 <cellXfs count="2"><xf numFmtId="0"/><xf numFmtId="14" applyNumberFormat="1"/></cellXfs>
</styleSheet>"""


def _column_name(index: int) -> str:
    name = ""
    index += 1
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(ord("A") + remainder) + name
    return name


def _cell(reference: str, value) -> str:
    if isinstance(value, tuple) and value and value[0] == "date":
        return f'<c r="{reference}" s="1"><v>{value[1]}</v></c>'
    if isinstance(value, tuple) and value and value[0] == "shared":
        return f'<c r="{reference}" t="s"><v>{value[1]}</v></c>'
    if isinstance(value, (int, float)):
        return f'<c r="{reference}"><v>{value}</v></c>'
    if value == "":
        return ""
    return f'<c r="{reference}" t="inlineStr"><is><t>{escape(str(value))}</t></is></c>'


def _sheet_xml(rows: list[list]) -> str:
    parts = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">',
             "<sheetData>"]
    for row_index, row in enumerate(rows, start=1):
        cells = "".join(_cell(f"{_column_name(i)}{row_index}", value)
                        for i, value in enumerate(row))
        parts.append(f'<row r="{row_index}">{cells}</row>')
    parts.append("</sheetData></worksheet>")
    return "".join(parts)


def build_xlsx(sheets: dict[str, list[list]], shared_strings: list[str] | None = None) -> bytes:
    """sheets: {tab name: rows}. Cell values may be str/int/float, or the tuples
    ("shared", index) and ("date", excel serial)."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", CONTENT_TYPES)
        zf.writestr("_rels/.rels", ROOT_RELS)
        zf.writestr("xl/styles.xml", STYLES)

        sheet_entries, rel_entries = [], []
        for index, name in enumerate(sheets, start=1):
            rel_id = f"rId{index}"
            sheet_entries.append(
                f'<sheet name="{escape(name)}" sheetId="{index}" '
                f'r:id="{rel_id}"/>')
            rel_entries.append(
                f'<Relationship Id="{rel_id}" Type="http://schemas.openxmlformats.org/'
                f'officeDocument/2006/relationships/worksheet" '
                f'Target="worksheets/sheet{index}.xml"/>')
            zf.writestr(f"xl/worksheets/sheet{index}.xml", _sheet_xml(sheets[name]))

        zf.writestr("xl/workbook.xml",
                    '<?xml version="1.0" encoding="UTF-8"?>'
                    '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                    f'<sheets>{"".join(sheet_entries)}</sheets></workbook>')
        zf.writestr("xl/_rels/workbook.xml.rels",
                    '<?xml version="1.0" encoding="UTF-8"?>'
                    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
                    f'relationships">{"".join(rel_entries)}</Relationships>')

        if shared_strings is not None:
            items = "".join(f"<si><t>{escape(text)}</t></si>" for text in shared_strings)
            zf.writestr("xl/sharedStrings.xml",
                        '<?xml version="1.0" encoding="UTF-8"?>'
                        '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                        f'count="{len(shared_strings)}">{items}</sst>')

    return buffer.getvalue()
