"""The case-study data (products, campaign history, inventory, briefs) as a read-only
SQLite database, `case.db`, kept separate from the chat database.

- `source_dir()`: where the four source files are (APP_CASE_DATA_DIR, else the
  app/api/case_data/ when running from the repo checkout).
- `case_db_path()`: where `case.db` is built (APP_CASE_DB_PATH, else ./data/case.db).
- `connect_readonly()`: a read-only connection for the API's own tools.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from .build import SOURCE_FILES, build_case_db

__all__ = [
    "SOURCE_FILES",
    "build_case_db",
    "case_db_path",
    "connect_readonly",
    "ensure_case_db",
    "source_dir",
]


def source_dir() -> Path:
    configured = (os.getenv("APP_CASE_DATA_DIR") or "").strip()
    if configured:
        return Path(configured)
    # The source files live in app/api/case_data/ (three levels above this file, in app/api/).
    # The docker image copies them to /srv/case_data and sets APP_CASE_DATA_DIR.
    parents = Path(__file__).resolve().parents
    return parents[3] / "case_data" if len(parents) > 3 else Path.cwd() / "case_data"


def case_db_path() -> Path:
    configured = (os.getenv("APP_CASE_DB_PATH") or "").strip()
    return Path(configured) if configured else Path("data") / "case.db"


def connect_readonly(path: Path | None = None) -> sqlite3.Connection:
    """A read-only connection (SQLite `mode=ro`): even a buggy query can't write.

    Usable from any thread (the workflow DAG runs sync nodes in worker threads); safe because
    nothing can write through it.
    """
    db = Path(path) if path else case_db_path()
    conn = sqlite3.connect(f"file:{db.resolve()}?mode=ro", uri=True, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_case_db(rebuild: bool = False) -> Path:
    """Build case.db from the sources if it is missing (or always, with `rebuild`)."""
    path = case_db_path()
    if rebuild or not path.is_file():
        build_case_db(source_dir(), path)
    return path
