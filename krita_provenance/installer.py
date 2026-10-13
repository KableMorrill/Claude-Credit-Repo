"""Fetch, verify and unpack the pinned wheels at first run (I2).

Threat model, tampered dependency: every download is checked against the size and SHA-256 in
``wheel_pins.py`` before anything is unpacked. The pins come from PyPI's JSON API through
``scripts/update_wheel_pins.py`` and are reviewed in the diff that changes them.

Under the data folder the Krita glue chooses:

    site-packages/                the unpacked wheels, laid out like a venv's site-packages
    site-packages/installed.json  what is there: platform, Python, package versions, wheels
    downloads/                    wheels while they download and verify; emptied afterwards

A new install is built in ``site-packages.new`` and swapped in only when every wheel has
verified and unpacked, so a failed install leaves the previous one as it was. ``activate``
puts ``site-packages`` first on ``sys.path`` so the plugin's verified copies win over anything
else Krita's Python can see; ``sdk.py`` and ``keys.py`` import on demand after that. Once
``c2pa`` has been imported, a newer install takes effect at the next Krita start, because its
native library stays loaded.

Which wheels: c2pa-python always (I2); cryptography and cffi for K1 (decision 1 of 13 Oct
2026), both or neither, since cffi is only there for cryptography. A platform without a
cryptography wheel, such as macOS on Intel, still gets c2pa-python; K1 is then unavailable and
K2's own certificate is the way to sign.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
import urllib.request
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any
from zipfile import BadZipFile, ZipFile

from . import wheels
from .wheel_pins import PINS, WHEELS
from .wheels import NoWheel, Wheel

PACKAGES = ("c2pa-python", "cryptography", "cffi")
K1_PACKAGES = ("cryptography", "cffi")
SITE_PACKAGES = "site-packages"
RECORD = "installed.json"
CHUNK = 1024 * 1024

Progress = Callable[[Wheel, int, int], None]  # wheel, bytes so far, bytes expected
Fetch = Callable[[Wheel, Path, "Progress | None"], None]  # writes the wheel to the path


class InstallError(Exception):
    """The message is safe to show to the user."""


@dataclass(frozen=True)
class Plan:
    """What this machine needs, and what it cannot have."""

    platform: str
    python: str
    wheels: tuple[Wheel, ...]
    unavailable: tuple[str, ...] = ()  # why a package is not in `wheels`

    @property
    def packages(self) -> dict[str, str]:
        return {wheel.package: wheel.version for wheel in self.wheels}

    @property
    def total_size(self) -> int:
        return sum(wheel.size for wheel in self.wheels)

    @property
    def supports_k1(self) -> bool:
        return all(package in self.packages for package in K1_PACKAGES)


@dataclass(frozen=True)
class Installed:
    platform: str
    python: str
    packages: dict[str, str]
    wheels: tuple[str, ...] = field(default_factory=tuple)


def plan(
    platform_key: str | None = None,
    python: str | None = None,
    pins: Iterable[Wheel] = WHEELS,
    glibc: tuple[int, int] | None = None,
) -> Plan:
    """The wheels for a platform and Python; by default this machine's.

    ``glibc`` is the glibc version to check manylinux wheels against. With the platform left to
    detection it is looked up too; with an explicit platform, None means no check.
    """
    if platform_key is None:
        platform_key = wheels.platform_key()
        if glibc is None and platform_key.startswith("linux"):
            glibc = wheels.glibc_version()
    python = python or wheels.python_key()
    pins = tuple(pins)
    chosen: list[Wheel] = []
    unavailable: list[str] = []
    for package in PACKAGES:
        try:
            wheel = wheels.select(pins, package, platform_key, python)
        except NoWheel as error:
            unavailable.append(str(error))
            continue
        if wheel.min_glibc and glibc and glibc < wheel.min_glibc:
            unavailable.append(
                f"{package} needs glibc {_dotted(wheel.min_glibc)}; this system has"
                f" {_dotted(glibc)}"
            )
            continue
        chosen.append(wheel)
    names = {wheel.package for wheel in chosen}
    if "c2pa-python" not in names:
        raise InstallError("; ".join(unavailable) or "c2pa-python has no wheel for this machine")
    if not all(package in names for package in K1_PACKAGES):
        chosen = [wheel for wheel in chosen if wheel.package not in K1_PACKAGES]
        unavailable.append(
            "Local certificates (K1) are unavailable here: cryptography and cffi are installed"
            " together or not at all"
        )
    return Plan(platform_key, python, tuple(chosen), tuple(unavailable))


def site_packages(data_folder: Path) -> Path:
    return data_folder / SITE_PACKAGES


def installed(data_folder: Path) -> Installed | None:
    """What a previous install recorded, or None if there is none or it can't be read."""
    try:
        data = json.loads((site_packages(data_folder) / RECORD).read_text(encoding="utf-8"))
        return Installed(
            str(data["platform"]),
            str(data["python"]),
            {str(k): str(v) for k, v in data["packages"].items()},
            tuple(str(w) for w in data.get("wheels", [])),
        )
    except (OSError, ValueError, KeyError, AttributeError, TypeError):
        return None


def is_current(data_folder: Path, wanted: Plan) -> bool:
    """True when the install matches the plan, so nothing needs fetching."""
    current = installed(data_folder)
    return (
        current is not None
        and current.platform == wanted.platform
        and current.python == wanted.python
        and current.packages == wanted.packages
    )


