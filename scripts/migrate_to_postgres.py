"""Safely inspect or migrate local SQLite + Chroma data into configured Postgres."""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import settings
from src.database.connection import db_session, init_db

TABLES = (
    "teams",
    "players",
    "fixtures",
    "player_gameweek_history",
    "player_history_sync",
    "player_past_seasons",
    "ingestion_log",
    "my_team_settings",
    "my_team_squad",
)
CONFLICT_KEYS = {
    "teams": ("id",),
    "players": ("id",),
    "fixtures": ("id",),
    "player_gameweek_history": ("player_id", "round"),
    "player_history_sync": ("player_id",),
    "player_past_seasons": ("player_id", "season_name"),
    "ingestion_log": ("id",),
    "my_team_settings": ("id",),
    "my_team_squad": ("player_id",),
}
SEQUENCE_TABLES = ("player_gameweek_history", "player_past_seasons", "ingestion_log")
UNIQUE_CHECKS = {
    "player_gameweek_history": ("player_id", "round"),
    "player_past_seasons": ("player_id", "season_name"),
}


def _connect_readonly(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise FileNotFoundError(f"Source SQLite file does not exist: {path}")
    connection = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _source_counts(connection: sqlite3.Connection) -> dict[str, int]:
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    return {table: int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]) if table in tables else 0 for table in TABLES}


def _source_duplicates(connection: sqlite3.Connection) -> dict[str, int]:
    duplicates = {}
    available = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for table, columns in UNIQUE_CHECKS.items():
        if table not in available:
            duplicates[table] = 0
            continue
        names = ", ".join(f'"{name}"' for name in columns)
        duplicates[table] = int(connection.execute(
            f'SELECT COUNT(*) FROM (SELECT {names} FROM "{table}" GROUP BY {names} HAVING COUNT(*) > 1)'
        ).fetchone()[0])
    return duplicates


def _read_vectors(chroma_dir: Path) -> list[tuple[str, str, dict[str, Any], list[float]]]:
    if not chroma_dir.is_dir():
        raise FileNotFoundError(f"Source ChromaDB directory does not exist: {chroma_dir}")
    import chromadb

    client = chromadb.PersistentClient(path=str(chroma_dir))
    if settings.CHROMA_COLLECTION_NAME not in {item.name for item in client.list_collections()}:
        return []
    collection = client.get_collection(settings.CHROMA_COLLECTION_NAME)
    data = collection.get(include=["documents", "metadatas", "embeddings"])
    embeddings = data.get("embeddings")
    if hasattr(embeddings, "tolist"):
        embeddings = embeddings.tolist()
    embeddings = embeddings or []
    docs = data.get("documents") or []
    metas = data.get("metadatas") or [{}] * len(docs)
    ids = data.get("ids") or []
    if not (len(ids) == len(docs) == len(metas) == len(embeddings)):
        raise ValueError("Chroma returned mismatched IDs, documents, metadata, and embeddings.")
    rows = []
    for doc_id, doc, metadata, embedding in zip(ids, docs, metas, embeddings):
        vector = [float(value) for value in embedding]
        if len(vector) != 384:
            raise ValueError(f"Chroma vector {doc_id!r} has dimension {len(vector)}, expected 384.")
        rows.append((str(doc_id), str(doc), metadata or {}, vector))
    return rows


def _snapshot(source_db: Path, chroma_dir: Path):
    connection = _connect_readonly(source_db)
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise ValueError(f"SQLite integrity_check failed: {integrity}")
        counts = _source_counts(connection)
        if not counts["teams"] or not counts["players"]:
            raise ValueError("Source database has no saved team/player data; refusing to migrate an empty store.")
        duplicates = _source_duplicates(connection)
        if any(duplicates.values()):
            raise ValueError(f"Duplicate natural keys found: {duplicates}")
        rows = {
            table: [tuple(row) for row in connection.execute(f'SELECT * FROM "{table}"')]
            if counts[table] else []
            for table in TABLES
        }
    finally:
        connection.close()
    vectors = _read_vectors(chroma_dir)
    digest = hashlib.sha256()
    for table in TABLES:
        digest.update(table.encode())
        for row in rows[table]:
            digest.update(json.dumps(row, default=str, separators=(",", ":")).encode())
    for doc_id, document, metadata, embedding in vectors:
        digest.update(json.dumps([doc_id, document, metadata, embedding], sort_keys=True, default=str, separators=(",", ":")).encode())
    return counts, duplicates, rows, vectors, digest.hexdigest()


