from dataclasses import dataclass

from ddgs import DDGS


@dataclass
class WebHit:
    title: str
    url: str
    snippet: str


def search(query: str, max_results: int = 5) -> list[WebHit]:
    """Búsqueda gratuita y sin clave (DuckDuckGo). Si falla, devuelve lista vacía:
    el resumen no debe depender de que la web responda."""
    try:
        raw = DDGS().text(query, max_results=max_results) or []
    except Exception:
        return []
    return [
        WebHit(title=r.get("title", ""), url=r.get("href", ""), snippet=r.get("body", ""))
        for r in raw
        if r.get("href")
    ]
