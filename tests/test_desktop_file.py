"""Krita finds a Python plugin by pairing <name>.desktop with a folder <name> beside it (I1)."""

import configparser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class DesktopFile(configparser.ConfigParser):
    def __init__(self) -> None:
        super().__init__(interpolation=None)

    def optionxform(self, optionstr: str) -> str:
        return optionstr  # keys are case-sensitive


def read_entry() -> configparser.SectionProxy:
    parser = DesktopFile()
    parser.read(ROOT / "krita_provenance.desktop", encoding="utf-8")
    return parser["Desktop Entry"]


def test_desktop_file_declares_a_python_3_krita_plugin():
    entry = read_entry()
    assert entry["Type"] == "Service"
    assert entry["ServiceTypes"] == "Krita/PythonPlugin"
    assert entry["X-Python-2-Compatible"] == "false"
    assert entry["Name"].strip()
    assert entry["Comment"].strip()


def test_desktop_file_points_at_the_package_beside_it():
    library = read_entry()["X-KDE-Library"]
    assert (ROOT / library / "__init__.py").is_file()
    assert (ROOT / f"{library}.desktop").is_file()
