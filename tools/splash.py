#!/usr/bin/env python3
"""The raptaboard plugin's title card: the KitBots and the hunter, rendered
from their CAD like the game's sprites (tools/robot_sprites.py) but larger and
in full color, under the title, with "click to play".

    python3 tools/splash.py            # build/splash.rgba and build/splash.png

The plugin shows build/splash.rgba (copied into build/plugin by `make plugin`)
until its tab is clicked, and again after Quit. Its format: width and height
as little-endian u32, then RGBA rows, top first.
"""
from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent))
from robot_sprites import Cloud, hunter_eyes, load_robot, oil_damage, render  # noqa: E402

W, H = 640, 400
FLOOR_Y = 318       # where the robots stand, in pixels from the top


def background() -> np.ndarray:
    """Night-blue fading to black, and the field carpet's gray below."""
    img = np.zeros((H, W, 3))
    t = np.linspace(0, 1, FLOOR_Y)[:, None]
    img[:FLOOR_Y] = (np.array([0.05, 0.08, 0.20]) * (1 - t) + np.array([0.01, 0.01, 0.03]) * t)[:, None, :]
    floor = np.linspace(0, 1, H - FLOOR_Y)[:, None]
    img[FLOOR_Y:] = (np.array([0.22, 0.23, 0.25]) * (1 - floor * 0.6))[:, None, :]
    # A white field line across the carpet.
    img[FLOOR_Y + 22 : FLOOR_Y + 25] = 0.75
    return img


def paste(canvas: np.ndarray, rgba: np.ndarray, cx: int, floor_y: int) -> None:
    """Put a rendered robot (rgba uint8) centered on `cx`, its feet on `floor_y`."""
    a = rgba[..., 3:4] / 255.0
    rows = np.where(a[..., 0].max(axis=1) > 0)[0]
    if len(rows) == 0:
        return
    bottom = rows.max()
    h, w = rgba.shape[:2]
    top, left = floor_y - bottom, cx - w // 2
    y0, x0 = max(top, 0), max(left, 0)
    y1, x1 = min(top + h, H), min(left + w, W)
    src = rgba[y0 - top : y1 - top, x0 - left : x1 - left]
    alpha = src[..., 3:4] / 255.0
    canvas[y0:y1, x0:x1] = canvas[y0:y1, x0:x1] * (1 - alpha) + src[..., :3] / 255.0 * alpha


def text(draw: ImageDraw.ImageDraw, xy, s: str, size: int, fill, outline=(0, 0, 0), width=3):
    font = ImageFont.load_default(size=size)
    draw.text(xy, s, font=font, fill=fill, anchor="mm", stroke_width=width, stroke_fill=outline)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="build/splash.rgba")
    ap.add_argument("--preview", default="build/splash.png")
    ap.add_argument("--points", type=int, default=900_000)
    ap.add_argument("--seed", type=int, default=6328)
    args = ap.parse_args()
    rng = np.random.default_rng(args.seed)

    canvas = background()
    # Left and right: the 2024 and 2025 KitBots, turned toward the middle.
    for folder, cx, theta in [("downloads/kitbot2024", 118, 40.0), ("downloads/kitbot2025", 522, -40.0)]:
        cloud = load_robot(Path(folder), args.points, rng)
        rgba, _ = render(cloud, theta, ppm=165.0)
        paste(canvas, rgba, cx, FLOOR_Y)
    # The middle: the hunter (the 2026 KitBot, gunmetal and oil-stained, red eyes).
    hunter = oil_damage(load_robot(Path("downloads/kitbot2026"), args.points, rng), seed=26, blots=24)
    hunter = Cloud(hunter.p, hunter.n, hunter.c * np.array([0.82, 0.82, 0.88]))
    rgba, _ = render(hunter, 10.0, ppm=245.0, flashes=hunter_eyes(hunter), extent_m=1.7)
    paste(canvas, rgba, W // 2, FLOOR_Y + 14)

    img = Image.fromarray((np.clip(canvas, 0, 1) * 255).astype(np.uint8), "RGB").convert("RGBA")
    draw = ImageDraw.Draw(img)
    text(draw, (W // 2, 52), "REBUILT", 74, (220, 40, 30), width=4)
    text(draw, (W // 2, 102), "the FRC 2026 field, in Doom", 20, (230, 230, 230), width=2)
    text(draw, (W // 2, 360), "CLICK TO PLAY", 30, (255, 210, 60), width=3)
    text(draw, (W // 2, 386), "Shift+Esc gives back the keyboard and mouse", 13, (190, 190, 190), width=1)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(struct.pack("<II", W, H) + img.tobytes())
    if args.preview:
        img.save(args.preview)
    print(f"[splash] wrote {out} ({W}x{H}) and {args.preview}", file=sys.stderr)


if __name__ == "__main__":
    main()
