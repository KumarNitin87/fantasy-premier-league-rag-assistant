"""
SQLite Database Connection and Schema Initialization.
Phase 2: Added `code` and `photo_url` columns to players table.
"""

import sqlite3
from pathlib import Path
from contextlib import contextmanager
from typing import Generator
from config.settings import settings


def get_connection(db_path: Path = settings.SQLITE_DB_PATH) -> sqlite3.Connection:
    """Create a configured SQLite database connection with row factory and WAL mode."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    return conn


@contextmanager
def db_session(db_path: Path = settings.SQLITE_DB_PATH) -> Generator[sqlite3.Connection, None, None]:
    """Context manager for atomic database operations with automatic commit/rollback."""
    conn = get_connection(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path: Path = settings.SQLITE_DB_PATH) -> None:
    """Initialize SQLite database tables and indexes. Idempotent — safe to call multiple times."""
    with db_session(db_path) as conn:
        cursor = conn.cursor()

        # 1. Teams
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS teams (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                short_name TEXT NOT NULL,
                strength_overall_home INTEGER,
                strength_overall_away INTEGER,
                strength_attack_home INTEGER,
                strength_attack_away INTEGER,
                strength_defence_home INTEGER,
                strength_defence_away INTEGER
            );
        """)

        # 2. Players (Phase 2: adds `code` and `photo_url`)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS players (
                id INTEGER PRIMARY KEY,
                team_id INTEGER NOT NULL,
                web_name TEXT NOT NULL,
                first_name TEXT NOT NULL,
                second_name TEXT NOT NULL,
                position TEXT NOT NULL,
                element_type INTEGER NOT NULL,
                now_cost INTEGER NOT NULL,
                code INTEGER,
                photo_url TEXT,
                selected_by_percent REAL DEFAULT 0.0,
                status TEXT DEFAULT 'a',
                chance_of_playing_next_round INTEGER DEFAULT 100,
                chance_of_playing_this_round INTEGER DEFAULT 100,
                news TEXT DEFAULT '',
                news_added TEXT,
                form REAL DEFAULT 0.0,
                points_per_game REAL DEFAULT 0.0,
                event_points INTEGER DEFAULT 0,
                total_points INTEGER DEFAULT 0,
                minutes INTEGER DEFAULT 0,
                goals_scored INTEGER DEFAULT 0,
                assists INTEGER DEFAULT 0,
                clean_sheets INTEGER DEFAULT 0,
                goals_conceded INTEGER DEFAULT 0,
                own_goals INTEGER DEFAULT 0,
                penalties_saved INTEGER DEFAULT 0,
                penalties_missed INTEGER DEFAULT 0,
                yellow_cards INTEGER DEFAULT 0,
                red_cards INTEGER DEFAULT 0,
                saves INTEGER DEFAULT 0,
                bonus INTEGER DEFAULT 0,
                bps INTEGER DEFAULT 0,
                influence REAL DEFAULT 0.0,
                creativity REAL DEFAULT 0.0,
                threat REAL DEFAULT 0.0,
                ict_index REAL DEFAULT 0.0,
                expected_goals REAL DEFAULT 0.0,
                expected_assists REAL DEFAULT 0.0,
                expected_goal_involvements REAL DEFAULT 0.0,
                expected_goals_conceded REAL DEFAULT 0.0,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (team_id) REFERENCES teams(id)
            );
        """)

        # Migrate existing DB: add new columns if they don't exist yet
        _add_column_if_missing(cursor, "players", "code", "INTEGER")
        _add_column_if_missing(cursor, "players", "photo_url", "TEXT")
        _add_column_if_missing(cursor, "players", "event_points", "INTEGER DEFAULT 0")

        # 3. Player Gameweek History
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS player_gameweek_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                player_id INTEGER NOT NULL,
                round INTEGER NOT NULL,
                total_points INTEGER DEFAULT 0,
                minutes INTEGER DEFAULT 0,
                goals_scored INTEGER DEFAULT 0,
                assists INTEGER DEFAULT 0,
                clean_sheets INTEGER DEFAULT 0,
                goals_conceded INTEGER DEFAULT 0,
                yellow_cards INTEGER DEFAULT 0,
                red_cards INTEGER DEFAULT 0,
                saves INTEGER DEFAULT 0,
                bonus INTEGER DEFAULT 0,
                bps INTEGER DEFAULT 0,
                ict_index REAL DEFAULT 0.0,
                value INTEGER DEFAULT 0,
                expected_goals REAL DEFAULT 0.0,
                expected_assists REAL DEFAULT 0.0,
                expected_goal_involvements REAL DEFAULT 0.0,
                expected_goals_conceded REAL DEFAULT 0.0,
                kickoff_time TEXT,
                opponent_team_id INTEGER,
                was_home INTEGER DEFAULT 1,
                is_provisional INTEGER DEFAULT 0,
                FOREIGN KEY (player_id) REFERENCES players(id),
                UNIQUE(player_id, round)
            );
        """)
        _add_column_if_missing(cursor, "player_gameweek_history", "is_provisional", "INTEGER DEFAULT 0")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS player_history_sync (
                player_id INTEGER PRIMARY KEY,
                status TEXT NOT NULL CHECK (status IN ('ok', 'error')),
                record_count INTEGER NOT NULL DEFAULT 0,
                last_attempt_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                attempt_order INTEGER NOT NULL DEFAULT 0,
                error_message TEXT,
                FOREIGN KEY (player_id) REFERENCES players(id) ON DELETE CASCADE
            );
        """)
        _add_column_if_missing(cursor, "player_history_sync", "attempt_order", "INTEGER NOT NULL DEFAULT 0")
        cursor.execute("""
            UPDATE player_history_sync AS current
            SET attempt_order = (
                SELECT COUNT(*) FROM player_history_sync AS prior
                WHERE prior.last_attempt_at < current.last_attempt_at
                   OR (
                       prior.last_attempt_at = current.last_attempt_at
                       AND prior.player_id <= current.player_id
                   )
            )
            WHERE current.attempt_order = 0;
        """)

        # 4. Player Past Seasons
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS player_past_seasons (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                player_id INTEGER NOT NULL,
                season_name TEXT NOT NULL,
                total_points INTEGER DEFAULT 0,
                total_minutes INTEGER DEFAULT 0,
                goals_scored INTEGER DEFAULT 0,
                assists INTEGER DEFAULT 0,
                clean_sheets INTEGER DEFAULT 0,
                bps INTEGER DEFAULT 0,
                FOREIGN KEY (player_id) REFERENCES players(id),
                UNIQUE(player_id, season_name)
            );
        """)

        # 5. Fixtures
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS fixtures (
                id INTEGER PRIMARY KEY,
                event INTEGER,
                team_h INTEGER NOT NULL,
                team_a INTEGER NOT NULL,
                team_h_score INTEGER,
                team_a_score INTEGER,
                team_h_difficulty INTEGER DEFAULT 3,
                team_a_difficulty INTEGER DEFAULT 3,
                finished INTEGER DEFAULT 0,
                finished_provisional INTEGER DEFAULT 0,
                started INTEGER DEFAULT 0,
                kickoff_time TEXT,
                FOREIGN KEY (team_h) REFERENCES teams(id),
                FOREIGN KEY (team_a) REFERENCES teams(id)
            );
        """)
        _add_column_if_missing(cursor, "fixtures", "finished_provisional", "INTEGER DEFAULT 0")
        _add_column_if_missing(cursor, "fixtures", "started", "INTEGER DEFAULT 0")

        # 6. Ingestion audit log
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS ingestion_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sync_type TEXT NOT NULL,
                players_synced INTEGER DEFAULT 0,
                teams_synced INTEGER DEFAULT 0,
                fixtures_synced INTEGER DEFAULT 0,
                history_records INTEGER DEFAULT 0,
                started_at TEXT,
                completed_at TEXT,
                status TEXT DEFAULT 'ok',
                error_message TEXT
            );
        """)

        # Indexes
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_players_team ON players(team_id);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_players_position ON players(position);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_players_form ON players(form DESC);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_players_points ON players(total_points DESC);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_players_code ON players(code);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_history_player_round ON player_gameweek_history(player_id, round);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_fixtures_event ON fixtures(event);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_fixtures_teams ON fixtures(team_h, team_a);")

        # Phase 4: additive user-managed squad storage; existing FPL tables stay intact.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS my_team_settings (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                bank_millions REAL NOT NULL DEFAULT 0.0,
                free_transfers INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS my_team_squad (
                player_id INTEGER PRIMARY KEY,
                purchase_price REAL NOT NULL,
                bench_order INTEGER NOT NULL DEFAULT 0,
                is_captain INTEGER NOT NULL DEFAULT 0,
                is_vice_captain INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY (player_id) REFERENCES players(id) ON DELETE CASCADE
            );
        """)
        cursor.execute("INSERT OR IGNORE INTO my_team_settings (id, bank_millions, free_transfers) VALUES (1, 0.0, 1);")


def _add_column_if_missing(cursor: sqlite3.Cursor, table: str, column: str, col_type: str) -> None:
    """Safely add a column to an existing table if it does not already exist (migration helper)."""
    cursor.execute(f"PRAGMA table_info({table});")
    existing = [row["name"] for row in cursor.fetchall()]
    if column not in existing:
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type};")
