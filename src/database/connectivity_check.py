"""Read-only connectivity and required-table check for the hosted Postgres DB."""

from __future__ import annotations

from typing import Any, Callable

from src.database.diagnostics import database_failure_category


EXPECTED_REQUIRED_TABLES = (
    "schema_migrations",
    "teams",
    "players",
    "fixtures",
    "player_gameweek_history",
    "player_history_sync",
    "player_past_seasons",
    "ingestion_log",
    "my_team_settings",
    "my_team_squad",
    "news_vectors",
)


def check_postgres_connectivity(
    database_url: str | None,
    connector: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Probe a Postgres URL using a read-only transaction; never run DDL or DML."""
    if not database_url:
        return {"connected": False, "stage": "configuration", "category": "missing_database_url"}

    if connector is None:
        try:
            import psycopg
        except ImportError:
            return {"connected": False, "stage": "driver", "category": "driver_unavailable"}
        connector = psycopg.connect

    try:
        connection = connector(
            database_url,
            connect_timeout=10,
            options="-c default_transaction_read_only=on",
        )
    except Exception as exc:
        return {
            "connected": False,
            "stage": "connection",
            "category": database_failure_category(exc),
        }

    try:
        # The startup option protects every transaction. Explicit BEGIN READ
        # ONLY makes the intent visible and is independently verified below.
        connection.autocommit = True
        connection.execute("BEGIN READ ONLY")
        database_name, read_only = connection.execute(
            "SELECT current_database(), current_setting('transaction_read_only');"
        ).fetchone()
        if str(read_only).casefold() != "on":
            return {
                "connected": True,
                "read_only": False,
                "stage": "read_only_verification",
                "category": "read_only_not_enforced",
            }

        rows = connection.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = current_schema() AND table_type = 'BASE TABLE';"
        ).fetchall()
        existing = {str(row[0]) for row in rows}
        missing = [name for name in EXPECTED_REQUIRED_TABLES if name not in existing]
        return {
            "connected": True,
            "read_only": True,
            "database": str(database_name),
            "schema_ready": not missing,
            "missing_tables": missing,
            "required_tables": list(EXPECTED_REQUIRED_TABLES),
        }
    except Exception as exc:
        return {
            "connected": True,
            "read_only": True,
            "stage": "read_only_probe",
            "category": database_failure_category(exc),
        }
    finally:
        try:
            connection.execute("ROLLBACK")
        except Exception:
            pass
        try:
            connection.close()
        except Exception:
            # Keep driver diagnostics out of stdout/stderr, including close errors.
            pass
