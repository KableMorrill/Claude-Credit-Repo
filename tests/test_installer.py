"""I2: pinned wheels are fetched, verified by size and SHA-256, unpacked and imported lazily.

Downloads are faked; the pins table itself is checked in tests/test_wheel_pins.py.
"""

from __future__ import annotations

import hashlib
import importlib
import io
import json
import os
import sys
import threading
import time
import zipfile
from pathlib import Path

import pytest

from krita_provenance import installer, wheels
from krita_provenance.installer import InstallError, Plan
from krita_provenance.wheels import Wheel

LINUX = "linux-x86_64"


def make_wheel(
    package: str,
    version: str,
    files: dict[str, bytes],
    platforms=(LINUX,),
    pythons=("3.10",),
    dist_info: bool = True,
) -> tuple[Wheel, bytes]:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
        if dist_info:
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


class FakeFetch:
    """Serves wheels from memory and remembers what was asked for."""

    def __init__(self, contents: dict[str, bytes], delay: float = 0.0) -> None:
        self.contents = contents
        self.delay = delay
        self.calls: list[str] = []

    def __call__(self, wheel: Wheel, destination: Path, progress) -> None:
        self.calls.append(wheel.filename)
        if wheel.filename not in self.contents:
            raise InstallError(f"no fake for {wheel.filename}")
        if self.delay:
            time.sleep(self.delay)
        destination.write_bytes(self.contents[wheel.filename])
        if progress is not None:
            progress(wheel, wheel.size, wheel.size)


def fake_fetch(contents: dict[str, bytes], delay: float = 0.0) -> FakeFetch:
    return FakeFetch(contents, delay)


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


def plan_for(*items: Wheel) -> Plan:
    return Plan(LINUX, "3.10", tuple(items))


def this_machine(*items: Wheel) -> Plan:
    return Plan(wheels.platform_key(), wheels.python_key(), tuple(items))


def test_install_unpacks_verified_wheels_and_records_them(tmp_path: Path, alpha, beta):
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
    assert not list(data.glob("site-packages.new-*")) and not list(data.glob("site-packages.old-*"))


def test_activate_imports_from_an_install_made_for_this_machine(tmp_path: Path, alpha, monkeypatch):
    data = tmp_path / "data"
    installer.install(data, this_machine(alpha[0]), fake_fetch({alpha[0].filename: alpha[1]}))
    site = installer.site_packages(data).resolve()
    monkeypatch.setattr(sys, "path", list(sys.path))
    try:
        assert installer.activate(data) == site
        assert sys.path[0] == str(site)
        assert importlib.import_module("alpha").VERSION == "1.0"
        installer.activate(data)
        assert sys.path.count(str(site)) == 1
    finally:
        sys.modules.pop("alpha", None)
        sys.path_importer_cache.pop(str(site), None)


def test_activate_ignores_an_install_for_another_machine(tmp_path: Path, alpha, monkeypatch):
    data = tmp_path / "data"
    other = "win32-arm64" if wheels.platform_key() != "win32-arm64" else "linux-arm64"
    installer.install(
        data, Plan(other, "3.10", (alpha[0],)), fake_fetch({alpha[0].filename: alpha[1]})
    )
    monkeypatch.setattr(sys, "path", list(sys.path))
    assert installer.activate(data) is None
    assert str(installer.site_packages(data).resolve()) not in sys.path
    assert installer.activate(tmp_path / "elsewhere") is None


def test_nothing_is_installed_from_a_wheel_with_the_wrong_hash(tmp_path: Path, alpha):
    data = tmp_path / "data"
    wheel, content = alpha
    tampered = content[:-1] + bytes([content[-1] ^ 0xFF])
    with pytest.raises(InstallError, match="does not match its pinned SHA-256"):
        installer.install(data, plan_for(wheel), fake_fetch({wheel.filename: tampered}))
    assert not installer.site_packages(data).exists()
    assert installer.installed(data) is None
    assert list((data / "downloads").iterdir()) == []
    assert not list(data.glob("site-packages.new-*"))
    wrong_size = Wheel(**{**vars(wheel), "size": wheel.size + 1})
    with pytest.raises(InstallError, match="bytes; the pinned wheel is"):
        installer.install(data, plan_for(wrong_size), fake_fetch({wheel.filename: content}))
    assert not installer.site_packages(data).exists()


def test_verification_happens_before_anything_is_unpacked(tmp_path: Path, alpha):
    """Bytes that are not even a zip fail on the digest, not on unpacking."""
    wheel = alpha[0]
    with pytest.raises(InstallError, match="does not match its pinned SHA-256"):
        installer.install(
            tmp_path / "data", plan_for(wheel), fake_fetch({wheel.filename: b"x" * wheel.size})
        )


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
    assert not list(data.glob("site-packages.new-*"))


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
    assert not list(data.glob("site-packages.old-*"))


