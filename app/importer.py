"""Bytes or a link in, a structured order list out.

The data path, end to end: sniff the format, decode it, find the table, hand the
rows to schema.parse_table for structure, then to options.annotate to resolve
the values against the store's option set. Everything here is written to keep
going where it can and complain in the result rather than raise — a group order
with two odd rows should still reach the preview, with those two rows flagged.
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass, field

from . import options, sheets, xlsx
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


def parse_and_resolve(rows: list[list]) -> tuple[list[OrderRow], dict, list[Issue]]:
    """The two halves of parsing, in order: structure, then vocabulary.

    schema.parse_table finds the table and cleans it up; options.annotate maps
    what it found onto the store's real option set. Every entry point goes
    through here so an uploaded file, a Google Sheet and a JSON table all come
    out identically shaped.
    """
    order_rows, column_map, issues = parse_table(rows)
    if order_rows:
        issues = issues + options.annotate(order_rows)
    return order_rows, column_map, issues


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

    order_rows, column_map, table_issues = parse_and_resolve(rows)
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


class RowNotFound(KeyError):
    """That row number isn't in this run."""


#: What the preview's review screen is allowed to change on a row: the order
#: itself. The source, the column mapping and everything the sheet said stay as
#: they were — a correction is recorded alongside the original, never over it.
EDITABLE_FIELDS = frozenset({"drink", "size", "sugar", "ice", "milk", "toppings",
                             "quantity", "person", "notes"})

_FIELD_LABELS = {"drink": "drink", "size": "size", "sugar": "sugar level",
                 "ice": "ice level", "milk": "milk", "toppings": "toppings",
                 "quantity": "quantity", "person": "name", "notes": "notes"}

MAX_QUANTITY = 20  # same ceiling the importer puts on a typo'd quantity column


def _clean(field: str, value):
    """A change from the page -> the same shape the parser would have produced."""
    if field == "toppings":
        if isinstance(value, str):
            value = [value] if value.strip() else []
        return [str(entry).strip() for entry in value or [] if str(entry).strip()]
    if field == "quantity":
        try:
            count = int(str(value).strip() or 1)
        except ValueError:
            raise ValueError("that quantity isn't a number") from None
        if count < 1:
            raise ValueError("a quantity of nothing isn't an order — remove the row instead")
        return min(count, MAX_QUANTITY)
    return str(value or "").strip()


def _describe(value) -> str:
    return ", ".join(value) if isinstance(value, list) else str(value)


def apply_row_edit(run: dict, row_number: int, changes: dict) -> dict:
    """Correct one row of a saved run and re-resolve it.

    This is what "did you mean Winter Melon Tea?" does when the person says yes,
    and what the fix-it buttons on an option this drink can't have do. Only the
    fields in `changes` move; everything else about the run is left alone, and
    the row is put back through the same resolution the import used, so a
    correction can't produce a row the importer couldn't have.

    A correction is always recorded on the row — "size set to Large — the sheet
    said gigantic" — so the preview never quietly disagrees with the sheet.

    Returns the updated run, ready to save.
    """
    rows = [OrderRow.from_dict(entry) for entry in run.get("rows") or []]
    target = next((row for row in rows if row.row_number == row_number), None)
    if target is None:
        raise RowNotFound(row_number)

    edited = [field for field in changes if field in EDITABLE_FIELDS]
    if not edited:
        raise ValueError("nothing to change")

    # resolve_row() is about to say all of this again, for every field, and the
    # matcher re-derives its own notes on every read. Clear both first so a
    # correction never leaves the row saying two things about one value.
    target.issues = [issue for issue in target.issues
                     if not (issue.code or "").startswith(("option:", "match:"))]

    for field in edited:
        chosen = _clean(field, changes[field])
        if field == "drink" and not chosen:
            # Every other field can legitimately be cleared back to the store
            # default. A row with no drink is not a row.
            raise ValueError("pick a drink")
        # A separate Hot/Iced column resolves onto the same store axis as Size.
        # Once the user edits Size in the app it is the authoritative choice;
        # otherwise clearing the dropdown would unexpectedly resurrect "Hot"
        # from the original temperature column instead of using the default.
        previous = getattr(target, field)
        if field == "size" and target.temperature:
            previous = previous or target.temperature
            target.temperature = ""
        setattr(target, field, chosen)

        # Drop an earlier correction of the same field, and whatever the import
        # said about it while reading the sheet: "no drink in this row" and "no
        # name, so this drink will be unlabelled" are not true any more. Notes
        # that carry a code describe the sheet itself ("moved this from Ice to
        # Toppings") and are still true, so they stay.
        code = f"edited:{field}"
        target.issues = [
            issue for issue in target.issues
            if (issue.code or "") != code and not (issue.field == field and not issue.code)
        ]
        label = _FIELD_LABELS[field]
        if chosen or field == "quantity":
            message = f"{label} set to \"{_describe(chosen)}\""
        else:
            message = f"{label} cleared, so the store's default is used"
        if previous and previous != chosen:
            message += f" — the sheet said \"{_describe(previous)}\""
        elif not previous and field in ("drink", "person"):
            message += " — this row was blank"
        target.issues.append(Issue("info", message, field, row_number, code))

    options.resolve_row(target)

    # The sheet-level tally has to be recomputed, not patched: fixing one row
    # can take the count to zero and the note has to disappear with it.
    issues = [Issue.from_dict(entry) for entry in run.get("issues") or []
              if entry.get("code") not in options.SUMMARY_CODES]
    issues += options.summarise(rows)

    result = ImportResult(rows=rows, column_map=run.get("column_map") or {},
                          issues=issues, source=run.get("source") or {})
    updated = dict(run)
    updated.update(result.as_dict())
    return updated


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
    order_rows, column_map, issues = parse_and_resolve(rows)
    return ImportResult(rows=order_rows, column_map=column_map, issues=issues,
                        source={"kind": "json", "format": "json"})
