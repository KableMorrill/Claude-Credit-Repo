"""The generated pin table is complete and well formed (I2). No network: PyPI is not consulted."""

from __future__ import annotations

import re

import pytest

from krita_provenance import installer, wheels
from krita_provenance.wheel_pins import PINS, WHEELS
from krita_provenance.wheels import PLATFORMS, PYTHONS, NoWheel

K1_GAPS = {"darwin-x86_64", "win32-arm64"}  # cryptography ships no wheel for these


def test_pins_name_the_versions_the_project_relies_on():
    assert PINS == {"c2pa-python": "0.38.0", "cryptography": "50.0.2", "cffi": "2.1.1"}
    assert {w.package for w in WHEELS} == set(PINS)
    for w in WHEELS:
        assert w.version == PINS[w.package]


@pytest.mark.parametrize("w", WHEELS, ids=lambda w: w.filename)
def test_each_pin_is_a_pypi_file_with_a_digest(w):
    assert w.url.startswith("https://files.pythonhosted.org/packages/")
    assert w.url.rsplit("/", 1)[-1] == w.filename
    assert re.fullmatch(r"[0-9a-f]{64}", w.sha256)
    assert w.size > 0
    assert w.platforms and set(w.platforms) <= set(PLATFORMS)
    assert w.pythons and set(w.pythons) <= set(PYTHONS)
    assert (w.min_glibc is not None) == all(p.startswith("linux") for p in w.platforms)
    parsed = wheels.tags(w.filename)
    assert parsed is not None
    assert set(w.pythons) <= set(parsed.pythons)


@pytest.mark.parametrize("platform_key", PLATFORMS)
@pytest.mark.parametrize("python", PYTHONS)
def test_every_platform_and_python_has_one_c2pa_wheel(platform_key: str, python: str):
    assert wheels.select(WHEELS, "c2pa-python", platform_key, python).package == "c2pa-python"


@pytest.mark.parametrize("platform_key", PLATFORMS)
@pytest.mark.parametrize("python", PYTHONS)
def test_k1_wheels_exist_except_where_cryptography_has_none(platform_key: str, python: str):
    if platform_key in K1_GAPS:
        with pytest.raises(NoWheel):
            wheels.select(WHEELS, "cryptography", platform_key, python)
        return
    for package in installer.K1_PACKAGES:
        assert wheels.select(WHEELS, package, platform_key, python).package == package


def test_the_windows_download_is_the_large_one():
    """Spec: the Windows x64 wheel is about 88 MB, the others about 15 MB."""
    plan = installer.plan("win32-x86_64", "3.10")
    assert 80_000_000 < plan.total_size < 100_000_000
    plan = installer.plan("linux-x86_64", "3.10")
    assert 15_000_000 < plan.total_size < 25_000_000
