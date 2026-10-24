import sqlite3
import threading
from pathlib import Path

from .config import get_settings
from .schemas import Summary

# Postgres si hay DB_URL; si no, SQLite local (desarrollo y pruebas).
# Las consultas se escriben con "?" y se adaptan al driver en _run.

_pool = None
_pool_url = ""
_lock = threading.Lock()


def _pg_pool():
    global _pool, _pool_url
    url = get_settings().db_url
    with _lock:
        if _pool is None or _pool_url != url:
            from psycopg_pool import ConnectionPool

            _pool = ConnectionPool(url, min_size=1, max_size=4, open=True, check=ConnectionPool.check_connection)
            _pool_url = url
    return _pool


def _run(sql: str, params: tuple = (), fetch: str | None = None):
    """Ejecuta `sql` y devuelve filas ("all"/"one") o el número de filas afectadas."""
    if get_settings().db_url:
        with _pg_pool().connection() as con:
            cur = con.execute(sql.replace("?", "%s"), params)
            if fetch == "all":
                return cur.fetchall()
            if fetch == "one":
                return cur.fetchone()
            return cur.rowcount
    path = Path(get_settings().db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=30)
    try:
        cur = con.execute(sql, params)
        result = cur.fetchall() if fetch == "all" else cur.fetchone() if fetch == "one" else cur.rowcount
        con.commit()
        return result
    finally:
        con.close()


def init_db() -> None:
    _run(
        "CREATE TABLE IF NOT EXISTS summaries ("
        "id TEXT PRIMARY KEY, created_at TEXT NOT NULL, data TEXT NOT NULL)"
    )


def save(summary: Summary) -> None:
    _run(
        "INSERT INTO summaries (id, created_at, data) VALUES (?, ?, ?) "
        "ON CONFLICT (id) DO UPDATE SET data = excluded.data",
        (summary.id, summary.created_at, summary.model_dump_json(by_alias=True)),
    )


def get(summary_id: str) -> Summary | None:
    row = _run("SELECT data FROM summaries WHERE id = ?", (summary_id,), fetch="one")
    return Summary.model_validate_json(row[0]) if row else None


def list_all() -> list[Summary]:
    rows = _run("SELECT data FROM summaries ORDER BY created_at DESC", fetch="all")
    return [Summary.model_validate_json(r[0]) for r in rows]


def delete(summary_id: str) -> bool:
    return _run("DELETE FROM summaries WHERE id = ?", (summary_id,)) > 0


def fail_interrupted() -> None:
    """Marca como fallidos los trabajos que quedaron a medias al reiniciar."""
    for s in list_all():
        if s.status not in ("ready", "failed"):
            s.status = "failed"
            s.error = "El servidor se reinició durante el procesamiento."
            save(s)
