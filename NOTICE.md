# Notices

The code in this repository (`tools/`, `Makefile`, `patches/`) is MIT-licensed; see
[LICENSE](LICENSE).

This repository does **not** contain game data. The field model, the IWAD and the
generated map (`build/frc2026.wad`) are downloaded or built locally and are gitignored.
Anyone who redistributes a built WAD or a bundle containing these files must carry the
notices below.

## Third-party inputs

| What | Source | License |
|---|---|---|
| 2026 FRC field and 2024/2025/2026 KitBot 3D models (the KitBot sprites are renders of these) | [AdvantageScopeAssets](https://github.com/Mechanical-Advantage/AdvantageScopeAssets) by Littleton Robotics (FRC 6328) | BSD-3-Clause-style: keep the copyright notice and disclaimer; don't use their names to endorse derived products |
| Underlying field design and CAD | *FIRST*® ([playing field resources](https://www.firstinspires.org/robotics/frc/playing-field)) | No specific license published for the field CAD; *FIRST*® and *FIRST*® Robotics Competition are trademarks of *FIRST* |
| Freedoom Phase 2 IWAD (`freedoom2.wad`) | [Freedoom](https://freedoom.github.io/) | BSD-3-Clause; see `COPYING.txt` in the release |
| room4doom engine | [flukejones/room4doom](https://github.com/flukejones/room4doom) | MIT, Copyright (c) 2020 Luke Jones. `patches/room4doom.patch` is a modification of it under the same license |

The generated map is a simplified derivative of the field model: it voxelizes the
geometry into Doom floors and ceilings. This project is not affiliated with or
endorsed by *FIRST*, Littleton Robotics or the Freedoom project.
