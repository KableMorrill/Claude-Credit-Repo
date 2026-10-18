"""Fetch, verify and unpack the pinned wheels at first run (I2).

Threat model, tampered dependency: every download is checked against the size and SHA-256 in
``wheel_pins.py`` before anything is unpacked. The pins come from PyPI's JSON API through
``scripts/update_wheel_pins.py`` and are reviewed in the diff that changes them. What has been
unpacked is trusted as it stands: ``activate`` and an adopted staging folder don't hash it
again, since anyone who can write to the data folder can already change ``site-packages``.

Under the data folder the Krita glue chooses:

    site-packages/                the unpacked wheels, laid out like a venv's site-packages
    site-packages/installed.json  what is there: platform, Python, package versions, wheels
    site-packages.new-<id>/       an install being built; adopted or removed by a later run
    downloads/                    wheels while they download and verify; emptied afterwards

Each install builds its own ``site-packages.new-<id>`` and swaps it into place only when every
wheel has verified and unpacked, so a failed install leaves the previous one as it was, and two
installs at once (two Krita processes on first run, or two threads) cannot damage each other. If the swap
itself fails, as Windows does while a native library from the old install is loaded, the
verified folder is kept and adopted by the next install without downloading again. The record
is trusted only while every recorded wheel's ``dist-info`` folder is still there.

``activate`` puts ``site-packages`` first on ``sys.path`` when it was built for this platform
and Python, so the plugin's verified copies win over anything else Krita's Python can see;
``sdk.py`` and ``keys.py`` import on demand after that. Once ``c2pa`` has been imported, a newer
install takes effect at the next Krita start, because its native library stays loaded.

Which wheels: c2pa-python always (I2); cryptography and cffi for K1 (decision 1 of 13 Oct
2026), both or neither, since cffi is only there for cryptography. A platform without a
cryptography wheel, such as macOS on Intel, still gets c2pa-python; K1 is then unavailable and
K2's own certificate is the way to sign.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import time
import urllib.request
import uuid
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
STALE_AFTER = 24 * 60 * 60  # seconds; a staging folder older than this belongs to no live install
SWAP_ATTEMPTS = 5
WINDOWS_MAX_PATH = 260  # Krita's executable isn't known to opt in to long paths

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
    try:
        if platform_key is None:
            platform_key = wheels.platform_key()
            if glibc is None and platform_key.startswith("linux"):
                glibc = wheels.glibc_version()
        python = python or wheels.python_key()
    except NoWheel as error:
        raise InstallError(str(error)) from error
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
            "Key creation is not available on this platform yet (no cryptography wheel for it);"
            " signing will need your own certificate when that lands (K2)"
        )
    return Plan(platform_key, python, tuple(chosen), tuple(unavailable))


def site_packages(data_folder: Path) -> Path:
    return data_folder / SITE_PACKAGES


def installed(data_folder: Path) -> Installed | None:
    """What the install records, or None when there is none, it can't be read, or a recorded
    wheel's dist-info folder is gone (then it is reinstalled rather than trusted)."""
    return _read_record(site_packages(data_folder))


def is_current(data_folder: Path, wanted: Plan) -> bool:
    """True when the install matches the plan, so nothing needs fetching."""
    return _matches(installed(data_folder), wanted)


def activate(data_folder: Path) -> Path | None:
    """Put the installed packages first on sys.path, if they were built for this platform and
    Python. Returns the folder, or None if there is nothing usable."""
    current = installed(data_folder)
    if current is None:
        return None
    try:
        if (current.platform, current.python) != (wheels.platform_key(), wheels.python_key()):
            return None
    except NoWheel:
        return None
    site = site_packages(data_folder).resolve()
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
    """Fetch, verify and unpack every wheel in the plan, then swap the result into place.

    A verified folder left by an earlier run whose swap failed is adopted instead, without
    fetching anything.
    """
    fetch = fetch or download
    try:
        data_folder.mkdir(parents=True, exist_ok=True)
        _remove_stale_staging(data_folder)
        staged = _find_staged(data_folder, wanted)
        if staged is None:
            staged = _build(data_folder, wanted, fetch, progress)
        _swap_in(data_folder, staged)
    except InstallError:
        raise
    except Exception as error:
        raise InstallError(f"Installing the wheels failed: {error}") from error
    result = installed(data_folder)
    if result is None or not _matches(result, wanted):
        raise InstallError("The install could not be read back")
    return result


