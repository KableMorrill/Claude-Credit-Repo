"""Link this checkout into Krita's pykrita folder for development.

Users install the release zip instead (Tools > Scripts > Import Python Plugin from File).

    uv run python scripts/dev_install.py            # link
    uv run python scripts/dev_install.py --remove   # unlink, e.g. before testing a release zip

On Windows without symlink rights (Developer Mode off), the package folder becomes a directory
junction and the .desktop file a copy; re-run after editing the .desktop file.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

PACKAGE = "krita_provenance"
REPO = Path(__file__).resolve().parent.parent
_IO_REPARSE_TAG_MOUNT_POINT = 0xA0000003  # stat.IO_REPARSE_TAG_MOUNT_POINT, Windows-only name


def pykrita_dir(platform: str, env: Mapping[str, str], home: Path) -> Path:
    """Krita's per-user Python plugin folder for a standard (non-Flatpak, non-Snap) install."""
    if platform == "win32":
        appdata = env.get("APPDATA")
        return (Path(appdata) if appdata else home / "AppData" / "Roaming") / "krita" / "pykrita"
    if platform == "darwin":
        return home / "Library" / "Application Support" / "krita" / "pykrita"
    data = env.get("XDG_DATA_HOME")
    return (Path(data) if data else home / ".local" / "share") / "krita" / "pykrita"


def is_link(path: Path) -> bool:
    """True for a symlink or a Windows directory junction, even if its target is gone."""
    if path.is_symlink():
        return True
    try:
        return getattr(os.lstat(path), "st_reparse_tag", 0) == _IO_REPARSE_TAG_MOUNT_POINT
    except FileNotFoundError:
        return False


def make_junction(link: Path, target: Path) -> None:
    """Directory junctions need neither admin rights nor Developer Mode."""
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        text=True,
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        output = (result.stderr or result.stdout).strip()
        raise SystemExit(f"Could not create a junction at {link}: {output}")


def remove_link(path: Path) -> None:
    """Remove a symlink or junction without touching what it points to."""
    if sys.platform == "win32":
        try:
            os.rmdir(path)  # a directory symlink or junction: removes the link, never the target
            return
        except NotADirectoryError:
            pass
    path.unlink()


def install(pykrita: Path, repo: Path = REPO) -> list[str]:
    package, desktop = repo / PACKAGE, repo / f"{PACKAGE}.desktop"
    package_link, desktop_link = pykrita / package.name, pykrita / desktop.name
    for path in (package_link, desktop_link):
        if os.path.lexists(path) and not is_link(path) and not _same_file(path, desktop):
            raise SystemExit(f"{path} exists and is not a dev link. Remove that install first.")

    done: list[str] = []
    if is_link(package_link):
        remove_link(package_link)
    try:
        package_link.symlink_to(package, target_is_directory=True)
        done.append(f"Symlinked {package_link}")
    except OSError:
        if sys.platform != "win32":
            raise
        make_junction(package_link, package)
        done.append(f"Created junction {package_link}")

    if os.path.lexists(desktop_link):
        desktop_link.unlink()
    try:
        desktop_link.symlink_to(desktop)
        done.append(f"Symlinked {desktop_link}")
    except OSError:
        if sys.platform != "win32":
            raise
        shutil.copyfile(desktop, desktop_link)
        done.append(f"Copied {desktop_link}")
    return done


def uninstall(pykrita: Path, repo: Path = REPO) -> list[str]:
    desktop = repo / f"{PACKAGE}.desktop"
    package_link, desktop_link = pykrita / PACKAGE, pykrita / desktop.name
    if os.path.lexists(package_link) and not is_link(package_link):
        raise SystemExit(f"{package_link} is not a dev link; leaving it alone.")

    done: list[str] = []
    if is_link(package_link):
        remove_link(package_link)
        done.append(f"Removed {package_link}")
    if is_link(desktop_link) or _same_file(desktop_link, desktop):
        desktop_link.unlink()
        done.append(f"Removed {desktop_link}")
    return done


def _same_file(path: Path, original: Path) -> bool:
    """A plain copy of `original`, as left by the Windows fallback."""
    return path.is_file() and path.read_bytes() == original.read_bytes()


def main() -> None:
    parser = argparse.ArgumentParser(description="Link this checkout into Krita for development.")
    parser.add_argument("--remove", action="store_true", help="remove the dev links")
    parser.add_argument("--pykrita", type=Path, help="Krita's pykrita folder, if not the default")
    args = parser.parse_args()

    pykrita: Path = args.pykrita or pykrita_dir(sys.platform, os.environ, Path.home())
    if not pykrita.is_dir():
        raise SystemExit(f"{pykrita} does not exist. Start Krita once, or pass --pykrita.")
    if args.remove:
        print("\n".join(uninstall(pykrita)) or "Nothing to remove.")
    else:
        print("\n".join(install(pykrita)))
        print(
            "Restart Krita and enable 'Provenance (C2PA)' in"
            " Settings > Configure Krita > Python Plugin Manager."
        )


if __name__ == "__main__":
    main()
