from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


def _ensure_sqlite_parent(url: str) -> None:
    prefix = "sqlite:///"
    if url.startswith(prefix):
        path = url[len(prefix):]
        if path and path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)


def make_engine(database_url: str | None = None):
    url = database_url or get_settings().database_url
    _ensure_sqlite_parent(url)
    kwargs = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    return create_engine(url, **kwargs)


engine = make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def init_db(bind_engine=None) -> None:
    from app import models  # noqa: F401
    Base.metadata.create_all(bind=bind_engine or engine)
