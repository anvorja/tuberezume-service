import pytest
from fastapi.testclient import TestClient

from app.schemas import TranscriptLine
from app.services.summarize import build_chunks, fmt_ts
from app.services.youtube import parse_video_id


@pytest.mark.parametrize(
    "raw",
    [
        "https://www.youtube.com/watch?v=_B4jGz0JEGE",
        "youtube.com/watch?v=_B4jGz0JEGE&t=10",
        "https://youtu.be/_B4jGz0JEGE?si=x",
        "https://www.youtube.com/shorts/_B4jGz0JEGE",
        "_B4jGz0JEGE",
    ],
)
def test_parse_ok(raw):
    assert parse_video_id(raw) == "_B4jGz0JEGE"


@pytest.mark.parametrize("raw", ["https://example.com/watch?v=_B4jGz0JEGE", "hola", ""])
def test_parse_bad(raw):
    assert parse_video_id(raw) is None


def test_fmt_ts():
    assert fmt_ts(75) == "01:15"
    assert fmt_ts(3725) == "1:02:05"


def lines(n, step=10):
    return [TranscriptLine(start=i * step, text=f"frase {i}") for i in range(n)]


def test_chunks_by_window():
    chunks = build_chunks(lines(100), [], 360)
    assert [c.start for c in chunks] == [0, 360, 720]


def test_chunks_by_chapters():
    chunks = build_chunks(lines(30), [(0, "Intro"), (100, "Cuerpo")], 360)
    assert [(c.start, c.hint) for c in chunks] == [(0, "Intro"), (100, "Cuerpo")]


def test_long_chunk_is_split(monkeypatch):
    monkeypatch.setattr("app.services.summarize.MAX_CHUNK_CHARS", 200)
    chunks = build_chunks(lines(100), [], 100000)
    assert len(chunks) > 1
    assert len({c.start for c in chunks}) == len(chunks)


def fake_db(monkeypatch):
    """Sustituye Postgres por diccionarios en memoria."""
    import uuid

    from app import db

    sums: dict = {}
    owners: dict = {}
    users: dict = {}

    def save(s, user_id=None):
        sums[s.id] = s
        if user_id is not None:
            owners[s.id] = user_id

    def mine(uid):
        return [s for s in sums.values() if owners.get(s.id) == uid]

    def update_user(uid, password_hash=None, active=None):
        if uid not in users:
            return False
        if password_hash is not None:
            users[uid].password_hash = password_hash
        if active is not None:
            users[uid].active = active
        return True

    def create_user(name, ph, role):
        u = db.User(uuid.uuid4().hex[:12], name, ph, role, True, "2026-01-01")
        users[u.id] = u
        return u

    def delete_user(uid):
        for sid in [i for i, o in owners.items() if o == uid]:
            sums.pop(sid, None)
            owners.pop(sid)
        return users.pop(uid, None) is not None

    monkeypatch.setattr(db, "init_db", lambda: None)
    monkeypatch.setattr(db, "fail_interrupted", lambda: None)
    monkeypatch.setattr(db, "save", save)
    monkeypatch.setattr(db, "get", lambda i: sums.get(i))
    monkeypatch.setattr(db, "all_summaries", lambda: list(sums.values()))
    monkeypatch.setattr(db, "get_for_user", lambda i, u: sums.get(i) if owners.get(i) == u else None)
    monkeypatch.setattr(db, "list_for_user", mine)
    monkeypatch.setattr(db, "delete_for_user", lambda i, u: owners.get(i) == u and bool(sums.pop(i, None) or True))
    monkeypatch.setattr(db, "create_user", create_user)
    monkeypatch.setattr(db, "get_user", lambda i: users.get(i))
    monkeypatch.setattr(
        db, "get_user_by_name", lambda n: next((u for u in users.values() if u.username.lower() == n.lower()), None)
    )
    monkeypatch.setattr(db, "list_users", lambda: list(users.values()))
    monkeypatch.setattr(db, "update_user", update_user)
    monkeypatch.setattr(db, "delete_user", delete_user)
    return sums


def make_client(monkeypatch, can_process=True):
    import importlib

    fake_db(monkeypatch)
    monkeypatch.setenv("JWT_SECRET", "x" * 40)
    monkeypatch.setenv("CAN_PROCESS", "true" if can_process else "false")
    from app.config import get_settings

    get_settings.cache_clear()
    import app.main as m

    return TestClient(importlib.reload(m).app)


