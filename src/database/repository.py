"""
Data Access Layer (Repository Pattern) for Premier League and FPL database.
Phase 2: photo_url / code fields, ingestion_log writes, get_db_stats helper.
"""

from typing import List, Optional, Dict, Any, Tuple
from pathlib import Path
from datetime import datetime
from src.database.connection import db_session
from src.database.models import Team, Player, Fixture, PlayerGameweekHistory, PlayerPastSeason, SquadMember
from config.settings import settings


class FPLRepository:
    """Repository handling all CRUD and analytical queries on SQLite."""

    def __init__(self, db_path: Path = settings.SQLITE_DB_PATH):
        self.db_path = db_path

    # =========================================================================
    # Teams
    # =========================================================================
    def upsert_teams(self, teams: List[Team]) -> None:
        query = """
            INSERT INTO teams (
                id, name, short_name,
                strength_overall_home, strength_overall_away,
                strength_attack_home, strength_attack_away,
                strength_defence_home, strength_defence_away
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name=excluded.name, short_name=excluded.short_name,
                strength_overall_home=excluded.strength_overall_home,
                strength_overall_away=excluded.strength_overall_away,
                strength_attack_home=excluded.strength_attack_home,
                strength_attack_away=excluded.strength_attack_away,
                strength_defence_home=excluded.strength_defence_home,
                strength_defence_away=excluded.strength_defence_away;
        """
        with db_session(self.db_path) as conn:
            conn.executemany(query, [
                (t.id, t.name, t.short_name,
                 t.strength_overall_home, t.strength_overall_away,
                 t.strength_attack_home, t.strength_attack_away,
                 t.strength_defence_home, t.strength_defence_away)
                for t in teams
            ])

    def get_all_teams(self) -> List[Team]:
        with db_session(self.db_path) as conn:
            rows = conn.execute("SELECT * FROM teams ORDER BY name ASC;").fetchall()
            return [Team(**dict(row)) for row in rows]

    def get_team_by_id(self, team_id: int) -> Optional[Team]:
        with db_session(self.db_path) as conn:
            row = conn.execute("SELECT * FROM teams WHERE id = ?;", (team_id,)).fetchone()
            return Team(**dict(row)) if row else None

    def count_teams(self) -> int:
        with db_session(self.db_path) as conn:
            return conn.execute("SELECT COUNT(*) FROM teams;").fetchone()[0]

    # =========================================================================
    # Players
    # =========================================================================
    def upsert_players(self, players: List[Player]) -> None:
        query = """
            INSERT INTO players (
                id, team_id, web_name, first_name, second_name,
                position, element_type, now_cost, code, photo_url,
                selected_by_percent, status, chance_of_playing_next_round,
                chance_of_playing_this_round, news, news_added, form,
                points_per_game, event_points, total_points, minutes, goals_scored, assists,
                clean_sheets, goals_conceded, own_goals, penalties_saved,
                penalties_missed, yellow_cards, red_cards, saves, bonus, bps,
                influence, creativity, threat, ict_index,
                expected_goals, expected_assists, expected_goal_involvements,
                expected_goals_conceded, updated_at
            ) VALUES (
                ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,
                CURRENT_TIMESTAMP
            )
            ON CONFLICT(id) DO UPDATE SET
                team_id=excluded.team_id, web_name=excluded.web_name,
                first_name=excluded.first_name, second_name=excluded.second_name,
                position=excluded.position, element_type=excluded.element_type,
                now_cost=excluded.now_cost, code=excluded.code, photo_url=excluded.photo_url,
                selected_by_percent=excluded.selected_by_percent, status=excluded.status,
                chance_of_playing_next_round=excluded.chance_of_playing_next_round,
                chance_of_playing_this_round=excluded.chance_of_playing_this_round,
                news=excluded.news, news_added=excluded.news_added, form=excluded.form,
                points_per_game=excluded.points_per_game, event_points=excluded.event_points,
                total_points=excluded.total_points,
                minutes=excluded.minutes, goals_scored=excluded.goals_scored,
                assists=excluded.assists, clean_sheets=excluded.clean_sheets,
                goals_conceded=excluded.goals_conceded, own_goals=excluded.own_goals,
                penalties_saved=excluded.penalties_saved, penalties_missed=excluded.penalties_missed,
                yellow_cards=excluded.yellow_cards, red_cards=excluded.red_cards,
                saves=excluded.saves, bonus=excluded.bonus, bps=excluded.bps,
                influence=excluded.influence, creativity=excluded.creativity,
                threat=excluded.threat, ict_index=excluded.ict_index,
                expected_goals=excluded.expected_goals, expected_assists=excluded.expected_assists,
                expected_goal_involvements=excluded.expected_goal_involvements,
                expected_goals_conceded=excluded.expected_goals_conceded,
                updated_at=CURRENT_TIMESTAMP;
        """
        with db_session(self.db_path) as conn:
            conn.executemany(query, [
                (p.id, p.team_id, p.web_name, p.first_name, p.second_name,
                 p.position, p.element_type, p.now_cost, p.code, p.photo_url,
                 p.selected_by_percent, p.status, p.chance_of_playing_next_round,
                 p.chance_of_playing_this_round, p.news, p.news_added, p.form,
                 p.points_per_game, p.event_points, p.total_points, p.minutes, p.goals_scored,
                 p.assists, p.clean_sheets, p.goals_conceded, p.own_goals,
                 p.penalties_saved, p.penalties_missed, p.yellow_cards, p.red_cards,
                 p.saves, p.bonus, p.bps, p.influence, p.creativity, p.threat,
                 p.ict_index, p.expected_goals, p.expected_assists,
                 p.expected_goal_involvements, p.expected_goals_conceded)
                for p in players
            ])

    def update_player_photo_code(self, player_id: int, code: int, photo_url: str) -> None:
        """Repair only a player's existing FPL photo fields without rewriting stats."""
        with db_session(self.db_path) as conn:
            conn.execute(
                "UPDATE players SET code = ?, photo_url = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?;",
                (code, photo_url, player_id),
            )

    def get_players(
        self,
        position: Optional[str] = None,
        team_id: Optional[int] = None,
        min_price: Optional[float] = None,
        max_price: Optional[float] = None,
        status: Optional[str] = None,
        sort_by: str = "total_points",
        ascending: bool = False,
        limit: int = 100,
    ) -> List[Player]:
        valid_cols = {
            "total_points", "form", "now_cost", "selected_by_percent",
            "goals_scored", "assists", "clean_sheets", "ict_index",
            "expected_goals", "expected_assists", "expected_goal_involvements",
        }
        order_col = sort_by if sort_by in valid_cols else "total_points"
        order_dir = "ASC" if ascending else "DESC"

        conditions = ["1=1"]
        params: List[Any] = []

        if position:
            conditions.append("p.position = ?"); params.append(position)
        if team_id:
            conditions.append("p.team_id = ?"); params.append(team_id)
        if min_price is not None:
            conditions.append("p.now_cost >= ?"); params.append(int(min_price * 10))
        if max_price is not None:
            conditions.append("p.now_cost <= ?"); params.append(int(max_price * 10))
        if status:
            conditions.append("p.status = ?"); params.append(status)

        query = f"""
            SELECT p.*, t.name as team_name, t.short_name as team_short_name
            FROM players p JOIN teams t ON p.team_id = t.id
            WHERE {" AND ".join(conditions)}
            ORDER BY p.{order_col} {order_dir}
            LIMIT ?;
        """
        params.append(limit)
        with db_session(self.db_path) as conn:
            rows = conn.execute(query, params).fetchall()
            return [self._row_to_player(row) for row in rows]

    def get_player_by_id(self, player_id: int) -> Optional[Player]:
        query = """
            SELECT p.*, t.name as team_name, t.short_name as team_short_name
            FROM players p JOIN teams t ON p.team_id = t.id
            WHERE p.id = ?;
        """
        with db_session(self.db_path) as conn:
            row = conn.execute(query, (player_id,)).fetchone()
            return self._row_to_player(row) if row else None

    def search_players(self, search_term: str, limit: int = 20) -> List[Player]:
        term = f"%{search_term.strip()}%"
        query = """
            SELECT p.*, t.name as team_name, t.short_name as team_short_name
            FROM players p JOIN teams t ON p.team_id = t.id
            WHERE p.web_name LIKE ? OR p.first_name LIKE ? OR p.second_name LIKE ?
            ORDER BY p.total_points DESC LIMIT ?;
        """
        with db_session(self.db_path) as conn:
            rows = conn.execute(query, (term, term, term, limit)).fetchall()
            return [self._row_to_player(row) for row in rows]

    def count_players(self) -> int:
        with db_session(self.db_path) as conn:
            return conn.execute("SELECT COUNT(*) FROM players;").fetchone()[0]

    # =========================================================================
    # Fixtures
    # =========================================================================
    def upsert_fixtures(self, fixtures: List[Fixture]) -> None:
        query = """
            INSERT INTO fixtures (id, event, team_h, team_a, team_h_score, team_a_score,
                team_h_difficulty, team_a_difficulty, finished, finished_provisional, started, kickoff_time)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                event=excluded.event, team_h=excluded.team_h, team_a=excluded.team_a,
                team_h_score=excluded.team_h_score, team_a_score=excluded.team_a_score,
                team_h_difficulty=excluded.team_h_difficulty, team_a_difficulty=excluded.team_a_difficulty,
                finished=excluded.finished, finished_provisional=excluded.finished_provisional,
                started=excluded.started,
                kickoff_time=excluded.kickoff_time;
        """
        with db_session(self.db_path) as conn:
            conn.executemany(query, [
                (f.id, f.event, f.team_h, f.team_a, f.team_h_score, f.team_a_score,
                 f.team_h_difficulty, f.team_a_difficulty, 1 if f.finished else 0,
                 1 if getattr(f, "finished_provisional", False) else 0,
                 1 if getattr(f, "started", False) else 0, f.kickoff_time)
                for f in fixtures
            ])

    def get_upcoming_fixtures(self, team_id: Optional[int] = None, limit: int = 20) -> List[Fixture]:
        conditions = ["f.finished = 0"]
        params: List[Any] = []
        if team_id:
            conditions.append("(f.team_h = ? OR f.team_a = ?)"); params.extend([team_id, team_id])
        query = f"""
            SELECT f.*, th.name as team_h_name, th.short_name as team_h_short_name,
                   ta.name as team_a_name, ta.short_name as team_a_short_name
            FROM fixtures f JOIN teams th ON f.team_h=th.id JOIN teams ta ON f.team_a=ta.id
            WHERE {" AND ".join(conditions)}
            ORDER BY f.event ASC, f.kickoff_time ASC LIMIT ?;
        """
        params.append(limit)
        with db_session(self.db_path) as conn:
            rows = conn.execute(query, params).fetchall()
            return [self._row_to_fixture(row) for row in rows]

    def get_fixtures_by_gameweek(self, gameweek: int) -> List[Fixture]:
        query = """
            SELECT f.*, th.name as team_h_name, th.short_name as team_h_short_name,
                   ta.name as team_a_name, ta.short_name as team_a_short_name
            FROM fixtures f JOIN teams th ON f.team_h=th.id JOIN teams ta ON f.team_a=ta.id
            WHERE f.event = ? ORDER BY f.kickoff_time ASC;
        """
        with db_session(self.db_path) as conn:
            rows = conn.execute(query, (gameweek,)).fetchall()
            return [self._row_to_fixture(row) for row in rows]

    def get_current_gameweek(self) -> Optional[int]:
        """Derive the current/upcoming gameweek from the existing unplayed fixture data."""
        with db_session(self.db_path) as conn:
            row = conn.execute(
                "SELECT MIN(event) FROM fixtures WHERE finished = 0 AND event IS NOT NULL;"
            ).fetchone()
            return int(row[0]) if row and row[0] is not None else None

    # =========================================================================
    # Phase 4: persisted personal squad
    # =========================================================================
    def get_my_team_settings(self) -> Dict[str, Any]:
        with db_session(self.db_path) as conn:
            row = conn.execute("SELECT bank_millions, free_transfers FROM my_team_settings WHERE id=1;").fetchone()
            return dict(row) if row else {"bank_millions": 0.0, "free_transfers": 1}

    def get_my_team(self) -> List[Dict[str, Any]]:
        query = """
            SELECT p.*, t.name AS team_name, t.short_name AS team_short_name,
                   s.purchase_price, s.bench_order, s.is_captain, s.is_vice_captain
            FROM my_team_squad s
            JOIN players p ON p.id=s.player_id
            JOIN teams t ON t.id=p.team_id
            ORDER BY s.bench_order, p.position, p.web_name;
        """
        with db_session(self.db_path) as conn:
            rows = conn.execute(query).fetchall()
            result = []
            for row in rows:
                data = dict(row)
                membership = {key: data.pop(key) for key in ("purchase_price", "bench_order", "is_captain", "is_vice_captain")}
                result.append({"player": self._row_to_player(data), **membership})
            return result

    def save_my_team(self, members: List[SquadMember], bank_millions: float, free_transfers: int) -> None:
        """Atomically replace only the user's squad selection and settings."""
        if len(members) > 15 or len({m.player_id for m in members}) != len(members):
            raise ValueError("A squad may contain at most 15 unique players")
        captains = sum(bool(m.is_captain) for m in members)
        vice_captains = sum(bool(m.is_vice_captain) for m in members)
        if captains > 1 or vice_captains > 1 or any(m.is_captain and m.is_vice_captain for m in members):
            raise ValueError("Choose at most one captain and one different vice-captain")
        with db_session(self.db_path) as conn:
            conn.execute("DELETE FROM my_team_squad;")
            conn.executemany(
                "INSERT INTO my_team_squad (player_id,purchase_price,bench_order,is_captain,is_vice_captain) VALUES (?,?,?,?,?);",
                [(m.player_id, m.purchase_price, m.bench_order, int(m.is_captain), int(m.is_vice_captain)) for m in members],
            )
            conn.execute("""
                INSERT INTO my_team_settings (id,bank_millions,free_transfers,updated_at)
                VALUES (1,?,?,CURRENT_TIMESTAMP)
                ON CONFLICT(id) DO UPDATE SET bank_millions=excluded.bank_millions,
                    free_transfers=excluded.free_transfers,updated_at=CURRENT_TIMESTAMP;
            """, (float(bank_millions), int(free_transfers)))

    def count_fixtures(self) -> int:
        with db_session(self.db_path) as conn:
            return conn.execute("SELECT COUNT(*) FROM fixtures;").fetchone()[0]

    # =========================================================================
    # Gameweek History
    # =========================================================================
    def upsert_gameweek_history(self, history: List[PlayerGameweekHistory]) -> None:
        query = """
            INSERT INTO player_gameweek_history (
                player_id, round, total_points, minutes, goals_scored, assists,
                clean_sheets, goals_conceded, yellow_cards, red_cards, saves,
                bonus, bps, ict_index, value, expected_goals, expected_assists,
                expected_goal_involvements, expected_goals_conceded, kickoff_time,
                opponent_team_id, was_home, is_provisional
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(player_id, round) DO UPDATE SET
                total_points=excluded.total_points, minutes=excluded.minutes,
                goals_scored=excluded.goals_scored, assists=excluded.assists,
                clean_sheets=excluded.clean_sheets, goals_conceded=excluded.goals_conceded,
                yellow_cards=excluded.yellow_cards, red_cards=excluded.red_cards,
                saves=excluded.saves, bonus=excluded.bonus, bps=excluded.bps,
                ict_index=excluded.ict_index, value=excluded.value,
                expected_goals=excluded.expected_goals, expected_assists=excluded.expected_assists,
                expected_goal_involvements=excluded.expected_goal_involvements,
                expected_goals_conceded=excluded.expected_goals_conceded,
                kickoff_time=excluded.kickoff_time,
                opponent_team_id=excluded.opponent_team_id, was_home=excluded.was_home,
                is_provisional=excluded.is_provisional;
        """
        with db_session(self.db_path) as conn:
            conn.executemany(query, [
                (h.player_id, h.round, h.total_points, h.minutes, h.goals_scored,
                 h.assists, h.clean_sheets, h.goals_conceded, h.yellow_cards, h.red_cards,
                 h.saves, h.bonus, h.bps, h.ict_index, h.value, h.expected_goals,
                 h.expected_assists, h.expected_goal_involvements, h.expected_goals_conceded,
                 h.kickoff_time, h.opponent_team_id, 1 if h.was_home else 0,
                 1 if h.is_provisional else 0)
                for h in history
            ])

    def get_history_fixture_states(self) -> Dict[int, Tuple[Optional[int], bool, bool]]:
        """Return fixture event, completed, and provisional flags for history filtering."""
        with db_session(self.db_path) as conn:
            rows = conn.execute(
                "SELECT id, event, finished, finished_provisional FROM fixtures;"
            ).fetchall()
            return {
                int(row["id"]): (
                    row["event"],
                    bool(row["finished"] or row["finished_provisional"]),
                    bool(row["finished_provisional"]),
                )
                for row in rows
            }

    def get_player_history_sync_candidates(
        self, limit: Optional[int] = None, incomplete_only: bool = False
    ) -> List[Player]:
        """Select unsynced players for backfill or the least-recently-synced players for refresh."""
        query = """
            SELECT p.*, t.name AS team_name, t.short_name AS team_short_name
            FROM players p
            JOIN teams t ON p.team_id = t.id
            LEFT JOIN player_history_sync s ON s.player_id = p.id
        """
        if incomplete_only:
            query += " WHERE s.status IS NULL OR s.status != 'ok'"
            query += " ORDER BY p.id ASC"
        else:
            query += """
                ORDER BY CASE WHEN s.player_id IS NULL THEN 0 ELSE 1 END,
                         s.attempt_order ASC, p.id ASC
            """
        params: List[Any] = []
        if limit is not None:
            query += " LIMIT ?"
            params.append(max(0, int(limit)))
        with db_session(self.db_path) as conn:
            rows = conn.execute(query, params).fetchall()
            return [self._row_to_player(row) for row in rows]

    def record_player_history_sync(
        self, player_id: int, status: str, record_count: int = 0, error_message: Optional[str] = None
    ) -> None:
        if status not in {"ok", "error"}:
            raise ValueError(f"Unsupported player history sync status: {status}")
        with db_session(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO player_history_sync (
                    player_id, status, record_count, last_attempt_at, attempt_order, error_message
                ) VALUES (
                    ?, ?, ?, CURRENT_TIMESTAMP,
                    (SELECT COALESCE(MAX(attempt_order), 0) + 1 FROM player_history_sync), ?
                )
                ON CONFLICT(player_id) DO UPDATE SET
                    status=excluded.status,
                    record_count=excluded.record_count,
                    last_attempt_at=excluded.last_attempt_at,
                    attempt_order=excluded.attempt_order,
                    error_message=excluded.error_message;
                """,
                (player_id, status, record_count, error_message),
            )

    def get_player_gameweek_history(self, player_id: int, limit: Optional[int] = None) -> List[PlayerGameweekHistory]:
        with db_session(self.db_path) as conn:
            query = "SELECT * FROM player_gameweek_history WHERE player_id=? ORDER BY round DESC"
            params: List[Any] = [player_id]
            if limit:
                query += f" LIMIT {int(limit)}"
            rows = conn.execute(query, params).fetchall()
            history = []
            for row in rows:
                values = {
                    k: v for k, v in dict(row).items()
                    if k in PlayerGameweekHistory.__dataclass_fields__
                }
                values["was_home"] = bool(values["was_home"])
                values["is_provisional"] = bool(values["is_provisional"])
                history.append(PlayerGameweekHistory(**values))
            history.sort(key=lambda h: h.round)
            return history

    # =========================================================================
    # Database Stats (Phase 2 dashboard helper)
    # =========================================================================
    def get_db_stats(self) -> Dict[str, Any]:
        """Return aggregate stats for the dashboard overview."""
        with db_session(self.db_path) as conn:
            player_count = conn.execute("SELECT COUNT(*) FROM players;").fetchone()[0]
            team_count = conn.execute("SELECT COUNT(*) FROM teams;").fetchone()[0]
            fixture_count = conn.execute("SELECT COUNT(*) FROM fixtures;").fetchone()[0]
            upcoming_count = conn.execute("SELECT COUNT(*) FROM fixtures WHERE finished=0;").fetchone()[0]
            history_count = conn.execute("SELECT COUNT(*) FROM player_gameweek_history;").fetchone()[0]
            injured_count = conn.execute("SELECT COUNT(*) FROM players WHERE status IN ('i','d','s','u');").fetchone()[0]
            last_sync = conn.execute(
                "SELECT completed_at FROM ingestion_log "
                "WHERE sync_type = ? AND status = 'ok' ORDER BY id DESC LIMIT 1;",
                ("fpl",),
            ).fetchone()
            last_news_sync = conn.execute(
                "SELECT completed_at FROM ingestion_log "
                "WHERE sync_type = ? AND status = 'ok' ORDER BY id DESC LIMIT 1;",
                ("news",),
            ).fetchone()
            last_fpl_attempt = conn.execute(
                "SELECT status, error_message, started_at, completed_at FROM ingestion_log "
                "WHERE sync_type = ? ORDER BY id DESC LIMIT 1;",
                ("fpl",),
            ).fetchone()
            last_news_attempt = conn.execute(
                "SELECT status, error_message, started_at, completed_at FROM ingestion_log "
                "WHERE sync_type = ? ORDER BY id DESC LIMIT 1;",
                ("news",),
            ).fetchone()
        return {
            "total_players": player_count,
            "total_teams": team_count,
            "total_fixtures": fixture_count,
            "upcoming_fixtures": upcoming_count,
            "history_records": history_count,
            "injured_or_doubtful": injured_count,
            "last_sync": last_sync["completed_at"] if last_sync else "Never",
            "last_fpl_attempt_at": last_fpl_attempt["completed_at"] if last_fpl_attempt else None,
            "last_fpl_attempt_started_at": last_fpl_attempt["started_at"] if last_fpl_attempt else None,
            "last_news_sync": last_news_sync["completed_at"] if last_news_sync else "Never",
            "last_news_attempt_at": last_news_attempt["completed_at"] if last_news_attempt else None,
            "last_news_attempt_started_at": last_news_attempt["started_at"] if last_news_attempt else None,
            "last_fpl_status": last_fpl_attempt["status"] if last_fpl_attempt else "Never run",
            "last_fpl_error": last_fpl_attempt["error_message"] if last_fpl_attempt else "",
            "last_news_status": last_news_attempt["status"] if last_news_attempt else "Never run",
            "last_news_error": last_news_attempt["error_message"] if last_news_attempt else "",
        }

    # =========================================================================
    # Ingestion Log
    # =========================================================================
    def log_ingestion(
        self,
        sync_type: str,
        players: int = 0,
        teams: int = 0,
        fixtures: int = 0,
        history: int = 0,
        started_at: str = "",
        completed_at: str = "",
        status: str = "ok",
        error: str = "",
    ) -> None:
        with db_session(self.db_path) as conn:
            conn.execute("""
                INSERT INTO ingestion_log
                (sync_type, players_synced, teams_synced, fixtures_synced,
                 history_records, started_at, completed_at, status, error_message)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
            """, (sync_type, players, teams, fixtures, history,
                  started_at, completed_at, status, error))

    # =========================================================================
    # Helpers
    # =========================================================================
    @staticmethod
    def _row_to_player(row: Any) -> Player:
        d = dict(row)
        d.pop("updated_at", None)
        valid_keys = set(Player.__dataclass_fields__.keys())
        return Player(**{k: v for k, v in d.items() if k in valid_keys})

    @staticmethod
    def _row_to_fixture(row: Any) -> Fixture:
        d = dict(row)
        d["finished"] = bool(d.get("finished", 0))
        d["finished_provisional"] = bool(d.get("finished_provisional", 0))
        d["started"] = bool(d.get("started", 0))
        valid_keys = set(Fixture.__dataclass_fields__.keys())
        return Fixture(**{k: v for k, v in d.items() if k in valid_keys})
