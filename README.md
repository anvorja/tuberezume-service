# TubeRezume – backend

API que recibe un enlace de YouTube, obtiene la transcripción y genera dos resúmenes con un modelo local (Ollama, `qwen2.5:7b`):

- **Por secciones:** `chapters` (con minuto) y `keyPoints`.
- **Texto fluido:** `tldr`.
- **Búsqueda web opcional** (DuckDuckGo, sin clave): añade contexto externo y devuelve las `sources` usadas.

Sin claves ni suscripciones.

## Requisitos

- Python 3.12 y [uv](https://docs.astral.sh/uv/)
- [Ollama](https://ollama.com) en ejecución con el modelo descargado:

```bash
ollama pull qwen2.5:7b
```

## Arrancar

```bash
cp .env.example .env     # opcional
uv sync
uv run uvicorn app.main:app --reload --port 8000
```

Frontend: en `content-youtube-easy/.env.local` pon `VITE_API_URL=http://localhost:8000`.

## Endpoints

| Método y ruta           | Cuerpo                                 | Respuesta        |
| ----------------------- | -------------------------------------- | ---------------- |
| `POST /summaries`       | `{ "url": "…", "webSearch": true? }`   | `Summary` (cola) |
| `GET /summaries`        |                                        | `Summary[]`      |
| `GET /summaries/{id}`   |                                        | `Summary`        |
| `DELETE /summaries/{id}`|                                        | `204`            |
| `GET /health`           |                                        | estado y modelo  |

Los errores devuelven `{ "message": "…" }`.

## Pruebas

```bash
uv run pytest
```
