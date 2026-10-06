"""Build `case.db` from the case-study source files.

The four files are small (about 1,300 rows), so the database is rebuilt in full on
every API start rather than migrated: it can't drift from the sources. It's written
to a temporary file next to the target and renamed into place, so a reader never
sees a half-built database.
"""

from __future__ import annotations

import csv
import json
import os
import sqlite3
import tempfile
from pathlib import Path

SCHEMA_PATH = Path(__file__).resolve().with_name("schema.sql")

SOURCE_FILES = (
    "product_catalog.csv",
    "campaign_history.csv",
    "inventory_forecaster.csv",
    "client_briefs.json",
)


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as source:
        return list(csv.DictReader(source))


def _load(conn: sqlite3.Connection, source_dir: Path) -> None:
    conn.executemany(
        "INSERT INTO products VALUES (?, ?, ?, ?)",
        [
            (r["product_id"], r["product_name"], r["product_description"], float(r["cpm"]))
            for r in _rows(source_dir / "product_catalog.csv")
        ],
    )
    conn.executemany(
        "INSERT INTO campaign_line_items VALUES (?, ?, ?, ?, ?, ?)",
        [
            (
                n,
                r["product_id"],
                r["vertical"],
                int(r["impressions"]),
                int(r["clicks"]),
                int(r["viewable_impressions"]),
            )
            for n, r in enumerate(_rows(source_dir / "campaign_history.csv"), start=1)
        ],
    )
    conn.executemany(
        "INSERT INTO inventory VALUES (?, ?, ?, ?, ?)",
        [
            (
                r["product_id"],
                r["vertical"],
                r["geo"],
                int(r["available_imps"]),
                float(r["inventory_risk"]),
            )
            for r in _rows(source_dir / "inventory_forecaster.csv")
        ],
    )
    briefs = json.loads((source_dir / "client_briefs.json").read_text(encoding="utf-8"))
    conn.executemany(
        "INSERT INTO client_briefs VALUES (?, ?)",
        [(n, str(text)) for n, text in enumerate(briefs, start=1)],
    )


def build_case_db(source_dir: Path, out_path: Path) -> Path:
    """Load the four source files into a fresh SQLite file at `out_path`."""
    source_dir, out_path = Path(source_dir), Path(out_path)
    missing = [name for name in SOURCE_FILES if not (source_dir / name).is_file()]
    if missing:
        raise FileNotFoundError(f"case data missing in {source_dir}: {', '.join(missing)}")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".case-", suffix=".db", dir=out_path.parent)
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        conn = sqlite3.connect(tmp)
        try:
            with conn:
                conn.executescript(SCHEMA_PATH.read_text())
                _load(conn, source_dir)
        finally:
            conn.close()
        # Readable by the unprivileged analysis user; nobody writes it after this.
        tmp.chmod(0o644)
        os.replace(tmp, out_path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return out_path