def _validate_backup(source_db: Path, chroma_dir: Path, backup_dir: Path) -> None:
    backup_db = backup_dir / source_db.name
    backup_chroma = backup_dir / chroma_dir.name
    if not backup_db.is_file() or not backup_chroma.is_dir():
        raise FileNotFoundError("Backup must contain both the SQLite database and the complete ChromaDB directory.")
    original_counts, _, _, original_vectors, original_hash = _snapshot(source_db, chroma_dir)
    backup_counts, _, _, backup_vectors, backup_hash = _snapshot(backup_db, backup_chroma)
    if original_counts != backup_counts or len(original_vectors) != len(backup_vectors) or original_hash != backup_hash:
        raise ValueError("Backup contents do not exactly match the source; migration was not started.")


def inspect_source(source_db: Path, chroma_dir: Path) -> dict[str, Any]:
    counts, duplicates, _, vectors, source_hash = _snapshot(source_db, chroma_dir)
    return {"counts": counts, "duplicates": duplicates, "vectors": len(vectors), "source_hash": source_hash}


def migrate(source_db: Path, chroma_dir: Path, backup_dir: Path) -> dict[str, Any]:
    if not settings.DATABASE_URL:
        raise RuntimeError("Set DATABASE_URL locally or in the process environment; do not paste it into chat.")
    _validate_backup(source_db, chroma_dir, backup_dir)
    counts, _, rows, vectors, source_hash = _snapshot(source_db, chroma_dir)

    # Schema/extension setup is idempotent. Source files are opened read-only.
    init_db(settings.SQLITE_DB_PATH)
    from src.rag.postgres_vector_store import PostgresVectorStore
    PostgresVectorStore._ensure_schema()

    with db_session() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS deployment_migrations (
            migration_key TEXT PRIMARY KEY,
            source_hash TEXT NOT NULL,
            completed_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        );""")
        previous = conn.execute(
            "SELECT source_hash FROM deployment_migrations WHERE migration_key = ?;",
            ("sqlite-chroma-v1",),
        ).fetchone()
        if previous:
            if previous[0] != source_hash:
                raise RuntimeError("A different source was already migrated. Use a fresh destination or review it manually.")
            return {"status": "already_migrated", "counts": counts, "vectors": len(vectors)}

        destination_counts = {table: int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]) for table in TABLES}
        destination_vectors = int(conn.execute("SELECT COUNT(*) FROM news_vectors;").fetchone()[0])
        if any(destination_counts.values()) or destination_vectors:
            raise RuntimeError("Destination is not empty and has no matching migration record; refusing to overwrite it.")

        for table in TABLES:
            if not rows[table]:
                continue
            source_connection = _connect_readonly(source_db)
            try:
                columns = [column[1] for column in source_connection.execute(f'PRAGMA table_info("{table}")')]
            finally:
                source_connection.close()
            names = ", ".join(f'"{name}"' for name in columns)
            placeholders = ", ".join("?" for _ in columns)
            keys = CONFLICT_KEYS[table]
            target = ", ".join(f'"{name}"' for name in keys)
            update_columns = [name for name in columns if name not in keys]
            update_clause = ", ".join(f'"{name}"=EXCLUDED."{name}"' for name in update_columns)
            conflict = f"DO UPDATE SET {update_clause}" if update_clause else "DO NOTHING"
            conn.executemany(
                f'INSERT INTO "{table}" ({names}) VALUES ({placeholders}) ON CONFLICT ({target}) {conflict};',
                rows[table],
            )

        conn.executemany(
            """INSERT INTO news_vectors (id, document, metadata, embedding)
            VALUES (?, ?, ?::jsonb, ?::vector)
            ON CONFLICT (id) DO UPDATE SET document=EXCLUDED.document,
                metadata=EXCLUDED.metadata, embedding=EXCLUDED.embedding;""",
            [
                (doc_id, document, json.dumps(metadata), "[" + ",".join(format(x, ".9g") for x in embedding) + "]")
                for doc_id, document, metadata, embedding in vectors
            ],
        )
        for table in SEQUENCE_TABLES:
            conn.execute(
                "SELECT setval(pg_get_serial_sequence(?, 'id'), GREATEST(COALESCE(MAX(id), 1), 1), COUNT(*) > 0) FROM " + table + ";",
                (table,),
            )

        migrated_counts = {table: int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]) for table in TABLES}
        migrated_vectors = int(conn.execute("SELECT COUNT(*) FROM news_vectors;").fetchone()[0])
        if migrated_counts != counts or migrated_vectors != len(vectors):
            raise RuntimeError(f"Post-migration counts differ: expected {counts}/{len(vectors)} vectors, got {migrated_counts}/{migrated_vectors}.")

        # Compare deterministic primary-key samples before recording success.
        primary_keys = {
            "teams": ("id",), "players": ("id",), "fixtures": ("id",),
            "player_gameweek_history": ("id",), "player_history_sync": ("player_id",),
            "player_past_seasons": ("id",), "ingestion_log": ("id",),
            "my_team_settings": ("id",), "my_team_squad": ("player_id",),
        }
        for table, source_rows in rows.items():
            if not source_rows:
                continue
            source_connection = _connect_readonly(source_db)
            try:
                column_names = [column[1] for column in source_connection.execute(f'PRAGMA table_info("{table}")')]
            finally:
                source_connection.close()
            key_columns = primary_keys[table]
            key_indexes = [column_names.index(name) for name in key_columns]
            selected_columns = ", ".join('"' + name + '"' for name in column_names)
            for source_row in (source_rows[0], source_rows[len(source_rows) // 2], source_rows[-1]):
                where = " AND ".join(f'"{name}" = ?' for name in key_columns)
                target_row = conn.execute(
                    f'SELECT {selected_columns} FROM "{table}" WHERE {where};',
                    tuple(source_row[index] for index in key_indexes),
                ).fetchone()
                if target_row is None or tuple(_normal_value(value) for value in target_row) != tuple(_normal_value(value) for value in source_row):
                    raise RuntimeError(f"Representative row comparison failed for {table}.")

        conn.execute(
            "INSERT INTO deployment_migrations (migration_key, source_hash) VALUES (?, ?);",
            ("sqlite-chroma-v1", source_hash),
        )
    return {"status": "migrated", "counts": counts, "vectors": len(vectors), "source_hash": source_hash}


def _normal_value(value):
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).isoformat(sep=" ")
        except ValueError:
            return value
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-db", type=Path, default=settings.SQLITE_DB_PATH)
    parser.add_argument("--source-chroma", type=Path, default=settings.CHROMA_PERSIST_DIR)
    parser.add_argument("--dry-run", action="store_true", help="Inspect local source read-only without connecting to a destination.")
    parser.add_argument("--execute", action="store_true", help="Write to DATABASE_URL after validating a separate backup.")
    parser.add_argument("--backup-dir", type=Path, help="Directory containing a verified copy of the source DB and full chroma_data directory.")
    args = parser.parse_args()
    if args.dry_run == args.execute:
        parser.error("Choose exactly one of --dry-run or --execute.")
    if args.dry_run:
        report = inspect_source(args.source_db, args.source_chroma)
        print(json.dumps(report, indent=2))
        return 0
    if not args.backup_dir:
        parser.error("--execute requires --backup-dir with separately verified source copies.")
    report = migrate(args.source_db, args.source_chroma, args.backup_dir)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        # Deliberately do not include the exception text: driver errors can contain
        # connection details. The stack trace is also suppressed from standard output.
        print(f"Migration stopped safely ({type(exc).__name__}). No success was recorded.", file=sys.stderr)
        raise SystemExit(2)
