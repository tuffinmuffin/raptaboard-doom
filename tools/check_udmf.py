#!/usr/bin/env python3
"""Sanity-check a UDMF map in a PWAD: sector loops closed, references valid, textures exist."""
import re, sys, collections
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from wadlib import read_wad, Iwad

wad, iwad = sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "vendor/room4doom/data/doom1.wad"
_, lumps = read_wad(wad)
text = next(l.data for l in lumps if l.name == "TEXTMAP").decode()
blocks = collections.defaultdict(list)
for kind, body in re.findall(r"(\w+)\s*\{([^}]*)\}", text):
    blocks[kind].append(dict((k, v.strip().strip('"')) for k, v in re.findall(r"(\w+)\s*=\s*([^;]+);", body)))
V, L, S, SEC = blocks["vertex"], blocks["linedef"], blocks["sidedef"], blocks["sector"]
print({k: len(v) for k, v in blocks.items()})
bal = collections.defaultdict(int)
errs = 0
for ld in L:
    v1, v2 = int(ld["v1"]), int(ld["v2"])
    f = int(S[int(ld["sidefront"])]["sector"])
    bal[(f, v1)] += 1; bal[(f, v2)] -= 1
    if "sideback" in ld:
        b = int(S[int(ld["sideback"])]["sector"])
        bal[(b, v2)] += 1; bal[(b, v1)] -= 1
        errs += f == b
open_ = [k for k, v in bal.items() if v]
print("unclosed sector vertices:", len(open_), "self-referencing lines:", errs)
w = Iwad(iwad)
flats, tex = set(w.flats()), set(w.textures())
used_t = {s[k] for s in S for k in ("texturetop", "texturebottom", "texturemiddle") if k in s}
used_f = {s[k] for s in SEC for k in ("texturefloor", "textureceiling")}
print("missing textures:", used_t - tex, "missing flats:", used_f - flats)
print("textures used:", sorted(used_t)); print("flats used:", sorted(used_f))
sys.exit(1 if open_ or errs or used_t - tex or used_f - flats else 0)
