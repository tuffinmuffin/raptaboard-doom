#!/usr/bin/env python3
"""Pixel-art robot faces for Doom's status bar (replaces the STF* "Doomguy" faces).

A KitBot-style head: metal shell, antenna LED, visor with two LED eyes, an LED mouth
grille and a blue bumper collar. Five damage levels (dents, oil, a cracked visor,
sparks, a dead eye) times every expression the status bar uses: look straight
(3 eye positions), turn left/right, ouch, evil grin, rampage, god mode and dead.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from robot_sprites import encode_patch, quantize  # noqa: E402
from wadlib import Lump, read_wad, write_wad  # noqa: E402

W, H = 24, 30
LEFT, TOP = -5, -1          # status-bar placement offsets (like the IWAD faces)

OUTLINE = (34, 37, 45)
METAL_HI = (176, 180, 190)
METAL = (138, 143, 154)
METAL_LO = (100, 104, 114)
METAL_DK = (72, 75, 84)
VISOR = (18, 20, 28)
BUMPER = (32, 54, 175)
BUMPER_DK = (18, 30, 110)
OIL = (14, 13, 18)
SPARK = (255, 236, 120)
EYE_BY_PAIN = [(90, 255, 140), (180, 255, 90), (255, 220, 70), (255, 140, 40), (255, 55, 40)]
GOLD = (255, 210, 60)


class Face:
    def __init__(self):
        self.rgb = np.zeros((H, W, 3), float)
        self.a = np.zeros((H, W), bool)

    def px(self, x, y, c):
        if 0 <= x < W and 0 <= y < H:
            self.rgb[y, x] = c
            self.a[y, x] = True

    def rect(self, x0, y0, x1, y1, c):  # inclusive
        for y in range(y0, y1 + 1):
            for x in range(x0, x1 + 1):
                self.px(x, y, c)

    def clear(self, x, y):
        if 0 <= x < W and 0 <= y < H:
            self.a[y, x] = False

    def dim(self, x, y, k):
        if 0 <= x < W and 0 <= y < H and self.a[y, x]:
            self.rgb[y, x] *= k


def head(f: Face, turn: int, pain: int, antenna=None, dead=False):
    """Shell, ears, visor and collar. turn: -1 left, 0 front, +1 right."""
    x0, x1, y0, y1 = 3, 20, 4, 24
    # antenna (bent when dead)
    ax = 12 + turn
    if dead:
        for i, (x, y) in enumerate([(ax, 3), (ax + 1, 2), (ax + 2, 2), (ax + 3, 1)]):
            f.px(x, y, METAL_LO)
    else:
        f.rect(ax, 1, ax, 3, METAL_LO)
        f.px(ax, 0, antenna or EYE_BY_PAIN[pain])
    # shell with rounded corners, light from the upper left
    f.rect(x0, y0, x1, y1, METAL)
    for y in range(y0, y1 + 1):
        f.px(x0, y, METAL_HI)
        f.px(x0 + 1, y, METAL_HI)
        f.px(x1, y, METAL_LO)
        f.px(x1 - 1, y, METAL_LO)
    f.rect(x0 + 1, y0, x1 - 1, y0, METAL_HI)
    for x, y in [(x0, y0), (x1, y0), (x0, y1), (x1, y1)]:
        f.px(x, y, OUTLINE)
    # turned heads show one side darker and push the features over
    if turn:
        side = range(x0, x0 + 3) if turn > 0 else range(x1 - 2, x1 + 1)
        for x in side:
            for y in range(y0 + 1, y1):
                f.px(x, y, METAL_DK)
    # ear bolts
    for ex in (x0 - 1, x1 + 1):
        f.rect(ex, 12, ex, 15, METAL_LO)
        f.px(ex, 13, OUTLINE)
    # visor
    vx = turn * 2
    f.rect(5 + vx, 8, 18 + vx, 14, VISOR)
    f.rect(5 + vx, 15, 18 + vx, 15, METAL_LO)
    # mouth grille slot
    f.rect(7 + vx, 18, 16 + vx, 21, (40, 43, 52))
    # bumper collar with dotted "KIT BOT" lettering
    f.rect(2, 25, 21, 29, BUMPER)
    f.rect(2, 29, 21, 29, BUMPER_DK)
    f.rect(2, 25, 21, 25, (70, 95, 220))
    for x in range(5, 19):
        if x not in (11, 12) and x % 2:
            f.px(x, 27, (210, 215, 230))
    return vx


def eyes(f: Face, vx, look, color, kind="normal"):
    lx, rx, y = 7 + vx + look, 14 + vx + look, 10
    hot = tuple(min(255, c + 90) for c in color)
    for ex in (lx, rx):
        if kind == "normal":
            f.rect(ex, y, ex + 2, y + 2, color)
            f.px(ex + 1, y + 1, hot)
        elif kind == "wide":   # ouch
            f.rect(ex - 1, y - 1, ex + 3, y + 3, color)
            f.rect(ex, y, ex + 2, y + 2, (255, 255, 235))
        elif kind == "narrow":  # alarmed / turned
            f.rect(ex, y + 1, ex + 2, y + 2, color)
            f.px(ex + 1, y + 1, hot)
    if kind == "evil":    # slanted slits, low toward the middle
        for i in range(3):
            f.px(lx + i, y + i // 2, color)
            f.px(lx + i, y + 1 + i // 2, color)
            f.px(rx + 2 - i, y + i // 2, color)
            f.px(rx + 2 - i, y + 1 + i // 2, color)
    if kind == "angry":   # brows cut down toward the middle
        f.rect(lx, y, lx + 2, y + 2, color)
        f.rect(rx, y, rx + 2, y + 2, color)
        f.px(lx + 2, y, VISOR)
        f.px(lx + 1, y, VISOR)
        f.px(lx + 2, y + 1, VISOR)
        f.px(rx, y, VISOR)
        f.px(rx + 1, y, VISOR)
        f.px(rx, y + 1, VISOR)


def mouth(f: Face, vx, color, kind="smile"):
    x0 = 7 + vx
    dim = tuple(int(c * 0.75) for c in color)
    if kind == "smile":
        for x, y in [(1, 18), (2, 19), (3, 20), (4, 20), (5, 20), (6, 20), (7, 19), (8, 18)]:
            f.px(x0 + x, y, dim)
    elif kind == "flat":
        f.rect(x0 + 2, 20, x0 + 7, 20, dim)
    elif kind == "frown":
        for x, y in [(1, 21), (2, 20), (3, 19), (4, 19), (5, 19), (6, 19), (7, 20), (8, 21)]:
            f.px(x0 + x, y, dim)
    elif kind == "o":
        f.rect(x0 + 3, 18, x0 + 6, 21, dim)
        f.rect(x0 + 4, 19, x0 + 5, 20, (40, 43, 52))
    elif kind == "grin":      # evil zigzag
        for x in range(0, 10):
            f.px(x0 + x, 19 + (x % 2), color)
        f.px(x0 - 1, 18, color)
        f.px(x0 + 10, 18, color)
    elif kind == "grit":      # rampage: teeth grid
        f.rect(x0, 19, x0 + 9, 20, dim)
        for x in range(0, 10, 2):
            f.rect(x0 + x, 18, x0 + x, 21, color)


def damage(f: Face, pain: int, vx: int, rng):
    """Progressive wear: dents, oil, a cracked visor, sparks, a dead eye."""
    if pain >= 1:   # forehead dent + oil drip from the top left
        for x, y in [(8, 5), (9, 6), (10, 5)]:
            f.dim(x, y, 0.6)
        for y in range(4, 8):
            f.px(5, y, OIL)
    if pain >= 2:   # scratch across the cheek + oil streak under the visor
        for i in range(5):
            f.dim(15 + i // 2 + vx, 16 + i, 0.55)
        for y in range(16, 22):
            f.px(6 + vx, y, OIL)
    if pain >= 3:   # cracked visor and a spark at the ear
        for x, y in [(10, 8), (11, 9), (11, 10), (12, 11), (13, 12), (13, 13), (14, 14)]:
            f.px(x + vx, y, (120, 128, 140))
        f.px(21, 11, SPARK)
        f.px(22, 10, SPARK)
    if pain >= 4:   # chunk out of the head corner, more oil
        for x, y in [(19, 4), (20, 4), (20, 5), (19, 5), (20, 6)]:
            f.clear(x, y)
        f.px(18, 5, OUTLINE)
        f.px(19, 6, OUTLINE)
        for y in range(9, 18):
            f.px(17 + vx, y, OIL)


def build(kind: str, pain: int = 0, look: int = 0, rng=None) -> Face:
    rng = rng or np.random.default_rng(0)
    f = Face()
    color = EYE_BY_PAIN[pain]
    turn = {"tr": 1, "tl": -1}.get(kind, 0)
    if kind == "dead":
        vx = head(f, 0, 4, dead=True)
        damage(f, 4, vx, rng)
        for ex in (7, 14):   # X eyes
            for i in range(3):
                f.px(ex + i, 10 + i, (130, 30, 30))
                f.px(ex + 2 - i, 10 + i, (130, 30, 30))
        for y in range(13, 20):
            f.px(8, y, OIL)
            f.px(15, y + 1, OIL)
        f.rgb[:24] *= np.array([0.7, 0.7, 0.75])
        return f
    if kind == "god":
        vx = head(f, 0, 0, antenna=GOLD)
        eyes(f, vx, 0, GOLD)
        for ex in (7, 14):   # halo glow around the eyes
            for x, y in [(ex - 1, 11), (ex + 3, 11), (ex + 1, 9), (ex + 1, 13)]:
                f.px(x, y, (200, 150, 40))
        mouth(f, vx, GOLD, "smile")
        f.rect(2, 25, 21, 25, GOLD)
        return f
    vx = head(f, turn, pain)
    if kind == "st":
        eyes(f, vx, look, color)
        mouth(f, vx, color, ["smile", "smile", "flat", "flat", "frown"][pain])
    elif kind in ("tr", "tl"):
        eyes(f, vx, turn, color, "narrow")
        mouth(f, vx, color, "flat" if pain < 3 else "frown")
    elif kind == "ouch":
        eyes(f, vx, 0, color, "wide")
        mouth(f, vx, color, "o")
        for x, y in [(1, 6), (22, 7), (0, 18), (23, 19), (2, 2)]:
            f.px(x, y, SPARK)
    elif kind == "evl":
        eyes(f, vx, 0, (255, 70, 150), "evil")
        mouth(f, vx, (255, 70, 150), "grin")
    elif kind == "kill":
        eyes(f, vx, 0, (255, 40, 30), "angry")
        mouth(f, vx, (255, 40, 30), "grit")
        for x, y in [(1, 3), (2, 2), (21, 3), (22, 2)]:   # steam
            f.px(x, y, (200, 200, 205))
    damage(f, pain, vx, rng)
    # the dead eye at the last damage level flickers dark
    if pain == 4 and kind in ("st", "tr", "tl", "ouch"):
        rx = 14 + vx + (look if kind == "st" else 0)
        f.rect(rx, 10, rx + 2, 12, (70, 18, 18))
    return f


def all_faces() -> dict[str, Face]:
    faces = {}
    for p in range(5):
        for s in range(3):
            faces[f"STFST{p}{s}"] = build("st", p, look=[1, 0, -1][s])
        faces[f"STFTR{p}0"] = build("tr", p)
        faces[f"STFTL{p}0"] = build("tl", p)
        faces[f"STFOUCH{p}"] = build("ouch", p)
        faces[f"STFEVL{p}"] = build("evl", p)
        faces[f"STFKILL{p}"] = build("kill", p)
    faces["STFGOD0"] = build("god")
    faces["STFDEAD0"] = build("dead")
    return faces


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--iwad", default="downloads/freedoom/freedoom2.wad")
    ap.add_argument("--out", default="build/face.wad")
    ap.add_argument("--preview", default="build/face_preview.png")
    args = ap.parse_args()
    _, iw = read_wad(args.iwad)
    palette = np.frombuffer(next(l.data for l in iw if l.name == "PLAYPAL")[:768], np.uint8).reshape(256, 3).astype(float)
    iwad_faces = {l.name for l in iw if l.name.startswith("STF") and not l.name.startswith("STFB")}
    faces = all_faces()
    missing = iwad_faces - faces.keys()
    assert not missing, f"faces not generated: {sorted(missing)}"
    lumps, imgs = [], {}
    for name, f in faces.items():
        rgba = np.concatenate([np.clip(f.rgb, 0, 255), f.a[..., None] * 255.0], 2).astype(np.uint8)
        idx = quantize(rgba, palette)
        lumps.append(Lump(name, encode_patch(idx, f.a, LEFT, TOP)))
        imgs[name] = (palette[idx].astype(np.uint8), f.a)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    write_wad(args.out, lumps)
    print(f"[robot_face] wrote {args.out} ({len(lumps)} faces)", file=sys.stderr)
    if args.preview:
        from PIL import Image
        order = [[f"STFST{p}{s}" for s in range(3)] + [f"STFTL{p}0", f"STFTR{p}0", f"STFOUCH{p}", f"STFEVL{p}", f"STFKILL{p}"] for p in range(5)]
        order.append(["STFGOD0", "STFDEAD0"])
        sc, pad = 4, 4
        sheet = Image.new("RGB", (8 * (W * sc + pad), 6 * (H * sc + pad)), (60, 52, 40))
        for r, row in enumerate(order):
            for c, nm in enumerate(row):
                rgb, a = imgs[nm]
                im = Image.fromarray(np.concatenate([rgb, (a * 255).astype(np.uint8)[..., None]], 2), "RGBA")
                im = im.resize((W * sc, H * sc), Image.NEAREST)
                sheet.paste(im, (c * (W * sc + pad), r * (H * sc + pad)), im)
        sheet.save(args.preview)
        print(f"[robot_face] preview {args.preview}", file=sys.stderr)


if __name__ == "__main__":
    main()
