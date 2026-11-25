PY := .venv/bin/python -I
R4D := vendor/room4doom
IWAD := downloads/freedoom/freedoom2.wad

.PHONY: setup assets wad sprites face check patched engine plugin play clean

setup:
	python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt

# AdvantageScope's 2026 field + KitBot models (MIT-licensed asset repo),
# and Freedoom Phase 2 (BSD): a Doom II-compatible IWAD that is safe to bundle.
assets:
	mkdir -p downloads/field2026 downloads/kitbot2026 downloads/freedoom
	mkdir -p downloads/kitbot2025 downloads/kitbot2024
	gh release download archive-v1 -R Mechanical-Advantage/AdvantageScopeAssets \
	  -p Field3d_2026FRCFieldV2.zip -p Robot_2026FRCKitBotV2.zip \
	  -p Robot_2025FRCKitBotV3.zip -p Robot_2024FRCKitBotV4.zip -D downloads --skip-existing
	unzip -q -o downloads/Field3d_2026FRCFieldV2.zip -d downloads/field2026
	unzip -q -o downloads/Robot_2026FRCKitBotV2.zip -d downloads/kitbot2026
	unzip -q -o downloads/Robot_2025FRCKitBotV3.zip -d downloads/kitbot2025
	unzip -q -o downloads/Robot_2024FRCKitBotV4.zip -d downloads/kitbot2024
	gh release download v0.13.0 -R freedoom/freedoom -p freedoom-0.13.0.zip -D downloads --skip-existing
	unzip -q -o -j downloads/freedoom-0.13.0.zip '*/freedoom2.wad' '*/COPYING.txt' -d downloads/freedoom

wad:
	$(PY) tools/field2wad.py

# KitBot 2026/2025/2024 rendered over the zombieman/shotgunner/chaingunner sprites (~1.5 min)
sprites: build/robots.wad
build/robots.wad: tools/robot_sprites.py
	$(PY) tools/robot_sprites.py

# Pixel-art robot faces for the status bar (replaces the STF* faces)
face: build/face.wad
build/face.wad: tools/robot_face.py
	$(PY) tools/robot_face.py

check: wad
	$(PY) tools/check_udmf.py build/frc2026.wad

# Pinned upstream revision plus our gameplay patch (the hunter monster).
$(R4D):
	git clone https://github.com/flukejones/room4doom $(R4D)
	git -C $(R4D) checkout -q `cat patches/room4doom.rev`

patched: $(R4D)
	$(PY) tools/prep_room4doom.py $(R4D)
	cd $(R4D) && (git apply --reverse --check ../../patches/room4doom.patch 2>/dev/null || git apply ../../patches/room4doom.patch)

engine: patched
	cd $(R4D) && cargo build --release -p room4doom

# The raptaboard tab plugin (plugin/): the engine built for WebAssembly, in
# build/plugin with its plugin.toml and WADs, as raptaboard loads it:
#   raptaboard plugin dev build/plugin
# Needs `rustup target add wasm32-wasip2`.
PLUGIN := build/plugin
plugin: patched wad sprites face
	cd plugin && cargo build --release --target wasm32-wasip2
	mkdir -p $(PLUGIN)
	cp plugin/target/wasm32-wasip2/release/doom.wasm $(PLUGIN)/widget.wasm
	cp plugin/plugin.toml $(IWAD) build/frc2026.wad build/robots.wad build/face.wad $(PLUGIN)/

play: wad sprites face
	$(R4D)/target/release/room4doom -i $(IWAD) -p build/frc2026.wad -p build/robots.wad -p build/face.wad -m 1

clean:
	rm -rf build
