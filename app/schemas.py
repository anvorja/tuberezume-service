from typing import Literal

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic.alias_generators import to_camel

SummaryStatus = Literal["queued", "transcribing", "summarizing", "ready", "failed"]


class CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class Chapter(CamelModel):
    start: float
    title: str
    summary: str


class TranscriptLine(CamelModel):
    start: float
    text: str


class Source(CamelModel):
    title: str
    url: str


class Summary(CamelModel):
    """Contrato que consume el frontend (src/lib/types.ts)."""

    id: str
    url: str
    video_id: str
    title: str = ""
    channel: str = ""
    duration_sec: int = 0
    created_at: str
    status: SummaryStatus = "queued"
    progress: float = 0
    error: str | None = None
    tldr: str = ""
    key_points: list[str] = Field(default_factory=list)
    chapters: list[Chapter] = Field(default_factory=list)
    transcript: list[TranscriptLine] = Field(default_factory=list)
    web_search: bool = False
    sources: list[Source] = Field(default_factory=list)
    notes: str = ""
    tags: list[str] = Field(default_factory=list)


class CreateSummary(CamelModel):
    url: str
    web_search: bool | None = None


class NotesIn(CamelModel):
    notes: str = Field(max_length=20000)


MAX_TAGS = 10
_TAG_OK = re.compile(r"^[\w .+#&-]{1,24}$", re.UNICODE)


class TagsIn(CamelModel):
    tags: list[str] = Field(max_length=MAX_TAGS)

    @field_validator("tags")
    @classmethod
    def clean(cls, tags: list[str]) -> list[str]:
        """Espacios normalizados, sin repetidos (sin distinguir mayúsculas) y con caracteres seguros."""
        out: list[str] = []
        seen: set[str] = set()
        for raw in tags:
            tag = " ".join(raw.split())
            if not _TAG_OK.match(tag):
                raise ValueError(
                    "Cada etiqueta debe tener entre 1 y 24 caracteres (letras, números, espacios y . + # & -)."
                )
            if tag.casefold() not in seen:
                seen.add(tag.casefold())
                out.append(tag)
        return out


class LoginIn(CamelModel):
    username: str = Field(max_length=64)
    password: str = Field(max_length=200)


class UserOut(CamelModel):
    id: str
    username: str
    role: Literal["admin", "user"]
    active: bool
    created_at: str


class Session(CamelModel):
    token: str
    user: UserOut
    can_process: bool


class UserCreate(CamelModel):
    username: str = Field(min_length=3, max_length=32, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=8, max_length=200)
    role: Literal["admin", "user"] = "user"


class UserPatch(CamelModel):
    password: str | None = Field(default=None, min_length=8, max_length=200)
    active: bool | None = None
