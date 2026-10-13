"""Regenerate krita_provenance/wheel_pins.py from PyPI (I2).

    uv run python scripts/update_wheel_pins.py

For each pinned package and version in PINS, this reads PyPI's JSON API, picks one wheel per
platform and Python the plugin supports (krita_provenance/wheels.py says which), preferring
the wheel with the lowest glibc floor on Linux and the architecture-specific one on macOS,
and writes their file names, URLs, sizes and SHA-256 digests. Review the diff before
committing it: those digests are what the plugin checks every download against.

Change a version here, then run this.
"""

from __future__ import annotations

import json
import sys
import urllib.request
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))  # the package is not installed into the venv

from krita_provenance import wheels

PINS = {"c2pa-python": "0.38.0", "cryptography": "50.0.2", "cffi": "2.1.1"}
OUTPUT = REPO / "krita_provenance" / "wheel_pins.py"
PYPI = "https://pypi.org/pypi/{package}/{version}/json"

HEADER = '''"""Pinned wheels the plugin fetches at first run (I2). GENERATED: do not edit by hand.

Made by scripts/update_wheel_pins.py on {today} from PyPI's JSON API. Each entry is one file
as PyPI published it; installer.py refuses any download whose size or SHA-256 differs.
See wheels.py for how a wheel is chosen for a platform and Python.
"""

from __future__ import annotations

from .wheels import Wheel

PINS = {pins!r}

WHEELS: tuple[Wheel, ...] = (
'''


def fetch(package: str, version: str) -> list[dict]:
    with urllib.request.urlopen(PYPI.format(package=package, version=version), timeout=60) as r:
        return json.load(r)["urls"]


def choose(package: str, version: str, files: list[dict]) -> list[wheels.Wheel]:
    """One wheel per (platform, python), merged into entries that list what each serves."""
    best: dict[tuple[str, str], tuple[tuple[int, ...], dict, tuple[int, int] | None]] = {}
    for file in files:
        if not file["filename"].endswith(".whl") or file.get("yanked"):
            continue
        tags = wheels.tags(file["filename"])
        if tags is None:
            continue
        platforms = dict(tags.platforms)
        universal = "darwin-any" in platforms
        if universal:
            floor = platforms.pop("darwin-any")
            for key in ("darwin-x86_64", "darwin-arm64"):
                platforms.setdefault(key, floor)
        for platform_key, floor in platforms.items():
            rank = _rank(floor, universal, tags.specificity)
            for python in tags.pythons:
                current = best.get((platform_key, python))
                if current is None or rank > current[0]:
                    best[(platform_key, python)] = (rank, file, floor)

    by_file: dict[str, tuple[dict, tuple[int, int] | None, set[str], set[str]]] = {}
    for (platform_key, python), (_, file, floor) in best.items():
        entry = by_file.setdefault(file["filename"], (file, floor, set(), set()))
        entry[2].add(platform_key)
        entry[3].add(python)
    chosen = []
    for file, floor, platforms_served, pythons_served in by_file.values():
        chosen.append(
            wheels.Wheel(
                package=package,
                version=version,
                filename=file["filename"],
                url=file["url"],
                size=int(file["size"]),
                sha256=file["digests"]["sha256"],
                platforms=tuple(p for p in wheels.PLATFORMS if p in platforms_served),
                pythons=tuple(p for p in wheels.PYTHONS if p in pythons_served),
                min_glibc=floor,
            )
        )
    return sorted(chosen, key=lambda w: w.filename)


def _rank(floor: tuple[int, int] | None, universal: bool, specificity: int) -> tuple[int, ...]:
    """Higher is better: the lowest glibc floor, an architecture-specific macOS wheel, then the
    build made for the newest CPython, as pip would pick."""
    glibc = -(floor[0] * 1000 + floor[1]) if floor else 0
    return (glibc, 0 if universal else 1, specificity)


def render(chosen: list[wheels.Wheel]) -> str:
    lines = [HEADER.format(today=date.today().isoformat(), pins=PINS)]
    for wheel in chosen:
        lines.append("    Wheel(")
        for field in (
            "package",
            "version",
            "filename",
            "url",
            "size",
            "sha256",
            "platforms",
            "pythons",
            "min_glibc",
        ):
            lines.append(f"        {field}={getattr(wheel, field)!r},")
        lines.append("    ),")
    lines.append(")\n")
    return "\n".join(lines)


def main() -> None:
    chosen: list[wheels.Wheel] = []
    for package, version in PINS.items():
        chosen += choose(package, version, fetch(package, version))
    OUTPUT.write_text(render(chosen), encoding="utf-8")
    for wheel in chosen:
        print(
            f"{wheel.package} {wheel.filename}: {', '.join(wheel.platforms)} | {', '.join(wheel.pythons)} | {wheel.size} bytes"
        )
    print(f"wrote {OUTPUT.relative_to(REPO)} with {len(chosen)} wheels")


if __name__ == "__main__":
    main()
