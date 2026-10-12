"""Import rules from the spec, checked on every push.

- Capture, log, mapping, disclosure and report code runs without Krita or Qt, so it can be
  unit-tested (Testing and validation).
- Qt comes only through krita_provenance/qt.py, so PyQt5 (Krita 5) and PyQt6 (Krita 6) share
  one code path (I3).
- c2pa-python is used through one wrapper module, so SDK churn stays contained (Risks).
- Krita AI Diffusion internals are read only by its version-checked adapter (Phase A).
"""

import ast
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "krita_provenance"

# Top-level module -> the package files allowed to import it. Grow these lists on purpose.
ALLOWED_IMPORTERS = {
    "krita": {"extension.py"},
    "PyQt5": {"qt.py"},
    "PyQt6": {"qt.py"},
    "c2pa": {"sdk.py"},
    "ai_diffusion": {"adapters/ai_diffusion.py"},
}


def package_files() -> list[str]:
    return sorted(path.relative_to(PACKAGE).as_posix() for path in PACKAGE.rglob("*.py"))


def imported_top_level_modules(source: str) -> set[str]:
    """Absolute imports anywhere in the file, including in functions and TYPE_CHECKING blocks."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module.split(".")[0])
    return found


def test_restricted_modules_are_imported_only_where_allowed():
    violations = []
    for file in package_files():
        imported = imported_top_level_modules((PACKAGE / file).read_text(encoding="utf-8"))
        for module, allowed in ALLOWED_IMPORTERS.items():
            if module in imported and file not in allowed:
                violations.append(f"krita_provenance/{file} imports {module}")
    assert violations == []


def test_everything_but_krita_glue_imports_without_krita_or_qt():
    """Catches indirect imports too, by importing each module in a clean interpreter."""
    krita_glue = ALLOWED_IMPORTERS["krita"]
    modules = [
        "krita_provenance." + file.removesuffix(".py").replace("/", ".")
        for file in package_files()
        if file not in krita_glue
    ]
    modules = [name.removesuffix(".__init__") for name in modules]
    script = (
        "import importlib, json, sys\n"
        f"for name in {modules!r}:\n"
        "    importlib.import_module(name)\n"
        f"print(json.dumps(sorted(set({sorted(ALLOWED_IMPORTERS)!r}) & set(sys.modules))))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=ROOT, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == []


def test_the_rules_see_imports():
    source = "import krita\nfrom PyQt5.QtCore import QObject\nfrom . import qt\n"
    assert imported_top_level_modules(source) == {"krita", "PyQt5"}
