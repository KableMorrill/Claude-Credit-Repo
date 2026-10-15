"""I2 and K1 at first run: ask, install, activate, make the keys, prove they sign.

Downloads are faked with wheels that hold only metadata, so the c2pa-python and cryptography
installed for the tests do the real work. That also makes them "loaded from elsewhere", which
the report must say.
"""

from __future__ import annotations

import hashlib
import io
import sys
import threading
import types
import zipfile
from pathlib import Path

import pytest

from krita_provenance import firstrun, installer, keys, wheels
from krita_provenance.firstrun import Cancelled, FirstRunError
from krita_provenance.installer import Plan
from krita_provenance.sdk import SignerConfig
from krita_provenance.wheels import Wheel

CERTS = Path(__file__).parent / "fixtures" / "certs"
VERSIONS = {"c2pa-python": "0.38.0", "cryptography": "50.0.2", "cffi": "2.1.1"}
UNAVAILABLE = "Key creation is not available on this platform yet (no cryptography wheel for it)"


def metadata_wheel(package: str, version: str) -> tuple[Wheel, bytes]:
    name = package.replace("-", "_")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            f"{name}-{version}.dist-info/METADATA", f"Name: {package}\nVersion: {version}\n"
        )
    data = buffer.getvalue()
    filename = f"{name}-{version}-py3-none-any.whl"
    wheel = Wheel(
        package=package,
        version=version,
        filename=filename,
        url=f"https://files.pythonhosted.org/packages/x/{filename}",
        size=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        platforms=(wheels.platform_key(),),
        pythons=(wheels.python_key(),),
    )
    return wheel, data


