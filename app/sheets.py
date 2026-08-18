"""Turn a pasted Google Sheets link into CSV bytes.

Google serves any sheet as CSV from an export endpoint, so a link is really
just a fetch. The two things that go wrong in practice are (a) people paste the
edit URL with a #gid fragment, and (b) the sheet is private, in which case
Google answers with an HTML sign-in page instead of an error. Both are handled
here so the page can say something useful.

Only docs.google.com is ever fetched: this endpoint takes a URL from the
browser, so anything else would be an open proxy into whatever the server can
reach.
"""

from __future__ import annotations

import re
import urllib.error
import urllib.parse
import urllib.request

ALLOWED_HOSTS = {"docs.google.com"}
MAX_BYTES = 8 * 1024 * 1024
TIMEOUT = 30
USER_AGENT = "boba-builder/0.2 (+group order helper)"

_DOC_ID_RE = re.compile(r"/spreadsheets/d/(?:e/)?([a-zA-Z0-9-_]+)")
_PUBLISHED_RE = re.compile(r"/spreadsheets/d/e/([a-zA-Z0-9-_]+)")
_BARE_ID_RE = re.compile(r"^[a-zA-Z0-9-_]{20,}$")


class SheetError(ValueError):
    """Something the user can act on: bad link, private sheet, no network."""


def _gid(parsed: urllib.parse.ParseResult) -> str:
    for source in (parsed.fragment, parsed.query):
        match = re.search(r"gid=(\d+)", source or "")
        if match:
            return match.group(1)
    return "0"


def export_url(link: str) -> str:
    """Any Google Sheets link -> the CSV export URL for the referenced tab."""
    link = (link or "").strip()
    if not link:
        raise SheetError("paste a Google Sheets link first")

    if _BARE_ID_RE.match(link):
        link = f"https://docs.google.com/spreadsheets/d/{link}/edit"
    if "://" not in link:
        link = "https://" + link

    parsed = urllib.parse.urlparse(link)
    host = (parsed.hostname or "").lower()

    if host == "drive.google.com":
        raise SheetError(
            "that's a Google Drive link. Open the file in Google Sheets and copy "
            "the link from there, or download it and upload the file instead")
    if host not in ALLOWED_HOSTS:
        raise SheetError(
            "that doesn't look like a Google Sheets link. It should start with "
            "https://docs.google.com/spreadsheets/ — for anything else, upload the file")
    if "/spreadsheets/" not in parsed.path:
        raise SheetError("that's a Google link, but not to a spreadsheet")

    gid = _gid(parsed)

    published = _PUBLISHED_RE.search(parsed.path)
    if published:
        # "Publish to the web" links have their own endpoint; /export 404s.
        return (f"https://docs.google.com/spreadsheets/d/e/{published.group(1)}"
                f"/pub?output=csv&gid={gid}&single=true")

    match = _DOC_ID_RE.search(parsed.path)
    if not match:
        raise SheetError("couldn't find the sheet id in that link — copy it again from the "
                         "browser address bar, or use Share > Copy link")

    return (f"https://docs.google.com/spreadsheets/d/{match.group(1)}"
            f"/export?format=csv&gid={gid}")


def _looks_like_html(payload: bytes) -> bool:
    head = payload[:1500].lstrip().lower()
    return head.startswith(b"<!doctype html") or head.startswith(b"<html") or b"<head>" in head


def fetch_csv(link: str) -> tuple[bytes, str]:
    """Fetch a sheet as CSV. Returns (bytes, resolved export url)."""
    url = export_url(link)

    request = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "text/csv,text/plain,*/*",
    })
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            final_host = (urllib.parse.urlparse(response.geturl()).hostname or "").lower()
            if final_host and final_host not in ALLOWED_HOSTS | {"accounts.google.com"}:
                raise SheetError("that link redirected off Google; not following it")
            payload = response.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise SheetError(
                "Google won't let us read that sheet. In Share, set General access to "
                "\"Anyone with the link\" (Viewer is enough), then paste the link again"
            ) from exc
        if exc.code == 404:
            raise SheetError("no sheet at that link — check the URL, or that the tab still exists") from exc
        raise SheetError(f"Google returned an error fetching that sheet (HTTP {exc.code})") from exc
    except urllib.error.URLError as exc:
        raise SheetError(f"couldn't reach Google to read that sheet ({exc.reason})") from exc
    except TimeoutError as exc:
        raise SheetError("timed out reading that sheet from Google") from exc

    if len(payload) > MAX_BYTES:
        raise SheetError("that sheet is too large to import")
    if not payload.strip():
        raise SheetError("that sheet tab is empty — check you copied the link from the right tab")
    if _looks_like_html(payload):
        # A sign-in page. Google answers 200 with HTML for link-restricted sheets.
        raise SheetError(
            "that sheet isn't shared. In Share, set General access to \"Anyone with the "
            "link\", then paste the link again — or upload the file instead")

    return payload, url
