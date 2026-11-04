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

- Una base de datos Postgres (`DB_URL`), por ejemplo Neon. Es obligatoria: sin ella el backend no guarda nada.

## Arrancar

```bash
cp .env.example .env     # y rellena DB_URL y JWT_SECRET
uv sync
uv run python -m app.cli crear-usuario admin --rol admin     # pide la contraseña por teclado
uv run python -m app.cli crear-usuario tunombre --rol user
uv run uvicorn app.main:app --port 8000
```

## Configuración (`.env`)

`.env` está en `.gitignore`: **nunca lo subas**. `.env.example` documenta cada variable sin valores reales.

| Variable | Para qué sirve |
| --- | --- |
| `DB_URL` | URL de Postgres (`postgresql://usuario:clave@host:5432/bd`; añade `?sslmode=require` si el proveedor lo pide). Obligatoria. |
| `JWT_SECRET` | Clave (≥ 32 caracteres) con la que se firman las sesiones. **Obligatoria**, y la misma en todos los servidores que compartan base de datos. Genera una con `python -c "import secrets; print(secrets.token_urlsafe(48))"`. |
| `TOKEN_DAYS` | Días de validez de cada inicio de sesión (30 por defecto). |
| `CAN_PROCESS` | `true` (por defecto) en tu equipo con ngrok: procesa videos. `false` en el servidor de nube: solo consulta, notas y borrado. |
| `CORS_ORIGINS` | Orígenes del frontend permitidos, separados por coma (local y desplegado). |
| `CORS_ORIGIN_REGEX` | Opcional, para URLs variables como las previews de Netlify. |
| `OLLAMA_URL`, `OLLAMA_MODEL`, `OLLAMA_NUM_CTX`, `OLLAMA_TIMEOUT` | Conexión y modelo de Ollama. |
| `SUMMARY_LANGUAGE` | Idioma de los resúmenes (por defecto `español`). |
| `WEB_SEARCH_DEFAULT` | Si la búsqueda web va activada cuando la petición no lo indica. |
| `CHUNK_SECONDS` | Tope de la ventana para videos sin capítulos. |

## Usuarios y sesión

Hay dos roles. Las contraseñas se guardan con hash (scrypt) y nunca en el código ni en el repositorio.

- **`user`**: dueño de sus resúmenes. Cada uno ve solo los suyos. Puede leerlos, escribir **notas** por video, borrarlos y, en el servidor con `CAN_PROCESS=true`, procesar videos.
- **`admin`**: solo gestiona usuarios (crear, cambiar clave, desactivar, eliminar). No tiene resúmenes ni puede procesar.

La cuenta de usuario se crea con la CLI (`app.cli`, ver «Arrancar») o, si ya hay un admin, desde la pantalla **Usuarios** de la web. Tras 5 contraseñas erróneas seguidas se bloquea ese usuario+IP durante 1 minuto. Cambiar la contraseña de alguien: `uv run python -m app.cli cambiar-clave NOMBRE`.

## Dos servidores, una base de datos

La idea es procesar en tu PC (más potente, gratis) y poder **consultar el historial desde cualquier equipo** sin que el PC esté encendido:

| | Servidor local (ngrok) | Servidor de nube (solo lectura) |
| --- | --- | --- |
| `CAN_PROCESS` | `true` | `false` |
| `DB_URL` y `JWT_SECRET` | los mismos | los mismos |
| Procesar videos | sí | no (`403` con un mensaje claro) |
| Leer, notas y borrar | sí | sí |

Como comparten `JWT_SECRET`, el mismo inicio de sesión vale en ambos. En la nube basta cualquier servicio que ejecute `uvicorn app.main:app` con las variables de entorno definidas en su panel (nunca en el repositorio); no necesita Ollama.

### Desplegar el servidor de consulta en Render

Es un *Web Service* conectado a este repositorio (rama `main`). La versión de Python sale de `.python-version` (3.12).

| Campo | Valor |
| --- | --- |
| Root Directory | vacío |
| Build Command | `uv sync --frozen && uv cache prune --ci` |
| Start Command | `uv run uvicorn app.main:app --host 0.0.0.0 --port $PORT` |
| Health Check Path | `/health` |
| Auto-Deploy | `On Commit` (cada `git push` redespliega) |
| Plan | el gratuito basta: este servidor no ejecuta Ollama |

Variables de entorno (se definen en el panel de Render, **nunca** en el repositorio):

