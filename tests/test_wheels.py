"""Which pinned wheel fits a machine (I2), from file names alone."""

from __future__ import annotations

import platform

import pytest

from krita_provenance import wheels
from krita_provenance.wheels import PLATFORMS, PYTHONS, NoWheel, Wheel, tags

ALL = PYTHONS


@pytest.mark.parametrize(
    ("filename", "pythons", "platforms", "specificity"),
    [
        (
            "c2pa_python-0.38.0-py3-none-manylinux_2_28_x86_64.whl",
            ALL,
            {"linux-x86_64": (2, 28)},
            0,
        ),
        (
            "c2pa_python-0.38.0-py3-none-manylinux_2_28_aarch64.whl",
            ALL,
            {"linux-arm64": (2, 28)},
            0,
        ),
        ("c2pa_python-0.38.0-py3-none-macosx_10_9_x86_64.whl", ALL, {"darwin-x86_64": None}, 0),
        ("c2pa_python-0.38.0-py3-none-macosx_11_0_arm64.whl", ALL, {"darwin-arm64": None}, 0),
        ("x-1.0-py3-none-macosx_10_9_universal2.whl", ALL, {"darwin-any": None}, 0),
        ("c2pa_python-0.38.0-py3-none-win_amd64.whl", ALL, {"win32-x86_64": None}, 0),
        ("c2pa_python-0.38.0-py3-none-win_arm64.whl", ALL, {"win32-arm64": None}, 0),
        (
            "cryptography-50.0.2-cp39-abi3-manylinux2014_x86_64.manylinux_2_17_x86_64.whl",
            ALL,
            {"linux-x86_64": (2, 17)},
            9,
        ),
        (
            "cryptography-50.0.2-cp311-abi3-win_amd64.whl",
            ("3.11", "3.12", "3.13", "3.14"),
            {"win32-x86_64": None},
            11,
        ),
        ("cffi-2.1.1-cp310-cp310-win_amd64.whl", ("3.10",), {"win32-x86_64": None}, 10),
        ("cffi-2.1.1-cp314-cp314-macosx_10_15_x86_64.whl", ("3.14",), {"darwin-x86_64": None}, 14),
        (
            "x-1.0-cp310-cp310-manylinux1_x86_64.manylinux_2_28_x86_64.whl",
            ("3.10",),
            {"linux-x86_64": (2, 5)},
            10,
        ),
    ],
)
def test_tags_read_pythons_and_platforms(filename: str, pythons, platforms, specificity: int):
    result = tags(filename)
    assert result is not None
    assert result.pythons == tuple(pythons)
    assert result.platforms == platforms
    assert result.specificity == specificity


@pytest.mark.parametrize(
    "filename",
    [
        "cryptography-50.0.2-cp314-cp314t-win_amd64.whl",  # free-threaded
        "cryptography-50.0.2-pp311-pypy311_pp73-win_amd64.whl",  # PyPy
        "x-1.0-cp310-cp310-musllinux_1_2_x86_64.whl",  # musl
        "x-1.0-cp310-cp310-manylinux_2_17_i686.manylinux2014_i686.whl",  # 32-bit
        "x-1.0-cp39-cp39-win_amd64.whl",  # a Python Krita doesn't embed
        "x-1.0-cp313-cp313-ios_13_0_arm64_iphoneos.whl",
        "x-1.0.tar.gz",
        "nonsense.whl",
    ],
)
def test_tags_skip_what_krita_cannot_use(filename: str):
    assert tags(filename) is None


@pytest.mark.parametrize(
    ("system", "machine", "expected"),
    [
        ("linux", "x86_64", "linux-x86_64"),
        ("linux", "aarch64", "linux-arm64"),
        ("linux2", "x86_64", "linux-x86_64"),
        ("darwin", "arm64", "darwin-arm64"),
        ("darwin", "x86_64", "darwin-x86_64"),
        ("win32", "AMD64", "win32-x86_64"),
        ("win32", "ARM64", "win32-arm64"),
    ],
)
def test_platform_key(system: str, machine: str, expected: str):
    assert wheels.platform_key(system, machine) == expected
    assert expected in PLATFORMS


@pytest.mark.parametrize(
    ("system", "machine"), [("freebsd14", "x86_64"), ("linux", "i686"), ("win32", "x86")]
)
def test_platform_key_refuses_the_unpinned(system: str, machine: str):
    with pytest.raises(NoWheel, match="No pinned wheels"):
        wheels.platform_key(system, machine)


def test_python_key():
    assert wheels.python_key((3, 10, 11)) == "3.10"
    assert wheels.python_key((3, 14, 0)) == "3.14"
    for version in ((3, 9, 18), (3, 15, 0), (4, 0, 0)):
        with pytest.raises(NoWheel, match="No pinned wheels for Python"):
            wheels.python_key(version)


def wheel(
    package: str, platforms: tuple[str, ...], pythons: tuple[str, ...], name: str = "a"
) -> Wheel:
    return Wheel(package, "1.0", f"{name}.whl", "https://x/a.whl", 1, "0" * 64, platforms, pythons)


def test_select_wants_exactly_one_match():
    pins = [
        wheel("p", ("linux-x86_64",), ("3.10",), "one"),
        wheel("p", ("win32-x86_64",), PYTHONS, "two"),
        wheel("q", ("linux-x86_64",), ("3.10",), "three"),
    ]
    assert wheels.select(pins, "p", "linux-x86_64", "3.10").filename == "one.whl"
    assert wheels.select(pins, "p", "win32-x86_64", "3.14").filename == "two.whl"
    with pytest.raises(NoWheel, match=r"No pinned p wheel for linux-x86_64 on Python 3\.11"):
        wheels.select(pins, "p", "linux-x86_64", "3.11")
    with pytest.raises(NoWheel, match="2 pinned p wheels"):
        wheels.select(
            pins + [wheel("p", ("linux-x86_64",), ("3.10",), "four")], "p", "linux-x86_64", "3.10"
        )


def test_glibc_version(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(platform, "libc_ver", lambda: ("glibc", "2.35"))
    assert wheels.glibc_version() == (2, 35)
    monkeypatch.setattr(platform, "libc_ver", lambda: ("", ""))
    assert wheels.glibc_version() is None


def test_this_machine_has_a_key():
    assert wheels.platform_key() in PLATFORMS
    assert wheels.python_key() in PYTHONS
