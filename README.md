# doomMod — FRC 2026 field as a Doom map

Converts the FRC 2026 (REBUILT) field CAD into a playable Doom UDMF map, played
in [room4doom](https://github.com/flukejones/room4doom), an MIT-licensed Doom engine
written in Rust that builds its own BSP and understands UDMF.

## Quick start

```sh
make setup assets   # python venv + AdvantageScope field/KitBot GLBs into downloads/
make check          # build/frc2026.wad + structural validation + build/frc2026_preview.png
make engine         # clone + cargo build room4doom into vendor/
make play           # E1M1 replaced by the field, using room4doom's bundled doom1.wad
```

## How the conversion works (`tools/field2wad.py`)

| Step | What |
|---|---|
| Load | All meshes from AdvantageScope's `Field3d_2026FRCFieldV2` GLB, converted to field coords (x length, y width, z up). Fuel balls are pulled out and become ammo pickups. |
| Voxelize | 2in × 2in columns, 4-unit (3.1 cm) z bins. Steep faces are rasterized along their edges so thin panels/posts survive; flat faces are sampled at cell centers. |
| Columns | Floor climbs from the carpet through anything within Doom's 24-unit step. A gap ≥ 56 units above that becomes a low ceiling (the trench); otherwise the column becomes a solid block topped at its highest geometry. |
| Sectors | Equal (floor, ceiling, flat) cells are merged; regions < 3 cells are absorbed. Boundary edges become linedefs with collinear runs merged. |
| Textures | CAD material colors → nearest from a curated list of doom1.wad flats/walls (red/blue alliance, aluminum, black, etc). |
| Things | Player start on blue alliance; 396 reachable fuel → ammo clips; 8 zombiemen on red side as placeholders for robot monsters. |

Scale is 128 map units per meter, so the Doom player (56 tall, 32 wide) is a
~0.44 m robot that fits under the 22.25in trench. A flood fill from the start checks
the field is sealed and the trench is drivable.

## Layout

- `tools/field2wad.py` — converter
- `tools/check_udmf.py` — closed sector loops, valid refs, textures exist in the IWAD
- `tools/wadlib.py` — WAD read/write + palette-averaged texture colors
- `downloads/`, `vendor/`, `build/` — gitignored

## Next steps

- KitBot monster sprites: render `downloads/kitbot2026/model.glb` from 8 angles
  (Doom rotations 1–8) × walk/attack/pain/death frames, quantize to PLAYPAL, pack as
  sprite lumps replacing a monster (e.g. POSS* zombieman) via a PWAD.
- Real slopes for the bumps (UDMF `floorplane_*`, supported by room4doom's rbsp)
  instead of 4-unit stairs.
- Custom textures (carpet, polycarb, AprilTags) via TEXTURE1/PNAMES in the PWAD.
- Consider Freedoom as the IWAD for fully redistributable builds.
