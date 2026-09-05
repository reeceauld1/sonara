"""Checks GitHub Releases for a newer Sonara build and, if the user accepts,
swaps the running .exe for it and relaunches.

Only works in the packaged .exe (PyInstaller onefile build) — self-replacing
a script run from source makes no sense, so `apply_update_and_restart` raises
if `sys.frozen` isn't set.

Publishing an update: bump APP_VERSION in core/version.py, build with
`pyinstaller build.spec`, then create a GitHub Release tagged e.g. "v1.0.1"
with dist/Sonara.exe attached as an asset (any name ending in .exe works).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from core.version import APP_VERSION

GITHUB_REPO = "reeceauld1/sonara"
_RELEASES_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
_USER_AGENT = "Sonara-Updater"

ProgressCallback = Optional[Callable[[float], None]]


class UpdateError(RuntimeError):
    pass


@dataclass
class UpdateInfo:
    version: str
    download_url: str
    notes: str


def _parse_version(text: str) -> tuple[int, ...]:
    text = text.strip().lstrip("vV")
    parts = []
    for chunk in text.split("."):
        digits = "".join(ch for ch in chunk if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts) or (0,)


def is_newer(remote_version: str, local_version: str = APP_VERSION) -> bool:
    return _parse_version(remote_version) > _parse_version(local_version)


def check_for_update(timeout: float = 10.0) -> Optional[UpdateInfo]:
    """Returns UpdateInfo if a newer release is published on GitHub, else None."""
    request = urllib.request.Request(
        _RELEASES_URL,
        headers={"Accept": "application/vnd.github+json", "User-Agent": _USER_AGENT},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None  # repo has no releases published yet
        raise UpdateError(f"GitHub API error: {exc.code} {exc.reason}") from exc
    except Exception as exc:  # noqa: BLE001 - network hiccups, DNS, etc.
        raise UpdateError(str(exc)) from exc

    tag = str(data.get("tag_name", "")).strip()
    if not tag or not is_newer(tag):
        return None

    asset = next(
        (a for a in data.get("assets", []) if a.get("name", "").lower().endswith(".exe")),
        None,
    )
    if asset is None:
        raise UpdateError(f"Release {tag} has no .exe asset to download.")

    return UpdateInfo(
        version=tag.lstrip("vV"),
        download_url=asset["browser_download_url"],
        notes=str(data.get("body", "")).strip(),
    )


def download_update(info: UpdateInfo, on_progress: ProgressCallback = None) -> Path:
    """Downloads the new exe to a fresh temp folder and returns its path."""
    tmp_dir = Path(tempfile.mkdtemp(prefix="sonara_update_"))
    dest = tmp_dir / "Sonara_new.exe"
    request = urllib.request.Request(info.download_url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=30) as response, open(dest, "wb") as out:
            total = int(response.headers.get("Content-Length", 0))
            read = 0
            while True:
                chunk = response.read(1 << 16)
                if not chunk:
                    break
                out.write(chunk)
                read += len(chunk)
                if on_progress and total > 0:
                    on_progress(min(read / total, 1.0))
    except Exception as exc:  # noqa: BLE001
        raise UpdateError(str(exc)) from exc
    return dest


_RELAUNCH_SCRIPT = """\
param(
    [int]$ProcId,
    [string]$NewExe,
    [string]$TargetExe
)
try { Wait-Process -Id $ProcId -ErrorAction SilentlyContinue } catch {}
Start-Sleep -Seconds 1
$backup = "$TargetExe.old"
for ($i = 0; $i -lt 15; $i++) {
    try {
        if (Test-Path $backup) { Remove-Item $backup -Force -ErrorAction SilentlyContinue }
        Move-Item -Path $TargetExe -Destination $backup -Force -ErrorAction Stop
        break
    } catch {
        Start-Sleep -Seconds 1
    }
}
Move-Item -Path $NewExe -Destination $TargetExe -Force
Start-Process -FilePath $TargetExe
Remove-Item $backup -Force -ErrorAction SilentlyContinue
Remove-Item -Path $MyInvocation.MyCommand.Path -Force -ErrorAction SilentlyContinue
"""


def apply_update_and_restart(new_exe_path: Path) -> None:
    """Schedules the running exe to be replaced by `new_exe_path` and
    relaunched once this process exits, then returns immediately — the
    caller must quit the application right after calling this."""
    if not getattr(sys, "frozen", False):
        raise UpdateError("Self-update only works in the packaged .exe, not from source.")

    target_exe = Path(sys.executable).resolve()
    script_path = new_exe_path.parent / "relaunch.ps1"
    script_path.write_text(_RELAUNCH_SCRIPT, encoding="utf-8")

    subprocess.Popen(
        [
            "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-WindowStyle", "Hidden", "-File", str(script_path),
            "-ProcId", str(os.getpid()),
            "-NewExe", str(new_exe_path),
            "-TargetExe", str(target_exe),
        ],
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        close_fds=True,
    )
