"""What this Krita's Python can see, for the M0 go/no-go note (spikes S1 and S3).

Krita-free so it can be unit-tested; the extension only displays the result.
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from . import __version__
from .qt import BINDINGS

MIN_PYTHON = (3, 10)  # c2pa-python 0.38 requires Python 3.10 or newer


@dataclass(frozen=True)
class Environment:
    plugin_version: str
    python_version: tuple[int, ...]
    krita_version: str | None
    qt_bindings: tuple[str, ...]  # bindings already loaded, normally exactly one
    ai_diffusion: str
    c2pa_python: str | None  # installed version, None if not installed

    def problems(self) -> list[str]:
        found: list[str] = []
        if self.python_version[:2] < MIN_PYTHON:
            found.append(
                f"Python {_dotted(self.python_version)} is older than {_dotted(MIN_PYTHON)},"
                " which c2pa-python needs."
            )
        if len(self.qt_bindings) > 1:
            found.append(
                "More than one Qt binding is loaded; another plugin may have imported a"
                " system-wide copy."
            )
        return found

    def to_text(self) -> str:
        lines = [
            f"krita-provenance {self.plugin_version}",
            f"Krita: {self.krita_version or 'unknown'}",
            f"Python: {_dotted(self.python_version)}",
            f"Qt binding: {', '.join(self.qt_bindings) or 'none loaded'}",
            f"Krita AI Diffusion: {self.ai_diffusion}",
            f"c2pa-python: {self.c2pa_python or 'not installed'}",
        ]
        if problems := self.problems():
            lines += ["", "Problems:", *(f"- {problem}" for problem in problems)]
        return "\n".join(lines)


def collect_environment(
    krita_version: str | None = None,
    *,
    modules: Mapping[str, object] = sys.modules,
    find_spec: Callable[[str], object | None] = importlib.util.find_spec,
    dist_version: Callable[[str], str] = importlib.metadata.version,
) -> Environment:
    """Collect the facts without importing anything new. Lookups are injectable for tests."""
    return Environment(
        plugin_version=__version__,
        python_version=tuple(sys.version_info[:3]),
        krita_version=krita_version,
        qt_bindings=tuple(name for name in BINDINGS if name in modules),
        ai_diffusion=_ai_diffusion_status(modules, find_spec),
        c2pa_python=_installed_version("c2pa-python", dist_version),
    )


def _ai_diffusion_status(
    modules: Mapping[str, object], find_spec: Callable[[str], object | None]
) -> str:
    module = modules.get("ai_diffusion")
    if module is not None:
        return f"{getattr(module, '__version__', 'unknown version')} (loaded)"
    # find_spec locates a top-level package without running it.
    if find_spec("ai_diffusion") is not None:
        return "installed but not loaded (disabled in the Python Plugin Manager?)"
    return "not installed"


def _installed_version(distribution: str, dist_version: Callable[[str], str]) -> str | None:
    try:
        return dist_version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return None


def _dotted(version: tuple[int, ...]) -> str:
    return ".".join(str(part) for part in version)
