"""Combine a still image and an audio track into an MP4 suitable for YouTube upload."""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from core.ffmpeg_utils import FFMPEG, ProbeError, probe_duration_seconds

ProgressCallback = Optional[Callable[[float], None]]


class VideoBuildError(RuntimeError):
    pass


def _probe_duration_seconds(path: Path) -> float:
    try:
        return probe_duration_seconds(path)
    except ProbeError as exc:
        raise VideoBuildError(str(exc)) from exc


_TIME_RE = re.compile(r"time=(\d+):(\d+):(\d+\.\d+)")

SQUARE_SIZE = 1080
CANVAS_WIDTH, CANVAS_HEIGHT = 1920, 1080

# Fit the source image into a SQUARE_SIZE square, preserving its aspect ratio
# and padding whatever that leaves (top/bottom for wide images, left/right for
# tall ones) with black, then pillarbox that square into the standard 16:9
# canvas — a 1080x1080 square centered in the frame with black on each side.
SQUARE_PAD_FILTER = (
    f"scale={SQUARE_SIZE}:{SQUARE_SIZE}:force_original_aspect_ratio=decrease,"
    f"pad={SQUARE_SIZE}:{SQUARE_SIZE}:(ow-iw)/2:(oh-ih)/2:color=black,"
    f"pad={CANVAS_WIDTH}:{CANVAS_HEIGHT}:(ow-iw)/2:(oh-ih)/2:color=black"
)

WATERMARK_MIN_SIZE = 8
WATERMARK_MAX_SIZE = 40
WATERMARK_MARGIN = 30
WATERMARK_CORNERS = ("top-left", "top-right", "bottom-left", "bottom-right")

_FONT_CANDIDATES = [
    Path(r"C:\Windows\Fonts\segoeui.ttf"),
    Path(r"C:\Windows\Fonts\arial.ttf"),
]


@dataclass
class Watermark:
    text: str
    corner: str = "bottom-right"  # one of WATERMARK_CORNERS
    size: int = 16  # font size in px on the 1920x1080 canvas, clamped to [8, 24]


def _watermark_font() -> Optional[Path]:
    for candidate in _FONT_CANDIDATES:
        if candidate.exists():
            return candidate
    return None


def _escape_drawtext(text: str) -> str:
    text = text.replace("\\", "\\\\").replace(":", "\\:").replace("%", "\\%")
    # Sidestep ffmpeg's fragile single-quote-inside-single-quote escaping.
    return text.replace("'", "’")


def _watermark_position(corner: str) -> tuple[str, str]:
    margin = WATERMARK_MARGIN
    x = f"w-text_w-{margin}" if "right" in corner else str(margin)
    y = f"h-text_h-{margin}" if "bottom" in corner else str(margin)
    return x, y


def _watermark_filter(watermark: Optional[Watermark]) -> tuple[str, Optional[Path]]:
    """Returns (filter graph suffix, working directory the ffmpeg subprocess
    must run from). Windows font paths like "C:/Windows/Fonts/x.ttf" break
    drawtext's own colon parsing no matter how the colon is escaped/quoted —
    tested directly against ffmpeg and confirmed. The reliable fix is to run
    ffmpeg with its cwd set to the font's folder and reference it by filename
    only, avoiding the drive-letter colon entirely."""
    if watermark is None or not watermark.text.strip():
        return "", None
    size = max(WATERMARK_MIN_SIZE, min(WATERMARK_MAX_SIZE, watermark.size))
    corner = watermark.corner if watermark.corner in WATERMARK_CORNERS else "bottom-right"
    x_expr, y_expr = _watermark_position(corner)
    text = _escape_drawtext(watermark.text.strip())
    font = _watermark_font()
    font_clause = f"fontfile={font.name}:" if font else ""
    font_dir = font.parent if font else None
    filter_str = (
        f",drawtext={font_clause}text='{text}':fontsize={size}:fontcolor=white:"
        f"shadowcolor=black@0.6:shadowx=1:shadowy=1:x={x_expr}:y={y_expr}"
    )
    return filter_str, font_dir


def build_video(
    image_path: Path,
    audio_path: Path,
    out_path: Path,
    watermark: Optional[Watermark] = None,
    on_progress: ProgressCallback = None,
) -> Path:
    """Render a static-image video with the given audio track, writing an H.264/AAC MP4.

    on_progress receives a float in [0, 1] as ffmpeg reports encoding time.
    """
    image_path = Path(image_path).resolve()
    audio_path = Path(audio_path).resolve()
    out_path = Path(out_path).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    duration = _probe_duration_seconds(audio_path)
    watermark_suffix, font_cwd = _watermark_filter(watermark)
    vf = SQUARE_PAD_FILTER + watermark_suffix

    cmd = [
        FFMPEG, "-y",
        "-loop", "1", "-i", str(image_path),
        "-i", str(audio_path),
        "-c:v", "libx264", "-tune", "stillimage",
        "-c:a", "aac", "-b:a", "192k",
        "-pix_fmt", "yuv420p",
        "-vf", vf,
        "-shortest",
        "-movflags", "+faststart",
        "-progress", "pipe:1", "-nostats",
        str(out_path),
    ]

    # image_path/audio_path/out_path are resolved to absolute above, so
    # changing cwd to the font's folder (needed so drawtext's fontfile can be
    # a bare filename, sidestepping the Windows drive-letter colon parsing
    # issue) doesn't affect how those paths resolve.
    process = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1, cwd=str(font_cwd) if font_cwd else None,
    )

    stderr_lines: list[str] = []
    assert process.stdout is not None
    for line in process.stdout:
        stderr_lines.append(line)
        match = _TIME_RE.search(line)
        if match and on_progress and duration > 0:
            h, m, s = match.groups()
            elapsed = int(h) * 3600 + int(m) * 60 + float(s)
            on_progress(min(elapsed / duration, 1.0))

    process.wait()
    if process.returncode != 0:
        raise VideoBuildError("".join(stderr_lines[-30:]))

    if on_progress:
        on_progress(1.0)
    return out_path
