from abc import ABC, abstractmethod
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator


class DatabaseError(Exception):
    pass


class PoolTimeout(DatabaseError):
    """Every pooled connection is lent out and none came free within the checkout timeout.

    Distinct from ``DatabaseError`` because it means something different to the caller: the
    request was valid and the database is healthy, we are simply at capacity. Routes answer
    503 + ``Retry-After`` for this, not 500.
    """


@dataclass(frozen=True)
class RlsContext:
    user_id: str | None = None
    service_reason: str | None = None

    @property
    def has_context(self) -> bool:
        return bool(self.user_id or self.service_reason)


class DatabaseClient(ABC):
    @contextmanager
    def service_context(self, *, reason: str) -> Iterator[None]:
        yield

    @contextmanager
    def rls_context(self, *, user_id: str | None = None) -> Iterator[None]:
        yield

    @contextmanager
    def transaction(self) -> Iterator[None]:
        yield

    @abstractmethod
    def execute(self, query: str, params: tuple = ()) -> list[dict]:
        """Execute a SELECT and return all rows as dicts."""

    @abstractmethod
    def execute_one(self, query: str, params: tuple = ()) -> dict | None:
        """Execute a SELECT and return the first row, or None."""

    @abstractmethod
    def execute_write(self, query: str, params: tuple = ()) -> None:
        """Execute an INSERT, UPDATE, or DELETE."""
