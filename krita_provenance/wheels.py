"""Which pinned wheel fits this machine (I2), with nothing but the standard library.

The plugin fetches three wheels at first run: c2pa-python (spec I2), and cryptography with its
dependency cffi for K1 (decision 1 of 13 Oct 2026). ``wheel_pins.py`` holds the pinned
versions and, for each wheel, its exact file name, URL, size and SHA-256 as PyPI published
them; ``scripts/update_wheel_pins.py`` regenerates it. This module picks the wheel for a
platform and Python version, both when generating the pins and at runtime inside Krita.

Platforms are keyed ``<sys.platform>-<arch>`` with ``arch`` ``x86_64`` or ``arm64``:
``linux-x86_64``, ``linux-arm64``, ``darwin-x86_64``, ``darwin-arm64``, ``win32-x86_64`` and
``win32-arm64``. Pythons are ``3.10`` to ``3.14``: Krita 5 embeds 3.10, and CI tests up to 3.14.
Free-threaded and PyPy builds are not Krita's and are skipped.
"""

from __future__ import annotations

import platform
import re
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

PYTHONS = ("3.10", "3.11", "3.12", "3.13", "3.14")
PLATFORMS = (
    "linux-x86_64",
    "linux-arm64",
    "darwin-x86_64",
    "darwin-arm64",
    "win32-x86_64",
    "win32-arm64",
)

_ARCHES = {
    "x86_64": "x86_64",
    "amd64": "x86_64",
    "aarch64": "arm64",
    "arm64": "arm64",
}


@dataclass(frozen=True)
class Wheel:
    package: str
    version: str
    filename: str
    url: str
    size: int
    sha256: str
    platforms: tuple[str, ...]  # keys from PLATFORMS this wheel serves
    pythons: tuple[str, ...]  # entries of PYTHONS this wheel serves
    min_glibc: tuple[int, int] | None = None  # for manylinux wheels

    def serves(self, platform_key: str, python: str) -> bool:
        return platform_key in self.platforms and python in self.pythons


class NoWheel(LookupError):
    """No pinned wheel serves this platform and Python."""


def platform_key(system: str = sys.platform, machine: str | None = None) -> str:
    """This machine's key, or a clear error for one the pins do not cover."""
    machine = machine if machine is not None else platform.machine()
    system = "linux" if system.startswith("linux") else system
    arch = _ARCHES.get(machine.lower())
    if arch is None or system not in ("linux", "darwin", "win32"):
        raise NoWheel(f"No pinned wheels for {system} on {machine}")
    return f"{system}-{arch}"


def python_key(version_info: tuple[int, ...] = sys.version_info[:2]) -> str:
    key = f"{version_info[0]}.{version_info[1]}"
    if key not in PYTHONS:
        raise NoWheel(f"No pinned wheels for Python {key}; this plugin covers {', '.join(PYTHONS)}")
    return key


def select(wheels: Iterable[Wheel], package: str, platform_key: str, python: str) -> Wheel:
    matches = [w for w in wheels if w.package == package and w.serves(platform_key, python)]
    if not matches:
        raise NoWheel(f"No pinned {package} wheel for {platform_key} on Python {python}")
    if len(matches) > 1:
        raise NoWheel(
            f"{len(matches)} pinned {package} wheels for {platform_key} on Python {python}"
        )
    return matches[0]


def glibc_version() -> tuple[int, int] | None:
    """The running glibc, or None when there is none or it can't be told (then: try)."""
    _, version = platform.libc_ver()
    match = re.fullmatch(r"(\d+)\.(\d+)", version)
    return (int(match.group(1)), int(match.group(2))) if match else None


# What a wheel's file name tells, for scripts/update_wheel_pins.py: PEP 427 names are
# {name}-{version}(-{build})?-{python}-{abi}-{platform}.whl, each tag set dot-joined.

_WHEEL_NAME = re.compile(
    r"^(?P<name>[^-]+)-(?P<version>[^-]+)(-(?P<build>[^-]+))?"
    r"-(?P<python>[^-]+)-(?P<abi>[^-]+)-(?P<platform>[^-]+)\.whl$"
)
_MANYLINUX = re.compile(r"manylinux_(\d+)_(\d+)_(x86_64|aarch64)")
_LEGACY_MANYLINUX = {"manylinux1": (2, 5), "manylinux2010": (2, 12), "manylinux2014": (2, 17)}


@dataclass(frozen=True)
class WheelTags:
    pythons: tuple[str, ...]  # of PYTHONS
    platforms: dict[str, tuple[int, int] | None]  # platform key -> glibc floor (Linux only)
    specificity: int  # the cp minor an abi3 or version-specific wheel was built for; 0 for py3


def tags(filename: str) -> WheelTags | None:
    """The Pythons and platforms a wheel serves, or None if it is not for a CPython Krita uses."""
    match = _WHEEL_NAME.match(filename)
    if match is None:
        return None
    pythons: set[str] = set()
    specificity = 0
    for python_tag in match.group("python").split("."):
        for abi_tag in match.group("abi").split("."):
            served = _pythons_for(python_tag, abi_tag)
            pythons.update(served)
            if served and python_tag.startswith("cp3"):
                specificity = max(specificity, int(python_tag[3:]))
    platforms: dict[str, tuple[int, int] | None] = {}
    for platform_tag in match.group("platform").split("."):
        key, floor = _platform_for(platform_tag)
        if key is not None and (key not in platforms or _lower(floor, platforms[key])):
            platforms[key] = floor
    if not pythons or not platforms:
        return None
    return WheelTags(tuple(p for p in PYTHONS if p in pythons), platforms, specificity)


def _pythons_for(python_tag: str, abi_tag: str) -> Sequence[str]:
    if python_tag == "py3" and abi_tag == "none":
        return PYTHONS
    version = re.fullmatch(r"cp3(\d+)", python_tag)
    if version is None:
        return ()
    minor = int(version.group(1))
    if abi_tag == "abi3":
        return tuple(p for p in PYTHONS if int(p.split(".")[1]) >= minor)
    if abi_tag == f"cp3{minor}":  # a version-specific, non-free-threaded build
        return tuple(p for p in PYTHONS if p == f"3.{minor}")
    return ()


def _platform_for(tag: str) -> tuple[str | None, tuple[int, int] | None]:
    if tag.startswith("win_"):
        arch = _ARCHES.get(tag[len("win_") :])
        return (f"win32-{arch}" if arch else None), None
    macos = re.fullmatch(r"macosx_(\d+)_(\d+)_(x86_64|arm64|universal2)", tag)
    if macos:
        if macos.group(3) == "universal2":
            return "darwin-any", None  # expanded by the caller for both macOS arches
        return f"darwin-{_ARCHES[macos.group(3)]}", None
    modern = _MANYLINUX.fullmatch(tag)
    if modern:
        arch = _ARCHES[modern.group(3)]
        return f"linux-{arch}", (int(modern.group(1)), int(modern.group(2)))
    legacy = re.fullmatch(r"(manylinux1|manylinux2010|manylinux2014)_(x86_64|aarch64)", tag)
    if legacy:
        return f"linux-{_ARCHES[legacy.group(2)]}", _LEGACY_MANYLINUX[legacy.group(1)]
    return None, None  # musllinux, iOS, i686, ppc64le, s390x: not Krita's


def _lower(a: tuple[int, int] | None, b: tuple[int, int] | None) -> bool:
    return a is not None and (b is None or a < b)
