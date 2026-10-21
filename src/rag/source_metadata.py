"""Helpers for displaying source dates and safe article links."""

from typing import Any, Dict


def source_date(item: Dict[str, Any]) -> str:
    metadata = item.get("metadata", {})
    return (
        metadata.get("published_at")
        or metadata.get("updated_at")
        or metadata.get("timestamp")
        or ""
    )


def source_url(item: Dict[str, Any]) -> str:
    metadata = item.get("metadata", {})
    value = metadata.get("url") or metadata.get("link") or metadata.get("source_url")
    return value if isinstance(value, str) and value.startswith(("https://", "http://")) else ""
