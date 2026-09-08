"""Square thumbnail export: turns a crop/zoom result into a YouTube-ready
file at a fixed size."""
from __future__ import annotations

from pathlib import Path

from PIL import Image

OUTPUT_SIZES = (3000, 1500)
DEFAULT_OUTPUT_SIZE = 1500

# YouTube's thumbnails().set() rejects uploads over 2MB; quality is stepped
# down until the saved file clears that with margin.
JPEG_QUALITY = 92
MAX_UPLOAD_BYTES = 2 * 1024 * 1024


def export_square(image: Image.Image, size: int, out_path: Path) -> Path:
    """Resizes an already-square image to size x size and saves it as a JPEG
    under YouTube's 2MB thumbnail limit. Quality is stepped down first; on
    the rare source (e.g. pure noise) where even minimum quality can't clear
    the cap, the image itself is shrunk and re-tried."""
    working = image.convert("RGB").resize((size, size), Image.LANCZOS)
    for _ in range(12):
        quality = JPEG_QUALITY
        while True:
            working.save(out_path, format="JPEG", quality=quality)
            if out_path.stat().st_size <= MAX_UPLOAD_BYTES or quality <= 40:
                break
            quality -= 8
        if out_path.stat().st_size <= MAX_UPLOAD_BYTES:
            return out_path
        smaller = (max(1, round(working.width * 0.85)), max(1, round(working.height * 0.85)))
        working = working.resize(smaller, Image.LANCZOS)
    return out_path
