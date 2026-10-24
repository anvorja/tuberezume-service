import json

import httpx

from ..config import get_settings


class LLMError(Exception):
    pass


def chat_json(system: str, user: str, schema: dict) -> dict:
    """Llama a Ollama forzando una salida JSON que cumpla `schema`."""
    s = get_settings()
    payload = {
        "model": s.ollama_model,
        "stream": False,
        "format": schema,
        "options": {"temperature": 0.2, "num_ctx": s.ollama_num_ctx},
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    try:
        r = httpx.post(f"{s.ollama_url}/api/chat", json=payload, timeout=s.ollama_timeout)
    except httpx.ConnectError as e:
        raise LLMError(
            f"No hay conexión con Ollama en {s.ollama_url}. ¿Está en ejecución?"
        ) from e
    except httpx.HTTPError as e:
        raise LLMError(f"Fallo al hablar con Ollama: {e.__class__.__name__}") from e
    if r.status_code == 404:
        raise LLMError(f"El modelo {s.ollama_model} no está descargado (ollama pull {s.ollama_model}).")
    if r.status_code >= 400:
        raise LLMError(f"Ollama respondió {r.status_code}: {r.text[:200]}")
    try:
        return json.loads(r.json()["message"]["content"])
    except (KeyError, json.JSONDecodeError) as e:
        raise LLMError("El modelo devolvió una respuesta que no es JSON válido.") from e
