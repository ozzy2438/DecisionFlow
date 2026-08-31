"""Database connection helpers."""

from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from psycopg import Connection

from decisionflow.config import get_settings


@contextmanager
def connect(database_url: str | None = None) -> Iterator[Connection[tuple[object, ...]]]:
    """Open a typed psycopg connection with explicit transaction control."""

    dsn = database_url or get_settings().psycopg_dsn
    dsn = dsn.replace("postgresql+psycopg://", "postgresql://", 1)
    with psycopg.connect(dsn, autocommit=False) as connection:
        yield connection