| Variable | Valor |
| --- | --- |
| `DB_URL` | la misma que en tu equipo |
| `JWT_SECRET` | **idéntico** al de tu equipo; si difiere, la sesión de un servidor no vale en el otro |
| `CAN_PROCESS` | `false` |
| `CORS_ORIGINS` | incluye el origen del frontend, p. ej. `https://tuberezume.netlify.app` |
| `TOKEN_DAYS`, `SUMMARY_LANGUAGE`, `WEB_SEARCH_DEFAULT`, `CHUNK_SECONDS` | opcionales (valores por defecto en `.env.example`) |

Comprueba el despliegue en `https://TU-SERVICIO.onrender.com/health`: debe responder `{"ok":true,"auth":true,"canProcess":false}`. Después, en la web, **Ajustes → Conexión**, pega esa URL.

Notas de funcionamiento:

- En el plan gratuito Render apaga el servicio tras un rato sin uso. La primera petición después tarda 30–60 s; luego va normal.
- Al arrancar, solo el servidor con `CAN_PROCESS=true` marca como fallidos los trabajos que quedaron a medias. El de consulta no los toca, para no estropear un video que tu equipo esté procesando mientras Render se reinicia.

## Usarlo con el frontend desplegado (Netlify + ngrok)

El backend sigue en tu máquina y un túnel lo hace accesible desde internet. YouTube bloquea las transcripciones desde IPs de servidores en la nube, pero desde una IP residencial como la tuya suele funcionar.

1. Terminal 1: `uv run uvicorn app.main:app --port 8000`
2. Terminal 2: `ngrok http 8000` y copia la URL `https://….ngrok-free.app`.
3. En la web, **Entrar**: pega esa URL, tu usuario y tu contraseña. (Después se cambia de servidor en **Ajustes**.)
4. Comprueba el túnel abriendo `https://TU-URL.ngrok-free.app/health`: debe responder `{"ok":true,"auth":true,"canProcess":true}`.

La URL gratuita de ngrok **cambia en cada arranque**; los resúmenes y las cuentas no, porque están en Postgres. El origen del frontend debe estar en `CORS_ORIGINS`. Para desarrollo local, en `content-youtube-easy/.env.local` basta `VITE_API_URL=http://localhost:8000`.

## Endpoints

Todas las rutas salvo `/health` y `/auth/login` exigen `Authorization: Bearer <sesión>`.

| Método y ruta | Cuerpo | Respuesta |
| --- | --- | --- |
| `POST /auth/login` | `{ "username", "password" }` | `{ token, user, canProcess }` |
| `GET /auth/me` | | lo mismo, validando la sesión |
| `POST /summaries` | `{ "url": "…", "webSearch": true? }` | `201` con el `Summary` en cola (`403` si `CAN_PROCESS=false`) |
| `GET /summaries` | | `Summary[]` del usuario, del más reciente al más antiguo |
| `GET /summaries/{id}` | | `Summary` |
| `PATCH /summaries/{id}/notes` | `{ "notes": "…" }` (≤ 20 000 caracteres) | `Summary` actualizado |
| `DELETE /summaries/{id}` | | `204` |
| `GET/POST /admin/users`, `PATCH/DELETE /admin/users/{id}` | solo `admin` | gestión de usuarios |
| `GET /health` | | `{ "ok": true, "auth": true, "canProcess": <bool> }` (público) |

Los errores devuelven `{ "message": "…" }` (el frontend lo muestra tal cual); sin sesión válida, `401`; con el rol equivocado, `403`.

## Límites conocidos

- Un modelo de 7B puede cometer errores: confundir un término, o inventar un detalle que el video no menciona. Hay salvaguardas (corrección de nombres propios y eliminación de siglas inventadas), pero no son infalibles.
- Los subtítulos automáticos de YouTube pueden escribir mal los nombres propios.
- Videos sin subtítulos no están soportados todavía (devuelven un error claro; la transcripción con audio local, Whisper, queda pendiente).
- Si reinicias el servidor a mitad de un resumen, ese trabajo queda como `failed` y se puede reintentar desde la web.

## Pruebas

```bash
uv run pytest
```

Las pruebas usan un almacén en memoria, así que nunca tocan tu base de datos real.

## Estructura

```
app/
  main.py         rutas, CORS, sesión y roles
  security.py     hash de contraseñas, tokens y freno de intentos
  cli.py          crear usuarios y cambiar contraseñas
  pipeline.py     orquestación de cada trabajo y progreso
  db.py           Postgres (pool de conexiones)
  schemas.py      contrato JSON (camelCase) que consume el frontend
  services/       youtube, llm (Ollama), summarize, websearch
tests/
```
