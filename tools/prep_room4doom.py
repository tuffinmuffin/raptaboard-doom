#!/usr/bin/env python3
"""Trim room4doom's workspace so the game builds from a plain clone.

Upstream patches gpui/tiny-skia to local vendor dirs created by `just setup-gpui`
(a Zed checkout). Only the GUI dev tools need them, so drop those members and patches.
Idempotent.
"""
import re
import sys
from pathlib import Path

GUI_TOOLS = ["tools/bsp-viewer", "tools/editor", "tools/voxel-viewer", "tools/ui-common"]

path = Path(sys.argv[1] if len(sys.argv) > 1 else "vendor/room4doom") / "Cargo.toml"
text = path.read_text()
orig = text
for member in GUI_TOOLS:
    text = re.sub(rf'^\s*"{re.escape(member)}",\n', "", text, flags=re.M)
# remove [patch.*] tables (header through the next blank line)
text = re.sub(r'^\[patch\.[^\]]+\]\n(?:[^\n\[][^\n]*\n)*\n?', "", text, flags=re.M)
if text != orig:
    path.write_text(text)
    print(f"patched {path}")
else:
    print(f"{path} already prepared")
