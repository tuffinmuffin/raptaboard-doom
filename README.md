# doomMod — FRC 2026 field as a Doom map

An easter-egg prototype for a raptaboard WASM widget: the FRC 2026 (REBUILT) field
CAD converted into a playable Doom map, running on
[room4doom](https://github.com/flukejones/room4doom) (MIT, Rust). We carry a small
gameplay patch on room4doom, so the map targets **our room4doom build**, not other
Doom ports (it uses UDMF with Doom-numbered specials and a custom monster).

## Quick start

```sh
make setup assets   # python venv; field/KitBot GLBs + Freedoom Phase 2 into downloads/
make check          # build/frc2026.wad + validation + build/frc2026_preview.png
make engine         # clone room4doom at the pinned rev, apply our patch, cargo build
make play           # MAP01 with freedoom2.wad + build/robots.wad (renders sprites on first run)
```

## What's in the map

- **The field**: hubs, bumps, trench (drivable underneath), guardrails, driver stations.
- **Towers + elevators**: each alliance tower is a platform at its upright height
  (1.83 m), with a lift pad on the field side that rests on the carpet. Walk onto it and
  it starts cycling up to the tower top and back down, waiting 3 s at each end
  (Doom's perpetual lift, special 87).
  BFG on the red tower top, megasphere on the blue one.
- **Fuel → loot**: reachable Fuel balls become a weighted mix of ammo, health and armor,
  and six spread-out ones become weapons (shotgun, super shotgun, chaingun, rocket
  launcher, plasma, chainsaw).
- **Endless waves**: Doom II's monster spawner (invisible shooter outside the far guardrail
  + 10 spawn spots). It wakes when it sees or hears you and spits a monster roughly
  every 5 s (none on the easiest skill). There is no boss brain, so it never stops.
- **Robot enemies**: the zombieman, shotgunner and chaingunner are FIRST KitBots
  (2026, 2025, 2024), rendered from AdvantageScope's CAD by `tools/robot_sprites.py`
  into `build/robots.wad`. 12 of them start on the red side. Spawned waves are still
  stock Doom II monsters.
- **The hunter** (thing 30001, patch below): always knows where you are, 1200 HP,
  faster than a Revenant, homing rockets, and it comes back 20 s after you kill it.
  It uses Revenant art until the robot sprites exist.

## Engine patch (`patches/room4doom.patch`, against `patches/room4doom.rev`)

- `MT_HUNTER` added to `gameplay/src/info.rs` (Revenant states, own stats).
- `hunter_acquire` in `thing/shooting.rs`, used by `a_chase`: target a live player
  without a sight check.
- `thing/mod.rs`: hunter corpses always respawn after 20 s, independent of `-respawn`.
- `wad/src/wad.rs`: map lumps are found by position after the marker; a UDMF map has no
  REJECT/BLOCKMAP, so the lookup now checks the lump name. Without this, a second PWAD
  (the robot sprites) made the engine read a sprite as REJECT and panic.

## Robot sprites (`tools/robot_sprites.py`)

Point-splat renderer: ~1.5M colored points sampled from the CAD surfaces, projected
orthographically with a z-buffer at 3× supersampling from each of Doom's 8 rotations
(14° downward pitch), then quantized to the IWAD palette. Walk frames bob, attack
frames add a muzzle flash, pain is tinted red, death tips the robot over and burns out,
and the gib death blows it into ~12 cm chunks that settle as debris. Lump names match
the IWAD's exactly (including mirrored pairs like `SPOSA2A8`) so they override
one-for-one. `build/robots_preview.png` is a contact sheet. Sprite scale is
`--ppm` (60 px per meter).

`tools/prep_room4doom.py` also drops upstream's GUI dev tools from the workspace (they
need a local Zed checkout).

## How the conversion works (`tools/field2wad.py`)

| Step | What |
|---|---|
| Load | All meshes from AdvantageScope's `Field3d_2026FRCFieldV2` GLB in field coords. Fuel balls and tower parts are pulled out. |
| Voxelize | 2in × 2in columns, 4-unit (3.1 cm) z bins. Steep faces are rasterized along their edges so thin panels/posts survive. |
| Columns | Floor climbs from the carpet through anything within Doom's 24-unit step. A gap ≥ 56 units becomes a low ceiling (trench); otherwise a solid block. |
| Towers | Footprint of the uprights/supports → platform; lift sector tagged 1 (red) / 2 (blue). |
| Sectors | Equal (floor, ceiling, tag, flat) cells merged; tiny regions absorbed; boundary runs → linedefs. Lift faces get special 62 (SR), tower-top edges 88 (WR). |
| Textures | CAD material colors → nearest from a curated list of IWAD flats/walls. |

Scale is 128 map units per meter, so the player is a ~0.44 m robot that fits under
the trench. A flood fill from the start checks the field is sealed.

## Next steps

- Robot sprites for the hunter (its own sprite prefix in the engine) and for the
  spawner's monsters (or an engine patch so the spawner picks robot types).
- WASM build of room4doom's core for the widget host once raptaboard's plugin API can
  return a framebuffer and key up/down events.
- Real slopes for the bumps; custom textures (carpet, polycarb, AprilTags).

## License

Our code is MIT ([LICENSE](LICENSE)). Game data (field model, Freedoom, the built WAD)
is not in this repo; see [NOTICE.md](NOTICE.md) for its sources and terms.
