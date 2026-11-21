#!/usr/bin/env python3
"""Convert an AdvantageScope FRC field GLB into a playable Doom UDMF map (PWAD).

Pipeline
  1. Load every mesh in field coordinates (x = length, y = width, z = up; meters).
  2. Voxelize: every triangle marks the (cell, z-range) it occupies. Steep faces
     are rasterized along their projected edges so thin walls/posts survive.
  3. Per column, derive a Doom floor / ceiling:
       - floor climbs from the carpet through any stack reachable by a step
       - a gap above the floor tall enough for the player becomes a low ceiling
         (e.g. the trench); otherwise the whole column becomes a solid block
  4. Group equal cells into sectors, emit boundary linedefs (collinear runs merged).
  5. Pick flats / wall textures by nearest CAD material color, write TEXTMAP.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
import trimesh
from scipy import ndimage

sys.path.insert(0, str(Path(__file__).parent))
from wadlib import Lump, write_wad  # noqa: E402

# ---------------------------------------------------------------- parameters
UNITS_PER_M = 128.0          # Doom map units per meter (player 56u tall -> 0.44 m robot)
Z_BIN = 4                    # vertical quantization in map units
STEP = 24                    # Doom max step-up
PLAYER_H = 56                # Doom player height (clearance needed under an overhang)
SKY_H = 512                  # ceiling height of open sky (4 m)
FLOOR_EPS_M = 0.012          # geometry below this (carpet, tape) is the ground

# Curated texture palettes (doom1.wad names). Colors are matched after scaling the
# CAD color into Doom's darker range.
FLATS = {
    "MFLR8_1": None, "FLOOR4_8": None, "CEIL5_1": None, "FLAT1": None, "FLAT5_4": None,
    "FLAT18": None, "FLAT2": None, "FLOOR1_7": None, "FLOOR6_1": None, "FLAT22": None,
    "FLAT14": None, "FLOOR0_3": None, "FLOOR5_2": None, "CEIL3_5": None,
}
WALLS = {
    "GRAY4": None, "GRAY5": None, "STARGR1": None, "SUPPORT2": None, "STONE": None,
    "STONE2": None, "METAL1": None, "COMPSPAN": None, "REDWALL1": None, "LITEBLU4": None,
    "LITEBLU1": None, "DOORYEL": None, "LITE3": None, "COMPTILE": None, "DOORRED": None, "REDWALL": None,
    "DOORBLU": None,
}
BORDER_WALL = "STARTAN3"
OVERHANG_CEIL = "CEIL5_1"


def log(*a):
    print("[field2wad]", *a, file=sys.stderr, flush=True)


# ---------------------------------------------------------------- loading
def load_field(glb: Path, skip=("Fuel",)):
    scene = trimesh.load(glb, force="scene")
    tris, cols, fuel = [], [], []
    tower_parts = {}  # side (+1 blue/+x, -1 red/-x) -> list of vertex arrays
    # glTF is Y-up; AdvantageScope rotates +90deg about X: field = (x, -z, y)
    conv = np.array([[1, 0, 0], [0, 0, -1], [0, 1, 0]], dtype=np.float64)
    for node in scene.graph.nodes_geometry:
        T, gname = scene.graph[node]
        mesh = scene.geometry[gname]
        v = trimesh.transform_points(mesh.vertices, T) @ conv.T
        if any(s in node for s in skip):
            if "GE-26900" in node:
                fuel.append(v.mean(axis=0))
            continue
        if any(k in node for k in ("Tower Upright", "Tower Support Tube", "Tower Brace")):
            tower_parts.setdefault(1 if v[:, 0].mean() > 0 else -1, []).append(v)
        col = np.array([0.5, 0.5, 0.5])
        mat = getattr(mesh.visual, "material", None)
        bcf = getattr(mat, "baseColorFactor", None)
        if bcf is not None:
            col = np.asarray(bcf[:3], dtype=np.float64) / 255.0
        f = v[mesh.faces]
        tris.append(f)
        cols.append(np.repeat(col[None], len(f), axis=0))
    towers = {}
    for side, vs in tower_parts.items():
        v = np.concatenate(vs)
        towers[side] = (*(float(a) for a in (v[:, 0].min(), v[:, 0].max(), v[:, 1].min(), v[:, 1].max())),
                        float(v[:, 2].max()))
    return np.concatenate(tris), np.concatenate(cols), np.array(fuel), towers


# ---------------------------------------------------------------- voxelize
def rasterize(tris, cols, origin, cell, nx, ny, nz, zbin_m):
    """Return occupancy[nx,ny,nz] and per-cell color of the highest surface sample."""
    hits_cell, hits_lo, hits_hi, hits_col = [], [], [], []

    def emit(px, py, zlo, zhi, ci):
        ix = np.floor((px - origin[0]) / cell).astype(np.int64)
        iy = np.floor((py - origin[1]) / cell).astype(np.int64)
        ok = (ix >= 0) & (ix < nx) & (iy >= 0) & (iy < ny)
        hits_cell.append(ix[ok] * ny + iy[ok])
        hits_lo.append(zlo[ok])
        hits_hi.append(zhi[ok])
        hits_col.append(ci[ok])

    n = len(tris)
    ci_all = np.arange(n)
    a, b, c = tris[:, 0], tris[:, 1], tris[:, 2]
    nrm = np.cross(b - a, c - a)
    area2 = np.linalg.norm(nrm, axis=1) + 1e-12
    nz_abs = np.abs(nrm[:, 2]) / area2
    steep = nz_abs < 0.35
    zmin = tris[:, :, 2].min(1)
    zmax = tris[:, :, 2].max(1)

    # (a) edge sampling for every triangle
    step = cell * 0.5
    for p, q in ((a, b), (b, c), (c, a)):
        L = np.linalg.norm((q - p)[:, :2], axis=1)
        k = np.ceil(L / step).astype(np.int64) + 1
        idx = np.repeat(np.arange(n), k)
        start = np.repeat(np.cumsum(k) - k, k)
        t = (np.arange(len(idx)) - start) / np.maximum(np.repeat(k, k) - 1, 1)
        pts = p[idx] + (q[idx] - p[idx]) * t[:, None]
        lo = np.where(steep[idx], zmin[idx], pts[:, 2])
        hi = np.where(steep[idx], zmax[idx], pts[:, 2])
        emit(pts[:, 0], pts[:, 1], lo, hi, ci_all[idx])
    log(f"edge samples done ({sum(len(h) for h in hits_cell):,} hits)")

    # (b) interior sampling for non-steep triangles, bucketed by bbox size
    flat = ~steep
    fa, fb, fc = a[flat], b[flat], c[flat]
    fidx = ci_all[flat]
    bx0 = np.floor((np.minimum(np.minimum(fa[:, 0], fb[:, 0]), fc[:, 0]) - origin[0]) / cell).astype(int)
    bx1 = np.floor((np.maximum(np.maximum(fa[:, 0], fb[:, 0]), fc[:, 0]) - origin[0]) / cell).astype(int)
    by0 = np.floor((np.minimum(np.minimum(fa[:, 1], fb[:, 1]), fc[:, 1]) - origin[1]) / cell).astype(int)
    by1 = np.floor((np.maximum(np.maximum(fa[:, 1], fb[:, 1]), fc[:, 1]) - origin[1]) / cell).astype(int)
    bx0, by0 = np.clip(bx0, 0, nx - 1), np.clip(by0, 0, ny - 1)
    bx1, by1 = np.clip(bx1, 0, nx - 1), np.clip(by1, 0, ny - 1)
    w = bx1 - bx0 + 1
    h = by1 - by0 + 1
    big = (w * h) > 1
    for W in np.unique(w[big]):
        selw = big & (w == W)
        for H in np.unique(h[selw]):
            sel = np.nonzero(selw & (h == H))[0]
            if len(sel) == 0:
                continue
            # chunk to bound memory
            per = max(1, 4_000_000 // int(W * H))
            for s0 in range(0, len(sel), per):
                ss = sel[s0 : s0 + per]
                gx, gy = np.meshgrid(np.arange(W), np.arange(H), indexing="ij")
                gx, gy = gx.ravel(), gy.ravel()
                ix = (bx0[ss][:, None] + gx[None]).ravel()
                iy = (by0[ss][:, None] + gy[None]).ravel()
                tri = np.repeat(ss, len(gx))
                px = origin[0] + (ix + 0.5) * cell
                py = origin[1] + (iy + 0.5) * cell
                A, B, C = fa[tri], fb[tri], fc[tri]
                d = (B[:, 1] - C[:, 1]) * (A[:, 0] - C[:, 0]) + (C[:, 0] - B[:, 0]) * (A[:, 1] - C[:, 1])
                d = np.where(np.abs(d) < 1e-15, 1e-15, d)
                l1 = ((B[:, 1] - C[:, 1]) * (px - C[:, 0]) + (C[:, 0] - B[:, 0]) * (py - C[:, 1])) / d
                l2 = ((C[:, 1] - A[:, 1]) * (px - C[:, 0]) + (A[:, 0] - C[:, 0]) * (py - C[:, 1])) / d
                l3 = 1 - l1 - l2
                inside = (l1 >= -1e-9) & (l2 >= -1e-9) & (l3 >= -1e-9)
                z = l1 * A[:, 2] + l2 * B[:, 2] + l3 * C[:, 2]
                emit(px[inside], py[inside], z[inside], z[inside], fidx[tri[inside]])
    log(f"interior samples done ({sum(len(h) for h in hits_cell):,} hits)")

    cellid = np.concatenate(hits_cell)
    lo = np.concatenate(hits_lo)
    hi = np.concatenate(hits_hi)
    colid = np.concatenate(hits_col)

    # occupancy via difference array along z (ground slab excluded)
    solid = hi > FLOOR_EPS_M
    klo = np.clip(np.floor(np.maximum(lo[solid], FLOOR_EPS_M) / zbin_m).astype(np.int64), 0, nz - 1)
    khi = np.clip(np.floor(hi[solid] / zbin_m).astype(np.int64), 0, nz - 1)
    diff = np.zeros(nx * ny * (nz + 1), dtype=np.int32)
    base = cellid[solid] * (nz + 1)
    np.add.at(diff, base + klo, 1)
    np.add.at(diff, base + khi + 1, -1)
    occ = np.cumsum(diff.reshape(nx * ny, nz + 1), axis=1)[:, :nz] > 0
    return occ.reshape(nx, ny, nz), (cellid, lo, hi, colid)


def columns(occ, step_bins, clear_bins):
    """Floor top bin and ceiling bin (-1 = sky) per column."""
    nx, ny, nz = occ.shape
    floor = np.zeros((nx, ny), dtype=np.int32)       # first free bin above solid stack
    for k in range(nz):
        climb = occ[:, :, k] & (k < floor + step_bins)
        floor = np.where(climb, k + 1, floor)
    kk = np.arange(nz)[None, None, :]
    above = occ & (kk >= floor[:, :, None])
    has = above.any(axis=2)
    ceil = np.where(has, above.argmax(axis=2), -1)
    blocked = has & ((ceil - floor) < clear_bins)
    top = nz - 1 - occ[:, :, ::-1].argmax(axis=2) + 1
    floor = np.where(blocked, top, floor)
    ceil = np.where(blocked, -1, ceil)
    # anything still with geometry above a solid block (re-check) -> sky
    return floor, ceil


def top_colors(hits, floor_m, ny, cols):
    """Material color of the highest sample at or below the column floor."""
    cellid, lo, hi, colid = hits
    ft = floor_m.ravel()[cellid]
    ok = hi <= ft + 0.02
    cid, h, ci = cellid[ok], hi[ok], colid[ok]
    order = np.lexsort((h, cid))
    cid, ci = cid[order], ci[order]
    last = np.r_[cid[1:] != cid[:-1], True]
    out = np.full((floor_m.size, 3), 0.22)
    out[cid[last]] = cols[ci[last]]
    return out.reshape(*floor_m.shape, 3)


# ---------------------------------------------------------------- textures
def nearest(table, rgb):
    names = list(table)
    arr = np.array([table[n] for n in names])
    target = np.asarray(rgb) * 0.62 * 255 + 12
    # weight chroma: compare both raw and normalized color
    d = np.linalg.norm(arr - target, axis=1)
    def chroma(x):
        x = np.asarray(x, dtype=float)
        return x / (x.sum(axis=-1, keepdims=True) + 1e-6)
    d += 600 * np.linalg.norm(chroma(arr) - chroma(target), axis=1)
    return names[int(np.argmin(d))]


def load_texture_colors(iwad):
    from wadlib import Iwad
    w = Iwad(iwad)
    f, t = w.flats(), w.textures()
    flats = {k: f[k] for k in FLATS if k in f}
    walls = {k: t[k] for k in WALLS if k in t}
    return flats, walls


# ---------------------------------------------------------------- geometry
def build_lines(label, nx, ny):
    """Boundary edges between differing labels, merged into straight runs.

    Returns list of (x0, y0, x1, y1, right_label, left_label) in grid coords;
    left/right label -1 means outside the map.
    """
    L = np.pad(label, 1, constant_values=-1)
    segs = []
    # vertical grid lines at x = i (between column i-1 and i), for y in [j, j+1]
    a = L[:-1, 1:-1]  # cell left of line  (x = i-1)
    b = L[1:, 1:-1]   # cell right of line (x = i)
    for i, j in zip(*np.nonzero(a != b)):
        segs.append(("v", i, j, int(a[i, j]), int(b[i, j])))
    a = L[1:-1, :-1]  # cell below (y = j-1)
    b = L[1:-1, 1:]   # cell above (y = j)
    for i, j in zip(*np.nonzero(a != b)):
        segs.append(("h", j, i, int(a[i, j]), int(b[i, j])))
    # merge runs: key by orientation, line index, label pair
    segs.sort(key=lambda s: (s[0], s[1], s[3], s[4], s[2]))
    lines = []
    run = None
    for s in segs:
        if run and run[0] == s[0] and run[1] == s[1] and run[3] == s[3] and run[4] == s[4] and run[5] == s[2]:
            run[5] = s[2] + 1
            continue
        if run:
            lines.append(run)
        run = [s[0], s[1], s[2], s[3], s[4], s[2] + 1]
    if run:
        lines.append(run)
    out = []
    for o, k, t0, lo_lab, hi_lab, t1 in lines:
        if o == "v":
            # line x=k from y=t0..t1; lo_lab is at x<k (west), hi_lab at x>k (east)
            # Doom: front side is on the right of v1->v2. Going north (+y), right is east.
            out.append((k, t0, k, t1, hi_lab, lo_lab))
        else:
            # line y=k from x=t0..t1; lo_lab south, hi_lab north. Going east, right is south.
            out.append((t0, k, t1, k, lo_lab, hi_lab))
    return out


# ---------------------------------------------------------------- main
# Doom thing types (Doom II / Freedoom Phase 2)
T_PLAYER = [1, 2, 3, 4]
T_SPAWN_SHOOTER, T_SPAWN_SPOT = 89, 87
WEAPONS = [2001, 82, 2002, 2003, 2004, 2005]           # shotgun, SSG, chaingun, RL, plasma, chainsaw
T_BFG, T_MEGASPHERE = 2006, 83
T_HUNTER = 30001                                      # needs patches/room4doom.patch
# fuel -> pickup mix (type, weight)
PICKUPS = [
    (2007, 22), (2048, 3),   # clip, ammo box
    (2008, 20), (2049, 3),   # shells, shell box
    (2010, 8), (2046, 1),    # rocket, rocket box
    (2047, 8), (17, 1),      # cell, cell pack
    (2014, 14), (2011, 4), (2012, 2),  # health bonus, stimpack, medikit
    (2015, 12), (2018, 1),   # armor bonus, green armor
]
STARTING_MONSTERS = [3004, 9, 3001, 3002]  # zombieman, shotgunner, imp, demon

# Doom line/sector specials
SP_LIFT_SR = 62      # SR lower lift, wait, raise (press use on the lift face)
SP_LIFT_WR = 88      # WR same, walk-over (top of the tower -> step onto the lift)
LIFT_TEX = "PLAT1"
LIFT_DEPTH_M = 0.75  # lift pad depth in front of the tower


def farthest_points(pts, k, rng, seed_pt=None):
    """Greedy farthest-point sampling: k well-spread indices into pts."""
    if len(pts) == 0:
        return []
    chosen = [int(rng.integers(len(pts)))] if seed_pt is None else []
    d = np.full(len(pts), np.inf)
    if seed_pt is not None:
        d = np.linalg.norm(pts - np.asarray(seed_pt), axis=1)
    for c in chosen:
        d = np.minimum(d, np.linalg.norm(pts - pts[c], axis=1))
    while len(chosen) < min(k, len(pts)):
        c = int(np.argmax(d))
        chosen.append(c)
        d = np.minimum(d, np.linalg.norm(pts - pts[c], axis=1))
    return chosen


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--glb", default="downloads/field2026/model.glb")
    ap.add_argument("--iwad", default="downloads/freedoom/freedoom2.wad")
    ap.add_argument("--out", default="build/frc2026.wad")
    ap.add_argument("--map", default="MAP01")
    ap.add_argument("--cell-in", type=float, default=2.0, help="grid cell size in inches")
    ap.add_argument("--min-region", type=int, default=3, help="merge regions smaller than N cells")
    ap.add_argument("--preview", default="build/frc2026_preview.png")
    ap.add_argument("--monsters", type=int, default=8, help="starting monsters on the red side")
    ap.add_argument("--spawn-spots", type=int, default=10, help="boss-spawner targets (0 = no spawner)")
    ap.add_argument("--seed", type=int, default=2026)
    ap.add_argument("--no-hunter", dest="hunter", action="store_false", help="omit the doomMod hunter")
    args = ap.parse_args()
    rng = np.random.default_rng(args.seed)

    cell = args.cell_in * 0.0254
    zbin_m = Z_BIN / UNITS_PER_M
    tris, cols, fuel, towers = load_field(Path(args.glb))
    log(f"{len(tris):,} triangles, {len(fuel)} fuel, towers: {towers}")
    lo = tris.reshape(-1, 3).min(0)
    hi = tris.reshape(-1, 3).max(0)
    origin = lo[:2]
    nx = int(math.ceil((hi[0] - lo[0]) / cell))
    ny = int(math.ceil((hi[1] - lo[1]) / cell))
    nz = int(math.ceil(hi[2] / zbin_m)) + 2
    log(f"grid {nx}x{ny}x{nz}")

    occ, hits = rasterize(tris, cols, origin, cell, nx, ny, nz, zbin_m)
    floor_b, ceil_b = columns(occ, STEP // Z_BIN, PLAYER_H // Z_BIN)
    floor_u = floor_b * Z_BIN
    ceil_u = np.where(ceil_b < 0, SKY_H, ceil_b * Z_BIN)
    colors = top_colors(hits, floor_u / UNITS_PER_M, ny, cols)

    def box(x0, x1, y0, y1):
        i0, i1 = int(np.floor((x0 - origin[0]) / cell)), int(np.ceil((x1 - origin[0]) / cell))
        j0, j1 = int(np.floor((y0 - origin[1]) / cell)), int(np.ceil((y1 - origin[1]) / cell))
        return slice(max(i0, 0), min(i1, nx)), slice(max(j0, 0), min(j1, ny))

    # -------- towers: solid platform at the uprights' height, lift pad on the field side
    tag = np.zeros((nx, ny), dtype=np.int64)
    tower_tops = {}  # side -> (x, y) center of the platform
    for t, side in enumerate(sorted(towers), start=1):
        x0, x1, y0, y1, ztop = towers[side]
        top = int(round(ztop * UNITS_PER_M / Z_BIN)) * Z_BIN
        plat = box(x0, x1, y0, y1)
        floor_u[plat], ceil_u[plat], colors[plat] = top, SKY_H, (0.79, 0.79, 0.79)
        lx0, lx1 = (x0 - LIFT_DEPTH_M, x0) if side > 0 else (x1, x1 + LIFT_DEPTH_M)
        lift = box(lx0, lx1, y0, y1)
        floor_u[lift], ceil_u[lift], colors[lift], tag[lift] = top, SKY_H, (0.96, 0.6, 0.05), t
        tower_tops[side] = ((x0 + x1) / 2, (y0 + y1) / 2)
        log(f"tower {'blue' if side > 0 else 'red'}: top {top}u, lift tag {t}")

    flats, walls = load_texture_colors(args.iwad)
    # color -> flat id
    qcol = np.round(colors * 20) / 20
    uniq, inv = np.unique(qcol.reshape(-1, 3), axis=0, return_inverse=True)
    flat_for = [nearest(flats, c) for c in uniq]
    flat_names = sorted(set(flat_for))
    flat_idx = np.array([flat_names.index(f) for f in flat_for])[inv.ravel()].reshape(nx, ny)

    # sector key: floor | ceiling | tag | flat ; then absorb tiny regions
    key = (floor_u.astype(np.int64) << 32) | (ceil_u.astype(np.int64) << 16) | (tag << 8) | flat_idx
    for _ in range(4):
        lab, keys = label_regions(key)
        sizes = np.bincount(lab.ravel())
        small = (sizes[lab] < args.min_region) & (tag == 0)
        if not small.any():
            break
        key = np.where(small, neighbour_mode(key, ~small), key)
    lab, keys = label_regions(key)
    log(f"{len(keys)} sectors")

    sec_floor = (keys >> 32).astype(int)
    sec_ceil = ((keys >> 16) & 0xFFFF).astype(int)
    sec_tag = ((keys >> 8) & 0xFF).astype(int)
    sec_flat = [flat_names[int(k & 0xFF)] for k in keys]
    sec_rgb = np.zeros((len(keys), 3))
    np.add.at(sec_rgb, lab.ravel(), colors.reshape(-1, 3))
    sec_rgb /= np.bincount(lab.ravel(), minlength=len(keys))[:, None]
    sec_wall = [LIFT_TEX if sec_tag[s] else nearest(walls, c) for s, c in enumerate(sec_rgb)]

    lines = build_lines(lab, nx, ny)
    log(f"{len(lines)} linedefs")

    # -------- emit UDMF
    def to_map(gx, gy):
        return ((origin[0] + gx * cell) * UNITS_PER_M, (origin[1] + gy * cell) * UNITS_PER_M)

    vid = {}
    verts = []

    def vertex(gx, gy):
        k = (gx, gy)
        if k not in vid:
            vid[k] = len(verts)
            verts.append(to_map(gx, gy))
        return vid[k]

    def side(sec, other):
        parts = [f"sector = {sec}"]
        if sec_floor[other] > sec_floor[sec]:
            parts.append(f'texturebottom = "{sec_wall[other]}"')
        if sec_ceil[other] < sec_ceil[sec]:
            parts.append(f'texturetop = "{sec_wall[other]}"')
        return "sidedef { " + "; ".join(parts) + "; }"

    out = ['namespace = "zdoom";', ""]
    sides = []
    linedefs = []
    nlift = 0
    for x0, y0, x1, y1, right, left in lines:
        if right < 0:  # flip so the front faces a real sector
            x0, y0, x1, y1, right, left = x1, y1, x0, y0, left, right
        if left < 0:
            v1, v2 = vertex(x0, y0), vertex(x1, y1)
            sides.append(f'sidedef {{ sector = {right}; texturemiddle = "{BORDER_WALL}"; }}')
            linedefs.append(f"linedef {{ v1 = {v1}; v2 = {v2}; sidefront = {len(sides) - 1}; blocking = true; }}")
            continue
        extra = ""
        t = sec_tag[right] or sec_tag[left]
        if t and not (sec_tag[right] and sec_tag[left]):
            lift, other = (right, left) if sec_tag[right] else (left, right)
            if sec_floor[other] >= sec_floor[lift]:
                extra = f" special = {SP_LIFT_WR}; id = {t};"          # tower top -> lift
            else:
                extra = f" special = {SP_LIFT_SR}; id = {t};"          # use the lift face
                if right == lift:  # switches only work from the front side
                    x0, y0, x1, y1, right, left = x1, y1, x0, y0, left, right
            nlift += 1
        v1, v2 = vertex(x0, y0), vertex(x1, y1)
        sides.append(side(right, left))
        front = len(sides) - 1
        sides.append(side(left, right))
        linedefs.append(
            f"linedef {{ v1 = {v1}; v2 = {v2}; sidefront = {front}; sideback = {front + 1}; twosided = true;{extra} }}"
        )
    log(f"{nlift} lift trigger lines")

    # -------- things
    things = []

    def thing(x_m, y_m, ttype, angle=0, skills=(1, 2, 3, 4, 5)):
        sk = " ".join(f"skill{s} = true;" for s in skills)
        things.append(
            f"thing {{ x = {x_m * UNITS_PER_M:.1f}; y = {y_m * UNITS_PER_M:.1f}; angle = {angle}; type = {ttype}; "
            f"single = true; coop = true; dm = true; {sk} }}"
        )

    def cell_of(x_m, y_m):
        return (int((x_m - origin[0]) / cell), int((y_m - origin[1]) / cell))

    def open_floor(x_m, y_m):
        i, j = cell_of(x_m, y_m)
        return 0 <= i < nx and 0 <= j < ny and floor_u[i, j] <= STEP and ceil_u[i, j] - floor_u[i, j] >= PLAYER_H

    # player start: blue alliance side (+x in this model), facing the field (-x)
    start = (5.5, 0.0)
    if not open_floor(*start):
        start = find_open(open_floor, start)
    thing(*start, 1, 180)
    for k, (x, y) in enumerate([(6.0, -1.5), (6.0, 1.5), (5.0, 3.0)]):
        thing(x, y, T_PLAYER[k + 1], 180) if open_floor(x, y) else None

    reach = reachable(floor_u, ceil_u, cell_of(*start))
    xs = origin[0] + (np.nonzero(reach)[0] + 0.5) * cell
    ys = origin[1] + (np.nonzero(reach)[1] + 0.5) * cell
    log(f"reachable under overhangs (trench): {(reach & (ceil_u < SKY_H)).sum()} cells")
    log(f"reachable from start: {reach.sum()} cells, x [{xs.min():.2f}, {xs.max():.2f}] y [{ys.min():.2f}, {ys.max():.2f}]")
    np.save(Path(args.out).with_suffix(".reach.npy"), reach)

    def walkable(x, y):
        i, j = cell_of(x, y)
        return open_floor(x, y) and reach[i, j]

    # tower-top rewards: BFG on the enemy (red) tower, megasphere on ours
    for side_, (tx, ty) in tower_tops.items():
        thing(tx, ty, T_BFG if side_ < 0 else T_MEGASPHERE)

    # fuel -> weapons (spread out) + weighted ammo/health/armor mix
    fpos = np.array([(fx, fy) for fx, fy, _ in fuel if walkable(fx, fy)])
    weapon_at = {i: WEAPONS[n] for n, i in enumerate(farthest_points(fpos, len(WEAPONS), rng, seed_pt=start))}
    types, weights = zip(*PICKUPS)
    p = np.array(weights, dtype=float) / sum(weights)
    counts = {}
    for i, (fx, fy) in enumerate(fpos):
        tt = weapon_at.get(i) or int(rng.choice(types, p=p))
        counts[tt] = counts.get(tt, 0) + 1
        thing(fx, fy, tt)
    log(f"fuel -> {dict(sorted(counts.items()))}")

    # starting monsters on the red side
    placed = 0
    while placed < args.monsters:
        x, y = rng.uniform(-7.5, -2.0), rng.uniform(-3.6, 3.6)
        if walkable(x, y):
            thing(x, y, int(rng.choice(STARTING_MONSTERS)), 0)
            placed += 1

    # the hunter (doomMod engine thing 30001): starts on the red side and never stops
    if args.hunter:
        hx, hy = find_open(walkable, (-6.5, -2.0)) if not walkable(-6.5, -2.0) else (-6.5, -2.0)
        thing(hx, hy, T_HUNTER, 0, skills=(2, 3, 4, 5))
        log(f"hunter at ({hx:.2f}, {hy:.2f})")

    # endless waves: Doom II boss spawner. The shooter is invisible and wakes on sight or
    # gunfire; it spits a cube at the next spawn spot every ~5 s (none on skill 1).
    if args.spawn_spots:
        shooter = (0.0, 4.35)  # outside the guardrail on the far side, centered
        cand = np.array([(x, y) for x in np.arange(-7.8, 7.81, 0.4) for y in np.arange(-3.6, 3.61, 0.4)
                         if walkable(x, y) and abs(y - shooter[1]) > 1.0
                         and math.dist((x, y), start) > 3.5])
        spots = cand[farthest_points(cand, args.spawn_spots, rng)]
        thing(*shooter, T_SPAWN_SHOOTER, 270)
        for x, y in spots:
            thing(x, y, T_SPAWN_SPOT)
        log(f"spawner at {shooter} with {len(spots)} spots")
    log(f"things: {len(things)}")

    out += things
    out += [f"vertex {{ x = {x:.3f}; y = {y:.3f}; }}" for x, y in verts]
    out += linedefs
    out += sides
    for s in range(len(keys)):
        sky = sec_ceil[s] >= SKY_H
        tid = f" id = {sec_tag[s]};" if sec_tag[s] else ""
        out.append(
            "sector { "
            f"heightfloor = {sec_floor[s]}; heightceiling = {sec_ceil[s]}; "
            f'texturefloor = "{sec_flat[s]}"; textureceiling = "{"F_SKY1" if sky else OVERHANG_CEIL}"; '
            f"lightlevel = {200 if sky else 150};{tid} }}"
        )
    textmap = ("\n".join(out) + "\n").encode()
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    write_wad(args.out, [Lump(args.map, b""), Lump("TEXTMAP", textmap), Lump("ENDMAP", b"")])
    log(f"wrote {args.out} ({len(textmap) / 1e6:.1f} MB TEXTMAP, {len(verts)} vertices)")

    meta = {"sectors": len(keys), "linedefs": len(linedefs), "vertices": len(verts), "things": len(things),
            "grid": [nx, ny, nz], "units_per_m": UNITS_PER_M, "map": args.map}
    Path(args.out).with_suffix(".json").write_text(json.dumps(meta, indent=2))
    if args.preview:
        write_preview(args.preview, floor_u, ceil_u, colors)


def label_regions(key):
    uniq, inv = np.unique(key.ravel(), return_inverse=True)
    inv = inv.reshape(key.shape)
    lab = np.zeros(key.shape, dtype=np.int64)
    keys = []
    nxt = 0
    for u in range(len(uniq)):
        comp, n = ndimage.label(inv == u)  # 4-connected
        m = comp > 0
        lab[m] = comp[m] - 1 + nxt
        keys += [uniq[u]] * n
        nxt += n
    return lab, np.array(keys, dtype=np.int64)


def neighbour_mode(key, valid):
    """For each cell, a neighbouring valid key (the max-floor one among 4-neighbours)."""
    best = np.full(key.shape, -1, dtype=np.int64)
    P = np.pad(key, 1, constant_values=-1)
    V = np.pad(valid, 1, constant_values=False)
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        k = P[1 + dx : P.shape[0] - 1 + dx, 1 + dy : P.shape[1] - 1 + dy]
        v = V[1 + dx : V.shape[0] - 1 + dx, 1 + dy : V.shape[1] - 1 + dy]
        best = np.where(v & (k > best), k, best)
    return np.where(best >= 0, best, key)


def reachable(floor_u, ceil_u, start, radius_cells=2):
    """Flood fill of cells a Doom player can walk to (step + headroom + body width)."""
    nx, ny = floor_u.shape
    # a cell is standable if the player's body (square approx) fits: the local max floor
    # within the radius must be within a step and headroom must hold everywhere.
    size = 2 * radius_cells + 1
    fmax = ndimage.maximum_filter(floor_u, size=size, mode="nearest")
    cmin = ndimage.minimum_filter(ceil_u, size=size, mode="nearest")
    stand = (fmax - floor_u <= STEP) & (cmin - fmax >= PLAYER_H)
    seen = np.zeros_like(stand)
    stack = [start]
    seen[start] = True
    while stack:
        i, j = stack.pop()
        for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            a, b = i + di, j + dj
            if 0 <= a < nx and 0 <= b < ny and not seen[a, b] and stand[a, b]:
                if floor_u[a, b] - floor_u[i, j] <= STEP and min(ceil_u[a, b], ceil_u[i, j]) - max(floor_u[a, b], floor_u[i, j]) >= PLAYER_H:
                    seen[a, b] = True
                    stack.append((a, b))
    return seen


def find_open(pred, start):
    for r in np.arange(0.1, 3.0, 0.1):
        for a in np.linspace(0, 2 * np.pi, 24, endpoint=False):
            p = (start[0] + r * np.cos(a), start[1] + r * np.sin(a))
            if pred(*p):
                return p
    raise RuntimeError("no open floor near start")


def write_preview(path, floor_u, ceil_u, colors):
    from PIL import Image
    shade = np.clip(0.45 + floor_u / 400.0, 0, 1)[..., None]
    img = np.clip(colors * 255 * shade, 0, 255)
    over = ceil_u < SKY_H
    img[over] = img[over] * 0.5 + np.array([255, 0, 255]) * 0.5
    im = Image.fromarray(np.transpose(img, (1, 0, 2))[::-1].astype(np.uint8))
    im = im.resize((im.width * 2, im.height * 2), Image.NEAREST)
    im.save(path)
    log(f"preview {path}")


if __name__ == "__main__":
    main()