def download(
    wheel: Wheel,
    destination: Path,
    progress: Progress | None = None,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> None:
    """Stream the wheel from PyPI to ``destination``, never past its pinned size."""
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
                try:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(member) as source, open(target, "wb") as output:
                        shutil.copyfileobj(source, output)
                except OSError as error:
                    raise InstallError(write_failure(wheel_path.name, target, error)) from error
    except BadZipFile as error:
        raise InstallError(f"{wheel_path.name} is not a wheel: {error}") from error


def write_failure(wheel_name: str, target: Path, error: OSError, system: str = sys.platform) -> str:
    """Why a file couldn't be unpacked. Windows reports a path past its 260-character limit as
    "No such file or directory", which says nothing useful (seen 14 Oct 2026 in a deep folder)."""
    length = len(str(target))
    if system == "win32" and length >= WINDOWS_MAX_PATH:
        return (
            f"{wheel_name} could not be unpacked: a file in it would need a path {length}"
            f" characters long, past Windows' {WINDOWS_MAX_PATH}-character limit. The data"
            " folder is too deeply nested."
        )
    return f"{wheel_name} could not be unpacked: {error}"


def dist_info(wheel_filename: str) -> str:
    """The dist-info folder a wheel unpacks to: c2pa_python-0.38.0.dist-info."""
    name, version = wheel_filename.split("-", 2)[:2]
    return f"{name}-{version}.dist-info"


def _build(data_folder: Path, wanted: Plan, fetch: Fetch, progress: Progress | None) -> Path:
    """Download, verify and unpack into this process's own staging folder."""
    downloads = data_folder / "downloads"
    run_id = (
        f"{os.getpid()}-{uuid.uuid4().hex[:8]}"  # unique per call, across processes and threads
    )
    staging = data_folder / f"{SITE_PACKAGES}.new-{run_id}"
    downloads.mkdir(parents=True, exist_ok=True)
    staging.mkdir(parents=True)
    try:
        for wheel in wanted.wheels:
            target = downloads / f"{wheel.filename}.{run_id}.part"
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
    except BaseException:
        _remove_tree(staging)
        raise
    return staging


def _find_staged(data_folder: Path, wanted: Plan) -> Path | None:
    """A complete staging folder for this plan, left by a run whose swap failed."""
    for staging in sorted(data_folder.glob(f"{SITE_PACKAGES}.new-*")):
        if _matches(_read_record(staging), wanted):
            return staging
    return None


def _remove_stale_staging(data_folder: Path) -> None:
    """Staging folders older than STALE_AFTER were left by crashed runs."""
    now = time.time()
    for staging in data_folder.glob(f"{SITE_PACKAGES}.new-*"):
        try:
            if now - staging.stat().st_mtime > STALE_AFTER:
                _remove_tree(staging)
        except OSError:
            pass


def _swap_in(data_folder: Path, staging: Path) -> None:
    """Make ``staging`` the site-packages, retrying while a file is briefly locked (Windows)."""
    site = site_packages(data_folder)
    old = data_folder / f"{SITE_PACKAGES}.old-{staging.name.split('new-', 1)[1]}"
    for attempt in range(SWAP_ATTEMPTS):
        try:
            if site.exists():
                _rename(site, old)
            _rename(staging, site)
            break
        except OSError as error:
            if attempt == SWAP_ATTEMPTS - 1:
                raise InstallError(
                    "The downloaded packages verified but could not be moved into place"
                    f" ({error}); restart Krita and run the install again, it will finish"
                    " without downloading"
                ) from error
            time.sleep(0.2 * (attempt + 1))
    _remove_tree(old)  # best effort; Windows keeps a loaded native library busy until restart


def _rename(source: Path, destination: Path) -> None:
    source.rename(destination)


def _read_record(site: Path) -> Installed | None:
    try:
        data = json.loads((site / RECORD).read_text(encoding="utf-8"))
        record = Installed(
            str(data["platform"]),
            str(data["python"]),
            {str(k): str(v) for k, v in data["packages"].items()},
            tuple(str(w) for w in data.get("wheels", [])),
        )
    except (OSError, ValueError, KeyError, AttributeError, TypeError):
        return None
    if not all((site / dist_info(wheel)).is_dir() for wheel in record.wheels):
        return None
    return record


def _matches(current: Installed | None, wanted: Plan) -> bool:
    return (
        current is not None
        and current.platform == wanted.platform
        and current.python == wanted.python
        and current.packages == wanted.packages
    )


def _member_parts(name: str, wheel_name: str) -> tuple[str, ...] | None:
    """Where a wheel member lands, or None to skip it (PEP 427's .data folders aside from
    purelib and platlib hold scripts and headers the plugin has no use for)."""
    path = PurePosixPath(name)
    parts = path.parts
    if not parts:
        return None
    if path.is_absolute() or ".." in parts or any(":" in part or "\\" in part for part in parts):
        raise InstallError(f"{wheel_name} has an unsafe path: {name}")
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
    "dist_info",
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
