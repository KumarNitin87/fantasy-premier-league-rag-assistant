import base64
from pathlib import Path

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


def test_photo_availability_uses_a_bounded_image_get_and_closes_response(monkeypatch):
    calls = []

    class Response:
        status_code = 206
        headers = {"content-type": "image/png"}
        closed = False

        def close(self):
            self.closed = True

    response = Response()

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return response

    monkeypatch.setattr(player_photo.requests, "get", fake_get)

    assert player_photo._official_photo_is_available("https://photo.example/unique-audit-image.png")
    assert calls[0][1]["stream"] is True
    assert calls[0][1]["timeout"] == (3, 4)
    assert calls[0][1]["headers"]["Range"] == "bytes=0-0"
    assert response.closed


def test_photo_resolver_repairs_missing_code_in_memory_without_database_write(monkeypatch):
    player = Player(
        id=411, team_id=1, web_name="Example", first_name="Erling", second_name="Haaland",
        position="FWD", element_type=4, now_cost=150, code=None,
    )
    class Repository:
        def update_player_photo_code(self, player_id, code, photo_url):
            raise AssertionError("Photo resolution must not write to the database")

    monkeypatch.setattr(player_photo, "_live_photo_codes", lambda: {411: 223094})
    monkeypatch.setattr(player_photo, "_official_photo_is_available", lambda url: True)

    resolved = player_photo.resolve_player_photo_url(player, Repository())

    assert resolved.endswith("/p223094.png")
    assert player.code == 223094
    assert player.photo_url.endswith("/p223094.png")


def test_photo_resolver_uses_neutral_fallback_when_cdn_image_is_unavailable(monkeypatch):
    player = Player(
        id=411, team_id=1, web_name="Example", first_name="Erling", second_name="Haaland",
        position="FWD", element_type=4, now_cost=150, code=223094,
    )
    monkeypatch.setattr(player_photo, "_official_photo_is_available", lambda url: False)

    placeholder = player_photo.resolve_player_photo_url(player, object())

    assert placeholder == player_photo.PHOTO_PLACEHOLDER
    assert Path(player_photo._PLACEHOLDER_PATH).is_file()
    assert base64.b64decode(placeholder.split(",", 1)[1]).startswith(b"\x89PNG\r\n\x1a\n")


def test_photo_resolver_uses_local_fallback_for_missing_or_invalid_codes(monkeypatch):
    player = Player(
        id=411, team_id=1, web_name="Example", first_name="Erling", second_name="Haaland",
        position="FWD", element_type=4, now_cost=150, code="invalid",
    )
    monkeypatch.setattr(player_photo, "_live_photo_codes", lambda: {})
    monkeypatch.setattr(player_photo, "_official_photo_is_available", lambda _url: pytest.fail("invalid code must not be checked"))

    assert player_photo.resolve_player_photo_url(player, object()) == player_photo.PHOTO_PLACEHOLDER
