import re
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlparse

import httpx
import yt_dlp
from youtube_transcript_api import YouTubeTranscriptApi

from ..schemas import TranscriptLine

_ID = re.compile(r"^[\w-]{11}$")
_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}
LANG_PREFERENCE = ["es", "es-419", "es-ES", "en", "en-US"]


class VideoError(Exception):
    """Error mostrable al usuario."""


def parse_video_id(raw: str) -> str | None:
    raw = raw.strip()
    if _ID.match(raw):
        return raw
    if "://" not in raw:
        raw = "https://" + raw
    try:
        u = urlparse(raw)
    except ValueError:
        return None
    host = (u.hostname or "").lower()
    if host == "youtu.be":
        vid = u.path.strip("/").split("/")[0]
        return vid if _ID.match(vid) else None
    if host in _HOSTS:
        if u.path == "/watch":
            vid = parse_qs(u.query).get("v", [""])[0]
            return vid if _ID.match(vid) else None
        m = re.match(r"^/(?:shorts|embed|live|v)/([\w-]{11})", u.path)
        if m:
            return m.group(1)
    return None


@dataclass
class VideoInfo:
    title: str = ""
    channel: str = ""
    duration: int = 0
    chapters: list[tuple[float, str]] = field(default_factory=list)
    caption_tracks: dict = field(default_factory=dict)


def fetch_info(video_id: str) -> VideoInfo:
    opts = {"quiet": True, "no_warnings": True, "skip_download": True}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            d = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
    except Exception as e:
        raise VideoError(f"No se pudo leer el video: {_short(e)}") from e
    chapters = [(float(c["start_time"]), c.get("title") or "") for c in d.get("chapters") or []]
    tracks = {**(d.get("automatic_captions") or {}), **(d.get("subtitles") or {})}
    return VideoInfo(
        title=d.get("title") or "",
        channel=d.get("channel") or d.get("uploader") or "",
        duration=int(d.get("duration") or 0),
        chapters=chapters,
        caption_tracks=tracks,
    )


def _short(e: Exception) -> str:
    return str(e).splitlines()[0][:200] if str(e) else e.__class__.__name__


def _via_transcript_api(video_id: str) -> list[TranscriptLine]:
    fetched = YouTubeTranscriptApi().fetch(video_id, languages=LANG_PREFERENCE)
    return [TranscriptLine(start=s.start, text=s.text) for s in fetched]


def _via_ytdlp_tracks(info: VideoInfo) -> list[TranscriptLine]:
    for lang in LANG_PREFERENCE + list(info.caption_tracks):
        for fmt in info.caption_tracks.get(lang, []):
            if fmt.get("ext") != "json3":
                continue
            data = httpx.get(fmt["url"], timeout=30, follow_redirects=True).json()
            lines = []
            for ev in data.get("events", []):
                text = "".join(s.get("utf8", "") for s in ev.get("segs", [])).replace("\n", " ").strip()
                if text:
                    lines.append(TranscriptLine(start=ev.get("tStartMs", 0) / 1000, text=text))
            if lines:
                return lines
    return []


def fetch_transcript(video_id: str, info: VideoInfo) -> list[TranscriptLine]:
    """Subtítulos de YouTube (sin clave); si el primer método falla, prueba con yt-dlp."""
    errors: list[str] = []
    for name, fn in (
        ("youtube-transcript-api", lambda: _via_transcript_api(video_id)),
        ("yt-dlp", lambda: _via_ytdlp_tracks(info)),
    ):
        try:
            lines = [ln for ln in fn() if ln.text.strip()]
            if lines:
                return lines
        except Exception as e:
            errors.append(f"{name}: {_short(e)}")
    raise VideoError(
        "No se pudo obtener la transcripción (el video puede no tener subtítulos "
        "o YouTube bloqueó la petición). " + " | ".join(errors)
    )