def test_two_installs_at_once_leave_one_complete_install(tmp_path: Path, alpha, beta):
    """Two Krita processes on first run must not damage each other's work."""
    data = tmp_path / "data"
    wanted = plan_for(alpha[0], beta[0])
    contents = {alpha[0].filename: alpha[1], beta[0].filename: beta[1]}
    outcomes: list[object] = []

    def run() -> None:
        try:
            outcomes.append(installer.install(data, wanted, fake_fetch(contents, delay=0.05)))
        except InstallError as error:  # acceptable: the other run won
            outcomes.append(error)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert any(isinstance(outcome, installer.Installed) for outcome in outcomes)
    site = installer.site_packages(data)
    assert installer.is_current(data, wanted)
    assert (site / "alpha" / "__init__.py").is_file() and (site / "beta.py").is_file()
    assert not list(data.glob("site-packages.new-*"))


def test_a_record_that_lists_a_missing_package_is_not_trusted(tmp_path: Path, alpha, beta):
    data = tmp_path / "data"
    wanted = plan_for(alpha[0], beta[0])
    installer.install(
        data, wanted, fake_fetch({alpha[0].filename: alpha[1], beta[0].filename: beta[1]})
    )
    import shutil

    shutil.rmtree(installer.site_packages(data) / "beta-2.0.dist-info")
    assert installer.installed(data) is None
    assert not installer.is_current(data, wanted)
    assert installer.activate(data) is None


def test_a_swap_that_keeps_failing_keeps_the_verified_folder_for_next_time(
    tmp_path: Path, alpha, monkeypatch
):
    data = tmp_path / "data"
    fetch = fake_fetch({alpha[0].filename: alpha[1]})
    monkeypatch.setattr(installer, "SWAP_ATTEMPTS", 2)
    monkeypatch.setattr(installer.time, "sleep", lambda seconds: None)

    def locked(source: Path, destination: Path) -> None:
        raise PermissionError("busy")

    monkeypatch.setattr(installer, "_rename", locked)
    with pytest.raises(InstallError, match="restart Krita and run the install again"):
        installer.install(data, plan_for(alpha[0]), fetch)
    staged = list(data.glob("site-packages.new-*"))
    assert len(staged) == 1 and (staged[0] / installer.RECORD).is_file()
    assert installer.installed(data) is None

    monkeypatch.undo()
    monkeypatch.setattr(installer.time, "sleep", lambda seconds: None)
    result = installer.install(data, plan_for(alpha[0]), fetch)  # adopts the staged folder
    assert result.packages == {"alpha": "1.0"}
    assert fetch.calls == [alpha[0].filename]  # nothing was downloaded again
    assert not list(data.glob("site-packages.new-*"))


def test_a_briefly_locked_swap_is_retried(tmp_path: Path, alpha, monkeypatch):
    data = tmp_path / "data"
    attempts: list[int] = []
    real = installer._rename

    def flaky(source: Path, destination: Path) -> None:
        attempts.append(1)
        if len(attempts) < 3:
            raise PermissionError("busy")
        real(source, destination)

    monkeypatch.setattr(installer, "_rename", flaky)
    monkeypatch.setattr(installer.time, "sleep", lambda seconds: None)
    installer.install(data, plan_for(alpha[0]), fake_fetch({alpha[0].filename: alpha[1]}))
    assert installer.is_current(data, plan_for(alpha[0]))
    assert len(attempts) >= 3


def test_stale_staging_folders_are_removed(tmp_path: Path, alpha):
    data = tmp_path / "data"
    stale = data / "site-packages.new-99999"
    stale.mkdir(parents=True)
    old_time = time.time() - installer.STALE_AFTER - 60
    os.utime(stale, (old_time, old_time))
    fresh = data / "site-packages.new-99998"
    fresh.mkdir()
    installer.install(data, plan_for(alpha[0]), fake_fetch({alpha[0].filename: alpha[1]}))
    assert not stale.exists()
    assert fresh.exists()  # another live install's folder is left alone


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


def test_dist_info_names():
    assert (
        installer.dist_info("c2pa_python-0.38.0-py3-none-win_amd64.whl")
        == "c2pa_python-0.38.0.dist-info"
    )
    assert (
        installer.dist_info("cryptography-50.0.2-cp39-abi3-win_amd64.whl")
        == "cryptography-50.0.2.dist-info"
    )
    assert installer.dist_info("cffi-2.1.1-cp310-cp310-win_amd64.whl") == "cffi-2.1.1.dist-info"


def test_download_streams_and_caps_the_size(tmp_path: Path, alpha):
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


def test_plan_reports_an_unsupported_machine_as_an_install_error(monkeypatch):
    def unsupported() -> str:
        raise wheels.NoWheel("No pinned wheels for freebsd14 on amd64")

    monkeypatch.setattr(wheels, "platform_key", unsupported)
    with pytest.raises(InstallError, match="No pinned wheels for freebsd14"):
        installer.plan()
    with pytest.raises(
        InstallError, match=r"No pinned c2pa-python wheel for linux-x86_64 on Python 3\.9"
    ):
        installer.plan("linux-x86_64", "3.9")


def test_plan_for_this_machine():
    this = installer.plan()
    assert this.packages["c2pa-python"] == "0.38.0"
    assert this.total_size > 10_000_000
