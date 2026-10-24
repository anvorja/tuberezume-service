# TubeRezume – backend

API que recibe un enlace de YouTube, obtiene la transcripción y genera dos resúmenes con un modelo que corre en tu máquina (Ollama, `qwen2.5:7b`). Sin claves de pago ni suscripciones.

- **Por secciones:** `chapters` (con minuto) y `keyPoints`.
- **Texto fluido:** `tldr`.
- **Búsqueda web opcional** (DuckDuckGo, sin clave): aporta contexto, ayuda a escribir bien los nombres propios y devuelve las `sources` que usó.

El frontend vive en `../content-youtube-easy` y consume esta API.

## Cómo funciona

```
URL ─► metadatos (yt-dlp: título, canal, duración, capítulos)
    ─► transcripción con minutos (youtube-transcript-api; respaldo: subtítulos vía yt-dlp)
    ─► tramos: por capítulos del video o, si no los hay, ventanas de ~8 secciones (90 s – 6 min)
    ─► (opcional) búsqueda web → nombres propios correctos
    ─► resumen de cada tramo con el modelo → secciones
    ─► síntesis final → tldr + keyPoints
```

El estado avanza `queued → transcribing → summarizing → ready` (o `failed`), con `progress` de 0 a 1. Se procesa **un video a la vez**, porque el modelo local satura la GPU/CPU. Un video de 36 min con capítulos tardó unos 7 min en una GTX 1650 Ti (4 GB).

## Requisitos

- Python 3.12 y [uv](https://docs.astral.sh/uv/)
- [Ollama](https://ollama.com) en ejecución con el modelo descargado:

```bash
ollama pull qwen2.5:7b
```

- Una base de datos Postgres (`DB_URL`). Sin ella usa SQLite local, solo para desarrollo y pruebas.

## Arrancar

```bash
cp .env.example .env     # y rellena DB_URL y API_TOKEN
uv sync
uv run uvicorn app.main:app --port 8000
```

## Configuración (`.env`)

`.env` está en `.gitignore`: **nunca lo subas**. `.env.example` documenta cada variable sin valores reales.

| Variable | Para qué sirve |
| --- | --- |
| `DB_URL` | URL de Postgres (`postgresql://usuario:clave@host:5432/bd`; añade `?sslmode=require` si el proveedor lo pide). Vacía = SQLite en `DB_PATH`. |
| `API_TOKEN` | Clave de acceso. Con ella definida, `/summaries` exige `Authorization: Bearer <clave>`. **Obligatoria si expones el backend.** Genera una con `python -c "import secrets; print(secrets.token_urlsafe(32))"`. |
| `CORS_ORIGINS` | Orígenes del frontend permitidos, separados por coma (local y desplegado). |
| `CORS_ORIGIN_REGEX` | Opcional, para URLs variables como las previews de Netlify. |
| `OLLAMA_URL`, `OLLAMA_MODEL`, `OLLAMA_NUM_CTX`, `OLLAMA_TIMEOUT` | Conexión y modelo de Ollama. |
| `SUMMARY_LANGUAGE` | Idioma de los resúmenes (por defecto `español`). |
| `WEB_SEARCH_DEFAULT` | Si la búsqueda web va activada cuando la petición no lo indica. |
| `CHUNK_SECONDS` | Tope de la ventana para videos sin capítulos. |

## Usarlo con el frontend desplegado (Netlify + ngrok)

El backend sigue en tu máquina y un túnel lo hace accesible desde internet. YouTube bloquea las transcripciones desde IPs de servidores en la nube, pero desde una IP residencial como la tuya suele funcionar.

1. Terminal 1: `uv run uvicorn app.main:app --port 8000`
2. Terminal 2: `ngrok http 8000` y copia la URL `https://….ngrok-free.app`.
3. En la web, **Ajustes → Conexión con el backend**: pega esa URL y el valor de `API_TOKEN`.
4. Comprueba el túnel abriendo `https://TU-URL.ngrok-free.app/health`: debe responder `{"ok":true,"auth":true}`.

La URL gratuita de ngrok **cambia en cada arranque**; la clave no. El origen del frontend debe estar en `CORS_ORIGINS`. Para desarrollo local, en `content-youtube-easy/.env.local` basta `VITE_API_URL=http://localhost:8000`.

## Endpoints

| Método y ruta | Cuerpo | Respuesta |
| --- | --- | --- |
| `POST /summaries` | `{ "url": "…", "webSearch": true? }` | `201` con el `Summary` en cola |
| `GET /summaries` | | `Summary[]`, del más reciente al más antiguo |
| `GET /summaries/{id}` | | `Summary` |
| `DELETE /summaries/{id}` | | `204` |
| `GET /health` | | `{ "ok": true, "auth": <hay clave definida> }` (público) |

Los errores devuelven `{ "message": "…" }` (el frontend lo muestra tal cual); sin clave o con una incorrecta, `401`.

## Límites conocidos

- Un modelo de 7B puede cometer errores: confundir un término, o inventar un detalle que el video no menciona. Hay salvaguardas (corrección de nombres propios y eliminación de siglas inventadas), pero no son infalibles.
- Los subtítulos automáticos de YouTube pueden escribir mal los nombres propios.
- Videos sin subtítulos no están soportados todavía (devuelven un error claro; la transcripción con audio local, Whisper, queda pendiente).
- Si reinicias el servidor a mitad de un resumen, ese trabajo queda como `failed` y se puede reintentar desde la web.

## Pruebas

```bash
uv run pytest
```

Las pruebas fuerzan SQLite aislado, así que nunca tocan tu base de datos real.

## Estructura

```
app/
  main.py         rutas, CORS y clave de acceso
  pipeline.py     orquestación de cada trabajo y progreso
  db.py           Postgres (pool) o SQLite
  schemas.py      contrato JSON (camelCase) que consume el frontend
  services/       youtube, llm (Ollama), summarize, websearch
tests/
```