def seed(monkeypatch):
    from app import db, security

    admin = db.create_user("admin", security.hash_password("clave-admin-1"), "admin")
    ana = db.create_user("ana", security.hash_password("clave-ana-123"), "user")
    return admin, ana


def login(c, name, pw):
    r = c.post("/auth/login", json={"username": name, "password": pw})
    return r, {"Authorization": f"Bearer {r.json().get('token', '')}"}


def test_merge_similar_titles():
    from app.schemas import Chapter
    from app.services.summarize import merge_similar

    chs = [
        Chapter(start=300, title="Embedding para RAG", summary="A."),
        Chapter(start=360, title="Embeddings para RAG", summary="B."),
        Chapter(start=600, title="Inforce y su uso", summary="C."),
    ]
    out = merge_similar(chs)
    assert [(c.start, c.summary) for c in out] == [(300, "A. B."), (600, "C.")]


def test_apply_terms():
    from app.services.summarize import apply_terms

    terms = ["InsForge", "pgvector", "Cursor"]
    assert apply_terms("usamos Inforce y vector con cursos", terms) == "usamos InsForge y vector con cursos"
    assert apply_terms("la insforce es", terms) == "la InsForge es"


def test_drop_unsupported_expansions():
    from app.services.summarize import drop_unsupported_expansions as d

    src = "retrieval augmented generation es un método"
    assert d("RAG (Reinforced Active Generation) sirve", src) == "RAG sirve"
    assert d("RAG (Retrieval-Augmented Generation) sirve", src) == "RAG (Retrieval-Augmented Generation) sirve"


def test_window_seconds():
    from app.pipeline import window_seconds

    assert window_seconds(lines(27, step=10)) == 90          # corto: mínimo
    assert window_seconds(lines(60, step=10)) == 90
    assert window_seconds(lines(300, step=10)) == 360        # largo: tope
    assert window_seconds(lines(100, step=10)) == 123        # intermedio: ~8 secciones


def test_login_and_isolation(monkeypatch):
    with make_client(monkeypatch) as c:
        seed(monkeypatch)
        assert c.get("/summaries").status_code == 401
        assert login(c, "ana", "mal")[0].status_code == 401
        assert login(c, "nadie", "x")[0].status_code == 401
        r, h = login(c, "ANA", "clave-ana-123")  # el usuario no distingue mayúsculas
        assert r.status_code == 200 and r.json()["user"]["role"] == "user" and r.json()["canProcess"] is True
        assert c.get("/summaries", headers=h).json() == []
        assert c.get("/auth/me", headers=h).json()["user"]["username"] == "ana"
        assert c.post("/summaries", json={"url": "no es youtube"}, headers=h).status_code == 422
        assert c.get("/summaries/nope", headers=h).status_code == 404
        assert c.get("/health").json()["canProcess"] is True


def test_notes_delete_and_ownership(monkeypatch):
    from app import db
    from app.schemas import Summary

    with make_client(monkeypatch) as c:
        _, ana = seed(monkeypatch)
        db.create_user("beto", __import__("app.security", fromlist=["x"]).hash_password("clave-beto-12"), "user")
        s = Summary(id="abc", url="u", video_id="v", created_at="2026-01-01", status="ready")
        db.save(s, ana.id)
        _, h = login(c, "ana", "clave-ana-123")
        _, hb = login(c, "beto", "clave-beto-12")
        r = c.patch("/summaries/abc/notes", json={"notes": "mis apuntes"}, headers=h)
        assert r.status_code == 200 and r.json()["notes"] == "mis apuntes"
        assert c.get("/summaries/abc", headers=h).json()["notes"] == "mis apuntes"
        assert c.get("/summaries/abc", headers=hb).status_code == 404  # otro usuario no lo ve
        assert c.patch("/summaries/abc/notes", json={"notes": "x"}, headers=hb).status_code == 404
        assert c.delete("/summaries/abc", headers=hb).status_code == 404
        assert c.delete("/summaries/abc", headers=h).status_code == 204
        assert c.get("/summaries", headers=h).json() == []


