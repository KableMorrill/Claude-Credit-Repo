import sys
from pathlib import Path

import pytest

import dev_install

HOME = Path("/home/artist")


@pytest.mark.parametrize(
    ("platform", "env", "expected"),
    [
        ("win32", {"APPDATA": "/appdata"}, Path("/appdata/krita/pykrita")),
        ("win32", {}, HOME / "AppData/Roaming/krita/pykrita"),
        ("darwin", {}, HOME / "Library/Application Support/krita/pykrita"),
        ("linux", {}, HOME / ".local/share/krita/pykrita"),
        ("linux", {"XDG_DATA_HOME": "/xdg"}, Path("/xdg/krita/pykrita")),
    ],
)
def test_finds_krita_plugin_folder(platform: str, env: dict[str, str], expected: Path):
    assert dev_install.pykrita_dir(platform, env, HOME) == expected


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "krita_provenance").mkdir(parents=True)
    (repo / "krita_provenance" / "__init__.py").write_text("")
    (repo / "krita_provenance.desktop").write_text("[Desktop Entry]\n")
    return repo


@pytest.fixture
def pykrita(tmp_path: Path) -> Path:
    path = tmp_path / "pykrita"
    path.mkdir()
    return path


def test_install_links_package_and_desktop_file(repo: Path, pykrita: Path):
    dev_install.install(pykrita, repo)
    assert dev_install.is_link(pykrita / "krita_provenance")
    assert (pykrita / "krita_provenance" / "__init__.py").is_file()
    assert (pykrita / "krita_provenance.desktop").read_text() == "[Desktop Entry]\n"


def test_install_twice_is_harmless(repo: Path, pykrita: Path):
    dev_install.install(pykrita, repo)
    dev_install.install(pykrita, repo)
    assert dev_install.is_link(pykrita / "krita_provenance")


def test_install_refuses_to_replace_a_real_install(repo: Path, pykrita: Path):
    (pykrita / "krita_provenance").mkdir()
    with pytest.raises(SystemExit, match="not a dev link"):
        dev_install.install(pykrita, repo)
    assert not dev_install.is_link(pykrita / "krita_provenance")


def test_uninstall_removes_links_and_keeps_the_checkout(repo: Path, pykrita: Path):
    dev_install.install(pykrita, repo)
    dev_install.uninstall(pykrita, repo)
    assert list(pykrita.iterdir()) == []
    assert (repo / "krita_provenance" / "__init__.py").is_file()
    assert (repo / "krita_provenance.desktop").is_file()


def test_uninstall_leaves_a_real_install_alone(repo: Path, pykrita: Path):
    (pykrita / "krita_provenance").mkdir()
    with pytest.raises(SystemExit, match="leaving it alone"):
        dev_install.uninstall(pykrita, repo)
    assert (pykrita / "krita_provenance").is_dir()


@pytest.mark.skipif(sys.platform != "win32", reason="junctions are Windows-only")
def test_junction_round_trip(repo: Path, pykrita: Path):
    link = pykrita / "krita_provenance"
    dev_install.make_junction(link, repo / "krita_provenance")
    assert dev_install.is_link(link)
    assert (link / "__init__.py").is_file()
    dev_install.remove_link(link)
    assert not link.exists()
    assert (repo / "krita_provenance" / "__init__.py").is_file()
