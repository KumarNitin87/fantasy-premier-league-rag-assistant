"""Cross-process lock for FPL writes to the shared SQLite database."""

import os
from contextlib import contextmanager
from pathlib import Path
from typing import ContextManager, Iterator
from config.settings import settings


@contextmanager
def acquire_sync_lock(database_path: Path, sync_name: str) -> Iterator[bool]:
    """Try a shared Postgres lock for hosted runs or an OS lock locally."""
    if not sync_name.isidentifier():
        raise ValueError("sync_name must be a simple identifier")
    if settings.DATABASE_URL and Path(database_path) == settings.SQLITE_DB_PATH:
        from src.database.connection import get_connection

        # A session-level lock is shared by the Community Cloud app and external
        # GitHub Actions runner, unlike a lock file on either ephemeral machine.
        lock_key = 4815162000 if sync_name == "fpl" else 4815162001
        connection = get_connection(database_path)
        acquired = False
        try:
            acquired = bool(connection.execute("SELECT pg_try_advisory_lock(?);", (lock_key,)).fetchone()[0])
            connection.commit()
            yield acquired
        finally:
            if acquired:
                try:
                    connection.execute("SELECT pg_advisory_unlock(?);", (lock_key,))
                    connection.commit()
                finally:
                    connection.close()
            else:
                connection.close()
        return

    lock_path = Path(f"{database_path}.{sync_name}-sync.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as lock_file:
        lock_file.seek(0, os.SEEK_END)
        if lock_file.tell() == 0:
            lock_file.write(b"\0")
            lock_file.flush()
        lock_file.seek(0)

        if os.name == "nt":
            import msvcrt

            try:
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                yield False
                return
            try:
                yield True
            finally:
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            return

        import fcntl

        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def acquire_fpl_sync_lock(database_path: Path) -> ContextManager[bool]:
    """Lock FPL synchronization across the web app and scheduler worker."""
    return acquire_sync_lock(database_path, "fpl")


def acquire_news_sync_lock(database_path: Path) -> ContextManager[bool]:
    """Lock news/vector ingestion across manual and scheduled callers."""
    return acquire_sync_lock(database_path, "news")
