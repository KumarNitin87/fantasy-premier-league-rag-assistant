import multiprocessing

from src.sync.process_lock import acquire_fpl_sync_lock


def _hold_fpl_lock(database_path, acquired_event, release_event):
    with acquire_fpl_sync_lock(database_path) as acquired:
        if acquired:
            acquired_event.set()
            release_event.wait(timeout=10)


def test_fpl_sync_lock_rejects_a_second_writer(tmp_path):
    database = tmp_path / "fpl.sqlite"

    with acquire_fpl_sync_lock(database) as first_acquired:
        assert first_acquired
        with acquire_fpl_sync_lock(database) as second_acquired:
            assert not second_acquired

    with acquire_fpl_sync_lock(database) as acquired_after_release:
        assert acquired_after_release


def test_fpl_sync_lock_is_shared_across_processes(tmp_path):
    database = tmp_path / "fpl.sqlite"
    context = multiprocessing.get_context("spawn")
    acquired = context.Event()
    release = context.Event()
    process = context.Process(
        target=_hold_fpl_lock,
        args=(database, acquired, release),
    )
    process.start()
    try:
        assert acquired.wait(timeout=10)
        with acquire_fpl_sync_lock(database) as second_acquired:
            assert not second_acquired
    finally:
        release.set()
        process.join(timeout=10)

    assert process.exitcode == 0