class Fetch:
    def __init__(self, contents: dict[str, bytes]) -> None:
        self.contents = contents
        self.calls = 0

    def __call__(self, wheel: Wheel, destination: Path, progress) -> None:
        self.calls += 1
        data = self.contents[wheel.filename]
        if progress is not None:
            progress(wheel, len(data) // 2, len(data))
        destination.write_bytes(data)
        if progress is not None:
            progress(wheel, len(data), len(data))


def plan_of(*packages: str, unavailable: tuple[str, ...] = ()) -> tuple[Plan, Fetch]:
    made = [metadata_wheel(package, VERSIONS[package]) for package in packages]
    plan = Plan(
        wheels.platform_key(),
        wheels.python_key(),
        tuple(wheel for wheel, _ in made),
        unavailable,
    )
    return plan, Fetch({wheel.filename: data for wheel, data in made})


@pytest.fixture(autouse=True)
def keep_sys_path(monkeypatch):
    """run() activates the install by putting it first on sys.path; undo that after each test."""
    monkeypatch.setattr(sys, "path", list(sys.path))


def test_data_folder_is_a_subfolder_of_app_data_never_of_the_plugin(tmp_path: Path):
    app_data = tmp_path / "AppData" / "Roaming" / "krita"
    assert firstrun.data_folder(str(app_data)) == app_data / "krita-provenance"
    plugin = app_data / "pykrita" / "krita_provenance"
    assert firstrun.data_folder(app_data, plugin) == app_data / "krita-provenance"
    with pytest.raises(FirstRunError, match="inside the plugin folder"):
        firstrun.data_folder(plugin, plugin)
    with pytest.raises(FirstRunError, match="did not report an app-data folder"):
        firstrun.data_folder("")


def test_consent_names_each_package_the_source_the_check_and_the_folder(tmp_path: Path):
    plan, _ = plan_of("c2pa-python", "cryptography", "cffi")
    text = firstrun.consent_text(plan, tmp_path / "krita-provenance")
    for wheel in plan.wheels:
        assert f"{wheel.package} {wheel.version} ({firstrun.megabytes(wheel.size)})" in text
    assert "files.pythonhosted.org" in text and "SHA-256" in text
    assert str(tmp_path / "krita-provenance") in text
    assert "not encrypted" in text and "unrecognized" in text
    assert text.endswith("Download and set up now?")


def test_consent_says_when_this_platform_cannot_make_keys(tmp_path: Path):
    plan, _ = plan_of("c2pa-python", unavailable=(UNAVAILABLE,))
    text = firstrun.consent_text(plan, tmp_path)
    assert UNAVAILABLE in text and "local signing certificate is made" not in text


def test_run_installs_makes_keys_and_signs_a_test_image(tmp_path: Path):
    folder = tmp_path / "krita-provenance"
    plan, fetch = plan_of("c2pa-python", "cryptography", "cffi")
    seen: list[tuple[int, int, str]] = []
    report = firstrun.run(folder, plan, fetch, lambda *update: seen.append(update))

    assert report.downloaded and fetch.calls == 3
    assert report.installed.packages == VERSIONS
    assert installer.site_packages(folder).resolve() == Path(sys.path[0])  # activated, first
    assert report.loaded == "c2pa-python 0.38.0 with c2pa-rs 0.91.0"
    assert report.certificate is not None
    assert report.certificate.common_name == keys.DEFAULT_COMMON_NAME
    assert keys.KeyFiles(folder / "keys").exists()
    assert report.self_test is not None and report.self_test.passed
    assert "untrusted, as expected for a local certificate" in report.self_test.detail

    done = [update[0] for update in seen]
    assert done == sorted(done) and done[-1] == plan.total_size
    assert all(update[1] == plan.total_size for update in seen)
    assert seen[0][2].startswith("Downloading c2pa-python 0.38.0: ")
    assert [update[2] for update in seen[-2:]] == [
        "Making the local signing certificate…",
        "Signing a test image…",
    ]

    again = firstrun.run(folder, plan, fetch)
    assert not again.downloaded and fetch.calls == 3  # nothing fetched the second time
    assert again.certificate == report.certificate  # the keys were kept, not remade


def test_report_flags_packages_loaded_from_outside_the_verified_install(tmp_path: Path):
    """The tests' own c2pa-python and cryptography stand in for copies another plugin loaded."""
    plan, fetch = plan_of("c2pa-python", "cryptography", "cffi")
    report = firstrun.run(tmp_path / "data", plan, fetch)
    assert any(problem.startswith("c2pa was loaded from ") for problem in report.problems)
    assert any(problem.startswith("cryptography was loaded from ") for problem in report.problems)
    assert not report.ready
    assert report.headline() == "Signing is not ready; see below."
    text = report.to_text()
    assert report.certificate is not None and report.certificate.fingerprint in text
    assert "Installed now; every downloaded file's size and SHA-256 matched the pins." in text
    assert f"Folder: {tmp_path / 'data'}" in text
    assert "Packages: c2pa-python 0.38.0, cryptography 50.0.2, cffi 2.1.1" in text


def test_loaded_from_elsewhere_checks_where_each_module_came_from(tmp_path: Path):
    site = tmp_path / "site-packages"
    inside = types.SimpleNamespace(__file__=str(site / "c2pa" / "__init__.py"))
    outside = types.SimpleNamespace(__file__=str(tmp_path / "other" / "cryptography.py"))
    modules = {"c2pa": inside, "cryptography": outside, "cffi": types.SimpleNamespace()}
    assert firstrun.loaded_from_elsewhere(site, modules) == {"cryptography": outside.__file__}


def test_a_ready_report_reads_set_up():
    certificate = keys.describe((CERTS / "signer.pem").read_bytes())
    report = firstrun.Report(
        Path("data"),
        installer.Installed("win32-x86_64", "3.13", dict(VERSIONS)),
        downloaded=False,
        loaded="c2pa-python 0.38.0 with c2pa-rs 0.91.0",
        certificate=certificate,
        self_test=firstrun.SelfTest(True, "Self-test: passed."),
    )
    assert report.ready and report.headline() == "Signing is set up."
    assert "Already installed; nothing was downloaded." in report.to_text()


def test_cancelling_the_download_installs_nothing(tmp_path: Path):
    folder = tmp_path / "data"
    plan, fetch = plan_of("c2pa-python", "cryptography", "cffi")
    cancelled = threading.Event()
    cancelled.set()
    with pytest.raises(Cancelled, match="nothing was installed"):
        firstrun.run(folder, plan, fetch, cancelled=cancelled)
    assert installer.installed(folder) is None
    assert not (folder / "keys").exists()
    assert not list(folder.glob("site-packages*"))


def test_without_key_creation_only_the_packages_are_set_up(tmp_path: Path):
    folder = tmp_path / "data"
    plan, fetch = plan_of("c2pa-python", unavailable=(UNAVAILABLE,))
    report = firstrun.run(folder, plan, fetch)
    assert report.certificate is None and report.self_test is None
    assert report.loaded == "c2pa-python 0.38.0 with c2pa-rs 0.91.0"
    assert not (folder / "keys").exists()
    assert UNAVAILABLE in report.to_text()


def test_self_test_reports_a_failure_instead_of_raising():
    chain = (CERTS / "chain.pem").read_bytes()
    passed = firstrun.sign_test_image(SignerConfig(chain, (CERTS / "signer.key").read_bytes()))
    assert passed.passed
    wrong_key = SignerConfig(chain, (CERTS / "root.key").read_bytes())  # not the signer's key
    failed = firstrun.sign_test_image(wrong_key)
    assert not failed.passed and failed.detail.startswith("Self-test failed: ")


def test_blank_png_is_a_png_the_sdk_accepts():
    data = firstrun.blank_png()
    assert (
        data.startswith(b"\x89PNG\r\n\x1a\n")
        and b"IHDR" in data
        and data.endswith(b"IEND\xaeB`\x82")
    )
