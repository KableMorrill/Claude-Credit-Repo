"""I2: pinned wheels are fetched, verified by size and SHA-256, unpacked and imported lazily.

Downloads are faked; the pins table itself is checked in tests/test_wheel_pins.py.
"""

from __future__ import annotations

import hashlib
import importlib
import io
import json
import sys
import zipfile
from pathlib import Path

import pytest

from krita_provenance import installer
from krita_provenance.installer import InstallError, Plan
from krita_provenance.wheels import Wheel

LINUX = "linux-x86_64"


def make_wheel(
    package: str, version: str, files: dict[str, bytes], platforms=(LINUX,), pythons=("3.10",)
) -> tuple[Wheel, bytes]:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
        archive.writestr(
            f"{package}-{version}.dist-info/METADATA", f"Name: {package}\nVersion: {version}\n"
        )
    data = buffer.getvalue()
    wheel = Wheel(
        package=package,
        version=version,
        filename=f"{package}-{version}-py3-none-any.whl",
        url=f"https://files.pythonhosted.org/packages/x/{package}-{version}-py3-none-any.whl",
        size=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        platforms=tuple(platforms),
        pythons=tuple(pythons),
        min_glibc=(2, 17),
    )
    return wheel, data


def fake_fetch(contents: dict[str, bytes]):
    calls: list[str] = []

    def fetch(wheel: Wheel, destination: Path, progress) -> None:
        calls.append(wheel.filename)
        if wheel.filename not in contents:
            raise InstallError(f"no fake for {wheel.filename}")
        destination.write_bytes(contents[wheel.filename])
        if progress is not None:
            progress(wheel, wheel.size, wheel.size)

    fetch.calls = calls  # type: ignore[attr-defined]
    return fetch


@pytest.fixture
def alpha():
    return make_wheel("alpha", "1.0", {"alpha/__init__.py": b"VERSION = '1.0'\n"})


@pytest.fixture
def beta():
    return make_wheel(
        "beta",
        "2.0",
        {
            "beta.py": b"VALUE = 2\n",
            "beta-2.0.data/purelib/beta_extra.py": b"EXTRA = True\n",
            "beta-2.0.data/scripts/beta": b"#!/bin/sh\n",
        },
    )


def plan_for(*wheels: Wheel) -> Plan:
    return Plan(LINUX, "3.10", tuple(wheels))


def test_install_unpacks_verified_wheels_and_records_them(
    tmp_path: Path, alpha, beta, monkeypatch: pytest.MonkeyPatch
):
    data = tmp_path / "data"
    fetch = fake_fetch({alpha[0].filename: alpha[1], beta[0].filename: beta[1]})
    seen: list[tuple[str, int, int]] = []
    result = installer.install(
        data,
        plan_for(alpha[0], beta[0]),
        fetch,
        lambda w, done, total: seen.append((w.filename, done, total)),
    )

    site = installer.site_packages(data)
    assert (site / "alpha" / "__init__.py").read_bytes() == b"VERSION = '1.0'\n"
    assert (site / "beta.py").is_file()
    assert (site / "beta_extra.py").is_file()  # .data/purelib lands in the root
    assert not (site / "beta").exists() and not list(site.glob("*.data"))  # scripts are skipped
    assert (site / "alpha-1.0.dist-info" / "METADATA").is_file()
    assert result == installer.Installed(
        LINUX, "3.10", {"alpha": "1.0", "beta": "2.0"}, (alpha[0].filename, beta[0].filename)
    )
    assert installer.installed(data) == result
    assert installer.is_current(data, plan_for(alpha[0], beta[0]))
    assert not installer.is_current(data, plan_for(alpha[0]))
    assert seen == [
        (alpha[0].filename, alpha[0].size, alpha[0].size),
        (beta[0].filename, beta[0].size, beta[0].size),
    ]
    assert list((data / "downloads").iterdir()) == []
    assert not (data / "site-packages.new").exists()

    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.delitem(sys.modules, "alpha", raising=False)
    assert installer.activate(data) == site
    assert sys.path[0] == str(site)
    assert importlib.import_module("alpha").VERSION == "1.0"
    installer.activate(data)
    assert sys.path.count(str(site)) == 1


def test_nothing_is_installed_from_a_wheel_with_the_wrong_hash(tmp_path: Path, alpha):
    data = tmp_path / "data"
    wheel, content = alpha
    tampered = content[:-1] + bytes([content[-1] ^ 0xFF])
    with pytest.raises(InstallError, match="does not match its pinned SHA-256"):
        installer.install(data, plan_for(wheel), fake_fetch({wheel.filename: tampered}))
    assert not installer.site_packages(data).exists()
    assert installer.installed(data) is None
    assert list((data / "downloads").iterdir()) == []
    wrong_size = Wheel(**{**vars(wheel), "size": wheel.size + 1})
    with pytest.raises(InstallError, match="bytes; the pinned wheel is"):
        installer.install(data, plan_for(wrong_size), fake_fetch({wheel.filename: content}))
    assert not installer.site_packages(data).exists()


def test_a_failed_install_keeps_the_previous_one(tmp_path: Path, alpha, beta):
    data = tmp_path / "data"
    installer.install(data, plan_for(alpha[0]), fake_fetch({alpha[0].filename: alpha[1]}))
    with pytest.raises(InstallError, match="no fake for"):
        installer.install(
            data, plan_for(alpha[0], beta[0]), fake_fetch({alpha[0].filename: alpha[1]})
        )
    assert installer.installed(data) == installer.Installed(
        LINUX, "3.10", {"alpha": "1.0"}, (alpha[0].filename,)
    )
    assert (installer.site_packages(data) / "alpha" / "__init__.py").is_file()
    assert not (data / "site-packages.new").exists()


