"""Transcode uploaded videos to a clean, browser-safe H.264/AAC MP4.

Some marketing-tool exports are valid files that desktop players (QuickTime) open
but browsers silently refuse to decode — they sit at readyState 0 with no error.
Re-encoding through ffmpeg to a plain High@yuv420p H.264 + AAC stream with
faststart guarantees in-browser playback.

ffmpeg comes from the bundled `imageio-ffmpeg` wheel (no system install needed),
with a fallback to any ffmpeg already on PATH.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Optional


def ffmpeg_exe() -> Optional[str]:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def available() -> bool:
    return ffmpeg_exe() is not None


def make_poster(src: Path, dst: Path, timeout: int = 60) -> bool:
    """Save a representative (non-black) frame of `src` as a JPEG at `dst`. Lets the
    library show a real still per video WITHOUT decoding video in 7 thumbnails at once."""
    exe = ffmpeg_exe()
    if not exe:
        return False
    src, dst = Path(src), Path(dst)
    tmp = dst.with_name(dst.stem + ".__p.jpg")
    try:
        proc = subprocess.run(
            [exe, "-y", "-i", str(src),
             "-vf", "thumbnail,scale=400:-2",   # picks a representative frame (skips black intros)
             "-frames:v", "1", "-q:v", "4", "-loglevel", "error", str(tmp)],
            capture_output=True, timeout=timeout,
        )
        if proc.returncode == 0 and tmp.exists() and tmp.stat().st_size > 0:
            tmp.replace(dst)
            return True
    except Exception:
        pass
    if tmp.exists():
        try:
            tmp.unlink()
        except Exception:
            pass
    return False


def transcode_to_web(src: Path, dst: Path, timeout: int = 300) -> bool:
    """Re-encode `src` into `dst` as a browser-safe MP4 (H.264 High / yuv420p +
    AAC, faststart). `src` and `dst` may be equal (writes via a temp first).
    Returns True on success (dst replaced), False otherwise (dst untouched)."""
    exe = ffmpeg_exe()
    if not exe:
        return False
    src, dst = Path(src), Path(dst)
    tmp = dst.with_name(dst.stem + ".__tc.mp4")
    try:
        proc = subprocess.run(
            [exe, "-y", "-i", str(src),
             "-map", "0:v:0", "-map", "0:a:0?",            # first video + audio if present
             "-c:v", "libx264", "-profile:v", "high", "-pix_fmt", "yuv420p",
             "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",   # libx264 requires even dimensions
             "-preset", "veryfast", "-crf", "20",
             "-c:a", "aac", "-b:a", "128k",
             "-movflags", "+faststart",
             "-loglevel", "error", str(tmp)],
            capture_output=True, timeout=timeout,
        )
        if proc.returncode == 0 and tmp.exists() and tmp.stat().st_size > 0:
            tmp.replace(dst)
            return True
    except Exception:
        pass
    if tmp.exists():
        try:
            tmp.unlink()
        except Exception:
            pass
    return False
