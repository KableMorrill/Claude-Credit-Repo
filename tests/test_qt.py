import pytest

from krita_provenance.qt import select_binding


def everything_installed(name: str) -> bool:
    return True


def nothing_installed(name: str) -> bool:
    return False


@pytest.mark.parametrize("binding", ["PyQt5", "PyQt6"])
def test_uses_the_binding_krita_already_loaded(binding: str):
    loaded = {binding, f"{binding}.QtCore"}
    assert select_binding(loaded, everything_installed) == binding


def test_prefers_pyqt5_if_both_are_loaded():
    assert select_binding({"PyQt5", "PyQt6"}, everything_installed) == "PyQt5"


def test_outside_krita_uses_the_installed_binding():
    assert select_binding(set(), lambda name: name == "PyQt6") == "PyQt6"


def test_fails_clearly_without_qt():
    with pytest.raises(ImportError, match="PyQt5"):
        select_binding(set(), nothing_installed)
