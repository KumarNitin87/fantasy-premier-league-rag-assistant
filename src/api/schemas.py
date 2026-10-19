"""
Pydantic schemas for validating responses from the Official Fantasy Premier League REST APIs.
"""

from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field, field_validator


class FPLTeamRaw(BaseModel):
    """Validation schema for a Premier League team from bootstrap-static."""
    id: int
    name: str
    short_name: str
    strength_overall_home: Optional[int] = None
    strength_overall_away: Optional[int] = None
    strength_attack_home: Optional[int] = None
    strength_attack_away: Optional[int] = None
    strength_defence_home: Optional[int] = None
    strength_defence_away: Optional[int] = None


class FPLPlayerRaw(BaseModel):
    """Validation schema for a player from bootstrap-static."""
    id: int
    team: int
    web_name: str
    first_name: str
    second_name: str
    element_type: int
    now_cost: int
    total_points: int = 0
    form: float = 0.0
    minutes: int = 0
    goals_scored: int = 0
    assists: int = 0
    clean_sheets: int = 0
    selected_by_percent: float = 0.0
    status: str = "a"
    code: Optional[int] = None
    photo: Optional[str] = None
    chance_of_playing_next_round: Optional[int] = 100
    chance_of_playing_this_round: Optional[int] = 100
    news: Optional[str] = ""
    news_added: Optional[str] = None
    points_per_game: float = 0.0
    event_points: int = 0
    ict_index: float = 0.0
    expected_goals: float = 0.0
    expected_assists: float = 0.0
    expected_goal_involvements: float = 0.0

    @field_validator("form", "selected_by_percent", "points_per_game", "ict_index", "expected_goals", "expected_assists", "expected_goal_involvements", mode="before")
    @classmethod
    def parse_float(cls, v: Any) -> float:
        if v is None or v == "":
            return 0.0
        try:
            return float(v)
        except (ValueError, TypeError):
            return 0.0


class FPLFixtureRaw(BaseModel):
    """Validation schema for a match fixture."""
    id: int
    event: Optional[int] = None
    team_h: int
    team_a: int
    team_h_score: Optional[int] = None
    team_a_score: Optional[int] = None
    team_h_difficulty: int = 3
    team_a_difficulty: int = 3
    finished: bool = False
    finished_provisional: bool = False
    started: bool = False
    kickoff_time: Optional[str] = None


class FPLHistoryRaw(BaseModel):
    """Validation schema for a player's per-fixture gameweek history."""
    fixture: int
    round: int
    total_points: int
    minutes: int = 0
    goals_scored: int = 0
    assists: int = 0
    clean_sheets: int = 0
    goals_conceded: int = 0
    yellow_cards: int = 0
    red_cards: int = 0
    saves: int = 0
    bonus: int = 0
    bps: int = 0
    ict_index: float = 0.0
    value: int = 0
    expected_goals: float = 0.0
    expected_assists: float = 0.0
    expected_goal_involvements: float = 0.0
    kickoff_time: Optional[str] = None
    opponent_team: Optional[int] = None
    was_home: bool = True

    @field_validator("ict_index", "expected_goals", "expected_assists", "expected_goal_involvements", mode="before")
    @classmethod
    def parse_float(cls, v: Any) -> float:
        if v is None or v == "":
            return 0.0
        try:
            return float(v)
        except (ValueError, TypeError):
            return 0.0


class FPLBootstrapStaticResponse(BaseModel):
    """Root response validation for /api/bootstrap-static/."""
    teams: List[FPLTeamRaw]
    elements: List[FPLPlayerRaw]
