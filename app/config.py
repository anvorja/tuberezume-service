from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:7b"
    ollama_num_ctx: int = 8192
    ollama_timeout: float = 600

    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    cors_origin_regex: str = ""

    # Clave con la que se firman las sesiones (obligatoria). Debe ser la misma en todos los servidores
    # que compartan base de datos (ngrok y nube), para que un mismo login valga en ambos.
    jwt_secret: str = ""
    token_days: int = 30
    # False en el servidor de nube: solo consulta, borrado y notas; no procesa videos.
    can_process: bool = True

    # URL de Postgres (postgresql://usuario:clave@host:5432/bd). Obligatoria.
    db_url: str = ""
    summary_language: str = "español"
    web_search_default: bool = False
    chunk_seconds: int = 360

    @property
    def origins(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
