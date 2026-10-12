"""The single import point for Qt.

Krita 5 ships PyQt5 and Krita 6 ships PyQt6 (spec I3: one code path for both). Import Qt from
here, never from PyQt5 or PyQt6 directly:

    from .qt import QtWidgets

Modules resolve on first use, so importing this file never imports Qt by itself.
"""

from __future__ import annotations

import functools
import importlib
import importlib.util
import sys
from collections.abc import Callable, Container
from typing import TYPE_CHECKING

BINDINGS = ("PyQt5", "PyQt6")


def select_binding(loaded: Container[str], importable: Callable[[str], bool]) -> str:
    """Pick the Qt binding to use.

    Inside Krita, the binding Krita itself uses is already loaded before plugins start: PyQt5
    under Krita 5, PyQt6 under Krita 6. If both are loaded, which only happens when another
    plugin pulls in a system-wide copy, the first in BINDINGS wins and the environment check
    reports it. Outside Krita (tests, tooling) the first installed binding is used.
    """
    for name in BINDINGS:
        if name in loaded:
            return name
    for name in BINDINGS:
        if importable(name):
            return name
    raise ImportError("krita-provenance needs PyQt5 (Krita 5) or PyQt6 (Krita 6)")


@functools.cache
def binding() -> str:
    return select_binding(sys.modules, lambda name: importlib.util.find_spec(name) is not None)


if TYPE_CHECKING:
    # Type-check against Krita 5.3's binding, the v1 target; scripts/typeshed/krita.pyi uses it too.
    from PyQt5 import QtCore, QtGui, QtWidgets
else:
    _MODULES = ("QtCore", "QtGui", "QtWidgets")

    def __getattr__(name: str):
        if name in _MODULES:
            module = importlib.import_module(f"{binding()}.{name}")
            globals()[name] = module
            return module
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["BINDINGS", "QtCore", "QtGui", "QtWidgets", "binding", "select_binding"]
