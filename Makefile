PY := .venv/bin/python -I
R4D := vendor/room4doom

.PHONY: setup assets wad check engine play clean

setup:
	python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt

# AdvantageScope's 2026 field + KitBot models (MIT-licensed asset repo)
assets:
	mkdir -p downloads/field2026 downloads/kitbot2026
	gh release download archive-v1 -R Mechanical-Advantage/AdvantageScopeAssets \
	  -p Field3d_2026FRCFieldV2.zip -p Robot_2026FRCKitBotV2.zip -D downloads --skip-existing
	unzip -q -o downloads/Field3d_2026FRCFieldV2.zip -d downloads/field2026
	unzip -q -o downloads/Robot_2026FRCKitBotV2.zip -d downloads/kitbot2026

wad:
	$(PY) tools/field2wad.py

check: wad
	$(PY) tools/check_udmf.py build/frc2026.wad

$(R4D):
	git clone --depth 1 https://github.com/flukejones/room4doom $(R4D)

engine: $(R4D)
	$(PY) tools/prep_room4doom.py $(R4D)
	cd $(R4D) && cargo build --release -p room4doom

play: wad
	$(R4D)/target/release/room4doom -i $(R4D)/data/doom1.wad -p build/frc2026.wad -e 1 -m 1

clean:
	rm -rf build
