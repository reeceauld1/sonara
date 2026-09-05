"""Dev runner: launches src/main.py and relaunches it whenever a .py file
under src/ changes. Stdlib only. Ctrl+C to quit.

Usage:  .venv\\Scripts\\python.exe dev.py
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WATCH_DIR = ROOT / "src"
ENTRY = WATCH_DIR / "main.py"
POLL_SECONDS = 0.5


def snapshot() -> dict[Path, float]:
    return {
        p: p.stat().st_mtime
        for p in WATCH_DIR.rglob("*.py")
        if "__pycache__" not in p.parts
    }


def launch() -> subprocess.Popen:
    print(f"[dev] starting {ENTRY.relative_to(ROOT)}")
    return subprocess.Popen([sys.executable, str(ENTRY)])


def main() -> int:
    proc = launch()
    state = snapshot()
    try:
        while True:
            time.sleep(POLL_SECONDS)

            if proc.poll() is not None:
                # App exited on its own (closed window / crash). Wait for an
                # edit before relaunching so we don't spin.
                print(f"[dev] app exited (code {proc.returncode}); waiting for changes")
                while snapshot() == state:
                    time.sleep(POLL_SECONDS)
                state = snapshot()
                proc = launch()
                continue

            current = snapshot()
            changed = [p for p in current if current.get(p) != state.get(p)]
            removed = [p for p in state if p not in current]
            if changed or removed:
                touched = ", ".join(
                    p.relative_to(WATCH_DIR).as_posix() for p in (changed + removed)
                )
                print(f"[dev] change detected ({touched}); restarting")
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                state = current
                proc = launch()
    except KeyboardInterrupt:
        print("\n[dev] stopping")
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
