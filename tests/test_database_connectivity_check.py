"""Read-only Postgres diagnostics tests with a fake DB-API connection."""

import logging
import sys

import psycopg
import pytest

from src.database.connectivity_check import EXPECTED_REQUIRED_TABLES, check_postgres_connectivity
from src.database.diagnostics import database_failure_category
from src.database.postgres import PostgresConnection, PostgresCursor, _sql_operation
from scripts import run_scheduled_sync


class _Result:
    def __init__(self, rows):
        self.rows = rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class _ReadOnlyConnection:
    def __init__(self, tables):
        self.tables = tables
        self.statements = []
        self.autocommit = False
        self.closed = False

    def execute(self, query):
        self.statements.append(query)
        if query == "SELECT current_database(), current_setting('transaction_read_only');":
            return _Result([("fpl_app", "on")])
        if "information_schema.tables" in query:
            return _Result([(table,) for table in self.tables])
        if query in {"BEGIN READ ONLY", "ROLLBACK"}:
            return _Result([])
        raise AssertionError(f"Unexpected diagnostic SQL: {query}")

    def close(self):
        self.closed = True


def test_read_only_connectivity_check_reports_database_and_required_tables():
    connection = _ReadOnlyConnection(EXPECTED_REQUIRED_TABLES)
    calls = []

    def connect(url, **kwargs):
        calls.append((url, kwargs))
        return connection

    result = check_postgres_connectivity("postgresql://user:password@host/fpl_app", connector=connect)

    assert result["connected"] is True
    assert result["read_only"] is True
    assert result["database"] == "fpl_app"
    assert result["schema_ready"] is True
    assert result["missing_tables"] == []
    assert calls[0][1] == {
        "connect_timeout": 10,
        "options": "-c default_transaction_read_only=on",
    }
    assert calls[0][0] not in str(result)
    assert connection.autocommit is True
    assert connection.closed is True
    assert all(
        statement == "BEGIN READ ONLY" or statement == "ROLLBACK" or statement.lstrip().upper().startswith("SELECT ")
        for statement in connection.statements
    )


def test_read_only_connectivity_check_reports_missing_tables_without_writes():
    connection = _ReadOnlyConnection(["teams", "players"])

    result = check_postgres_connectivity("redacted", connector=lambda *_args, **_kwargs: connection)

    assert result["connected"] is True
    assert result["schema_ready"] is False
    assert "news_vectors" in result["missing_tables"]
    assert all("CREATE" not in statement.upper() for statement in connection.statements)
    assert all("INSERT" not in statement.upper() for statement in connection.statements)


def test_read_only_connectivity_check_sanitizes_probe_sql_errors():
    secret = "schema-probe-secret"

    class FailingProbe(_ReadOnlyConnection):
        def execute(self, query):
            if "information_schema.tables" in query:
                raise psycopg.errors.UndefinedTable(f"relation missing password={secret}")
            return super().execute(query)

    connection = FailingProbe(EXPECTED_REQUIRED_TABLES)
    result = check_postgres_connectivity("redacted", connector=lambda *_args, **_kwargs: connection)

    assert result == {
        "connected": True,
        "read_only": True,
        "stage": "read_only_probe",
        "category": "missing_schema_object",
    }
    assert connection.closed is True
    assert secret not in str(result)


def test_read_only_connectivity_check_classifies_auth_failure_without_message():
    secret = "p@ssword-that-must-never-be-logged"

    def connect(*_args, **_kwargs):
        raise psycopg.OperationalError(f"password authentication failed; password={secret}")

    result = check_postgres_connectivity("postgresql://user:secret@host/fpl_app", connector=connect)

    assert result == {"connected": False, "stage": "connection", "category": "authentication"}
    assert secret not in str(result)


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("could not translate host name db.internal: getaddrinfo failed", "dns"),
        ("SSL SYSCALL error: EOF detected", "ssl"),
        ("connection timed out", "network"),
        ("relation players does not exist", "missing_schema_object"),
    ],
)
def test_database_failure_category_distinguishes_common_failures(message, expected):
    assert database_failure_category(psycopg.OperationalError(message)) == expected


def test_database_failure_category_uses_sqlstate_for_auth_and_missing_schema():
    class StatefulError(Exception):
        def __init__(self, state):
            self.sqlstate = state

    assert database_failure_category(StatefulError("28P01")) == "authentication"
    assert database_failure_category(StatefulError("42P01")) == "missing_schema_object"


def test_adapter_connection_logs_category_without_connection_secrets(monkeypatch, caplog):
    secret = "adapter-password-do-not-print"

    def fail_connect(*_args, **_kwargs):
        raise psycopg.OperationalError(f"connection refused for host=private password={secret}")

    monkeypatch.setattr(psycopg, "connect", fail_connect)
    caplog.set_level(logging.ERROR, logger="src.database.postgres")

    with pytest.raises(psycopg.OperationalError):
        PostgresConnection(f"postgresql://user:{secret}@private/fpl_app")

    assert "category=network" in caplog.text
    assert secret not in caplog.text
    assert "private" not in caplog.text


def test_adapter_sql_failure_logs_safe_operation_not_query_or_parameters(caplog):
    secret = "query-secret-must-not-appear"

    class FailingCursor:
        def execute(self, *_args, **_kwargs):
            raise psycopg.errors.UndefinedTable(f"relation missing password={secret}")

    caplog.set_level(logging.ERROR, logger="src.database.postgres")
    cursor = PostgresCursor(FailingCursor())

    with pytest.raises(psycopg.errors.UndefinedTable):
        cursor.execute("SELECT * FROM players WHERE api_token = ?", (secret,))

    assert "operation=SELECT players" in caplog.text
    assert "category=missing_schema_object" in caplog.text
    assert secret not in caplog.text
    assert "api_token" not in caplog.text


def test_scheduler_stage_and_final_error_logs_do_not_emit_exception_text(monkeypatch, caplog):
    secret = "scheduler-password-do-not-log"
    caplog.set_level(logging.ERROR, logger="scheduled_sync")

    def fail_stage(_sync_type):
        return run_scheduled_sync._run_stage(
            "database_initialization",
            lambda: (_ for _ in ()).throw(psycopg.OperationalError(
                f"connection refused host=private password={secret}"
            )),
        )

    monkeypatch.setattr(run_scheduled_sync, "run_once", fail_stage)
    monkeypatch.setattr(sys, "argv", ["run_scheduled_sync.py", "--type", "news"])

    assert run_scheduled_sync.main() == 1
    assert "stage=database_initialization" in caplog.text
    assert "category=network" in caplog.text
    assert secret not in caplog.text
    assert "host=private" not in caplog.text


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("CREATE TABLE IF NOT EXISTS schema_migrations (version INT)", "CREATE schema_migrations"),
        ("CREATE EXTENSION IF NOT EXISTS vector", "CREATE vector"),
        ("SELECT * FROM ingestion_log", "SELECT ingestion_log"),
    ],
)
def test_sql_operation_names_first_database_operation_without_logging_sql(query, expected):
    assert _sql_operation(query) == expected
