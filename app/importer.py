"""Bytes or a link in, a normalised order list out.

This is the whole of Task 2's data path: sniff the format, decode it, find the
table, hand the rows to schema.parse_table. Everything here is written to keep
going where it can and complain in the result rather than raise — a group order
with two odd rows should still reach the preview.
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass, field

from . import sheets, xlsx
from .schema import Issue, OrderRow, is_blank_row, parse_table, score_header_row

MAX_UPLOAD_BYTES = 8 * 1024 * 1024

# Encodings to try, in order. Sheets exported from Excel on Windows are very
# often cp1252, and latin-1 never fails so it is the backstop.
ENCODINGS = ("utf-8-sig", "utf-8", "cp1252", "latin-1")


class UnreadableFile(ValueError):
    """Fatal: nothing could be read at all."""


@dataclass
class ImportResult:
    rows: list[OrderRow] = field(default_factory=list)
    column_map: dict = field(default_factory=dict)
    issues: list[Issue] = field(default_factory=list)
    source: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return bool(self.rows) and not any(issue.level == "error" for issue in self.issues)

    @property
    def stats(self) -> dict:
        usable = [row for row in self.rows if row.ok]
        return {
            "rows": len(self.rows),
            "usable_rows": len(usable),
            "drinks": sum(row.quantity for row in usable),
            "people": len({row.person.strip().lower() for row in usable if row.person.strip()}),
            "errors": sum(1 for row in self.rows if not row.ok)
                      + sum(1 for issue in self.issues if issue.level == "error"),
            "warnings": sum(1 for row in self.rows for i in row.issues if i.level == "warning")
                        + sum(1 for issue in self.issues if issue.level == "warning"),
        }

    def as_dict(self) -> dict:
        return {
            "source": self.source,
            "column_map": self.column_map,
            "stats": self.stats,
            "issues": [issue.as_dict() for issue in self.issues],
            "rows": [row.as_dict() for row in self.rows],
            "ok": self.ok,
        }


# --- format sniffing --------------------------------------------------------


def sniff_kind(filename: str, data: bytes) -> str:
    """Decide the format from the bytes first, the extension second."""
    name = (filename or "").lower()
    if data[:4] == b"PK\x03\x04":
        # Every OOXML file is a zip. Numbers and ODS are too, so check inside.
        if name.endswith(".numbers"):
            return "numbers"
        if name.endswith(".ods"):
            return "ods"
        return "xlsx"
    if data[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        return "xls"  # legacy OLE2 container
    if name.endswith((".csv", ".tsv", ".txt")):
        return "csv"
    if name.endswith(".xlsx"):
        return "xlsx"  # claims to be xlsx but is not a zip; let the reader explain
    return "csv"  # anything else that decodes as text gets a fair try


def decode_text(data: bytes) -> tuple[str, str]:
    for encoding in ENCODINGS:
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1", errors="replace"), "latin-1"


def read_csv_rows(text: str) -> tuple[list[list[str]], str]:
    """Parse delimited text, sniffing between comma / tab / semicolon / pipe."""
    sample = "\n".join(text.splitlines()[:20])
    delimiter = ","
    try:
        delimiter = csv.Sniffer().sniff(sample, delimiters=",\t;|").delimiter
    except csv.Error:
        # Sniffer gives up on single-column files; count instead.
        counts = {candidate: sample.count(candidate) for candidate in ",\t;|"}
        best = max(counts, key=counts.get)
        if counts[best] > 0:
            delimiter = best

    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    rows = []
    for row in reader:
        rows.append(row)
        if len(rows) > 5000:
            break
    return rows, delimiter


def _pick_worksheet(worksheets: list[tuple[str, list[list]]]) -> tuple[str, list[list], list[str]]:
    """Choose the tab that most looks like an order list; name the rest."""
    best = None
    best_score = -1
    for name, rows in worksheets:
        if not any(not is_blank_row(row) for row in rows):
            continue
        score = max((score_header_row(row)[0] for row in rows[:15]), default=0)
        if score > best_score:
            best, best_score = (name, rows), score
    if best is None:
        name, rows = worksheets[0]
        best = (name, rows)
    others = [name for name, _rows in worksheets if name != best[0]]
    return best[0], best[1], others


# --- entry points -----------------------------------------------------------


def import_bytes(data: bytes, filename: str = "") -> ImportResult:
    if not data:
        raise UnreadableFile("that file is empty")
    if len(data) > MAX_UPLOAD_BYTES:
        raise UnreadableFile(f"that file is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")

    kind = sniff_kind(filename, data)
    source = {"kind": "upload", "filename": filename or "pasted data", "format": kind,
              "bytes": len(data)}
    issues: list[Issue] = []

    if kind == "xls":
        raise UnreadableFile(
            "that's an old-style .xls file. Open it and use File > Save As to make a "
            ".xlsx or .csv, then upload that")
    if kind in ("numbers", "ods"):
        raise UnreadableFile(
            f"{'Apple Numbers' if kind == 'numbers' else 'OpenDocument'} files aren't "
            "supported. Export as .xlsx or .csv and upload that")

    if kind == "xlsx":
        try:
            worksheets = xlsx.read_sheets(data)
        except xlsx.XlsxError as exc:
            raise UnreadableFile(str(exc)) from exc
        sheet_name, rows, others = _pick_worksheet(worksheets)
        source["sheet"] = sheet_name
        if others:
            issues.append(Issue("info", f"read the \"{sheet_name}\" tab; ignored "
                                        f"{', '.join(repr(o) for o in others)}"))
    else:
        text, encoding = decode_text(data)
        rows, delimiter = read_csv_rows(text)
        source["encoding"] = encoding
        source["delimiter"] = delimiter
        if encoding not in ("utf-8", "utf-8-sig"):
            issues.append(Issue("info", f"read as {encoding} text; accents may need a check"))

    order_rows, column_map, table_issues = parse_table(rows)
    return ImportResult(rows=order_rows, column_map=column_map,
                        issues=issues + table_issues, source=source)


def import_sheet_link(link: str) -> ImportResult:
    """Fetch a Google Sheet and import it. Raises sheets.SheetError on bad links."""
    data, resolved = sheets.fetch_csv(link)
    result = import_bytes(data, filename="google-sheet.csv")
    result.source = {"kind": "google_sheet", "url": link.strip(), "export_url": resolved,
                     "format": "csv", "bytes": len(data),
                     "encoding": result.source.get("encoding"),
                     "delimiter": result.source.get("delimiter")}
    return result


def import_json(payload: str) -> ImportResult:
    """Accept an already-parsed table (list of dicts or list of lists) as JSON.

    Not used by the page; it exists so Tasks 3-5 and the tests can feed the same
    normaliser without inventing a spreadsheet.
    """
    data = json.loads(payload)
    if isinstance(data, dict):
        data = data.get("rows", [])
    if data and isinstance(data[0], dict):
        headers = list(data[0].keys())
        rows = [headers] + [[row.get(header, "") for header in headers] for row in data]
    else:
        rows = [list(row) for row in data]
    order_rows, column_map, issues = parse_table(rows)
    return ImportResult(rows=order_rows, column_map=column_map, issues=issues,
                        source={"kind": "json", "format": "json"})
