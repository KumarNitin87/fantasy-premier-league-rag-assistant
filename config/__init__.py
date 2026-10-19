"""Configuration package."""

from config.settings import settings
from config.constants import (
    POSITION_MAP,
    POSITION_NAME_MAP,
    STATUS_MAP,
    STATUS_COLOR_MAP,
    FDR_COLORS,
    FDR_LABELS,
    CONFIDENCE_WEIGHTS,
    AVAILABILITY_PENALTIES,
    ROTATION_PENALTY,
    NEWS_SENTIMENT_PENALTY,
    DEFAULT_RSS_FEEDS,
)

__all__ = [
    "settings",
    "POSITION_MAP",
    "POSITION_NAME_MAP",
    "STATUS_MAP",
    "STATUS_COLOR_MAP",
    "FDR_COLORS",
    "FDR_LABELS",
    "CONFIDENCE_WEIGHTS",
    "AVAILABILITY_PENALTIES",
    "ROTATION_PENALTY",
    "NEWS_SENTIMENT_PENALTY",
    "DEFAULT_RSS_FEEDS",
]
