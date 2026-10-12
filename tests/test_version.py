import re
from pathlib import Path

from krita_provenance import __version__

ROOT = Path(__file__).resolve().parents[1]


def test_plugin_and_project_versions_match():
    """Krita reads __version__; the project metadata must not drift from it."""
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version = "([^"]+)"', pyproject, re.MULTILINE)
    assert match is not None
    assert match.group(1) == __version__
