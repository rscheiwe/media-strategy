"""SQLite implementation of DatabaseClient.

The repositories were written for psycopg2 (`%s` placeholders, `%s::jsonb` casts),
so queries are translated: casts are dropped and `%s` becomes `?`. JSON columns
are stored as text, which the repositories already serialize and parse.

One connection is shared by every thread, guarded by a lock. The API runs a
single uvicorn worker, and a file database uses WAL. `:memory:` gives a fresh
private database per client (one connection is one in-memory database), which
is what tests want.
"""

from __future__ import annotations

import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator

from .base import DatabaseClient, DatabaseError

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schema.sql"
_CAST = re.compile(r"::\w+(\[\])?")

# Timestamps are stored as ISO-8601 text, as the repositories expect when reading.
sqlite3.register_adapter(datetime, lambda value: value.isoformat())


def translate(query: str) -> str:
    """psycopg2-style SQL -> sqlite3: drop `::type` casts, `%s` -> `?`."""
    return _CAST.sub("", query).replace("%s", "?")


class SqliteDatabaseClient(DatabaseClient):
    def __init__(self, path: str = ":memory:") -> None:
        self.path = path
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._conn.execute("PRAGMA foreign_keys = ON")
            if path != ":memory:":
                self._conn.execute("PRAGMA journal_mode = WAL")
            self._conn.executescript(SCHEMA_PATH.read_text())

    @contextmanager
    def transaction(self) -> Iterator[None]:
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                yield
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            self._conn.execute("COMMIT")

    def execute(self, query: str, params: tuple = ()) -> list[dict]:
        with self._lock:
            try:
                rows = self._conn.execute(translate(query), params).fetchall()
            except sqlite3.Error as exc:
                raise DatabaseError(str(exc)) from exc
        return [dict(row) for row in rows]

    def execute_one(self, query: str, params: tuple = ()) -> dict | None:
        rows = self.execute(query, params)
        return rows[0] if rows else None

    def execute_write(self, query: str, params: tuple = ()) -> None:
        with self._lock:
            try:
                self._conn.execute(translate(query), params)
            except sqlite3.Error as exc:
                raise DatabaseError(str(exc)) from exc

    def close(self) -> None:
        with self._lock:
            self._conn.close()
