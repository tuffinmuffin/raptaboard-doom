#!/usr/bin/env python3
"""Render FRC robot GLBs (AdvantageScope format) into Doom monster sprites.

Each replaced monster keeps the IWAD's exact sprite lump names (including mirrored
pairs like SPOSA2A8), so the PWAD overrides them one-for-one. Frames are rendered
by point splatting: sample a dense colored point cloud on the CAD surfaces, then
orthographically project it with a z-buffer from each Doom rotation, at 3x
supersampling, and quantize to the IWAD palette.

Frame roles per sprite (vanilla state tables):
  A-D walk, E-F attack, G pain (8 rotations each), then death / gib frames (rotation 0).
"""
from __future__ import annotations

import argparse
import json
import math
import struct
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import trimesh

sys.path.insert(0, str(Path(__file__).parent))
from wadlib import Lump, read_wad, write_wad  # noqa: E402

SS = 3                 # supersampling factor
PITCH_DEG = 14.0       # camera looks slightly down, like Doom's hand-drawn sprites

# Which IWAD sprite gets which robot, and how its frames are used.
#   attack: frames shown while attacking; flash: the subset with a muzzle flash
#   death: normal death frames (rot 0); the remaining rot-0 frames are the gib death
@dataclass
class Monster:
    prefix: str
    robot: str
    flash: str
    death: str


MONSTERS = [
    Monster("POSS", "downloads/kitbot2026", flash="F", death="HIJKL"),     # zombieman
    Monster("SPOS", "downloads/kitbot2025", flash="F", death="HIJKL"),     # shotgunner
    Monster("CPOS", "downloads/kitbot2024", flash="EF", death="HIJKLMN"),  # chaingunner
]


def log(*a):
    print("[robot_sprites]", *a, file=sys.stderr, flush=True)


# ---------------------------------------------------------------- robot loading
def axis_rot(axis: str, deg: float) -> np.ndarray:
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return {
        "x": np.array([[1, 0, 0], [0, c, -s], [0, s, c]]),
        "y": np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]]),
        "z": np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]]),
    }[axis]


@dataclass
class Cloud:
    p: np.ndarray   # (N,3) points, robot frame: +x forward, +z up, origin at floor center
    n: np.ndarray   # (N,3) unit normals
    c: np.ndarray   # (N,3) colors 0..1


