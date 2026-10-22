"""Small DB-API compatibility layer for the repository's Postgres deployment.

The repository keeps SQLite for local use and uses this adapter only when
DATABASE_URL is configured. Query parameters and row access retain the shape
used by sqlite3 so application queries stay in the repository layer.
"""

from __future__ import annotations

import re
import logging
from typing import Any, Iterable, Sequence

from src.database.diagnostics import database_failure_category, safe_sqlstate

logger = logging.getLogger(__name__)


def _sql_operation(query: str) -> str:
    """Describe the SQL verb and target without logging SQL or parameters."""
    verb_match = re.match(r"\s*(CREATE|ALTER|DROP|INSERT|UPDATE|DELETE|SELECT|WITH|SET)\b", query, re.IGNORECASE)
    if not verb_match:
        return "statement"
    verb = verb_match.group(1).upper()
    target_match = None
    patterns = (
        r"\b(?:CREATE|ALTER|DROP)\s+TABLE\s+(?:IF\s+(?:NOT\s+)?EXISTS\s+)?([A-Za-z_][A-Za-z0-9_]*)",
        r"\bCREATE\s+EXTENSION\s+(?:IF\s+(?:NOT\s+)?EXISTS\s+)?([A-Za-z_][A-Za-z0-9_]*)",
        r"\b(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+([A-Za-z_][A-Za-z0-9_]*)",
        r"\bSELECT\b[\s\S]*?\bFROM\s+([A-Za-z_][A-Za-z0-9_]*)",
    )
    for pattern in patterns:
        target_match = re.search(pattern, query, re.IGNORECASE)
        if target_match:
            break
    return f"{verb} {target_match.group(1)}" if target_match else verb


class HybridRow:
    """A tuple-like row that also supports sqlite3.Row's mapping behavior."""

    def __init__(self, columns: Sequence[str], values: Sequence[Any]):
        self._columns = tuple(columns)
        self._values = tuple(values)
        self._positions = {name: index for index, name in enumerate(self._columns)}

    def __getitem__(self, key: int | slice | str) -> Any:
        if isinstance(key, str):
            return self._values[self._positions[key]]
        return self._values[key]

    def __iter__(self):
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def keys(self) -> tuple[str, ...]:
        return self._columns


def _postgres_sql(query: str) -> str:
    """Translate the small amount of SQLite-specific SQL used by schema setup."""
    query = re.sub(
        r"\bINTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT\b",
        "BIGSERIAL PRIMARY KEY",
        query,
        flags=re.IGNORECASE,
    )
    query = re.sub(r"\bINSERT\s+OR\s+IGNORE\s+INTO\b", "INSERT INTO", query, flags=re.IGNORECASE)
    if re.search(r"\bINSERT\s+INTO\s+my_team_settings\b", query, flags=re.IGNORECASE) and "ON CONFLICT" not in query.upper():
        query = query.rstrip().rstrip(";") + " ON CONFLICT (id) DO NOTHING;"
    return query.replace("?", "%s")


class PostgresCursor:
    is_postgres = True

    def __init__(self, raw_cursor):
        self._cursor = raw_cursor

    def execute(self, query: str, params: Sequence[Any] | None = None):
        try:
            self._cursor.execute(_postgres_sql(query), tuple(params or ()))
        except Exception as exc:
            logger.error(
                "PostgreSQL operation failed (operation=%s category=%s sqlstate=%s).",
                _sql_operation(query), database_failure_category(exc), safe_sqlstate(exc) or "unavailable",
            )
            raise
        return self

    def executemany(self, query: str, seq_of_params: Iterable[Sequence[Any]]):
        try:
            self._cursor.executemany(_postgres_sql(query), seq_of_params)
        except Exception as exc:
            logger.error(
                "PostgreSQL operation failed (operation=%s category=%s sqlstate=%s).",
                _sql_operation(query), database_failure_category(exc), safe_sqlstate(exc) or "unavailable",
            )
            raise
        return self

    def fetchone(self):
        value = self._cursor.fetchone()
        return self._wrap(value)

    def fetchall(self):
        return [self._wrap(row) for row in self._cursor.fetchall()]

    def _wrap(self, values):
        if values is None:
            return None
        columns = [column.name for column in (self._cursor.description or ())]
        return HybridRow(columns, values)

    def __getattr__(self, name):
        return getattr(self._cursor, name)


class PostgresConnection:
    def __init__(self, database_url: str, timeout_seconds: int = 10):
        try:
            import psycopg
        except ImportError as exc:
            raise RuntimeError(
                "The Postgres backend requires psycopg. Install the pinned requirements.txt dependencies."
            ) from exc
        try:
            self._connection = psycopg.connect(database_url, connect_timeout=timeout_seconds)
        except Exception as exc:
            logger.error("PostgreSQL connection failed (category=%s).", database_failure_category(exc))
            raise

    def execute(self, query: str, params: Sequence[Any] | None = None):
        cursor = self.cursor()
        return cursor.execute(query, params)

    def executemany(self, query: str, seq_of_params: Iterable[Sequence[Any]]):
        cursor = self.cursor()
        return cursor.executemany(query, seq_of_params)

    def cursor(self):
        return PostgresCursor(self._connection.cursor())

    def commit(self):
        try:
            self._connection.commit()
        except Exception as exc:
            logger.error("PostgreSQL operation failed (operation=COMMIT category=%s).", database_failure_category(exc))
            raise

    def rollback(self):
        try:
            self._connection.rollback()
        except Exception as exc:
            logger.error("PostgreSQL operation failed (operation=ROLLBACK category=%s).", database_failure_category(exc))
            raise

    def close(self):
        self._connection.close()
