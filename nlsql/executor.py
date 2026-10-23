"""Run checked SQL against a database in a read-only, time-limited way.

Static checks in safety.py are the first wall. This is the second: the database
itself refuses writes. The third wall is yours: connect with a database user that
only has SELECT grants. Do not skip that one in production.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Engine, create_engine, event, text


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[tuple]


def make_engine(url: str) -> Engine:
    if url.startswith("sqlite:///") and "mode=ro" not in url:
        path = url[len("sqlite:///"):]
        url = f"sqlite:///file:{path}?mode=ro&uri=true"
    engine = create_engine(url, pool_pre_ping=True)

    if engine.dialect.name == "sqlite":
        @event.listens_for(engine, "connect")
        def _ro(dbapi_conn, _):
            dbapi_conn.execute("PRAGMA query_only = ON")
    return engine


def _begin_read_only(conn, dialect: str, timeout_ms: int) -> None:
    if dialect == "postgresql":
        conn.execute(text("SET TRANSACTION READ ONLY"))
        conn.execute(text(f"SET LOCAL statement_timeout = {int(timeout_ms)}"))
    elif dialect in ("mysql", "mariadb"):
        if getattr(conn.dialect, "is_mariadb", False):
            conn.execute(text(f"SET SESSION max_statement_time = {timeout_ms / 1000:.3f}"))
        else:
            conn.execute(text(f"SET SESSION MAX_EXECUTION_TIME = {int(timeout_ms)}"))
        # Applies to the next transaction, which starts with the query itself.
        conn.execute(text("SET TRANSACTION READ ONLY"))
    # Other dialects: rely on static checks plus a SELECT-only database user.


def explain(engine: Engine, sql: str, timeout_ms: int = 5000) -> str | None:
    """Ask the database to plan the query without running it. Returns an error message or None."""
    d = engine.dialect.name
    prefix = {"sqlite": "EXPLAIN QUERY PLAN ", "mssql": None}.get(d, "EXPLAIN ")
    if prefix is None:
        return None
    try:
        with engine.connect() as conn:
            with conn.begin():
                _begin_read_only(conn, d, timeout_ms)
                conn.execute(text(prefix + sql)).fetchall()
                conn.rollback()
    except Exception as e:  # noqa: BLE001 - the message is fed back to the model
        return str(getattr(e, "orig", e)).strip().splitlines()[0]
    return None


def run(engine: Engine, sql: str, timeout_ms: int = 15000) -> QueryResult:
    d = engine.dialect.name
    with engine.connect() as conn:
        with conn.begin():
            _begin_read_only(conn, d, timeout_ms)
            res = conn.execute(text(sql))
            cols = list(res.keys())
            rows = [tuple(r) for r in res.fetchall()]
            conn.rollback()
    return QueryResult(cols, rows)
