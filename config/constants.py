"""
Constants and domain mappings for Fantasy Premier League & Premier League analytics.
"""

from typing import Dict, Final

# Element Type / Position Mappings in FPL API
POSITION_MAP: Final[Dict[int, str]] = {
    1: "GKP",
    2: "DEF",
    3: "MID",
    4: "FWD",
}

POSITION_NAME_MAP: Final[Dict[str, str]] = {
    "GKP": "Goalkeeper",
    "DEF": "Defender",
    "MID": "Midfielder",
    "FWD": "Forward",
}

# Player Availability Status Codes
STATUS_MAP: Final[Dict[str, str]] = {
    "a": "Available",
    "d": "Doubtful",
    "i": "Injured",
    "s": "Suspended",
    "u": "Unavailable",
}

STATUS_COLOR_MAP: Final[Dict[str, str]] = {
    "a": "#28a745",  # Green
    "d": "#ffc107",  # Yellow
    "i": "#dc3545",  # Red
    "s": "#fd7e14",  # Orange
    "u": "#6c757d",  # Grey
}

# Fixture Difficulty Rating (FDR) Mappings (FPL official 1-5 scale)
FDR_COLORS: Final[Dict[int, str]] = {
    1: "#00FF87",  # Very Easy (FPL Mint Green)
    2: "#01FC7A",  # Easy (Green)
    3: "#EFEFEF",  # Neutral / Medium (Light Grey / Silver)
    4: "#FF1751",  # Hard (Pinkish Red)
    5: "#80072D",  # Very Hard (Deep Maroon)
}

FDR_LABELS: Final[Dict[int, str]] = {
    1: "Very Easy",
    2: "Easy",
    3: "Moderate",
    4: "Difficult",
    5: "Very Hard",
}

# Decision Confidence Scoring Weights (Sum to 1.0)
CONFIDENCE_WEIGHTS: Final[Dict[str, float]] = {
    "form": 0.25,
    "fixture": 0.25,
    "underlying": 0.20,
    "value": 0.15,
    "team_delta": 0.15,
}

# Penalty Multipliers for Decision Confidence
AVAILABILITY_PENALTIES: Final[Dict[int, float]] = {
    100: 1.00,  # 100% chance of playing
    75: 0.75,   # 75% chance
    50: 0.45,   # 50% chance
    25: 0.15,   # 25% chance
    0: 0.00,    # 0% chance or confirmed out
}

ROTATION_PENALTY: Final[float] = 0.80  # Applied if avg minutes < 60 over last 3 GWs
NEWS_SENTIMENT_PENALTY: Final[float] = 0.85  # Applied if recent adverse injury/tactical news retrieved

# RSS Feeds for Premier League and Injury Updates
DEFAULT_RSS_FEEDS: Final[Dict[str, str]] = {
    "BBC Sport Football": "https://feeds.bbci.co.uk/sport/football/rss.xml",
    "Sky Sports Premier League": "https://www.skysports.com/rss/12040",
    "The Guardian Football": "https://www.theguardian.com/football/rss",
    "TalkSport Premier League": "https://talksport.com/football/premier-league/feed/",
}
