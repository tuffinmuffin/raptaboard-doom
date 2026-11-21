"""Minimal WAD reader/writer plus helpers for palette-averaged texture colors."""
from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

import numpy as np


@dataclass
class Lump:
    name: str
    data: bytes


def read_wad(path: str | Path) -> tuple[str, list[Lump]]:
    raw = Path(path).read_bytes()
    ident, count, offset = struct.unpack_from("<4sii", raw, 0)
    lumps = []
    for i in range(count):
        pos, size, name = struct.unpack_from("<ii8s", raw, offset + 16 * i)
        lumps.append(Lump(name.rstrip(b"\0").decode("ascii", "replace").upper(), raw[pos : pos + size]))
    return ident.decode(), lumps


def write_wad(path: str | Path, lumps: list[Lump], ident: str = "PWAD") -> None:
    body = bytearray()
    directory = bytearray()
    pos = 12
    for lump in lumps:
        directory += struct.pack("<ii8s", pos, len(lump.data), lump.name.encode("ascii")[:8])
        body += lump.data
        pos += len(lump.data)
    header = struct.pack("<4sii", ident.encode(), len(lumps), pos)
    Path(path).write_bytes(header + bytes(body) + bytes(directory))


class Iwad:
    """Lookup of flats, wall textures and their average RGB colors."""

    def __init__(self, path: str | Path):
        _, self.lumps = read_wad(path)
        self.by_name = {}
        for lump in self.lumps:
            self.by_name.setdefault(lump.name, lump)
        pal = np.frombuffer(self.by_name["PLAYPAL"].data[:768], dtype=np.uint8).reshape(256, 3)
        self.palette = pal.astype(np.float64)

    def flats(self) -> dict[str, np.ndarray]:
        out, inside = {}, False
        for lump in self.lumps:
            if lump.name in ("F_START", "FF_START"):
                inside = True
            elif lump.name in ("F_END", "FF_END"):
                inside = False
            elif inside and len(lump.data) == 4096:
                idx = np.frombuffer(lump.data, dtype=np.uint8)
                out[lump.name] = self.palette[idx].mean(axis=0)
        return out

    def _patch_pixels(self, name: str) -> np.ndarray | None:
        lump = self.by_name.get(name)
        if lump is None or len(lump.data) < 8:
            return None
        data = lump.data
        width, _height = struct.unpack_from("<hh", data, 0)
        cols = struct.unpack_from(f"<{width}i", data, 8)
        pix = []
        for off in cols:
            while off < len(data) and data[off] != 0xFF:
                length = data[off + 1]
                pix.extend(data[off + 3 : off + 3 + length])
                off += length + 4
        return np.array(pix, dtype=np.uint8) if pix else None

    def textures(self) -> dict[str, np.ndarray]:
        pnames_data = self.by_name["PNAMES"].data
        (n,) = struct.unpack_from("<i", pnames_data, 0)
        pnames = [
            pnames_data[4 + 8 * i : 12 + 8 * i].rstrip(b"\0").decode().upper() for i in range(n)
        ]
        cache: dict[str, np.ndarray | None] = {}
        out = {}
        for tl in ("TEXTURE1", "TEXTURE2"):
            if tl not in self.by_name:
                continue
            data = self.by_name[tl].data
            (count,) = struct.unpack_from("<i", data, 0)
            offs = struct.unpack_from(f"<{count}i", data, 4)
            for off in offs:
                name = data[off : off + 8].rstrip(b"\0").decode().upper()
                (npatch,) = struct.unpack_from("<h", data, off + 20)
                pix = []
                for p in range(npatch):
                    _ox, _oy, pidx = struct.unpack_from("<hhh", data, off + 22 + 10 * p)
                    pname = pnames[pidx]
                    if pname not in cache:
                        cache[pname] = self._patch_pixels(pname)
                    if cache[pname] is not None:
                        pix.append(cache[pname])
                if pix:
                    out[name] = self.palette[np.concatenate(pix)].mean(axis=0)
        return out