def load_robot(folder: Path, n_points: int, rng) -> Cloud:
    cfg = json.loads((folder / "config.json").read_text())
    R = np.eye(3)
    for r in cfg.get("rotations", []):
        R = axis_rot(r["axis"], r["degrees"]) @ R      # applied in order, world axes
    pos = np.asarray(cfg.get("position", [0, 0, 0]), dtype=float)
    scene = trimesh.load(folder / "model.glb", force="scene")
    tris, cols = [], []
    for node in scene.graph.nodes_geometry:
        T, g = scene.graph[node]
        m = scene.geometry[g]
        if len(m.faces) == 0:
            continue
        v = trimesh.transform_points(m.vertices, T) @ R.T + pos
        try:
            vc = np.asarray(m.visual.to_color().vertex_colors, dtype=float)[:, :3] / 255.0
            fc = vc[m.faces].mean(axis=1)
        except Exception:
            mat = getattr(m.visual, "material", None)
            bcf = getattr(mat, "baseColorFactor", None)
            base = np.asarray(bcf[:3], dtype=float) / 255.0 if bcf is not None else np.full(3, 0.5)
            fc = np.repeat(base[None], len(m.faces), axis=0)
        tris.append(v[m.faces])
        cols.append(fc)
    tris = np.concatenate(tris)
    cols = np.concatenate(cols)
    # recenter: floor at z=0, footprint centered on the origin
    lo, hi = tris.reshape(-1, 3).min(0), tris.reshape(-1, 3).max(0)
    tris = tris - np.array([(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, lo[2]])
    nrm = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    area = np.linalg.norm(nrm, axis=1)
    ok = area > 1e-12
    tris, cols, nrm, area = tris[ok], cols[ok], nrm[ok] / area[ok, None], area[ok]
    face = rng.choice(len(tris), size=n_points, p=area / area.sum())
    r1, r2 = rng.random(n_points), rng.random(n_points)
    s = np.sqrt(r1)
    bary = np.stack([1 - s, s * (1 - r2), s * r2], axis=1)
    pts = np.einsum("ni,nij->nj", bary, tris[face])
    ext = tris.reshape(-1, 3).max(0) - tris.reshape(-1, 3).min(0)
    log(f"{folder.name}: {len(tris):,} faces, size {ext.round(2).tolist()} m")
    return Cloud(pts, nrm[face], np.clip(cols[face], 0, 1))


# ---------------------------------------------------------------- rendering
def camera(theta_deg: float):
    """Unit vectors for an orthographic camera at azimuth theta (CCW from robot +x)."""
    th, ph = math.radians(theta_deg), math.radians(PITCH_DEG)
    eye = np.array([math.cos(th) * math.cos(ph), math.sin(th) * math.cos(ph), math.sin(ph)])
    d = -eye                                   # view direction
    right = np.cross(d, [0, 0, 1.0])
    right /= np.linalg.norm(right)
    up = np.cross(right, d)
    return d, right, up


def render(cloud: Cloud, theta: float, ppm: float, flashes=(), extent_m=1.6):
    """Return (rgba uint8 image, origin_px) for one view. flashes: [(center xyz, radius m)]."""
    d, right, up = camera(theta)
    s = ppm * SS
    W = H = int(extent_m * s)
    ox, oy = W / 2, H * 0.72
    p, n, c = cloud.p, cloud.n, cloud.c
    u = p @ right
    v = p @ up
    depth = p @ d
    x = np.floor(ox + u * s).astype(np.int64)
    y = np.floor(oy - v * s).astype(np.int64)
    # two-sided lambert, light from the upper left of the viewer
    nn = np.where(((n @ d) > 0)[:, None], -n, n)
    L = -d + 0.7 * up - 0.5 * right
    L /= np.linalg.norm(L)
    shade = 0.38 + 0.62 * np.clip(nn @ L, 0, 1)
    rgb = np.clip(c * shade[:, None], 0, 1)

    zbuf = np.full(W * H, np.inf)
    img = np.zeros((W * H, 3))
    for dx, dy in ((0, 0), (1, 0), (0, 1), (1, 1)):    # 2x2 splats close sampling gaps
        xx, yy = x + dx, y + dy
        ok = (xx >= 0) & (xx < W) & (yy >= 0) & (yy < H)
        idx = yy[ok] * W + xx[ok]
        dep = depth[ok] + 1e-4 * (dx + dy)
        np.minimum.at(zbuf, idx, dep)
        win = dep <= zbuf[idx]
        img[idx[win]] = rgb[ok][win]

    # muzzle flashes / fire: discs with a hot core, depth-tested against the robot
    yy, xx = np.mgrid[0:H, 0:W]
    for center, radius, color in flashes:
        cu, cv, cd = center @ right, center @ up, center @ d
        px, py = ox + cu * s, oy - cv * s
        r = radius * s
        dist = np.hypot(xx - px, yy - py).ravel()
        hit = (dist < r) & (cd - radius < zbuf)
        core = dist < r * 0.45
        zbuf[hit] = cd - radius
        img[hit] = color
        if color[0] > 0.8 and color[1] > 0.4:  # hot: yellow core
            img[hit & core] = (1.0, 0.92, 0.55)

    cov = np.isfinite(zbuf).reshape(H, W)
    img = img.reshape(H, W, 3)
    # downsample SSxSS: alpha by coverage, color = mean of covered subpixels
    h, w = H // SS, W // SS
    cov_b = cov[: h * SS, : w * SS].reshape(h, SS, w, SS)
    img_b = img[: h * SS, : w * SS].reshape(h, SS, w, SS, 3)
    cnt = cov_b.sum(axis=(1, 3))
    col = (img_b * cov_b[..., None]).sum(axis=(1, 3)) / np.maximum(cnt, 1)[..., None]
    alpha = cnt >= (SS * SS) * 0.4
    rgba = np.concatenate([np.clip(col * 255, 0, 255), alpha[..., None] * 255.0], axis=2).astype(np.uint8)
    return rgba, (ox / SS, oy / SS)


# ---------------------------------------------------------------- poses
def transformed(cloud: Cloud, R=np.eye(3), t=np.zeros(3), tint=None, tint_amt=0.0, darken=1.0):
    p = cloud.p @ R.T + t
    n = cloud.n @ R.T
    c = cloud.c * darken
    if tint is not None:
        c = c * (1 - tint_amt) + np.asarray(tint) * tint_amt
    return Cloud(p, n, np.clip(c, 0, 1))


def robot_dims(cloud: Cloud):
    lo, hi = cloud.p.min(0), cloud.p.max(0)
    return lo, hi


def fire_particles(rng, center, spread, count, heat):
    """A burst of fire/smoke 'flashes' around center. heat 1 = bright fire, 0 = smoke."""
    out = []
    for _ in range(count):
        off = rng.normal(0, spread, 3) * np.array([1, 1, 0.6])
        off[2] = abs(off[2]) + spread * 0.3
        hot = rng.random() < heat
        col = (1.0, 0.45 + 0.25 * rng.random(), 0.05) if hot else (0.28 + 0.1 * rng.random(),) * 3
        out.append((center + off, spread * (0.2 + 0.25 * rng.random()), col))
    return out


def frame_pose(cloud: Cloud, role: str, k: int, n: int, rng):
    """Return (cloud, flashes) for a frame role at step k of n."""
    lo, hi = robot_dims(cloud)
    height = hi[2] - lo[2]
    if role == "walk":
        bob = [0.0, 0.012, 0.0, 0.012][k % 4]
        rock = axis_rot("y", [0.0, 1.5, 0.0, -1.5][k % 4])
        return transformed(cloud, rock, np.array([0, 0, bob])), []
    if role == "aim":
        return cloud, []
    if role == "fire":
        muzzle = np.array([hi[0] + 0.05, 0.0, lo[2] + 0.62 * height])
        return cloud, [(muzzle, 0.085, (1.0, 0.75, 0.2))]
    if role == "pain":
        return transformed(cloud, tint=(1.0, 0.25, 0.15), tint_amt=0.45), []
    if role == "death":
        t = k / max(n - 1, 1)
        ang = 90.0 * min(1.0, t * 1.25) ** 1.6
        # tip over sideways around the +y bottom edge
        pivot = np.array([0, hi[1], 0])
        R = axis_rot("x", -ang)
        c = transformed(cloud, darken=1.0 - 0.55 * t)
        c = Cloud((c.p - pivot) @ R.T + pivot, c.n @ R.T, c.c)
        mid = (c.p[:, :2].min(0) + c.p[:, :2].max(0)) / 2
        c.p[:, :2] -= mid * min(1.0, t * 1.25)              # keep the body over its origin
        heat = max(0.0, 1.0 - t)
        fl = fire_particles(rng, np.array([0, 0, height * (0.6 - 0.4 * t)]), 0.12, int(3 + 5 * (1 - abs(0.5 - t) * 2)), heat)
        return c, fl if k < n - 1 else fire_particles(rng, np.array([0, 0, 0.1]), 0.06, 2, 0.0)
    if role == "gib":
        t = k / max(n - 1, 1)
        center = np.array([0, 0, height * 0.5])
        # split the robot into ~12 cm chunks; each flies off on its own heading
        cell = np.floor(cloud.p / 0.12).astype(np.int64)
        _, chunk = np.unique(cell, axis=0, return_inverse=True)
        chunk = chunk.ravel()
        crng = np.random.default_rng(1234)
        nch = chunk.max() + 1
        dirs = crng.normal(0, 1, (nch, 3)) + (cloud.p.mean(0) * 0)[None]
        dirs[:, 2] = np.abs(dirs[:, 2]) + 0.6
        dirs /= np.linalg.norm(dirs, axis=1, keepdims=True)
        speed = crng.uniform(0.15, 0.55, nch)
        spin = crng.uniform(-1, 1, nch)
        burst = np.minimum(t * 1.8, 1.0)
        settle = max(0.0, (t - 0.4) / 0.6)
        p = cloud.p + dirs[chunk] * (speed[chunk] * burst)[:, None]
        p[:, 2] += 0.25 * burst * (1 - settle) - 0.1 * burst
        # settle: flatten every chunk onto the floor around where it landed
        p[:, 2] = p[:, 2] * (1 - settle) + (0.01 + 0.05 * np.abs(spin[chunk])) * settle
        keep = crng.random(nch)[chunk] < (1.0 - 0.45 * settle)
        c = Cloud(p[keep], cloud.n[keep], np.clip(cloud.c[keep] * (1 - 0.6 * t), 0, 1))
        heat = max(0.0, 1.0 - t * 1.3)
        fl = fire_particles(rng, center, 0.18 + 0.2 * burst, int(10 * heat) + 1, heat) if t < 0.8 else []
        return c, fl
    raise ValueError(role)


# ---------------------------------------------------------------- Doom picture format
def quantize(rgba: np.ndarray, palette: np.ndarray) -> np.ndarray:
    pal = palette.copy()
    pal[247] = 1e9  # keep the traditional transparent-cyan slot unused
    rgb = rgba[..., :3].reshape(-1, 3).astype(float)
    w = np.array([0.30, 0.59, 0.11]) ** 0.5
    d = (((rgb[:, None, :] - pal[None]) * w) ** 2).sum(-1)
    return d.argmin(1).reshape(rgba.shape[:2]).astype(np.uint8)


def encode_patch(idx: np.ndarray, alpha: np.ndarray, left: int, top: int) -> bytes:
    h, w = idx.shape
    cols = []
    for x in range(w):
        col = bytearray()
        y = 0
        while y < h:
            if not alpha[y, x]:
                y += 1
                continue
            y0 = y
            while y < h and alpha[y, x] and y - y0 < 127:
                y += 1
            run = idx[y0:y, x]
            col += bytes([y0, len(run), 0]) + run.tobytes() + b"\0"
        col += b"\xff"
        cols.append(bytes(col))
    header = struct.pack("<hhhh", w, h, left, top)
    offs, pos = [], 8 + 4 * w
    for col in cols:
        offs.append(pos)
        pos += len(col)
    return header + struct.pack(f"<{w}i", *offs) + b"".join(cols)


def to_patch(rgba, origin, palette, sink=2):
    alpha = rgba[..., 3] > 0
    if not alpha.any():
        alpha[0, 0] = True
    ys, xs = np.nonzero(alpha)
    y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    crop = rgba[y0:y1, x0:x1]
    idx = quantize(crop, palette)
    left = int(round(origin[0] - x0))
    top = int(round(origin[1] - y0)) + sink
    return encode_patch(idx, alpha[y0:y1, x0:x1], left, top), idx, alpha[y0:y1, x0:x1]


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--iwad", default="downloads/freedoom/freedoom2.wad")
    ap.add_argument("--out", default="build/robots.wad")
    ap.add_argument("--preview", default="build/robots_preview.png")
    ap.add_argument("--ppm", type=float, default=60.0, help="sprite pixels per meter")
    ap.add_argument("--points", type=int, default=1_500_000)
    ap.add_argument("--seed", type=int, default=6328)
    args = ap.parse_args()
    rng = np.random.default_rng(args.seed)

    _, iw = read_wad(args.iwad)
    palette = np.frombuffer(next(l.data for l in iw if l.name == "PLAYPAL")[:768], np.uint8).reshape(256, 3).astype(float)
    inside, sprite_names = False, []
    for l in iw:
        if l.name in ("S_START", "SS_START"):
            inside = True
        elif l.name in ("S_END", "SS_END"):
            inside = False
        elif inside:
            sprite_names.append(l.name)

    lumps = [Lump("S_START", b"")]
    previews = []
    for mon in MONSTERS:
        cloud = load_robot(Path(mon.robot), args.points, rng)
        names = [nm for nm in sprite_names if nm.startswith(mon.prefix)]
        rot0 = sorted({nm[4] for nm in names if nm[5] == "0"})
        gib = [f for f in rot0 if f not in mon.death]
        cache = {}

        def role_of(frame):
            if frame in "ABCD":
                return "walk", "ABCD".index(frame), 4
            if frame in "EF":
                return ("fire" if frame in mon.flash else "aim"), 0, 1
            if frame == "G":
                return "pain", 0, 1
            if frame in mon.death:
                return "death", mon.death.index(frame), len(mon.death)
            return "gib", gib.index(frame), len(gib)

        def frame_image(frame, rot):
            key = (frame, rot)
            if key not in cache:
                role, k, n = role_of(frame)
                pose, flashes = frame_pose(cloud, role, k, n, np.random.default_rng(hash((mon.prefix, frame)) & 0xFFFF))
                theta = 25.0 if rot == 0 else (rot - 1) * 45.0
                cache[key] = to_patch(*render(pose, theta, args.ppm, flashes), palette)
            return cache[key]

        for nm in names:
            frame, rot = nm[4], int(nm[5])   # mirrored pairs (e.g. A2A8) reuse the first view
            data, idx, alpha = frame_image(frame, rot)
            lumps.append(Lump(nm, data))
            if rot in (0, 1, 3) or nm == names[0]:
                previews.append((mon.prefix, nm, idx, alpha))
        log(f"{mon.prefix} <- {Path(mon.robot).name}: {len(names)} lumps, {len(cache)} unique views")
    lumps.append(Lump("S_END", b""))
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    write_wad(args.out, lumps)
    log(f"wrote {args.out} ({len(lumps) - 2} sprite lumps)")
    if args.preview:
        write_preview(args.preview, previews, palette)


def write_preview(path, previews, palette):
    from PIL import Image, ImageDraw
    cell = 96
    rows = {}
    for prefix, nm, idx, alpha in previews:
        rows.setdefault(prefix, []).append((nm, idx, alpha))
    ncol = max(len(v) for v in rows.values())
    sheet = Image.new("RGB", (ncol * cell, len(rows) * (cell + 12)), (40, 44, 52))
    draw = ImageDraw.Draw(sheet)
    for r, (prefix, items) in enumerate(rows.items()):
        for cidx, (nm, idx, alpha) in enumerate(items):
            rgb = palette[idx].astype(np.uint8)
            im = Image.fromarray(np.concatenate([rgb, (alpha * 255).astype(np.uint8)[..., None]], 2), "RGBA")
            sc = min(1.0, (cell - 4) / max(im.size))
            im = im.resize((max(1, int(im.width * sc)), max(1, int(im.height * sc))), Image.NEAREST)
            x0, y0 = cidx * cell + (cell - im.width) // 2, r * (cell + 12) + 12 + (cell - im.height) // 2
            sheet.paste(im, (x0, y0), im)
            draw.text((cidx * cell + 2, r * (cell + 12)), nm, fill=(200, 200, 200))
    sheet.save(path)
    log(f"preview {path}")


if __name__ == "__main__":
    main()