def test_a_newer_install_replaces_the_old_one(tmp_path: Path, alpha):
    data = tmp_path / "data"
    installer.install(data, plan_for(alpha[0]), fake_fetch({alpha[0].filename: alpha[1]}))
    newer, content = make_wheel("alpha", "1.1", {"alpha/__init__.py": b"VERSION = '1.1'\n"})
    assert not installer.is_current(data, plan_for(newer))
    installer.install(data, plan_for(newer), fake_fetch({newer.filename: content}))
    assert (
        installer.site_packages(data) / "alpha" / "__init__.py"
    ).read_bytes() == b"VERSION = '1.1'\n"
    assert installer.installed(data) == installer.Installed(
        LINUX, "3.10", {"alpha": "1.1"}, (newer.filename,)
    )
    assert not (data / "site-packages.old").exists()


@pytest.mark.parametrize(
    "member", ["../evil.py", "/etc/evil.py", "pkg/../../evil.py", "C:\\evil.py"]
)
def test_a_wheel_with_an_unsafe_path_is_refused(tmp_path: Path, member: str):
    wheel, content = make_wheel("evil", "1.0", {member: b"print('evil')\n", "ok.py": b""})
    data = tmp_path / "data"
    with pytest.raises(InstallError, match="unsafe path"):
        installer.install(data, plan_for(wheel), fake_fetch({wheel.filename: content}))
    assert not installer.site_packages(data).exists()
    assert not list(tmp_path.rglob("evil.py"))


def test_a_file_that_is_not_a_wheel_is_refused(tmp_path: Path, alpha):
    wheel = Wheel(**{**vars(alpha[0]), "size": 5, "sha256": hashlib.sha256(b"hello").hexdigest()})
    with pytest.raises(InstallError, match="is not a wheel"):
        installer.install(
            tmp_path / "data", plan_for(wheel), fake_fetch({wheel.filename: b"hello"})
        )


def test_installed_record_is_read_defensively(tmp_path: Path):
    site = installer.site_packages(tmp_path)
    site.mkdir(parents=True)
    assert installer.installed(tmp_path) is None
    (site / installer.RECORD).write_text("{not json", encoding="utf-8")
    assert installer.installed(tmp_path) is None
    (site / installer.RECORD).write_text(json.dumps({"platform": LINUX}), encoding="utf-8")
    assert installer.installed(tmp_path) is None
    assert installer.activate(tmp_path / "elsewhere") is None


def test_download_streams_hashes_and_caps_the_size(tmp_path: Path, alpha):
    wheel, content = alpha

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.close()

    seen: list[int] = []
    installer.download(
        wheel,
        tmp_path / "w.whl",
        lambda w, done, total: seen.append(done),
        opener=lambda url, timeout: Response(content),
    )
    assert (tmp_path / "w.whl").read_bytes() == content
    assert seen[-1] == wheel.size
    installer.verify_file(wheel, tmp_path / "w.whl")

    with pytest.raises(InstallError, match="larger than the pinned"):
        installer.download(
            wheel, tmp_path / "big.whl", opener=lambda url, timeout: Response(content + b"x")
        )

    def failing(url, timeout):
        raise OSError("no network")

    with pytest.raises(InstallError, match="Could not download"):
        installer.download(wheel, tmp_path / "none.whl", opener=failing)


def test_fetch_from_folder_installs_offline(tmp_path: Path, alpha):
    wheel, content = alpha
    (tmp_path / "wheels").mkdir()
    (tmp_path / "wheels" / wheel.filename).write_bytes(content)
    result = installer.install(
        tmp_path / "data", plan_for(wheel), installer.fetch_from_folder(tmp_path / "wheels")
    )
    assert result.packages == {"alpha": "1.0"}
    with pytest.raises(InstallError, match="is not in"):
        installer.fetch_from_folder(tmp_path / "empty")(wheel, tmp_path / "x.whl", None)


def test_plan_picks_the_wheels_for_a_machine():
    linux = installer.plan("linux-x86_64", "3.10", glibc=(2, 35))
    assert [w.package for w in linux.wheels] == ["c2pa-python", "cryptography", "cffi"]
    assert linux.supports_k1 and linux.unavailable == ()
    assert linux.packages == installer.PINS

    intel_mac = installer.plan("darwin-x86_64", "3.12")
    assert [w.package for w in intel_mac.wheels] == ["c2pa-python"]
    assert not intel_mac.supports_k1
    assert any("cryptography" in message for message in intel_mac.unavailable)
    assert any("K1" in message for message in intel_mac.unavailable)


def test_plan_refuses_a_machine_without_c2pa_python():
    with pytest.raises(InstallError, match=r"needs glibc 2\.28; this system has 2\.17"):
        installer.plan("linux-x86_64", "3.10", glibc=(2, 17))  # c2pa-python needs 2.28
    newer = installer.plan("linux-x86_64", "3.10", glibc=(2, 28))
    assert [w.package for w in newer.wheels] == ["c2pa-python", "cryptography", "cffi"]


def test_plan_for_this_machine():
    this = installer.plan()
    assert this.packages["c2pa-python"] == "0.38.0"
    assert this.total_size > 10_000_000
