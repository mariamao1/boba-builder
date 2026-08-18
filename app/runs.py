"""Where an import lives between the upload page and the preview page.

One JSON file per import under .runs/, keyed by an unguessable id. A file
rather than memory so the server can restart mid-demo, and so Tasks 3-5 can be
run against a real import from the command line:

    python3 -m app.pipeline .runs/<id>.json
"""

from __future__ import annotations

import json
import os
import secrets
import time
from pathlib import Path

RUN_DIR = Path(os.environ.get("BOBA_RUN_DIR", Path(__file__).resolve().parent.parent / ".runs"))
KEEP_RUNS = 50


def _dir() -> Path:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    return RUN_DIR


def new_id() -> str:
    return secrets.token_hex(8)


def path_for(run_id: str) -> Path:
    if not run_id or not all(char in "0123456789abcdef" for char in run_id):
        raise ValueError("bad run id")
    return _dir() / f"{run_id}.json"


def save(payload: dict, run_id: str | None = None) -> str:
    run_id = run_id or new_id()
    payload = {"run_id": run_id, "created_at": time.time(), **payload}
    path = path_for(run_id)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=1, default=str), encoding="utf-8")
    tmp.replace(path)
    prune()
    return run_id


def load(run_id: str) -> dict | None:
    path = path_for(run_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def prune(keep: int = KEEP_RUNS) -> None:
    """Keep the newest runs only. These carry people's names; don't hoard them."""
    files = sorted(_dir().glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for stale in files[keep:]:
        try:
            stale.unlink()
        except OSError:
            pass
