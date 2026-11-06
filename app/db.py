import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from .config import get_settings
from .schemas import Summary

# Todo se guarda en Postgres (DB_URL). Las consultas se escriben con "?" y se adaptan en _run.

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
    if not get_settings().db_url:
        raise RuntimeError("Falta DB_URL en el .env: el backend guarda todo en Postgres (Neon).")
    with _pg_pool().connection() as con:
        cur = con.execute(sql.replace("?", "%s"), params)
        if fetch == "all":
            return cur.fetchall()
        if fetch == "one":
            return cur.fetchone()
        return cur.rowcount


@dataclass
class User:
    id: str
    username: str
    password_hash: str
    role: str  # "admin" | "user"
    active: bool
    created_at: str


def _user(r) -> User:
    return User(r[0], r[1], r[2], r[3], bool(r[4]), r[5])


_USER_COLS = "id, username, password_hash, role, active, created_at"


def init_db() -> None:
    _run(
        "CREATE TABLE IF NOT EXISTS summaries ("
        "id TEXT PRIMARY KEY, created_at TEXT NOT NULL, data TEXT NOT NULL)"
    )
    _run("ALTER TABLE summaries ADD COLUMN IF NOT EXISTS user_id TEXT")
    _run("CREATE INDEX IF NOT EXISTS summaries_user_idx ON summaries (user_id, created_at DESC)")
    _run(
        "CREATE TABLE IF NOT EXISTS users ("
        "id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, "
        "role TEXT NOT NULL, active BOOLEAN NOT NULL DEFAULT TRUE, created_at TEXT NOT NULL)"
    )


# --- Resúmenes ---------------------------------------------------------------
# `user_id` se fija al crear; las actualizaciones posteriores solo cambian `data`.


def save(summary: Summary, user_id: str | None = None) -> None:
    _run(
        "INSERT INTO summaries (id, created_at, data, user_id) VALUES (?, ?, ?, ?) "
        "ON CONFLICT (id) DO UPDATE SET data = excluded.data",
        (summary.id, summary.created_at, summary.model_dump_json(by_alias=True), user_id),
    )


def get(summary_id: str) -> Summary | None:
    """Uso interno (pipeline). Las rutas usan siempre `get_for_user`."""
    row = _run("SELECT data FROM summaries WHERE id = ?", (summary_id,), fetch="one")
    return Summary.model_validate_json(row[0]) if row else None


def all_summaries() -> list[Summary]:
    rows = _run("SELECT data FROM summaries ORDER BY created_at DESC", fetch="all")
    return [Summary.model_validate_json(r[0]) for r in rows]


def get_for_user(summary_id: str, user_id: str) -> Summary | None:
    row = _run("SELECT data FROM summaries WHERE id = ? AND user_id = ?", (summary_id, user_id), fetch="one")
    return Summary.model_validate_json(row[0]) if row else None


def list_for_user(user_id: str) -> list[Summary]:
    rows = _run(
        "SELECT data FROM summaries WHERE user_id = ? ORDER BY created_at DESC", (user_id,), fetch="all"
    )
    return [Summary.model_validate_json(r[0]) for r in rows]


def delete_for_user(summary_id: str, user_id: str) -> bool:
    return _run("DELETE FROM summaries WHERE id = ? AND user_id = ?", (summary_id, user_id)) > 0


def mark_interrupted() -> int:
    """Al arrancar, marca como interrumpidos los trabajos que quedaron a medias (la cola vive en memoria)."""
    n = 0
    for s in all_summaries():
        if s.status in ("queued", "transcribing", "summarizing"):
            s.status = "interrupted"
            s.error = "El servidor se reinició antes de terminar este resumen. Puedes reanudarlo o descartarlo."
            save(s)
            n += 1
    return n


def claim_orphans(user_id: str) -> int:
    """Asigna a `user_id` los resúmenes anteriores al login (sin dueño)."""
    return _run("UPDATE summaries SET user_id = ? WHERE user_id IS NULL", (user_id,))


# --- Usuarios ----------------------------------------------------------------


def create_user(username: str, password_hash: str, role: str) -> User:
    u = User(uuid.uuid4().hex[:12], username, password_hash, role, True, datetime.now(timezone.utc).isoformat())
    _run(
        "INSERT INTO users (id, username, password_hash, role, active, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (u.id, u.username, u.password_hash, u.role, u.active, u.created_at),
    )
    return u


def get_user(user_id: str) -> User | None:
    r = _run(f"SELECT {_USER_COLS} FROM users WHERE id = ?", (user_id,), fetch="one")
    return _user(r) if r else None


def get_user_by_name(username: str) -> User | None:
    r = _run(f"SELECT {_USER_COLS} FROM users WHERE lower(username) = lower(?)", (username,), fetch="one")
    return _user(r) if r else None


def list_users() -> list[User]:
    rows = _run(f"SELECT {_USER_COLS} FROM users ORDER BY created_at", fetch="all")
    return [_user(r) for r in rows]


def update_user(user_id: str, password_hash: str | None = None, active: bool | None = None) -> bool:
    n = 0
    if password_hash is not None:
        n += _run("UPDATE users SET password_hash = ? WHERE id = ?", (password_hash, user_id))
    if active is not None:
        n += _run("UPDATE users SET active = ? WHERE id = ?", (active, user_id))
    return n > 0


def delete_user(user_id: str) -> bool:
    """Borra al usuario y todos sus resúmenes."""
    _run("DELETE FROM summaries WHERE user_id = ?", (user_id,))
    return _run("DELETE FROM users WHERE id = ?", (user_id,)) > 0