def test_notes_blocked_while_processing(monkeypatch):
    from app import db
    from app.schemas import Summary

    with make_client(monkeypatch) as c:
        _, ana = seed(monkeypatch)
        db.save(Summary(id="p1", url="u", video_id="v", created_at="2026-01-01", status="summarizing"), ana.id)
        _, h = login(c, "ana", "clave-ana-123")
        assert c.patch("/summaries/p1/notes", json={"notes": "x"}, headers=h).status_code == 409


def test_cloud_mode_cannot_process_but_can_delete(monkeypatch):
    from app import db
    from app.schemas import Summary

    with make_client(monkeypatch, can_process=False) as c:
        _, ana = seed(monkeypatch)
        db.save(Summary(id="n1", url="u", video_id="v", created_at="2026-01-01", status="ready"), ana.id)
        r, h = login(c, "ana", "clave-ana-123")
        assert r.json()["canProcess"] is False
        p = c.post("/summaries", json={"url": "https://youtu.be/dQw4w9WgXcQ"}, headers=h)
        assert p.status_code == 403 and "solo de consulta" in p.json()["message"]
        assert c.delete("/summaries/n1", headers=h).status_code == 204


def test_admin_manages_users_only(monkeypatch):
    with make_client(monkeypatch) as c:
        seed(monkeypatch)
        _, ha = login(c, "admin", "clave-admin-1")
        _, hu = login(c, "ana", "clave-ana-123")
        # el admin no tiene resúmenes ni puede procesar
        assert c.get("/summaries", headers=ha).status_code == 403
        assert c.post("/summaries", json={"url": "https://youtu.be/dQw4w9WgXcQ"}, headers=ha).status_code == 403
        # un usuario normal no gestiona usuarios
        assert c.get("/admin/users", headers=hu).status_code == 403
        users = c.get("/admin/users", headers=ha).json()
        assert {u["username"] for u in users} == {"admin", "ana"}
        assert "passwordHash" not in users[0] and "password_hash" not in users[0]
        # crear, duplicado, desactivar, cambiar clave, borrar
        r = c.post("/admin/users", json={"username": "luz", "password": "clave-luz-123"}, headers=ha)
        assert r.status_code == 201 and r.json()["role"] == "user"
        assert c.post("/admin/users", json={"username": "LUZ", "password": "otra-clave-12"}, headers=ha).status_code == 409
        assert c.post("/admin/users", json={"username": "x", "password": "corta"}, headers=ha).status_code == 422
        uid = r.json()["id"]
        assert c.patch(f"/admin/users/{uid}", json={"active": False}, headers=ha).json()["active"] is False
        assert login(c, "luz", "clave-luz-123")[0].status_code == 401
        c.patch(f"/admin/users/{uid}", json={"active": True, "password": "nueva-clave-12"}, headers=ha)
        assert login(c, "luz", "nueva-clave-12")[0].status_code == 200
        me = c.get("/auth/me", headers=ha).json()["user"]["id"]
        assert c.delete(f"/admin/users/{me}", headers=ha).status_code == 409
        assert c.patch(f"/admin/users/{me}", json={"active": False}, headers=ha).status_code == 409
        assert c.delete(f"/admin/users/{uid}", headers=ha).status_code == 204


def test_login_lockout(monkeypatch):
    from app import security

    security._fails.clear()
    with make_client(monkeypatch) as c:
        seed(monkeypatch)
        for _ in range(5):
            assert login(c, "ana", "mal")[0].status_code == 401
        assert login(c, "ana", "clave-ana-123")[0].status_code == 429
    security._fails.clear()


def test_passwords_and_tokens(monkeypatch):
    from app import security
    from app.config import get_settings

    monkeypatch.setenv("JWT_SECRET", "y" * 40)
    get_settings.cache_clear()
    h = security.hash_password("abc12345")
    assert "abc12345" not in h and security.verify_password("abc12345", h)
    assert not security.verify_password("otra", h) and not security.verify_password("x", "basura")
    t = security.create_token("u1")
    assert security.read_token(t) == "u1" and security.read_token(t + "x") is None
    monkeypatch.setenv("JWT_SECRET", "z" * 40)
    get_settings.cache_clear()
    assert security.read_token(t) is None  # firmado con otra clave
