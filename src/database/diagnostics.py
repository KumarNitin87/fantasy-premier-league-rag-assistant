"""Safe, value-free diagnostics for Postgres connection and SQL failures."""

from __future__ import annotations

import re


def database_failure_category(exc: BaseException) -> str:
    """Classify common Postgres failures without returning any exception text."""
    state = getattr(exc, "sqlstate", None) or getattr(exc, "pgcode", None)
    if isinstance(state, str):
        if state.startswith("28"):
            return "authentication"
        if state == "3D000":
            return "database_not_found"
        if state == "42501":
            return "permission_denied"
        if state in {"42P01", "42703", "42704", "42883"}:
            return "missing_schema_object"
        if state.startswith("08"):
            return "connection"
        if state.startswith("42"):
            return "sql_or_schema_error"

    # Inspect internally only to choose a fixed category. Never return or log
    # this text because drivers may include connection details in it.
    message = str(exc).casefold()
    if any(marker in message for marker in (
        "could not translate host name", "name or service not known",
        "nodename nor servname", "getaddrinfo failed", "temporary failure in name resolution",
    )):
        return "dns"
    if "ssl" in message or "tls" in message or "certificate" in message:
        return "ssl"
    if any(marker in message for marker in (
        "password authentication failed", "authentication failed", "no pg_hba.conf entry",
        "role does not exist", "invalid authorization specification",
    )):
        return "authentication"
    if "database" in message and "does not exist" in message:
        return "database_not_found"
    if any(marker in message for marker in (
        "undefined table", "undefined column", "undefined object", "schema does not exist",
    )) or re.search(r"\b(?:relation|column)\b.*\bdoes not exist\b", message):
        return "missing_schema_object"
    if any(marker in message for marker in (
        "connection timed out", "timeout expired", "connection refused",
        "network is unreachable", "connection reset", "server closed the connection",
        "could not connect to server",
    )):
        return "network"

    name = type(exc).__name__.casefold()
    if name in {"operationalerror", "interfaceerror"}:
        return "database_operational_error"
    if name in {"programmingerror", "syntaxerror", "undefinedtable", "undefinedcolumn"}:
        return "sql_or_schema_error"
    return "database_error"


def safe_sqlstate(exc: BaseException) -> str | None:
    """Return a PostgreSQL SQLSTATE only when it has the expected safe shape."""
    state = getattr(exc, "sqlstate", None) or getattr(exc, "pgcode", None)
    return state if isinstance(state, str) and re.fullmatch(r"[0-9A-Z]{5}", state) else None
