import importlib.metadata
import sys
import types

from krita_provenance import __version__
from krita_provenance.environment import Environment, collect_environment
from krita_provenance.installer import Installed


def not_found(name: str) -> None:
    return None


def not_installed(distribution: str) -> str:
    raise importlib.metadata.PackageNotFoundError(distribution)


def collect(modules: dict[str, object] | None = None, **lookups) -> Environment:
    lookups = {"find_spec": not_found, "dist_version": not_installed, **lookups}
    return collect_environment("5.3.0", modules=modules or {}, **lookups)


def ai_diffusion_module(version: str) -> types.ModuleType:
    module = types.ModuleType("ai_diffusion")
    module.__dict__["__version__"] = version
    return module


def test_reports_this_plugin_and_interpreter():
    environment = collect()
    assert environment.plugin_version == __version__
    assert environment.python_version == tuple(sys.version_info[:3])
    assert environment.krita_version == "5.3.0"


def test_reports_the_loaded_qt_binding():
    assert collect({"PyQt5": object()}).qt_bindings == ("PyQt5",)
    assert collect().qt_bindings == ()


def test_reports_krita_ai_diffusion_loaded_installed_or_missing():
    loaded = collect({"ai_diffusion": ai_diffusion_module("1.53.0")})
    assert loaded.ai_diffusion == "1.53.0 (loaded)"
    installed = collect(find_spec=lambda name: object())
    assert installed.ai_diffusion.startswith("installed but not loaded")
    assert collect().ai_diffusion == "not installed"


def test_reports_c2pa_python_version():
    assert collect(dist_version=lambda distribution: "0.38.0").c2pa_python == "0.38.0"
    assert collect().c2pa_python is None


def test_reports_the_plugins_own_install_before_it_is_on_sys_path():
    own = Installed("win32-x86_64", "3.13", {"c2pa-python": "0.38.0", "cffi": "2.1.1"})
    assert collect(installed=own).c2pa_python == (
        "0.38.0 (installed by this plugin; loads when signing)"
    )
    loaded = collect(dist_version=lambda distribution: "0.38.0", installed=own)
    assert loaded.c2pa_python == "0.38.0"  # importable now: the plain version
    assert collect(installed=Installed("win32-x86_64", "3.13", {})).c2pa_python is None


def test_reports_generation_capture_once_the_adapter_has_tried():
    line = "Generation capture: observing Krita AI Diffusion 1.53.0."
    assert collect(capture=line).to_text().splitlines()[-1] == line
    assert "Generation capture" not in collect().to_text()


def test_flags_python_older_than_c2pa_python_supports():
    old = Environment("0.1.0", (3, 9, 18), None, ("PyQt5",), "not installed", None)
    assert old.problems() == ["Python 3.9.18 is older than 3.10, which c2pa-python needs."]
    current = Environment("0.1.0", (3, 10, 11), None, ("PyQt5",), "not installed", None)
    assert current.problems() == []


def test_flags_two_qt_bindings():
    both = Environment("0.1.0", (3, 10, 11), None, ("PyQt5", "PyQt6"), "not installed", None)
    assert len(both.problems()) == 1


def test_text_lists_every_fact_then_problems():
    text = Environment("0.1.0", (3, 9, 0), "5.3.0", ("PyQt5",), "1.53.0 (loaded)", None).to_text()
    assert text.splitlines() == [
        "krita-provenance 0.1.0",
        "Krita: 5.3.0",
        "Python: 3.9.0",
        "Qt binding: PyQt5",
        "Krita AI Diffusion: 1.53.0 (loaded)",
        "c2pa-python: not installed (Tools ▸ Scripts ▸ Provenance: Set Up Signing installs it)",
        "",
        "Problems:",
        "- Python 3.9.0 is older than 3.10, which c2pa-python needs.",
    ]
