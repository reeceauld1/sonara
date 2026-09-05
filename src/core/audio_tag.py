"""Mix a producer/anti-theft audio tag (e.g. "purchase your tracks today")
into a beat at a fixed bar interval, timed from BPM, so leaked or stolen
beats still carry the tag throughout."""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from core.ffmpeg_utils import FFMPEG, ProbeError, probe_duration_seconds

BEATS_PER_BAR = 4  # standard 4/4 time, which type beats are made in
TAG_GAIN_DB = -4.0  # keeps the tag audible without drowning out the beat


class AudioTagError(RuntimeError):
    pass


@dataclass
class AudioTagSpec:
    tag_path: str
    bpm: float
    bars_per_tag: int = 8
    second_tag_path: Optional[str] = None


def bar_interval_seconds(bpm: float, bars: int) -> float:
    if bpm <= 0:
        raise AudioTagError("BPM must be greater than 0.")
    if bars <= 0:
        raise AudioTagError("Bars per tag must be greater than 0.")
    return bars * BEATS_PER_BAR * 60.0 / bpm


def apply_audio_tag(
    audio_path: Path,
    tag_path: Path,
    bpm: float,
    bars_per_tag: int,
    out_path: Path,
    second_tag_path: Optional[Path] = None,
) -> Path:
    """Overlay `tag_path` onto `audio_path` at the end of every
    `bars_per_tag` bars (timed from `bpm`), repeated for the length of the
    track, and write the result as a WAV at `out_path`. The output duration
    always matches the original beat's duration.

    If `second_tag_path` is given, occurrences alternate between the two tags
    (end of bar 8 -> tag 1, bar 16 -> tag 2, bar 24 -> tag 1, ...); otherwise
    every occurrence plays `tag_path`."""
    audio_path = Path(audio_path).resolve()
    tag_path = Path(tag_path).resolve()
    out_path = Path(out_path).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    second_tag_path = Path(second_tag_path).resolve() if second_tag_path else None

    try:
        duration = probe_duration_seconds(audio_path)
    except ProbeError as exc:
        raise AudioTagError(str(exc)) from exc

    interval = bar_interval_seconds(bpm, bars_per_tag)

    delays_ms: list[int] = []
    t = interval
    while t < duration:
        delays_ms.append(int(round(t * 1000)))
        t += interval
    if not delays_ms:
        # Track is shorter than one interval — still tag it, right from the start.
        delays_ms = [0]

    # Occurrence 0 -> tag 1, occurrence 1 -> tag 2, occurrence 2 -> tag 1, ...
    # (all occurrences use tag 1 when there's no second tag).
    per_tag_delays: list[list[int]] = [[], []]
    for i, delay in enumerate(delays_ms):
        slot = (i % 2) if second_tag_path else 0
        per_tag_delays[slot].append(delay)

    cmd = [FFMPEG, "-y", "-i", str(audio_path), "-i", str(tag_path)]
    if second_tag_path:
        cmd += ["-i", str(second_tag_path)]

    filter_parts: list[str] = []
    mix_inputs = ["[0:a]"]
    delay_index = 0
    for tag_slot, delays in enumerate(per_tag_delays):
        if not delays:
            continue
        input_index = tag_slot + 1  # 1 = tag_path, 2 = second_tag_path
        split_labels = "".join(f"[s{tag_slot}_{i}]" for i in range(len(delays)))
        filter_parts.append(f"[{input_index}:a]asplit={len(delays)}{split_labels}")
        for i, delay in enumerate(delays):
            filter_parts.append(
                f"[s{tag_slot}_{i}]adelay={delay}|{delay},"
                f"volume={TAG_GAIN_DB}dB[d{delay_index}]"
            )
            mix_inputs.append(f"[d{delay_index}]")
            delay_index += 1
    filter_parts.append(
        f"{''.join(mix_inputs)}amix=inputs={len(mix_inputs)}:duration=first:normalize=0[aout]"
    )
    filter_complex = ";".join(filter_parts)

    cmd += ["-filter_complex", filter_complex, "-map", "[aout]", str(out_path)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise AudioTagError(result.stderr[-2000:])
    return out_path
