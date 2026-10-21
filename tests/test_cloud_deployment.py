import sqlite3
from pathlib import Path

import pytest

from config.settings import settings
from scripts import migrate_to_postgres
from src.database.postgres import HybridRow, _postgres_sql
from src.rag.postgres_vector_store import _vector_literal


def test_postgres_sql_adapter_translates_existing_sqlite_queries():
    query = "INSERT OR IGNORE INTO my_team_settings (id) VALUES (?);"
    assert _postgres_sql(query) == "INSERT INTO my_team_settings (id) VALUES (%s) ON CONFLICT (id) DO NOTHING;"
    assert _postgres_sql("CREATE TABLE x (id INTEGER PRIMARY KEY AUTOINCREMENT);") == "CREATE TABLE x (id BIGSERIAL PRIMARY KEY);"


def test_hybrid_row_supports_mapping_and_positional_access():
    row = HybridRow(("id", "name"), (7, "Arsenal"))
    assert row[0] == 7
    assert row["name"] == "Arsenal"
    assert dict(row) == {"id": 7, "name": "Arsenal"}


def test_vector_literal_requires_the_existing_embedding_dimension():
    assert _vector_literal([0.0] * 384).startswith("[0,0,0")
    with pytest.raises(ValueError, match="384-dimensional"):
        _vector_literal([0.0] * 383)


def _create_inspection_db(path: Path, duplicate_history: bool = False):
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE teams (id INTEGER PRIMARY KEY, name TEXT)")
    connection.executemany("INSERT INTO teams VALUES (?, ?)", [(1, "Arsenal"), (2, "Aston Villa")])
    connection.execute("CREATE TABLE players (id INTEGER PRIMARY KEY, team_id INTEGER, web_name TEXT)")
    connection.execute("INSERT INTO players VALUES (10, 1, 'Saka')")
    connection.execute("CREATE TABLE player_gameweek_history (player_id INTEGER, round INTEGER)")
    if duplicate_history:
        connection.executemany("INSERT INTO player_gameweek_history VALUES (?, ?)", [(9, 1), (9, 1)])
    connection.commit()
    connection.close()


def test_migration_dry_run_inspects_read_only_source(monkeypatch, tmp_path):
    source = tmp_path / "fpl_historical.db"
    _create_inspection_db(source)
    monkeypatch.setattr(migrate_to_postgres, "_read_vectors", lambda _path: [])
    monkeypatch.setattr(settings, "DATABASE_URL", None)

    report = migrate_to_postgres.inspect_source(source, tmp_path / "chroma_data")

    assert report["counts"]["teams"] == 2
    assert report["counts"]["players"] == 1
    assert report["vectors"] == 0
    check = sqlite3.connect(source)
    assert check.execute("SELECT COUNT(*) FROM teams").fetchone()[0] == 2
    assert check.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    check.close()


def test_migration_preflight_rejects_duplicate_natural_keys(monkeypatch, tmp_path):
    source = tmp_path / "fpl_historical.db"
    _create_inspection_db(source, duplicate_history=True)
    monkeypatch.setattr(migrate_to_postgres, "_read_vectors", lambda _path: [])
    with pytest.raises(ValueError, match="Duplicate natural keys"):
        migrate_to_postgres.inspect_source(source, tmp_path / "chroma_data")


def test_backup_preflight_compares_logical_source_fingerprint(monkeypatch, tmp_path):
    source_dir = tmp_path / "source"
    backup_dir = tmp_path / "backup"
    source_dir.mkdir()
    backup_dir.mkdir()
    source_db = source_dir / "fpl_historical.db"
    backup_db = backup_dir / "fpl_historical.db"
    _create_inspection_db(source_db)
    backup_connection = sqlite3.connect(backup_db)
    with sqlite3.connect(source_db) as source_connection:
        source_connection.backup(backup_connection)
    backup_connection.close()
    source_chroma = source_dir / "chroma_data"
    backup_chroma = backup_dir / "chroma_data"
    source_chroma.mkdir()
    backup_chroma.mkdir()
    monkeypatch.setattr(migrate_to_postgres, "_read_vectors", lambda _path: [])
    migrate_to_postgres._validate_backup(source_db, source_chroma, backup_dir)

    with sqlite3.connect(backup_db) as changed:
        changed.execute("INSERT INTO teams VALUES (3, 'Changed')")
    with pytest.raises(ValueError, match="Backup contents do not exactly match"):
        migrate_to_postgres._validate_backup(source_db, source_chroma, backup_dir)
