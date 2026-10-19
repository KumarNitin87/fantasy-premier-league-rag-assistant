import pytest

from src.api.fpl_client import FPLClient
from src.database.models import Player
from ui.components import player_photo


@pytest.mark.parametrize("value", [None, "", "0", 0, -1, "abc", True, 411])
def test_photo_url_rejects_missing_or_obviously_invalid_codes(value):
    assert FPLClient.build_photo_url(value) is None


def test_photo_url_uses_the_official_fpl_code():
    assert FPLClient.build_photo_url(243298) == (
        "https://resources.premierleague.com/premierleague/photos/players/250x250/p243298.png"
    )


def test_photo_resolver_repairs_missing_code_and_updates_existing_record(monkeypatch):
    player = Player(
        id=411, team_id=1, web_name="Example", first_name="Erling", second_name="Haaland",
        position="FWD", element_type=4, now_cost=150, code=None,
    )
    updated = []

    class Repository:
        def update_player_photo_code(self, player_id, code, photo_url):
            updated.append((player_id, code, photo_url))

    monkeypatch.setattr(player_photo, "_live_photo_codes", lambda: {411: 223094})
    monkeypatch.setattr(player_photo, "_official_photo_is_available", lambda url: True)

    resolved = player_photo.resolve_player_photo_url(player, Repository())

    assert resolved.endswith("/p223094.png")
    assert player.code == 223094
    assert updated[0][0:2] == (411, 223094)


def test_photo_resolver_uses_neutral_fallback_when_cdn_image_is_unavailable(monkeypatch):
    player = Player(
        id=411, team_id=1, web_name="Example", first_name="Erling", second_name="Haaland",
        position="FWD", element_type=4, now_cost=150, code=223094,
    )
    monkeypatch.setattr(player_photo, "_official_photo_is_available", lambda url: False)

    assert player_photo.resolve_player_photo_url(player, object()) == player_photo.PHOTO_PLACEHOLDER
