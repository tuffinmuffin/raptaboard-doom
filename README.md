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
make play           # MAP01 with freedoom2.wad + robot sprites + robot face (renders on first run)
make plugin         # the raptaboard tab plugin, in build/plugin (see below)
```

The standalone game (`make engine`, `make play`) is the place to build and try levels, and
works without raptaboard; the plugin is built from the same patched engine beside it.

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
- **Robot enemies**: three FIRST KitBots on the red side, one each of 2026, 2025 and
  2024, drawn over the zombieman, shotgunner and chaingunner. Each one comes back 10-30 s
  (random) after you kill it, on any skill. `--monsters N` changes the count.
- **The hunter** (thing 30001, patch below): the 2026 KitBot in gunmetal with glowing red
  eyes and oil splatter. It always knows where you are, has 1200 HP, fires homing
  rockets, punches, and comes back 10-30 s after you kill it.
- **Oil, not blood**: robots spark and leak oil when hit; the blood sprites are recolored.
- **Robot status-bar face**: the Doomguy face is a pixel-art robot head
  (`tools/robot_face.py` → `build/face.wad`): LED eyes that look around and shift
  green → red as health drops, dents, oil and a cracked visor with damage, plus ouch,
  evil-grin, rampage, god-mode and dead expressions. `build/face_preview.png` shows all 42.
- **Optional endless waves**: `--spawn-spots 10` adds Doom II's monster spawner
  (stock Doom II monsters every ~5 s). Off by default.

## Engine patch (`patches/room4doom.patch`, against `patches/room4doom.rev`)

- `gameplay/src/info.rs`: `MT_HUNTER` with its own `HUNT` sprite and 32 states
  (zombieman-style frames; Revenant missile and fist actions).
- `hunter_acquire` in `thing/shooting.rs`, used by `a_look` and `a_chase`: target a live
  player without a sight check.
- `thing/interact.rs` + `thing/mod.rs`: when a KitBot robot or the hunter dies, a
  10-30 s respawn timer starts; it respawns at its spawn point regardless of `-respawn`.
  Tests: `cargo test -p gameplay frc_robot_respawn`.
- `wad/src/wad.rs`: map lumps are found by position after the marker; a UDMF map has no
  REJECT/BLOCKMAP, so the lookup now checks the lump name. Without this, a second PWAD
  (the robot sprites) made the engine read a sprite as REJECT and panic.

- `gamestate/src/lib.rs`: `Game::new` takes the sound thread as an `Option`, for an
  engine that mixes its own sound (the plugin); the standalone game passes its thread as
  before.
- `render/backend`: a `display-buffer` backend (`BufferBackend`, `new_buffer`) that keeps
  each frame in memory for the caller instead of showing it in a window, and
  `RenderStack::backend()`/`backend_mut()` to read it. The standalone game's backends are
  unchanged.

## raptaboard plugin (`plugin/`)

The same engine as a [raptaboard](https://github.com/tuffinmuffin/raptaboard) tab plugin,
built for WebAssembly (`wasm32-wasip2`): an empty tab named **Doom** becomes the game. It
runs room4doom's game and software renderer with the buffer backend, steps the game 35
times a second by raptaboard's clock and draws about 60 frames a second between steps
(room4doom interpolates), as a pixel frame raptaboard scales to the tab. Click the tab for
the keyboard (raptaboard's shortcuts are off until Shift+Esc or a click elsewhere). Its
WADs are read from its own folder.

```sh
rustup target add wasm32-wasip2
make plugin                                  # build/plugin: widget.wasm, plugin.toml, the WADs
raptaboard plugin dev build/plugin           # try it in raptaboard's sandbox
```

`plugin/src/display.rs` is room4doom's frame drawing (`game-exe/src/d_main.rs`) kept to what
an embedded software renderer needs; `plugin/src/keys.rs` maps raptaboard's key names to
Doom's. It needs raptaboard with tuffinmuffin/raptaboard#103 (the mouse, `release-focus`,
`[limits] call_ms`): it runs at about 105 MiB, loading the WADs or a level takes about
60 ms to 200 ms, each frame about 1 ms.

It opens on a title card (`tools/splash.py`: the KitBots and the hunter rendered from their
CAD; `make splash`) and loads the game when clicked. Quitting from the game's menu gives the
keyboard and mouse back to raptaboard and shows the card again; a click starts a new game in
the data already loaded.

Controls: the arrows move and turn (W and S move too), A and D strafe, Ctrl fires, Space
uses (doors, lifts), Alt strafes with the arrows, Shift runs, 1 to 7 pick a weapon, Escape
is the menu. The mouse turns, its left button fires and its right moves forward (held while
the tab has the keyboard; Shift+Esc gives both back). Cheats work as in the standalone game
(`iddqd`, `idkfa`, `idclip`, ...). Not yet: sound (the game's sound messages are dropped).

## Robot sprites (`tools/robot_sprites.py`)

Point-splat renderer: ~1.5M colored points sampled from the CAD surfaces, projected
orthographically with a z-buffer at 3× supersampling from each of Doom's 8 rotations
(14° downward pitch), then quantized to the IWAD palette. Walk frames bob, attack
frames add a muzzle flash, pain throws sparks and oil, death tips the robot over and burns out,
and the gib death blows it into ~12 cm chunks that settle as debris. Lump names match
the IWAD's exactly (including mirrored pairs like `SPOSA2A8`) so they override
one-for-one. The hunter's `HUNT` frames are generated from the 2026 KitBot with oil
blots and red eyes (only on views where its front faces the camera). The IWAD's `BLUD`
sprites are recolored to oil. `build/robots_preview.png` is a contact sheet. Sprite scale is
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
| Sectors | Equal (floor, ceiling, tag, flat) cells merged; tiny regions absorbed; boundary runs → linedefs. Lift edges get special 87 (WR perpetual lift). |
| Textures | CAD material colors → nearest from a curated list of IWAD flats/walls. |

Scale is 128 map units per meter, so the player is a ~0.44 m robot that fits under
the trench. A flood fill from the start checks the field is sealed.

## Next steps

- Split the converter from the map mods (a mod file layered on the base conversion).
- The plugin's sound: a mixer for the sound effects and music (room4doom's rodio backend,
  without rodio), into raptaboard's audio stream.
- A raptaboard content pack built from this repo's CI artifacts.
- Real slopes for the bumps; custom textures (carpet, polycarb, AprilTags).

## License

Our code is MIT ([LICENSE](LICENSE)). Game data (field model, Freedoom, the built WAD)
is not in this repo; see [NOTICE.md](NOTICE.md) for its sources and terms.
