"""Shared resolver for official FPL player photos and safe fallbacks."""

from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Iterable, List

import requests
import streamlit as st

from src.api.fpl_client import FPLClient
from src.database.models import Player
from src.database.repository import FPLRepository

PHOTO_PLACEHOLDER = (
    "https://resources.premierleague.com/premierleague/photos/players/250x250/Photo-Missing.png"
)


@st.cache_data(ttl=300, show_spinner=False)
def _live_photo_codes() -> Dict[int, int]:
    """Fetch a small ID-to-code map only when a stored code needs repair."""
    try:
        payload = FPLClient().get_bootstrap_static()
    except Exception:
        return {}

    codes = {}
    for item in payload.get("elements", []):
        code = FPLClient.normalize_photo_code(item.get("code"))
        if item.get("id") is not None and code is not None:
            codes[int(item["id"])] = code
    return codes


@st.cache_data(ttl=21600, show_spinner=False)
def _official_photo_is_available(url: str) -> bool:
    """Validate CDN responses without downloading image bodies; cache for six hours."""
    try:
        response = requests.head(
            url,
            timeout=4,
            allow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 FPL-Assistant/1.0"},
        )
        return response.status_code == 200 and response.headers.get("content-type", "").lower().startswith("image/")
    except requests.RequestException:
        return False


def resolve_player_photo_urls(players: Iterable[Player], repo: FPLRepository) -> Dict[int, str]:
    """Repair invalid stored codes from live FPL data and return validated photo URLs."""
    player_list = list(players)
    invalid = [p for p in player_list if FPLClient.normalize_photo_code(p.code) is None]
    if invalid:
        latest_codes = _live_photo_codes()
        for player in invalid:
            code = latest_codes.get(player.id)
            if code is None:
                continue
            url = FPLClient.build_photo_url(code)
            repo.update_player_photo_code(player.id, code, url)
            player.code = code
            player.photo_url = url

    candidates = {
        player.id: FPLClient.build_photo_url(player.code)
        for player in player_list
    }
    candidates = {player_id: url for player_id, url in candidates.items() if url}

    # A small worker pool keeps first-render validation responsive for table results.
    with ThreadPoolExecutor(max_workers=12) as executor:
        availability = list(executor.map(_official_photo_is_available, candidates.values()))

    resolved = {
        player_id: url if available else PHOTO_PLACEHOLDER
        for (player_id, url), available in zip(candidates.items(), availability)
    }
    for player in player_list:
        resolved.setdefault(player.id, PHOTO_PLACEHOLDER)
    return resolved


def resolve_player_photo_url(player: Player, repo: FPLRepository) -> str:
    """Single-player convenience wrapper used by cards and search spotlights."""
    return resolve_player_photo_urls([player], repo).get(player.id, PHOTO_PLACEHOLDER)
