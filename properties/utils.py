import re
from urllib.parse import parse_qs, urlparse

YOUTUBE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


def youtube_video_id(url):
    if not url:
        return None
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    vid = None
    if host in {"youtu.be"}:
        vid = parsed.path.lstrip("/").split("/")[0]
    elif host.endswith("youtube.com") or host.endswith("youtube-nocookie.com"):
        if parsed.path == "/watch":
            vid = parse_qs(parsed.query).get("v", [None])[0]
        elif parsed.path.startswith(("/embed/", "/shorts/", "/live/")):
            vid = parsed.path.split("/")[2] if len(parsed.path.split("/")) > 2 else None
    return vid if vid and YOUTUBE_ID_RE.match(vid) else None


def youtube_embed_url(url):
    vid = youtube_video_id(url)
    return f"https://www.youtube-nocookie.com/embed/{vid}?rel=0" if vid else None


def youtube_thumbnail(url):
    vid = youtube_video_id(url)
    return f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg" if vid else None
