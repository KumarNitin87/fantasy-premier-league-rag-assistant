from ui.views import my_team


class _Tab:
    def __init__(self, is_open):
        self.open = is_open

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def test_my_team_renders_only_the_selected_dynamic_tab(monkeypatch):
    rendered = []
    tab_specs = []

    class FakeStreamlit:
        def header(self, *_args, **_kwargs):
            pass

        def caption(self, *_args, **_kwargs):
            pass

        def tabs(self, labels, **kwargs):
            tab_specs.append((labels, kwargs))
            return [_Tab(index == 1) for index in range(len(labels))]

    class FakeRepository:
        def get_my_team(self):
            return []

        def get_my_team_settings(self):
            return {"bank_millions": 0, "free_transfers": 1}

        def get_players(self, **_kwargs):
            return []

    monkeypatch.setattr(my_team, "st", FakeStreamlit())
    monkeypatch.setattr(my_team, "_render_gameweek_analysis", lambda *_args: rendered.append("analysis"))
    monkeypatch.setattr(my_team, "_render_squad_editor", lambda *_args: rendered.append("overview"))
    monkeypatch.setattr(my_team, "_render_squad_summary", lambda *_args: rendered.append("summary"))
    monkeypatch.setattr(my_team, "_render_starting_xi", lambda *_args: rendered.append("xi"))
    monkeypatch.setattr(my_team, "_render_transfer_planner", lambda *_args: rendered.append("transfers"))
    monkeypatch.setattr(my_team, "_render_what_if", lambda *_args: rendered.append("what_if"))
    monkeypatch.setattr(my_team, "_render_captain", lambda *_args: rendered.append("captain"))
    monkeypatch.setattr(my_team, "_render_alternatives", lambda *_args: rendered.append("alternatives"))
    monkeypatch.setattr(my_team, "_render_risk_radar", lambda *_args: rendered.append("risk"))
    monkeypatch.setattr(my_team, "_render_player_search", lambda *_args: rendered.append("search"))
    monkeypatch.setattr(my_team, "_render_fixture_timeline", lambda *_args: rendered.append("timeline"))

    my_team.render_my_team(FakeRepository(), None, None, None, None, None)

    assert rendered == ["analysis"]
    assert tab_specs[0][1]["on_change"] == "rerun"
    assert tab_specs[0][1]["key"] == "my_team_tabs"


def test_gameweek_analysis_exposes_retry_after_database_failure(monkeypatch):
    labels = []
    errors = []

    class FakeProgress:
        def progress(self, *_args, **_kwargs):
            pass

        def empty(self):
            pass

    class FakeStreamlit:
        session_state = {}

        def subheader(self, *_args, **_kwargs):
            pass

        def button(self, label, **_kwargs):
            labels.append(label)
            return len(labels) == 1

        def progress(self, *_args, **_kwargs):
            return FakeProgress()

        def error(self, message):
            errors.append(message)

        def info(self, *_args, **_kwargs):
            pass

    monkeypatch.setattr(my_team, "st", FakeStreamlit())
    monkeypatch.setattr(my_team, "validate_squad", lambda _players: (True, []))
    monkeypatch.setattr(my_team, "build_squad_report", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("database unavailable")))

    for _ in range(2):
        my_team._render_gameweek_analysis(object(), [object()] * 15, {"bank_millions": 0}, None, None, None, {})

    assert labels == ["⭐ Analyze My Gameweek", "↻ Retry Gameweek Analysis"]
    assert errors and "database was unavailable" in errors[0]
