"""Transaction scopes used by the chat path.

The chat path opens one scope per request and a separate one for writes made
inside the SSE generator, after the request scope has closed. With SQLite in autocommit mode on one locked
connection, every statement is already its own transaction, so these scopes
are no-ops that mark where a server database would open transactions.
"""

from __future__ import annotations

from contextlib import asynccontextmanager, contextmanager
from typing import AsyncIterator, Iterator


@contextmanager
def request_transaction_scope() -> Iterator[None]:
    yield


@asynccontextmanager
async def async_request_transaction_scope() -> AsyncIterator[None]:
    yield


@contextmanager
def tool_transaction_scope() -> Iterator[None]:
    yield
