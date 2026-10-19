"""
Typed Data Models representing Premier League and Fantasy Football domain entities.
Updated for Phase 2: added `code` (photo key) and `photo_url` fields to Player.
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Team:
    """Represents a Premier League club."""
    id: int
    name: str
    short_name: str
    # Ratings are optional: using an invented neutral default makes a missing
    # FPL team-strength feed look like measured data to the scoring engine.
    strength_overall_home: Optional[int] = None
    strength_overall_away: Optional[int] = None
    strength_attack_home: Optional[int] = None
    strength_attack_away: Optional[int] = None
    strength_defence_home: Optional[int] = None
    strength_defence_away: Optional[int] = None


@dataclass
class Player:
    """Represents an FPL Player and current aggregated statistics."""
    id: int
    team_id: int
    web_name: str
    first_name: str
    second_name: str
    position: str          # "GKP", "DEF", "MID", "FWD"
    element_type: int      # 1: GKP, 2: DEF, 3: MID, 4: FWD
    now_cost: int          # stored in tenths, e.g. 100 = £10.0m

    # Phase 2 addition: player photo key from FPL API
    code: Optional[int] = None          # FPL internal photo code (used to build CDN URL)
    photo_url: Optional[str] = None     # Built as: resources.premierleague.com/.../p{code}.png

    selected_by_percent: float = 0.0
    status: str = "a"
    chance_of_playing_next_round: Optional[int] = 100
    chance_of_playing_this_round: Optional[int] = 100
    news: str = ""
    news_added: Optional[str] = None
    form: float = 0.0
    points_per_game: float = 0.0
    event_points: int = 0
    total_points: int = 0
    minutes: int = 0
    goals_scored: int = 0
    assists: int = 0
    clean_sheets: int = 0
    goals_conceded: int = 0
    own_goals: int = 0
    penalties_saved: int = 0
    penalties_missed: int = 0
    yellow_cards: int = 0
    red_cards: int = 0
    saves: int = 0
    bonus: int = 0
    bps: int = 0
    influence: float = 0.0
    creativity: float = 0.0
    threat: float = 0.0
    ict_index: float = 0.0
    expected_goals: float = 0.0
    expected_assists: float = 0.0
    expected_goal_involvements: float = 0.0
    expected_goals_conceded: float = 0.0
    team_name: Optional[str] = None
    team_short_name: Optional[str] = None

    @property
    def cost_float(self) -> float:
        """Returns cost in millions, e.g. 10.0."""
        return self.now_cost / 10.0

    @property
    def full_name(self) -> str:
        """Returns full readable name."""
        return f"{self.first_name} {self.second_name}"

    @property
    def points_per_million(self) -> float:
        """Returns points per million cost."""
        return round(self.total_points / max(self.cost_float, 4.0), 2)


@dataclass
class PlayerGameweekHistory:
    """Historical gameweek match performance for a player."""
    id: Optional[int] = None
    player_id: int = 0
    round: int = 0
    total_points: int = 0
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
    expected_goals_conceded: float = 0.0
    kickoff_time: Optional[str] = None
    opponent_team_id: Optional[int] = None
    was_home: bool = True
    is_provisional: bool = False


@dataclass
class PlayerPastSeason:
    """Archival season records for career historical analysis."""
    id: Optional[int] = None
    player_id: int = 0
    season_name: str = ""
    total_points: int = 0
    total_minutes: int = 0
    goals_scored: int = 0
    assists: int = 0
    clean_sheets: int = 0
    bps: int = 0


@dataclass
class SquadMember:
    """A saved user's FPL squad membership and personal purchase/captain settings."""
    player_id: int
    purchase_price: float
    bench_order: int = 0
    is_captain: bool = False
    is_vice_captain: bool = False


@dataclass
class Fixture:
    """Upcoming or completed match fixture."""
    id: int
    event: Optional[int]   # Gameweek number (1-38)
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
    team_h_name: Optional[str] = None
    team_a_name: Optional[str] = None
    team_h_short_name: Optional[str] = None
    team_a_short_name: Optional[str] = None
