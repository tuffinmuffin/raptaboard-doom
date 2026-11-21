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
    "LITEBLU1": None, "DOORYEL": None, "LITE3": None, "COMPTILE": None, "DOORRED": None,
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
        col = np.array([0.5, 0.5, 0.5])
        mat = getattr(mesh.visual, "material", None)
        bcf = getattr(mat, "baseColorFactor", None)
        if bcf is not None:
            col = np.asarray(bcf[:3], dtype=np.float64) / 255.0
        f = v[mesh.faces]
        tris.append(f)
        cols.append(np.repeat(col[None], len(f), axis=0))
    return np.concatenate(tris), np.concatenate(cols), np.array(fuel)


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
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--glb", default="downloads/field2026/model.glb")
    ap.add_argument("--iwad", default="vendor/room4doom/data/doom1.wad")
    ap.add_argument("--out", default="build/frc2026.wad")
    ap.add_argument("--map", default="E1M1")
    ap.add_argument("--cell-in", type=float, default=2.0, help="grid cell size in inches")
    ap.add_argument("--min-region", type=int, default=3, help="merge regions smaller than N cells")
    ap.add_argument("--preview", default="build/frc2026_preview.png")
    ap.add_argument("--monsters", type=int, default=8, help="placeholder monsters on red side")
    args = ap.parse_args()

    cell = args.cell_in * 0.0254
    zbin_m = Z_BIN / UNITS_PER_M
    tris, cols, fuel = load_field(Path(args.glb))
    log(f"{len(tris):,} triangles, {len(fuel)} fuel")
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

    flats, walls = load_texture_colors(args.iwad)
    # color -> flat id
    qcol = np.round(colors * 20) / 20
    uniq, inv = np.unique(qcol.reshape(-1, 3), axis=0, return_inverse=True)
    flat_for = [nearest(flats, c) for c in uniq]
    flat_names = sorted(set(flat_for))
    flat_idx = np.array([flat_names.index(f) for f in flat_for])[inv.ravel()].reshape(nx, ny)

    # sector key and cleanup of tiny regions
    key = (floor_u.astype(np.int64) << 20) | (ceil_u.astype(np.int64) << 8) | flat_idx
    for _ in range(4):
        lab, keys = label_regions(key)
        sizes = np.bincount(lab.ravel())
        small = sizes[lab] < args.min_region
        if not small.any():
            break
        # replace small cells with the most frequent neighbouring key (prefer higher floor)
        key = np.where(small, neighbour_mode(key, ~small), key)
    lab, keys = label_regions(key)
    log(f"{len(keys)} sectors")

    sec_floor = (keys >> 20).astype(int)
    sec_ceil = ((keys >> 8) & 0xFFF).astype(int)
    sec_flat = [flat_names[int(k & 0xFF)] for k in keys]
    sec_rgb = np.zeros((len(keys), 3))
    np.add.at(sec_rgb, lab.ravel(), colors.reshape(-1, 3))
    sec_rgb /= np.bincount(lab.ravel(), minlength=len(keys))[:, None]
    sec_wall = [nearest(walls, c) for c in sec_rgb]

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

    out = ['namespace = "zdoom";', ""]
    sides = []
    linedefs = []
    for x0, y0, x1, y1, right, left in lines:
        if right < 0:  # flip so the front faces a real sector
            x0, y0, x1, y1, right, left = x1, y1, x0, y0, left, right
        v1, v2 = vertex(x0, y0), vertex(x1, y1)
        if left < 0:
            sides.append(f'sidedef {{ sector = {right}; texturemiddle = "{BORDER_WALL}"; }}')
            linedefs.append(f"linedef {{ v1 = {v1}; v2 = {v2}; sidefront = {len(sides) - 1}; blocking = true; }}")
            continue
        # texture each side by the higher neighbour's material
        def side(sec, other):
            parts = [f"sector = {sec}"]
            if sec_floor[other] > sec_floor[sec]:
                parts.append(f'texturebottom = "{sec_wall[other]}"')
            if sec_ceil[other] < sec_ceil[sec]:
                parts.append(f'texturetop = "{sec_wall[other]}"')
            return "sidedef { " + "; ".join(parts) + "; }"
        sides.append(side(right, left))
        front = len(sides) - 1
        sides.append(side(left, right))
        linedefs.append(
            f"linedef {{ v1 = {v1}; v2 = {v2}; sidefront = {front}; sideback = {front + 1}; twosided = true; }}"
        )

    # -------- things
    things = []

    def thing(x_m, y_m, ttype, angle=0):
        things.append(
            f"thing {{ x = {x_m * UNITS_PER_M:.1f}; y = {y_m * UNITS_PER_M:.1f}; angle = {angle}; type = {ttype}; "
            "single = true; coop = true; dm = true; skill1 = true; skill2 = true; skill3 = true; skill4 = true; skill5 = true; }"
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
        thing(x, y, [2, 3, 4][k], 180) if open_floor(x, y) else None

    reach = reachable(floor_u, ceil_u, cell_of(*start))
    xs = origin[0] + (np.nonzero(reach)[0] + 0.5) * cell
    ys = origin[1] + (np.nonzero(reach)[1] + 0.5) * cell
    log(f"reachable under overhangs (trench): {(reach & (ceil_u < SKY_H)).sum()} cells")
    log(f"reachable from start: {reach.sum()} cells,x [{xs.min():.2f}, {xs.max():.2f}] y [{ys.min():.2f}, {ys.max():.2f}]")
    np.save(Path(args.out).with_suffix(".reach.npy"), reach)
    # fuel -> ammo clips (only where reachable floor)
    nfuel = 0
    for fx, fy, _fz in fuel:
        i, j = cell_of(fx, fy)
        if open_floor(fx, fy) and reach[i, j]:
            thing(fx, fy, 2007)
            nfuel += 1
    # placeholder monsters (zombiemen) on red side; future: FRC robot sprites
    rng = np.random.default_rng(2026)
    placed = 0
    while placed < args.monsters:
        x, y = rng.uniform(-7.5, -2.0), rng.uniform(-3.6, 3.6)
        i, j = cell_of(x, y)
        if open_floor(x, y) and reach[i, j]:
            thing(x, y, 3004, 180)
            placed += 1
    log(f"things: {len(things)} ({nfuel} fuel clips, {placed} monsters)")

    out += things
    out += [f"vertex {{ x = {x:.3f}; y = {y:.3f}; }}" for x, y in verts]
    out += linedefs
    out += sides
    for s in range(len(keys)):
        sky = sec_ceil[s] >= SKY_H
        out.append(
            "sector { "
            f"heightfloor = {sec_floor[s]}; heightceiling = {sec_ceil[s]}; "
            f'texturefloor = "{sec_flat[s]}"; textureceiling = "{"F_SKY1" if sky else OVERHANG_CEIL}"; '
            f"lightlevel = {200 if sky else 150}; }}"
        )
    textmap = ("\n".join(out) + "\n").encode()
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    write_wad(args.out, [Lump(args.map, b""), Lump("TEXTMAP", textmap), Lump("ENDMAP", b"")])
    log(f"wrote {args.out} ({len(textmap) / 1e6:.1f} MB TEXTMAP, {len(verts)} vertices)")

    meta = {"sectors": len(keys), "linedefs": len(linedefs), "vertices": len(verts), "things": len(things),
            "grid": [nx, ny, nz], "units_per_m": UNITS_PER_M}
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
