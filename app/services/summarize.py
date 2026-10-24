import re
from collections.abc import Callable
from difflib import SequenceMatcher
from dataclasses import dataclass

from ..config import get_settings
from ..schemas import Chapter, Source, TranscriptLine
from . import llm, websearch

MAX_CHUNK_CHARS = 9000  # ~2.5k tokens de entrada por tramo, cómodo para num_ctx 8192


@dataclass
class Chunk:
    start: float
    hint: str
    text: str


def fmt_ts(seconds: float) -> str:
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m:02d}:{sec:02d}"


def build_chunks(
    lines: list[TranscriptLine],
    chapters: list[tuple[float, str]],
    chunk_seconds: int,
) -> list[Chunk]:
    """Agrupa la transcripción por capítulos del video o, si no hay, por ventanas de tiempo."""
    if not lines:
        return []
    if chapters:
        bounds = [(start, title) for start, title in chapters]
        if bounds[0][0] > 0:
            bounds.insert(0, (0.0, ""))
    else:
        end = lines[-1].start
        bounds = [(float(t), "") for t in range(0, int(end) + 1, chunk_seconds)]

    groups: list[tuple[float, str, list[TranscriptLine]]] = [(start, hint, []) for start, hint in bounds]
    gi = 0
    for ln in lines:
        while gi + 1 < len(groups) and ln.start >= groups[gi + 1][0]:
            gi += 1
        groups[gi][2].append(ln)

    out: list[Chunk] = []
    for start, hint, glines in groups:
        # un capítulo muy largo se parte por líneas para no desbordar el contexto del modelo
        cur: list[TranscriptLine] = []
        size = 0
        first = True
        for ln in glines + [None]:
            if cur and (ln is None or size + len(ln.text) > MAX_CHUNK_CHARS):
                # el primer tramo conserva el inicio del capítulo; los demás, el de su primera línea
                out.append(Chunk(start if first else cur[0].start, hint, " ".join(x.text for x in cur)))
                cur, size, first = [], 0, False
            if ln is not None:
                cur.append(ln)
                size += len(ln.text) + 1
    return out


def _lang() -> str:
    return get_settings().summary_language


CHAPTER_SCHEMA = {
    "type": "object",
    "properties": {"title": {"type": "string"}, "summary": {"type": "string"}},
    "required": ["title", "summary"],
}

TERMS_SCHEMA = {
    "type": "object",
    "properties": {"terms": {"type": "array", "items": {"type": "string"}}},
    "required": ["terms"],
}

FINAL_SCHEMA = {
    "type": "object",
    "properties": {
        "tldr": {"type": "string"},
        "key_points": {"type": "array", "items": {"type": "string"}},
        "used_sources": {"type": "array", "items": {"type": "integer"}},
    },
    "required": ["tldr", "key_points", "used_sources"],
}


def _terms_note(terms: list[str]) -> str:
    if not terms:
        return ""
    return (
        "\nNombres propios correctos: " + ", ".join(terms) + ". "
        "Los subtítulos son automáticos y suelen escribir mal los nombres; si ves una variante "
        "que suena parecida a uno de ellos, usa siempre la forma correcta."
    )


def summarize_chunk(chunk: Chunk, video_title: str, terms: list[str]) -> Chapter:
    hint = f"Título del capítulo según el autor: {chunk.hint}\n" if chunk.hint else ""
    res = llm.chat_json(
        system=(
            f"Eres un asistente que resume transcripciones de videos de YouTube. Responde siempre en {_lang()}. "
            "La transcripción viene de subtítulos automáticos y puede contener errores. "
            "Basa tu respuesta únicamente en el fragmento dado, sin inventar datos ni atribuir al video lo contrario de lo que dice, y sin expandir siglas que el fragmento no expanda. "
            "Devuelve un título corto y específico (máx. 8 palabras, que distinga este fragmento de los demás) "
            "y un resumen de 1 a 3 frases con las ideas concretas del fragmento." + _terms_note(terms)
        ),
        user=f"Video: {video_title}\n{hint}\nFragmento de la transcripción:\n{apply_terms(chunk.text, terms)}",
        schema=CHAPTER_SCHEMA,
    )
    return Chapter(
        start=chunk.start,
        title=apply_terms((res.get("title") or chunk.hint or "Sección").strip(), terms),
        summary=drop_unsupported_expansions(
            apply_terms((res.get("summary") or "").strip(), terms), chunk.text
        ),
    )


_WORD = re.compile(r"[\wáéíóúüñÁÉÍÓÚÜÑ]+")


def apply_terms(text: str, terms: list[str], threshold: float = 0.8) -> str:
    """Corrige variantes mal transcritas de nombres propios de una sola palabra ("Inforce" -> "InsForge").

    Es conservador: exige nombres largos, misma inicial y que la variante no esté contenida en el
    nombre (evita convertir "vector" en "pgvector").
    """
    single = [t for t in terms if len(t) >= 7 and _WORD.fullmatch(t)]
    if not single:
        return text

    def fix(m: re.Match) -> str:
        w = m.group(0)
        wl = w.lower()
        for t in single:
            tl = t.lower()
            if wl == tl or wl in tl or tl in wl or wl[0] != tl[0] or abs(len(wl) - len(tl)) > 2:
                continue
            if SequenceMatcher(None, wl, tl).ratio() >= threshold:
                return t
        return w

    return _WORD.sub(fix, text)