def activate(data_folder: Path) -> Path | None:
    """Put the installed packages first on sys.path. Returns the folder, or None if none."""
    site = site_packages(data_folder)
    if not (site / RECORD).is_file():
        return None
    entry = str(site)
    if entry not in sys.path:
        sys.path.insert(0, entry)
    return site


def install(
    data_folder: Path,
    wanted: Plan,
    fetch: Fetch | None = None,
    progress: Progress | None = None,
) -> Installed:
    """Fetch, verify and unpack every wheel in the plan, then swap the result into place."""
    fetch = fetch or download
    downloads = data_folder / "downloads"
    staging = data_folder / f"{SITE_PACKAGES}.new"
    _remove_tree(staging)
    downloads.mkdir(parents=True, exist_ok=True)
    staging.mkdir(parents=True)
    try:
        for wheel in wanted.wheels:
            target = downloads / wheel.filename
            try:
                fetch(wheel, target, progress)
                verify_file(wheel, target)
                unpack(target, staging)
            finally:
                target.unlink(missing_ok=True)
        record = {
            "platform": wanted.platform,
            "python": wanted.python,
            "packages": wanted.packages,
            "wheels": [wheel.filename for wheel in wanted.wheels],
        }
        (staging / RECORD).write_text(json.dumps(record, indent=2), encoding="utf-8")
    except InstallError:
        _remove_tree(staging)
        raise
    except Exception as error:
        _remove_tree(staging)
        raise InstallError(f"Installing the wheels failed: {error}") from error

    site = site_packages(data_folder)
    old = data_folder / f"{SITE_PACKAGES}.old"
    _remove_tree(old)
    if site.exists():
        site.rename(old)
    staging.rename(site)
    _remove_tree(old)  # best effort; Windows keeps a loaded native library busy until restart
    result = installed(data_folder)
    if result is None:
        raise InstallError("The install could not be read back")
    return result


def download(
    wheel: Wheel,
    destination: Path,
    progress: Progress | None = None,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> None:
    """Stream the wheel from PyPI to ``destination``, never past its pinned size."""
    digest = hashlib.sha256()
    received = 0
    try:
        with opener(wheel.url, timeout=60) as response, open(destination, "wb") as output:
            while chunk := response.read(CHUNK):
                received += len(chunk)
                if received > wheel.size:
                    raise InstallError(
                        f"{wheel.filename} is larger than the pinned {wheel.size} bytes;"
                        " the download was abandoned"
                    )
                digest.update(chunk)
                output.write(chunk)
                if progress is not None:
                    progress(wheel, received, wheel.size)
    except InstallError:
        raise
    except OSError as error:
        raise InstallError(f"Could not download {wheel.filename}: {error}") from error


def fetch_from_folder(folder: Path) -> Fetch:
    """A fetch that copies wheels already on disk, for an offline install (I4)."""

    def fetch(wheel: Wheel, destination: Path, progress: Progress | None) -> None:
        source = folder / wheel.filename
        if not source.is_file():
            raise InstallError(f"{wheel.filename} is not in {folder}")
        shutil.copyfile(source, destination)
        if progress is not None:
            progress(wheel, wheel.size, wheel.size)

    return fetch


def verify_file(wheel: Wheel, path: Path) -> None:
    """Size and SHA-256 must match the pin exactly; anything else is refused."""
    size = path.stat().st_size
    if size != wheel.size:
        raise InstallError(f"{wheel.filename} is {size} bytes; the pinned wheel is {wheel.size}")
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        while chunk := source.read(CHUNK):
            digest.update(chunk)
    if digest.hexdigest() != wheel.sha256:
        raise InstallError(
            f"{wheel.filename} does not match its pinned SHA-256; it was not installed"
        )


def unpack(wheel_path: Path, destination: Path) -> None:
    """Extract a wheel into ``destination`` like a site-packages folder, refusing unsafe paths."""
    root = destination.resolve()
    try:
        with ZipFile(wheel_path) as archive:
            for member in archive.infolist():
                parts = _member_parts(member.filename, wheel_path.name)
                if parts is None or member.is_dir():
                    continue
                target = root.joinpath(*parts)
                if not target.resolve().is_relative_to(root):
                    raise InstallError(f"{wheel_path.name} has an unsafe path: {member.filename}")
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as source, open(target, "wb") as output:
                    shutil.copyfileobj(source, output)
    except BadZipFile as error:
        raise InstallError(f"{wheel_path.name} is not a wheel: {error}") from error


def _member_parts(name: str, wheel_name: str) -> tuple[str, ...] | None:
    """Where a wheel member lands, or None to skip it (PEP 427's .data folders aside from
    purelib and platlib hold scripts and headers the plugin has no use for)."""
    path = PurePosixPath(name)
    parts = path.parts
    if (
        not parts
        or path.is_absolute()
        or ".." in parts
        or any(":" in part or "\\" in part for part in parts)
    ):
        if parts and (
            path.is_absolute()
            or ".." in parts
            or any(":" in part or "\\" in part for part in parts)
        ):
            raise InstallError(f"{wheel_name} has an unsafe path: {name}")
        return None
    if parts[0].endswith(".data"):
        if len(parts) > 2 and parts[1] in ("purelib", "platlib"):
            return parts[2:]
        return None
    return parts


def _remove_tree(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


def _dotted(version: tuple[int, int]) -> str:
    return f"{version[0]}.{version[1]}"


__all__ = [
    "PACKAGES",
    "PINS",
    "InstallError",
    "Installed",
    "Plan",
    "activate",
    "download",
    "fetch_from_folder",
    "install",
    "installed",
    "is_current",
    "plan",
    "site_packages",
    "unpack",
    "verify_file",
]
