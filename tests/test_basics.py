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


def test_api_contract(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setenv("DB_URL", "")
    monkeypatch.setenv("API_TOKEN", "")
    from app.config import get_settings

    get_settings.cache_clear()
    import importlib

    import app.main as m

    m = importlib.reload(m)
    with TestClient(m.app) as c:
        assert c.get("/summaries").json() == []
        r = c.post("/summaries", json={"url": "no es youtube"})
        assert r.status_code == 422 and "message" in r.json()
        assert c.get("/summaries/nope").status_code == 404
        assert c.delete("/summaries/nope").status_code == 404


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


def test_token_required(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setenv("DB_URL", "")
    monkeypatch.setenv("API_TOKEN", "secreto")
    from app.config import get_settings

    get_settings.cache_clear()
    import importlib

    import app.main as m

    m = importlib.reload(m)
    with TestClient(m.app) as c:
        assert c.get("/health").status_code == 200
        r = c.get("/summaries")
        assert r.status_code == 401 and "message" in r.json()
        assert c.get("/summaries", headers={"Authorization": "Bearer mal"}).status_code == 401
        assert c.get("/summaries", headers={"Authorization": "Bearer secreto"}).status_code == 200
        pre = c.options(
            "/summaries",
            headers={
                "Origin": "https://tuberezume.netlify.app",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )
        assert pre.status_code == 200