_EXPANSION = re.compile(r"\b([A-Z]{2,6}) \(([^()]{4,80})\)")


def drop_unsupported_expansions(text: str, source: str) -> str:
    """Quita "SIGLA (Expansión inventada)" cuando las palabras de la expansión no aparecen en el material."""
    src = source.lower()

    def check(m: re.Match) -> str:
        words = [w for w in re.split(r"[\W_]+", m.group(2).lower()) if len(w) > 2]
        return m.group(0) if all(w in src for w in words) else m.group(1)

    return _EXPANSION.sub(check, text)


def _norm(title: str) -> str:
    return re.sub(r"\W+", " ", title.lower()).strip()


def merge_similar(chapters: list[Chapter], threshold: float = 0.8) -> list[Chapter]:
    """Une secciones consecutivas cuyo título es casi igual (el modelo repite títulos entre tramos contiguos)."""
    out: list[Chapter] = []
    for c in chapters:
        if out and SequenceMatcher(None, _norm(out[-1].title), _norm(c.title)).ratio() >= threshold:
            out[-1] = Chapter(
                start=out[-1].start,
                title=out[-1].title,
                summary=f"{out[-1].summary} {c.summary}".strip(),
            )
        else:
            out.append(c)
    return out


def summarize_all(
    video_title: str,
    chunks: list[Chunk],
    terms: list[str],
    on_progress: Callable[[float], None],
) -> list[Chapter]:
    chapters = []
    for i, c in enumerate(chunks):
        chapters.append(summarize_chunk(c, video_title, terms))
        on_progress((i + 1) / len(chunks))
    return merge_similar(chapters)


def fetch_context(video_title: str, channel: str) -> tuple[list[websearch.WebHit], list[str]]:
    """Busca el video en la web y extrae de ahí la escritura correcta de nombres propios."""
    hits = websearch.search(f"{video_title} {channel}".strip())
    if not hits:
        return [], []
    listed = "\n".join(f"- {h.title}: {h.snippet}" for h in hits)
    res = llm.chat_json(
        system=(
            "Extraes nombres propios de marcas, productos, herramientas, empresas o personas, escritos correctamente. "
            "Devuelve como máximo 10, solo los que aparecen en los resultados y se relacionan con el video. "
            "No incluyas conceptos ni términos genéricos (p. ej. 'embeddings', 'búsqueda semántica')."
        ),
        user=f"Video: {video_title}\nCanal: {channel}\n\nResultados de búsqueda:\n{listed}",
        schema=TERMS_SCHEMA,
    )
    terms = [t.strip() for t in res.get("terms", []) if isinstance(t, str) and t.strip()]
    return hits, terms[:12]


def final_summary(
    video_title: str,
    chapters: list[Chapter],
    hits: list[websearch.WebHit],
    terms: list[str],
) -> tuple[str, list[str], list[Source]]:
    outline = "\n".join(f"[{fmt_ts(c.start)}] {c.title}: {c.summary}" for c in chapters)

    web_block = ""
    if hits:
        listed = "\n".join(f"{i}. {h.title} — {h.snippet}" for i, h in enumerate(hits))
        web_block = (
            "\n\nContexto externo de una búsqueda web (úsalo solo si aporta claridad o contexto "
            "directamente relacionado con el video; si no, ignóralo). En `used_sources` lista los "
            f"números de las fuentes que realmente usaste (vacío si ninguna):\n{listed}"
        )

    res = llm.chat_json(
        system=(
            f"Eres un asistente que sintetiza videos de YouTube. Responde siempre en {_lang()}. "
            "`tldr`: un texto fluido de 1 a 3 párrafos cortos, en prosa, sin viñetas ni enumeraciones, que explique "
            "de qué trata el video y sus conclusiones; no menciones ni listes los puntos clave. `key_points`: entre 5 y 8 puntos clave distintos entre sí, sin repetir ideas, cada uno una frase completa. "
            "No inventes datos que no estén en el material, no contradigas lo que dicen las secciones "
            "y no expandas siglas (como RAG) salvo que las secciones ya lo hagan."
            + _terms_note(terms)
        ),
        user=f"Video: {video_title}\n\nResumen por secciones:\n{outline}{web_block}",
        schema=FINAL_SCHEMA,
    )
    used = {i for i in res.get("used_sources", []) if isinstance(i, int) and 0 <= i < len(hits)}
    sources = [Source(title=hits[i].title, url=hits[i].url) for i in sorted(used)]
    def clean(text: str) -> str:
        return drop_unsupported_expansions(apply_terms(text.strip(), terms), outline)

    return (
        clean(res.get("tldr") or ""),
        [clean(p) for p in res.get("key_points", []) if p.strip()],
        sources,
    )
