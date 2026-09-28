"""Pure-Python qt-faststart.

Marketing tools often export MP4s with the `moov` atom (the index: durations,
dimensions, codec config, chunk offsets) at the END of the file. Browsers then
can't begin playback until the whole file is fetched and parsed, so a `<video>`
just sits at readyState 0 — "uploads fine but won't play".

`fast_start()` rewrites such a file so `moov` sits right after `ftyp` (a
"web-optimized" / fast-start MP4), adjusting every stco/co64 chunk offset by the
amount `moov` moved. No ffmpeg needed. Best-effort: returns False and leaves the
file untouched if it's already fast-start or anything looks unexpected.
"""
from __future__ import annotations

import struct
from pathlib import Path

# Only these contain (eventually) the stco/co64 tables: moov→trak→mdia→minf→stbl.
_CONTAINERS = (b"moov", b"trak", b"mdia", b"minf", b"stbl")


def _atoms(data: bytes):
    """Yield (type, start, size, header_len) for each top-level atom."""
    i, n = 0, len(data)
    while i + 8 <= n:
        size = struct.unpack(">I", data[i:i + 4])[0]
        typ = data[i + 4:i + 8]
        header = 8
        if size == 1:
            if i + 16 > n:
                break
            size = struct.unpack(">Q", data[i + 8:i + 16])[0]
            header = 16
        elif size == 0:
            size = n - i
        if size < header or i + size > n:
            break
        yield typ, i, size, header
        i += size


def _patch_chunk_offsets(moov: bytearray, delta: int) -> None:
    """Add `delta` to every stco (32-bit) and co64 (64-bit) chunk offset in moov."""
    def walk(start, end):
        p = start
        while p + 8 <= end:
            size = struct.unpack(">I", moov[p:p + 4])[0]
            typ = moov[p + 4:p + 8]
            header = 8
            if size == 1:
                size = struct.unpack(">Q", moov[p + 8:p + 16])[0]
                header = 16
            elif size == 0:
                size = end - p
            if size < header or p + size > end:
                return
            aend = p + size
            if typ in _CONTAINERS:
                walk(p + header, aend)
            elif typ == b"stco":
                cnt = struct.unpack(">I", moov[p + header + 4:p + header + 8])[0]
                base = p + header + 8
                for k in range(cnt):
                    o = base + k * 4
                    v = struct.unpack(">I", moov[o:o + 4])[0]
                    struct.pack_into(">I", moov, o, (v + delta) & 0xFFFFFFFF)
            elif typ == b"co64":
                cnt = struct.unpack(">I", moov[p + header + 4:p + header + 8])[0]
                base = p + header + 8
                for k in range(cnt):
                    o = base + k * 8
                    v = struct.unpack(">Q", moov[o:o + 8])[0]
                    struct.pack_into(">Q", moov, o, v + delta)
            p = aend
    walk(0, len(moov))


def fast_start(path) -> bool:
    """Move moov before mdat if needed. Returns True iff the file was rewritten."""
    path = Path(path)
    try:
        data = path.read_bytes()
    except Exception:
        return False
    moov = mdat = None
    ftyp_end = 0
    for typ, start, size, _header in _atoms(data):
        if typ == b"ftyp":
            ftyp_end = start + size
        elif typ == b"moov":
            moov = (start, size)
        elif typ == b"mdat":
            mdat = (start, size)
    if not moov or not mdat or moov[0] < mdat[0]:
        return False  # missing atoms or already fast-start

    moov_start, moov_size = moov
    moov_bytes = bytearray(data[moov_start:moov_start + moov_size])
    _patch_chunk_offsets(moov_bytes, moov_size)

    rest = data[:moov_start] + data[moov_start + moov_size:]   # file minus moov
    new = rest[:ftyp_end] + bytes(moov_bytes) + rest[ftyp_end:]  # moov right after ftyp
    if len(new) != len(data):
        return False  # size must be preserved exactly

    path.write_bytes(new)
    return True
