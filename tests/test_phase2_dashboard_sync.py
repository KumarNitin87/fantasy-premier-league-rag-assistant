import os
from datetime import datetime
from pathlib import Path

from ui.views.phase2_dashboard import _last_sync_display


def test_sync_display_uses_logged_sync_before_cache_metadata(tmp_path: Path):
    (tmp_path / "bootstrap_static.json").write_text("{}", encoding="utf-8")
    (tmp_path / "fixtures.json").write_text("[]", encoding="utf-8")

    timestamp, note = _last_sync_display(
        {"last_sync": "2026-10-01 12:30:00"}, raw_data_dir=tmp_path
    )

    assert timestamp == "2026-10-01 12:30:00"
    assert note == "Recorded in the ingestion log."


def test_sync_display_identifies_cache_timestamp_without_sync_log(tmp_path: Path):
    cache_files = [tmp_path / "bootstrap_static.json", tmp_path / "fixtures.json"]
    for path in cache_files:
        path.write_text("{}", encoding="utf-8")
    saved_at = 1_791_012_345
    for path in cache_files:
        os.utime(path, (saved_at, saved_at))

    timestamp, note = _last_sync_display({"last_sync": "Never"}, raw_data_dir=tmp_path)

    expected = datetime.fromtimestamp(saved_at).astimezone().strftime("%d %b %Y")
    assert timestamp == expected
    assert "FPL cache files last saved" in note
    assert "no ingestion-log timestamp" in note


def test_sync_display_does_not_invent_timestamp_without_metadata(tmp_path: Path):
    timestamp, note = _last_sync_display({"last_sync": "Never"}, raw_data_dir=tmp_path)

    assert timestamp == "Not recorded"
    assert "No successful sync timestamp" in note
