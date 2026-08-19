"""The Boba Builder web server — Task 2.

    python3 -m app.server            # http://127.0.0.1:8000
    python3 -m app.server --port 9000 --host 0.0.0.0

http.server rather than a framework because nothing installs on the machines
this has to run on (no pip, no node — same wall Task 1 hit). It is a local
single-group tool, so a threading stdlib server is genuinely enough.

Routes
    GET  /                       upload page
    GET  /preview/<run_id>       review the parsed order, hand off to Tasks 3-4
    GET  /template.csv           the order template, filled with real menu items
    POST /api/import             file upload or {"sheet_url": ...} -> run_id
    GET  /api/runs/<run_id>      the parsed order, matched to the menu, as JSON
    GET  /api/drinks?q=          type-ahead search over the store's menu
    POST /api/runs/<run_id>/rows/<n>  edit one row: any of {"drink", "size",
                                      "sugar", "ice", "milk", "toppings",
                                      "quantity", "person"}
    POST /api/runs/<run_id>/process   push it into the pipeline
    GET  /api/health

Reading a run runs the read-only stages first (`pipeline.enrich`), so the match
always reflects the rows as they are now rather than as they were when the file
was written. Nothing that talks to the store happens on a GET.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import traceback
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from . import importer, options, pipeline, runs, sheets, template

STATIC_DIR = Path(__file__).resolve().parent / "static"
MAX_BODY = importer.MAX_UPLOAD_BYTES + 512 * 1024  # payload plus multipart framing

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".json": "application/json",
}

RUN_ID_RE = re.compile(r"^[0-9a-f]{4,32}$")


# --- multipart/form-data ----------------------------------------------------
# cgi.FieldStorage is gone in 3.13 and this only needs one file field.


def parse_multipart(body: bytes, content_type: str) -> dict[str, dict]:
    match = re.search(r'boundary="?([^";]+)"?', content_type or "")
    if not match:
        return {}
    boundary = b"--" + match.group(1).strip().encode()

    fields: dict[str, dict] = {}
    for chunk in body.split(boundary):
        if chunk in (b"", b"--", b"--\r\n", b"\r\n"):
            continue
        chunk = chunk.lstrip(b"\r\n")
        head, _, payload = chunk.partition(b"\r\n\r\n")
        if not _:
            continue
        payload = payload[:-2] if payload.endswith(b"\r\n") else payload

        headers = head.decode("utf-8", "replace")
        name = re.search(r'name="([^"]*)"', headers)
        if not name:
            continue
        filename = re.search(r'filename="([^"]*)"', headers)
        fields[name.group(1)] = {
            "value": payload,
            "filename": filename.group(1) if filename else None,
        }
    return fields


class Handler(BaseHTTPRequestHandler):
    server_version = "BobaBuilder/0.2"
    protocol_version = "HTTP/1.1"

    # --- plumbing -----------------------------------------------------------

    def log_message(self, fmt, *args):  # quieter, and to stderr
        sys.stderr.write(f"  {self.command} {self.path} -> {args[1] if len(args) > 1 else ''}\n")

    def _send(self, status, body: bytes, content_type: str, extra: dict | None = None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, payload: dict, status=HTTPStatus.OK):
        body = json.dumps(payload, default=str).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _error(self, message: str, status=HTTPStatus.BAD_REQUEST, **extra):
        self._json({"ok": False, "error": message, **extra}, status)

    def _read_body(self) -> bytes:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return b""
        if length > MAX_BODY:
            raise ValueError(f"upload is larger than {MAX_BODY // (1024 * 1024)} MB")
        return self.rfile.read(length) if length else b""

    def _serve_static(self, name: str):
        target = (STATIC_DIR / name).resolve()
        if not str(target).startswith(str(STATIC_DIR.resolve())) or not target.is_file():
            self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain; charset=utf-8")
            return
        content_type = CONTENT_TYPES.get(target.suffix, "application/octet-stream")
        self._send(HTTPStatus.OK, target.read_bytes(), content_type,
                   {"Cache-Control": "no-cache"})

    # --- GET ----------------------------------------------------------------

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        parts = urlparse(self.path)
        path = unquote(parts.path)

        if path == "/":
            return self._serve_static("index.html")
        if path.startswith("/static/"):
            return self._serve_static(path[len("/static/"):])
        if path == "/favicon.ico":
            return self._serve_static("favicon.svg")

        if path == "/template.csv":
            body = template.template_csv().encode("utf-8")
            return self._send(HTTPStatus.OK, body, "text/csv; charset=utf-8", {
                "Content-Disposition": 'attachment; filename="boba-order-template.csv"'})

        if path.startswith("/preview/"):
            return self._serve_static("preview.html")

        if path == "/api/health":
            return self._json({"ok": True, "stages": pipeline.status()})

        if path == "/api/menu-hints":
            return self._json(template.menu_hints())

        if path == "/api/drinks":
            # Type-ahead for the "pick a drink" box on the preview page.
            query = parse_qs(parts.query)
            text = (query.get("q") or [""])[0]
            try:
                limit = max(1, min(20, int((query.get("limit") or ["6"])[0])))
            except ValueError:
                limit = 6
            return self._json({"drinks": options.store_options().search_drinks(text, limit)})

        match = re.fullmatch(r"/api/runs/([0-9a-f]+)", path)
        if match:
            return self._get_run(match.group(1))

        self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain; charset=utf-8")

    def _get_run(self, run_id: str):
        if not RUN_ID_RE.fullmatch(run_id):
            return self._error("bad run id", HTTPStatus.BAD_REQUEST)
        run = runs.load(run_id)
        if run is None:
            return self._error("that import has expired — upload the sheet again",
                               HTTPStatus.NOT_FOUND)
        # Matched on the way out rather than on the way in: the match is derived
        # from the rows, so deriving it fresh is the only way it can't go stale
        # behind a correction. Only read-only stages run here (pipeline.enrich).
        return self._json({"ok": True, "run": pipeline.enrich(run),
                           "stages": pipeline.status()})

    # --- POST ---------------------------------------------------------------

    def do_POST(self):
        path = unquote(urlparse(self.path).path)
        try:
            if path == "/api/import":
                return self._import()
            match = re.fullmatch(r"/api/runs/([0-9a-f]+)/process", path)
            if match:
                return self._process(match.group(1))
            match = re.fullmatch(r"/api/runs/([0-9a-f]+)/rows/(\d+)", path)
            if match:
                return self._edit_row(match.group(1), int(match.group(2)))
        except ValueError as exc:
            return self._error(str(exc))
        except Exception:  # never take the server down mid-demo
            traceback.print_exc()
            return self._error("something went wrong reading that — try again, or upload the "
                               "file instead of the link",
                               HTTPStatus.INTERNAL_SERVER_ERROR)
        self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain; charset=utf-8")

    def _import(self):
        content_type = self.headers.get("Content-Type", "")
        body = self._read_body()

        if content_type.startswith("multipart/form-data"):
            fields = parse_multipart(body, content_type)
            upload = fields.get("file")
            link = (fields.get("sheet_url", {}).get("value") or b"").decode("utf-8", "replace")
            if upload and upload["value"]:
                result = self._import_bytes(upload["value"], upload.get("filename") or "")
            elif link.strip():
                result = self._import_link(link)
            else:
                return self._error("choose a file or paste a Google Sheets link")
        elif content_type.startswith("application/json"):
            try:
                payload = json.loads(body or b"{}")
            except json.JSONDecodeError:
                return self._error("could not read that request")
            link = (payload.get("sheet_url") or "").strip()
            if not link:
                return self._error("paste a Google Sheets link first")
            result = self._import_link(link)
        else:
            return self._error("unsupported upload format")

        if result is None:
            return  # the helper already answered

        payload = result.as_dict()
        run_id = runs.save(payload)
        payload["run_id"] = run_id
        return self._json({"ok": result.ok, "run_id": run_id, "run": payload,
                           "preview_url": f"/preview/{run_id}"})

    def _import_bytes(self, data: bytes, filename: str):
        try:
            return importer.import_bytes(data, filename)
        except importer.UnreadableFile as exc:
            self._error(str(exc))
            return None

    def _import_link(self, link: str):
        try:
            return importer.import_sheet_link(link)
        except sheets.SheetError as exc:
            self._error(str(exc))
            return None
        except importer.UnreadableFile as exc:
            self._error(str(exc))
            return None

    def _edit_row(self, run_id: str, row_number: int):
        """Correct one row — the "did you mean…?" button on the preview."""
        if not RUN_ID_RE.fullmatch(run_id):
            return self._error("bad run id")
        run = runs.load(run_id)
        if run is None:
            return self._error("that import has expired — upload the sheet again",
                               HTTPStatus.NOT_FOUND)
        try:
            changes = json.loads(self._read_body() or b"{}")
        except json.JSONDecodeError:
            return self._error("could not read that request")
        if not isinstance(changes, dict) or not set(changes) & importer.EDITABLE_FIELDS:
            return self._error("nothing to change")

        try:
            updated = importer.apply_row_edit(run, row_number, changes)
        except importer.RowNotFound:
            return self._error(f"there is no row {row_number} in this import",
                               HTTPStatus.NOT_FOUND)
        runs.save(updated, run_id)
        updated["run_id"] = run_id
        return self._json({"ok": True, "run": pipeline.enrich(updated),
                           "stages": pipeline.status()})

    def _process(self, run_id: str):
        if not RUN_ID_RE.fullmatch(run_id):
            return self._error("bad run id")
        run = runs.load(run_id)
        if run is None:
            return self._error("that import has expired — upload the sheet again",
                               HTTPStatus.NOT_FOUND)
        try:
            result = pipeline.process(run)
        except pipeline.PipelineNotReady as exc:
            # Everything up to the missing stage still ran and is worth showing.
            return self._json({
                "ok": False,
                "pending": True,
                "error": str(exc),
                "run": pipeline.enrich(run),
                "stage": pipeline.next_stage(),
                "stages": pipeline.status(),
            }, HTTPStatus.ACCEPTED)
        runs.save(result, run_id)
        return self._json({"ok": True, "run": result})


def main(argv=None):
    parser = argparse.ArgumentParser(description="Boba Builder import & upload page")
    parser.add_argument("--host", default="127.0.0.1",
                        help="default 127.0.0.1; use 0.0.0.0 to share on your network")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    shown = "127.0.0.1" if args.host in ("", "0.0.0.0") else args.host
    print(f"Boba Builder — http://{shown}:{args.port}")
    for stage in pipeline.status():
        print(f"  [{'x' if stage['ready'] else ' '}] {stage['name']:7} {stage['description']}")
    print("  ctrl-c to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
