PY := .venv/bin/python -I
R4D := vendor/room4doom
IWAD := downloads/freedoom/freedoom2.wad

.PHONY: setup assets wad check engine play clean

setup:
	python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt

# AdvantageScope's 2026 field + KitBot models (MIT-licensed asset repo),
# and Freedoom Phase 2 (BSD): a Doom II-compatible IWAD that is safe to bundle.
assets:
	mkdir -p downloads/field2026 downloads/kitbot2026 downloads/freedoom
	gh release download archive-v1 -R Mechanical-Advantage/AdvantageScopeAssets \
	  -p Field3d_2026FRCFieldV2.zip -p Robot_2026FRCKitBotV2.zip -D downloads --skip-existing
	unzip -q -o downloads/Field3d_2026FRCFieldV2.zip -d downloads/field2026
	unzip -q -o downloads/Robot_2026FRCKitBotV2.zip -d downloads/kitbot2026
	gh release download v0.13.0 -R freedoom/freedoom -p freedoom-0.13.0.zip -D downloads --skip-existing
	unzip -q -o -j downloads/freedoom-0.13.0.zip '*/freedoom2.wad' '*/COPYING.txt' -d downloads/freedoom

wad:
	$(PY) tools/field2wad.py

check: wad
	$(PY) tools/check_udmf.py build/frc2026.wad

# Pinned upstream revision plus our gameplay patch (the hunter monster).
$(R4D):
	git clone https://github.com/flukejones/room4doom $(R4D)
	git -C $(R4D) checkout -q `cat patches/room4doom.rev`

engine: $(R4D)
	$(PY) tools/prep_room4doom.py $(R4D)
	cd $(R4D) && (git apply --reverse --check ../../patches/room4doom.patch 2>/dev/null || git apply ../../patches/room4doom.patch)
	cd $(R4D) && cargo build --release -p room4doom

play: wad
	$(R4D)/target/release/room4doom -i $(IWAD) -p build/frc2026.wad -m 1

clean:
	rm -rf build
