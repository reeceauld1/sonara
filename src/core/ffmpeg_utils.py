"""Shared ffmpeg/ffprobe binary resolution and audio probing."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path


def resolve_binary(name: str) -> str:
    """Prefer a copy bundled next to the exe (see README), fall back to PATH."""
    exe_name = f"{name}.exe" if sys.platform == "win32" else name
    base_dir = Path(getattr(sys, "_MEIPASS", Path(sys.argv[0]).resolve().parent))
    bundled = base_dir / exe_name
    if bundled.exists():
        return str(bundled)
    return shutil.which(name) or name


FFMPEG = resolve_binary("ffmpeg")
FFPROBE = resolve_binary("ffprobe")


class ProbeError(RuntimeError):
    pass


def probe_duration_seconds(path: Path) -> float:
    result = subprocess.run(
        [
            FFPROBE, "-v", "error",
            "-show_entries", "format=duration",
            "-of", "json", str(path),
        ],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise ProbeError(f"ffprobe failed: {result.stderr.strip()}")
    data = json.loads(result.stdout or "{}")
    try:
        return float(data["format"]["duration"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ProbeError("could not determine audio duration") from exc
