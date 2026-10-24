import logging
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from . import db
from .config import get_settings
from .schemas import Summary
from .services import llm, summarize, youtube

log = logging.getLogger("rezume")

# Un solo trabajo a la vez: el modelo local satura la GPU/CPU si se lanzan varios.
_executor = ThreadPoolExecutor(max_workers=1)


def create(url: str, web_search: bool | None) -> Summary:
    video_id = youtube.parse_video_id(url)
    if not video_id:
        raise ValueError("El enlace no parece ser de un video de YouTube.")
    s = Summary(
        id=uuid.uuid4().hex[:12],
        url=url.strip(),
        video_id=video_id,
        created_at=datetime.now(timezone.utc).isoformat(),
        web_search=get_settings().web_search_default if web_search is None else web_search,
    )
    db.save(s)
    _executor.submit(_run, s.id)
    return s


def window_seconds(lines) -> int:
    """Ventana para videos sin capítulos: apunta a ~8 secciones, entre 90 s y `chunk_seconds`."""
    total = lines[-1].start if lines else 0
    return int(max(90, min(get_settings().chunk_seconds, total / 8)))


def _update(s: Summary, **fields) -> None:
    for k, v in fields.items():
        setattr(s, k, v)
    db.save(s)


def _run(summary_id: str) -> None:
    s = db.get(summary_id)
    if s is None:
        return
    try:
        _update(s, status="transcribing", progress=0.05)
        info = youtube.fetch_info(s.video_id)
        _update(s, title=info.title, channel=info.channel, duration_sec=info.duration, progress=0.15)

        lines = youtube.fetch_transcript(s.video_id, info)
        _update(s, transcript=lines, status="summarizing", progress=0.3)

        chunks = summarize.build_chunks(lines, info.chapters, window_seconds(lines))

        hits, terms = summarize.fetch_context(info.title, info.channel) if s.web_search else ([], [])
        _update(s, progress=0.33)

        def on_progress(frac: float) -> None:
            _update(s, progress=round(0.33 + frac * 0.57, 3))

        chapters = summarize.summarize_all(info.title, chunks, terms, on_progress)
        _update(s, chapters=chapters, progress=0.92)

        tldr, key_points, sources = summarize.final_summary(info.title, chapters, hits, terms)
        _update(s, tldr=tldr, key_points=key_points, sources=sources, status="ready", progress=1.0)
    except (youtube.VideoError, llm.LLMError) as e:
        _update(s, status="failed", error=str(e))
    except Exception:
        log.exception("Fallo inesperado procesando %s", summary_id)
        _update(s, status="failed", error="Error inesperado procesando el video.")
