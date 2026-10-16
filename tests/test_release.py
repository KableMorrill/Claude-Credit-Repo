"""I1: the release zip installs through Krita's Import Python Plugin from File.

Krita 5.3's importer (plugins/python/plugin_importer/plugin_importer.py, read at bd6e8d0) finds a
plugin by a .desktop file's X-KDE-Library, needs a directory entry of that name with an
__init__.py inside (get_source_module), and copies only those two into pykrita.
"""

import configparser
import zipfile
from pathlib import Path

import build_release
import krita_provenance

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "krita_provenance"


def package_files() -> list[str]:
    return sorted(
        path.relative_to(PACKAGE).as_posix()
        for path in PACKAGE.rglob("*")
        if path.is_file() and "__pycache__" not in path.relative_to(PACKAGE).parts
    )


def test_the_zip_has_what_krita_looks_for(tmp_path: Path):
    target = build_release.build(tmp_path)
    assert target.name == f"krita-provenance-{krita_provenance.__version__}.zip"
    with zipfile.ZipFile(target) as archive:
        names = archive.namelist()
        desktop = configparser.ConfigParser(interpolation=None)
        desktop.read_string(archive.read("krita_provenance.desktop").decode("utf-8"))
    name = desktop["Desktop Entry"]["X-KDE-Library"]
    assert name == "krita_provenance" and desktop["Desktop Entry"]["Name"]
    assert f"{name}/" in names  # the directory entry itself, not only files under it
    assert f"{name}/__init__.py" in names


def test_the_zip_holds_the_package_and_its_licence_and_nothing_else(tmp_path: Path):
    with zipfile.ZipFile(build_release.build(tmp_path)) as archive:
        files = {
            info.filename: archive.read(info) for info in archive.infolist() if not info.is_dir()
        }
    assert files.pop("krita_provenance.desktop") == (ROOT / "krita_provenance.desktop").read_bytes()
    assert files.pop("krita_provenance/LICENSE") == (ROOT / "LICENSE").read_bytes()
    assert sorted(files) == [f"krita_provenance/{name}" for name in package_files()]
    assert all(data == (ROOT / name).read_bytes() for name, data in files.items())


def test_the_package_holds_only_python_files():
    """The build ships the package's .py files; anything else must be added to it on purpose."""
    assert [name for name in package_files() if not name.endswith(".py")] == []


def test_the_same_checkout_builds_the_same_bytes(tmp_path: Path):
    first = build_release.build(tmp_path / "a").read_bytes()
    assert build_release.build(tmp_path / "b").read_bytes() == first
