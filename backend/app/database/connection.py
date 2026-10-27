"""Database engine and session management.

SQLite is used locally. Because the FastAPI process *and* the two MCP server
processes open the same database file, we enable WAL mode and a busy timeout
so concurrent readers/writers wait instead of failing immediately.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


def _make_engine(url: str) -> Engine:
    connect_args: dict = {}
    if url.startswith("sqlite"):
        connect_args = {"check_same_thread": False, "timeout": 15}
    engine = create_engine(url, connect_args=connect_args, future=True)

    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _record):  # pragma: no cover - trivial
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            if ":memory:" not in url:
                cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=15000")
            cur.close()

    return engine


_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    global _engine, _SessionLocal
    if _engine is None:
        _engine = _make_engine(get_settings().database_url)
        _SessionLocal = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)
    return _engine


def configure_engine(url: str) -> Engine:
    """Point the app at a different database (used by tests)."""
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = _make_engine(url)
    _SessionLocal = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)
    return _engine


def SessionLocal() -> Session:  # noqa: N802 - factory-style name
    get_engine()
    assert _SessionLocal is not None
    return _SessionLocal()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope: commit on success, roll back on any error."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Iterator[Session]:
    """FastAPI dependency."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def init_db() -> None:
    from app.database import models  # noqa: F401 - register models

    Base.metadata.create_all(get_engine())
