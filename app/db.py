import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .config import get_settings
from .schemas import Summary


@contextmanager
def _conn():
    path = Path(get_settings().db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=30)
    try:
        yield con
        con.commit()
    finally:
        con.close()


def init_db() -> None:
    with _conn() as con:
        con.execute(
            "CREATE TABLE IF NOT EXISTS summaries ("
            "id TEXT PRIMARY KEY, created_at TEXT NOT NULL, data TEXT NOT NULL)"
        )


def save(summary: Summary) -> None:
    data = summary.model_dump_json(by_alias=True)
    with _conn() as con:
        con.execute(
            "INSERT INTO summaries (id, created_at, data) VALUES (?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET data = excluded.data",
            (summary.id, summary.created_at, data),
        )


def get(summary_id: str) -> Summary | None:
    with _conn() as con:
        row = con.execute("SELECT data FROM summaries WHERE id = ?", (summary_id,)).fetchone()
    return Summary.model_validate_json(row[0]) if row else None


def list_all() -> list[Summary]:
    with _conn() as con:
        rows = con.execute("SELECT data FROM summaries ORDER BY created_at DESC").fetchall()
    return [Summary.model_validate_json(r[0]) for r in rows]


def delete(summary_id: str) -> bool:
    with _conn() as con:
        cur = con.execute("DELETE FROM summaries WHERE id = ?", (summary_id,))
    return cur.rowcount > 0


def fail_interrupted() -> None:
    """Marca como fallidos los trabajos que quedaron a medias al reiniciar."""
    for s in list_all():
        if s.status not in ("ready", "failed"):
            s.status = "failed"
            s.error = "El servidor se reinició durante el procesamiento."
            save(s)
