"""
ETL Pipeline for syncing Official Fantasy Premier League Data into SQLite.
Supports both live API fetching and offline fallback from cached JSON dumps.
"""

import argparse
import json
import logging
import time
from collections import defaultdict
from pathlib import Path
from typing import Dict, Any, List, Optional, Set, Tuple
from datetime import datetime, timezone

from config.settings import settings
from config.constants import POSITION_MAP
from src.api.fpl_client import FPLClient
from src.database.connection import init_db
from src.database.models import Team, Player, Fixture, PlayerGameweekHistory, PlayerPastSeason
from src.database.repository import FPLRepository
from src.sync.process_lock import acquire_fpl_sync_lock

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("sync_fpl")


class FPLSyncPipeline:
    """Orchestrates syncing FPL data from API or offline dumps to SQLite."""

    def __init__(self, repository: Optional[FPLRepository] = None, client: Optional[FPLClient] = None):
        self.repo = repository or FPLRepository()
        self.client = client or FPLClient()
        self.raw_dir = settings.RAW_DATA_DIR
        self.raw_dir.mkdir(parents=True, exist_ok=True)

    def sync_all(
        self,
        sync_player_history_top_n: int = 50,
        force_refresh: bool = True,
        allow_fallback: bool = False,
    ) -> Dict[str, int]:
        with acquire_fpl_sync_lock(self.repo.db_path) as acquired:
            if not acquired:
                raise RuntimeError("Another FPL synchronization is already in progress.")
            return self._sync_all_unlocked(
                sync_player_history_top_n=sync_player_history_top_n,
                force_refresh=force_refresh,
                allow_fallback=allow_fallback,
            )

    def _sync_all_unlocked(
        self,
        sync_player_history_top_n: int,
        force_refresh: bool,
        allow_fallback: bool,
    ) -> Dict[str, int]:
        """
        Execute full synchronization:
        1. Initialize DB schema
        2. Sync Teams and Players from bootstrap-static
        3. Sync Fixtures
        4. Sync Element Summaries (gameweek match history) for top N players
        """
        started_at = datetime.now(timezone.utc).isoformat()
        teams_synced = players_synced = fixtures_synced = history_synced = 0
        if force_refresh and getattr(self, "client", None):
            self.client.clear_cache()
        try:
            init_db(self.repo.db_path)
            logger.info("Database schema verified.")

            # 1. Sync Bootstrap Static (Teams & Players)
            try:
                bootstrap_data = self._fetch_or_fallback_bootstrap(
                    force_refresh=force_refresh, allow_fallback=allow_fallback
                )
            except TypeError:
                bootstrap_data = self._fetch_or_fallback_bootstrap()
            teams_synced = self._process_teams(bootstrap_data.get("teams", []))
            players_synced = self._process_players(bootstrap_data.get("elements", []))

            # 2. Sync Fixtures
            try:
                fixtures_data = self._fetch_or_fallback_fixtures(
                    force_refresh=force_refresh, allow_fallback=allow_fallback
                )
            except TypeError:
                fixtures_data = self._fetch_or_fallback_fixtures()
            fixtures_synced = self._process_fixtures(fixtures_data)

            # 3. Sync History for Top Players
            if sync_player_history_top_n > 0:
                try:
                    history_synced = self._sync_top_players_history(
                        limit=sync_player_history_top_n, force_refresh=force_refresh
                    )
                except TypeError:
                    history_synced = self._sync_top_players_history(limit=sync_player_history_top_n)
        except Exception as exc:
            try:
                self.repo.log_ingestion(
                    sync_type="fpl",
                    players=players_synced,
                    teams=teams_synced,
                    fixtures=fixtures_synced,
                    history=history_synced,
                    started_at=started_at,
                    completed_at=datetime.now(timezone.utc).isoformat(),
                    status="error",
                    error=f"{type(exc).__name__}: {exc}",
                )
            except Exception as log_exc:
                logger.warning("Could not record failed FPL sync status (%s).", type(log_exc).__name__)
            raise

        completed_at = datetime.now(timezone.utc).isoformat()
        try:
            self.repo.log_ingestion(
                sync_type="fpl",
                players=players_synced,
                teams=teams_synced,
                fixtures=fixtures_synced,
                history=history_synced,
                started_at=started_at,
                completed_at=completed_at,
                status="ok",
            )
        except Exception as log_exc:
            logger.warning("FPL sync completed but its status could not be recorded (%s).", type(log_exc).__name__)

        logger.info(
            "Sync complete! Teams: %d | Players: %d | Fixtures: %d | Player History Records: %d",
            teams_synced, players_synced, fixtures_synced, history_synced,
        )

        return {
            "teams": teams_synced,
            "players": players_synced,
            "fixtures": fixtures_synced,
            "history_records": history_synced,
        }

    def _fetch_or_fallback_bootstrap(
        self, force_refresh: bool = True, allow_fallback: bool = False
    ) -> Dict[str, Any]:
        """Fetch bootstrap-static from live API; save dump or fallback to local cache if explicitly allowed."""
        raw_cache_file = self.raw_dir / "bootstrap_static.json"
        try:
            logger.info("Attempting live fetch of FPL bootstrap-static...")
            data = self.client.get_bootstrap_static(force_refresh=force_refresh)
            # Save dump for offline fallback
            with open(raw_cache_file, "w", encoding="utf-8") as f:
                json.dump(data, f)
            logger.info("Successfully fetched and cached live bootstrap-static (%d players)", len(data.get("elements", [])))
            return data
        except Exception as e:
            if allow_fallback and raw_cache_file.exists():
                logger.warning("Live bootstrap-static fetch failed: %s. Using offline fallback.", e)
                with open(raw_cache_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            logger.error("Live bootstrap-static fetch failed: %s", e)
            raise

    def _fetch_or_fallback_fixtures(
        self, force_refresh: bool = True, allow_fallback: bool = False
    ) -> List[Dict[str, Any]]:
        """Fetch fixtures from live API; save dump or fallback to local cache if explicitly allowed."""
        raw_cache_file = self.raw_dir / "fixtures.json"
        try:
            logger.info("Attempting live fetch of FPL fixtures...")
            data = self.client.get_fixtures(force_refresh=force_refresh)
            with open(raw_cache_file, "w", encoding="utf-8") as f:
                json.dump(data, f)
            logger.info("Successfully fetched and cached live fixtures (%d fixtures)", len(data))
            return data
        except Exception as e:
            if allow_fallback and raw_cache_file.exists():
                logger.warning("Live fixtures fetch failed: %s. Using offline fallback.", e)
                with open(raw_cache_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            logger.error("Live fixtures fetch failed: %s", e)
            raise

    def _process_teams(self, raw_teams: List[Dict[str, Any]]) -> int:
        """Parse and upsert teams."""
        teams = [
            Team(
                id=t["id"],
                name=t["name"],
                short_name=t["short_name"],
                strength_overall_home=t.get("strength_overall_home"),
                strength_overall_away=t.get("strength_overall_away"),
                strength_attack_home=t.get("strength_attack_home"),
                strength_attack_away=t.get("strength_attack_away"),
                strength_defence_home=t.get("strength_defence_home"),
                strength_defence_away=t.get("strength_defence_away"),
            )
            for t in raw_teams
        ]
        self.repo.upsert_teams(teams)
        return len(teams)

    def _process_players(self, raw_players: List[Dict[str, Any]]) -> int:
        """Parse and upsert players, storing photo information."""
        players = []
        for p in raw_players:
            element_type = p.get("element_type", 3)
            position = POSITION_MAP.get(element_type, "MID")

            player = Player(
                id=p["id"],
                team_id=p["team"],
                web_name=p["web_name"],
                first_name=p["first_name"],
                second_name=p["second_name"],
                position=position,
                element_type=element_type,
                now_cost=p.get("now_cost", 50),
                # Phase 2 addition – store player code and photo URL
                code=p.get("code"),
                photo_url=FPLClient.build_photo_url(p.get("code")),
                selected_by_percent=float(p.get("selected_by_percent", 0.0) or 0.0),
                status=p.get("status", "a"),
                chance_of_playing_next_round=p.get("chance_of_playing_next_round"),
                chance_of_playing_this_round=p.get("chance_of_playing_this_round"),
                news=p.get("news", "") or "",
                news_added=p.get("news_added"),
                form=float(p.get("form", 0.0) or 0.0),
                points_per_game=float(p.get("points_per_game", 0.0) or 0.0),
                event_points=int(p.get("event_points", 0) or 0),
                total_points=int(p.get("total_points", 0) or 0),
                minutes=int(p.get("minutes", 0) or 0),
                goals_scored=int(p.get("goals_scored", 0) or 0),
                assists=int(p.get("assists", 0) or 0),
                clean_sheets=int(p.get("clean_sheets", 0) or 0),
                goals_conceded=int(p.get("goals_conceded", 0) or 0),
                own_goals=int(p.get("own_goals", 0) or 0),
                penalties_saved=int(p.get("penalties_saved", 0) or 0),
                penalties_missed=int(p.get("penalties_missed", 0) or 0),
                yellow_cards=int(p.get("yellow_cards", 0) or 0),
                red_cards=int(p.get("red_cards", 0) or 0),
                saves=int(p.get("saves", 0) or 0),
                bonus=int(p.get("bonus", 0) or 0),
                bps=int(p.get("bps", 0) or 0),
                influence=float(p.get("influence", 0.0) or 0.0),
                creativity=float(p.get("creativity", 0.0) or 0.0),
                threat=float(p.get("threat", 0.0) or 0.0),
                ict_index=float(p.get("ict_index", 0.0) or 0.0),
                expected_goals=float(p.get("expected_goals", 0.0) or 0.0),
                expected_assists=float(p.get("expected_assists", 0.0) or 0.0),
                expected_goal_involvements=float(p.get("expected_goal_involvements", 0.0) or 0.0),
                expected_goals_conceded=float(p.get("expected_goals_conceded", 0.0) or 0.0),
            )
            players.append(player)

        self.repo.upsert_players(players)
        return len(players)

    def _process_fixtures(self, raw_fixtures: List[Dict[str, Any]]) -> int:
        """Parse and upsert fixtures."""
        fixtures = [
            Fixture(
                id=f["id"],
                event=f.get("event"),
                team_h=f["team_h"],
                team_a=f["team_a"],
                team_h_score=f.get("team_h_score"),
                team_a_score=f.get("team_a_score"),
                team_h_difficulty=f.get("team_h_difficulty", 3),
                team_a_difficulty=f.get("team_a_difficulty", 3),
                finished=bool(f.get("finished", False)),
                finished_provisional=bool(f.get("finished_provisional", False)),
                started=bool(f.get("started", False)),
                kickoff_time=f.get("kickoff_time"),
            )
            for f in raw_fixtures
        ]
        self.repo.upsert_fixtures(fixtures)
        return len(fixtures)

    def _sync_top_players_history(self, limit: int = 50, force_refresh: bool = True) -> int:
        """Refresh a bounded batch, rotating through all players by last attempt time."""
        result = self._sync_player_history_batch(
            limit=limit, incomplete_only=False, force_refresh=force_refresh
        )
        return result["history_records"]

    def sync_player_history_backfill(self, limit: Optional[int] = None) -> Dict[str, int]:
        """Backfill history for players not yet synced successfully; reruns resume safely."""
        init_db(self.repo.db_path)
        return self._sync_player_history_batch(
            limit=limit, incomplete_only=True, force_refresh=True, request_delay=0.15
        )

    def _sync_player_history_batch(
        self,
        limit: Optional[int],
        incomplete_only: bool,
        force_refresh: bool,
        request_delay: float = 0.15,
    ) -> Dict[str, int]:
        """Fetch and persist a bounded set of validated, completed-fixture history."""
        players = self.repo.get_player_history_sync_candidates(
            limit=limit, incomplete_only=incomplete_only
        )
        fixture_states = self.repo.get_history_fixture_states()
        provisional_gameweeks = {
            event
            for event, completed, provisional in fixture_states.values()
            if event is not None and (not completed or provisional)
        }
        result = {
            "players_attempted": 0,
            "players_succeeded": 0,
            "players_failed": 0,
            "history_records": 0,
        }

        for index, player in enumerate(players):
            if index and request_delay > 0:
                time.sleep(request_delay)
            result["players_attempted"] += 1
            try:
                summary = self.client.get_element_summary(player.id, force_refresh=force_refresh)
                history = summary.get("history")
                if not isinstance(history, list):
                    raise ValueError(f"Player {player.id} summary contains invalid history data")
                gw_records = self._aggregate_player_history(
                    player.id, history, fixture_states, provisional_gameweeks
                )
                if gw_records:
                    self.repo.upsert_gameweek_history(gw_records)
                self.repo.record_player_history_sync(player.id, "ok", len(gw_records))
                result["players_succeeded"] += 1
                result["history_records"] += len(gw_records)
            except Exception as e:
                message = f"{type(e).__name__}: {e}"
                result["players_failed"] += 1
                try:
                    self.repo.record_player_history_sync(player.id, "error", 0, message)
                except Exception:
                    logger.exception(
                        "Could not record history sync failure for player %s (ID: %d)",
                        player.web_name, player.id,
                    )
                logger.warning(
                    "Could not sync history for player %s (ID: %d): %s",
                    player.web_name, player.id, message,
                )

        logger.info(
            "Player history batch complete: %d attempted, %d succeeded, %d failed, %d gameweek records",
            result["players_attempted"], result["players_succeeded"],
            result["players_failed"], result["history_records"],
        )
        return result

    @staticmethod
    def _aggregate_player_history(
        player_id: int,
        history: List[Dict[str, Any]],
        fixture_states: Dict[int, Tuple[Optional[int], bool, bool]],
        provisional_gameweeks: Set[int],
    ) -> List[PlayerGameweekHistory]:
        """Aggregate per-fixture FPL rows into one record per completed gameweek."""
        gameweeks: Dict[int, List[Tuple[Dict[str, Any], bool]]] = defaultdict(list)
        for row in history:
            fixture_id = row.get("fixture")
            state = fixture_states.get(fixture_id)
            if state is None or not state[1]:
                continue
            round_number = int(row["round"])
            if round_number <= 0 or "total_points" not in row:
                continue
            gameweeks[round_number].append((row, state[2]))

        integer_fields = (
            "total_points", "minutes", "goals_scored", "assists", "clean_sheets",
            "goals_conceded", "yellow_cards", "red_cards", "saves", "bonus", "bps",
        )
        float_fields = (
            "ict_index", "expected_goals", "expected_assists",
            "expected_goal_involvements", "expected_goals_conceded",
        )
        records = []
        for round_number, fixture_rows in sorted(gameweeks.items()):
            rows = [item[0] for item in fixture_rows]
            latest = max(rows, key=lambda item: item.get("kickoff_time") or "")
            first = min(rows, key=lambda item: item.get("kickoff_time") or "")
            values = {
                field: sum(int(row.get(field, 0) or 0) for row in rows)
                for field in integer_fields
            }
            float_values = {
                field: sum(float(row.get(field, 0.0) or 0.0) for row in rows)
                for field in float_fields
            }
            records.append(PlayerGameweekHistory(
                player_id=player_id,
                round=round_number,
                **values,
                **float_values,
                value=int(latest.get("value", 0) or 0),
                kickoff_time=first.get("kickoff_time"),
                opponent_team_id=latest.get("opponent_team"),
                was_home=bool(latest.get("was_home", True)),
                is_provisional=(
                    round_number in provisional_gameweeks
                    or any(is_provisional for _, is_provisional in fixture_rows)
                ),
            ))
        return records


def main():
    """CLI Entry point for syncing FPL data."""
    parser = argparse.ArgumentParser(description="Sync official FPL data.")
    parser.add_argument(
        "--history-backfill", action="store_true",
        help="Fetch gameweek history for every player not yet successfully synced.",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Maximum players to backfill this run; omit to process all remaining players.",
    )
    args = parser.parse_args()
    pipeline = FPLSyncPipeline()
    if args.history_backfill:
        pipeline.sync_all(sync_player_history_top_n=0)
        result = pipeline.sync_player_history_backfill(limit=args.limit)
        logger.info("History backfill result: %s", result)
        if result["players_failed"]:
            raise SystemExit(1)
        return
    pipeline.sync_all(sync_player_history_top_n=30)


if __name__ == "__main__":
    main()
