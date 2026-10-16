"""Build the zip that Krita installs with Tools > Scripts > Import Python Plugin from File (I1).

    uv run python scripts/build_release.py              # dist/krita-provenance-<version>.zip
    uv run python scripts/build_release.py --out <dir>

Krita 5.3's importer (plugins/python/plugin_importer/plugin_importer.py, read at bd6e8d0) takes
the module name from each .desktop file's X-KDE-Library, looks for a directory entry of that
name with an __init__.py inside, and copies the .desktop file and that directory into pykrita,
replacing an earlier install. It ignores everything else. So the zip holds
krita_provenance.desktop and krita_provenance/, directory entries included, with every Python
file of the package and the licence. The wheels aren't in it: the plugin fetches them at first
run (I2), into a data folder outside pykrita that a reinstall leaves alone.

The same checkout always builds the same bytes: entries are sorted and carry a fixed date and
fixed permissions.
"""

from __future__ import annotations

import argparse
import ast
import zipfile
from pathlib import Path

PACKAGE = "krita_provenance"
REPO = Path(__file__).resolve().parent.parent
DIST = REPO / "dist"
DATE = (1980, 1, 1, 0, 0, 0)  # the earliest date a zip entry can hold


def version(repo: Path = REPO) -> str:
    """krita_provenance.__version__, read without importing the package."""
    tree = ast.parse((repo / PACKAGE / "__init__.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "__version__"
                for target in node.targets
            )
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            return node.value.value
    raise SystemExit(f"{PACKAGE}/__init__.py has no __version__")


def contents(repo: Path = REPO) -> dict[str, bytes | None]:
    """Each name in the zip and its bytes; None for a directory entry."""
    package = repo / PACKAGE
    entries: dict[str, bytes | None] = {
        f"{PACKAGE}.desktop": (repo / f"{PACKAGE}.desktop").read_bytes(),
        f"{PACKAGE}/": None,
        f"{PACKAGE}/LICENSE": (repo / "LICENSE").read_bytes(),
    }
    for path in package.rglob("*.py"):
        relative = path.relative_to(package)
        for folder in relative.parents:
            if folder != Path("."):
                entries[f"{PACKAGE}/{folder.as_posix()}/"] = None
        entries[f"{PACKAGE}/{relative.as_posix()}"] = path.read_bytes()
    return entries


def build(out: Path = DIST, repo: Path = REPO) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    target = out / f"krita-provenance-{version(repo)}.zip"
    with zipfile.ZipFile(target, "w") as archive:
        for name, data in sorted(contents(repo).items()):
            info = zipfile.ZipInfo(name, date_time=DATE)
            info.create_system = 3  # Unix, so the permissions below read the same everywhere
            if data is None:
                info.external_attr = (0o40755 << 16) | 0x10  # a directory, MS-DOS flag included
                archive.writestr(info, b"")
            else:
                info.external_attr = 0o100644 << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, data)
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the release zip (I1).")
    parser.add_argument("--out", type=Path, default=DIST, help="folder for the zip (default: dist)")
    args = parser.parse_args()
    target = build(args.out)
    print(f"Built {target} ({target.stat().st_size // 1024} KB).")
    print(
        "Install it with Tools > Scripts > Import Python Plugin from File. Remove a dev link"
        " first (scripts/dev_install.py --remove): Krita replaces the folder it installs into."
    )


if __name__ == "__main__":
    main()
