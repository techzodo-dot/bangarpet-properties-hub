"""Image processing: strips metadata (including GPS EXIF), resizes and
re-encodes uploads so only clean, web-optimised files are stored."""
import io
import uuid

from django.core.files.base import ContentFile
from PIL import Image, ImageOps, features

USE_WEBP = features.check("webp")
EXT = "webp" if USE_WEBP else "jpg"
FORMAT = "WEBP" if USE_WEBP else "JPEG"


def _encode(img, max_size, quality):
    img = img.copy()
    img.thumbnail(max_size, Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, FORMAT, quality=quality, optimize=True) if FORMAT == "JPEG" else img.save(buf, FORMAT, quality=quality, method=4)
    return buf.getvalue(), img.size


def process_upload(upload, max_size=(1920, 1440), thumb_size=(640, 480), quality=82):
    """Return (full_file, thumb_file, (width, height)) as ContentFiles."""
    upload.seek(0)
    with Image.open(upload) as src:
        img = ImageOps.exif_transpose(src)
        if img.mode not in ("RGB", "L"):
            background = Image.new("RGB", img.size, (255, 255, 255))
            if img.mode in ("RGBA", "LA", "P"):
                img = img.convert("RGBA")
                background.paste(img, mask=img.split()[-1])
                img = background
            else:
                img = img.convert("RGB")
        elif img.mode == "L":
            img = img.convert("RGB")
        full_bytes, size = _encode(img, max_size, quality)
        thumb_bytes, _ = _encode(img, thumb_size, quality - 4)
    token = uuid.uuid4().hex
    return (
        ContentFile(full_bytes, name=f"{token}.{EXT}"),
        ContentFile(thumb_bytes, name=f"{token}-thumb.{EXT}"),
        size,
    )
