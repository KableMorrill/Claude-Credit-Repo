"""First run: fetch the pinned wheels, make the local signing keys and prove they sign (I2, K1).

Krita-free. The glue (extension.py) finds the data folder, asks before anything is downloaded
(``consent_text``), runs ``run`` off the GUI thread with a progress dialog, and shows the
``Report``. Running it again is cheap: an install that matches the plan and keys that exist
are kept, and only the self-test runs again.

Where things go (decision 5 of 13 Oct 2026, second set): a ``krita-provenance`` folder in
Krita's app-data location, never inside the plugin folder, so reinstalling the plugin cannot
delete the keys. Under it, ``site-packages`` and friends belong to installer.py and ``keys``
to keys.py.

The self-test signs a blank image with the new certificate and reads it back through sdk.py,
so a report that says "set up" means c2pa-python loaded in this Python and accepted the key
(spike S1 asks for exactly that inside Krita). It also says when a package was loaded from
somewhere other than the verified install, such as a copy another plugin imported first: then
the code that runs is not the code whose SHA-256 was checked.
"""

from __future__ import annotations

import struct
import sys
import tempfile
import threading
import zlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from . import __version__, installer, keys, mapping, sdk
from .installer import Fetch, Installed, InstallError, Plan
from .keys import CertificateInfo
from .sdk import SignerConfig
from .wheels import Wheel

FOLDER = "krita-provenance"
KEYS = "keys"
PLUGIN_FOLDER = Path(__file__).parent
SOURCE = "PyPI (files.pythonhosted.org)"
WATCHED_MODULES = ("c2pa", "cryptography", "cffi", "_cffi_backend")  # what the wheels provide

Progress = Callable[[int, int, str], None]  # bytes done, bytes in all, what is happening


class FirstRunError(Exception):
    """The message is safe to show to the user."""


class Cancelled(InstallError):
    """The user cancelled the download; nothing was installed."""


@dataclass(frozen=True)
class SelfTest:
    passed: bool
    detail: str  # a sentence for the report


@dataclass(frozen=True)
class Report:
    folder: Path
    installed: Installed
    downloaded: bool  # False when the install already matched the plan
    loaded: str | None  # "c2pa-python 0.38.0 with c2pa-rs 0.91.0", once it imported here
    certificate: CertificateInfo | None  # None where K1 is unavailable
    self_test: SelfTest | None
    unavailable: tuple[str, ...] = ()  # from the plan: what this platform can't have
    problems: tuple[str, ...] = ()

    @property
    def ready(self) -> bool:
        """The local certificate signs, and nothing loaded from outside the verified install."""
        return (
            self.certificate is not None
            and self.self_test is not None
            and self.self_test.passed
            and not self.problems
        )

    def headline(self) -> str:
        if self.ready:
            return "Signing is set up."
        if self.certificate is None and not self.problems:
            return (
                "The packages are installed, but this platform can't make a local signing"
                " certificate yet."
            )
        return "Signing is not ready; see below."

    def to_text(self) -> str:
        packages = ", ".join(
            f"{name} {version}" for name, version in self.installed.packages.items()
        )
        lines = [
            self.headline(),
            "",
            f"Packages: {packages}",
            (
                "Installed now; every downloaded file's size and SHA-256 matched the pins."
                if self.downloaded
                else "Already installed; nothing was downloaded."
            ),
            f"Folder: {self.folder}",
        ]
        if self.loaded:
            lines.append(f"Loaded: {self.loaded}")
        if self.certificate is not None:
            lines += ["", "Local signing certificate:", *self.certificate.to_text().splitlines()]
        if self.self_test is not None:
            lines += ["", self.self_test.detail]
        if self.unavailable or self.problems:
            lines += ["", *self.unavailable, *self.problems]
        return "\n".join(lines)


def data_folder(app_data: str | Path, plugin_folder: Path = PLUGIN_FOLDER) -> Path:
    """The plugin's data folder under Krita's app-data location; never inside the plugin."""
    if not str(app_data).strip():
        raise FirstRunError("Krita did not report an app-data folder to keep packages and keys in")
    folder = Path(app_data) / FOLDER
    for plugin in {plugin_folder, plugin_folder.resolve()}:
        if folder.resolve().is_relative_to(plugin.resolve()) or folder.is_relative_to(plugin):
            raise FirstRunError(
                "The data folder would be inside the plugin folder, where reinstalling the plugin"
                " would delete the signing keys"
            )
    return folder


def consent_text(wanted: Plan, folder: Path) -> str:
    """What the user agrees to before anything is downloaded."""
    lines = [f"To sign exports, this plugin needs these packages from {SOURCE}:", ""]
    lines += [
        f"    {wheel.package} {wheel.version} ({megabytes(wheel.size)})" for wheel in wanted.wheels
    ]
    lines += [
        "",
        (
            f"{megabytes(wanted.total_size)} in all. Each file's size and SHA-256 must match the"
            " values pinned in this plugin, or nothing is installed. They are unpacked into:"
        ),
        "",
        f"    {folder}",
        "",
    ]
    if wanted.supports_k1:
        lines.append(
            "Then a local signing certificate is made and kept in that folder. Its private key"
            " is not encrypted; only your user account's file permissions protect it."
            " Validators will show this signer as unrecognized."
        )
    lines += [*wanted.unavailable, "", "Download and set up now?"]
    return "\n".join(lines)


def run(
    folder: Path,
    wanted: Plan,
    fetch: Fetch | None = None,
    progress: Progress | None = None,
    cancelled: threading.Event | None = None,
) -> Report:
    """Install what the plan needs, activate it, make the keys and sign a test image.

    Raises Cancelled when ``cancelled`` is set during the download, and InstallError,
    KeysError or FirstRunError when a step fails. A failing self-test is reported, not raised:
    the install and the keys are kept.
    """
    downloaded = not installer.is_current(folder, wanted)
    if downloaded:
        installer.install(folder, wanted, fetch, _download_progress(wanted, progress, cancelled))
    current = installer.installed(folder)
    if current is None:
        raise FirstRunError("The installed packages could not be read back")
    site = installer.activate(folder)
    if site is None:
        raise FirstRunError("The packages are installed, but not for this Python or platform")

    problems: list[str] = []
    loaded = None
    try:
        python_version, rust_version = sdk.version()
        loaded = f"c2pa-python {python_version} with c2pa-rs {rust_version}"
        if (problem := sdk.version_problem()) is not None:
            problems.append(problem)
    except (sdk.SdkError, OSError, RuntimeError, ValueError) as error:
        # ctypes raises OSError for a native library that won't load; c2pa-python's own loader
        # raises RuntimeError or ValueError when it finds none for this platform.
        problems.append(f"c2pa-python could not be loaded: {error}")

    certificate = self_test = None
    if wanted.supports_k1:
        _stage(progress, wanted, "Making the local signing certificate")
        files = keys.ensure(folder / KEYS)
        certificate = files.info()
        _stage(progress, wanted, "Signing a test image")
        self_test = sign_test_image(files.signer_config())

    for name, location in loaded_from_elsewhere(site).items():
        problems.append(
            f"{name} was loaded from {location}, outside the verified install; restart Krita so"
            " the checked copy is used."
        )
    return Report(
        folder,
        current,
        downloaded,
        loaded,
        certificate,
        self_test,
        wanted.unavailable,
        tuple(problems),
    )


def sign_test_image(signer: SignerConfig) -> SelfTest:
    """Sign a blank PNG in a temporary folder and read it back (E4); nothing is kept."""
    try:
        with tempfile.TemporaryDirectory(
            prefix="krita-provenance-", ignore_cleanup_errors=True
        ) as temporary:
            source = Path(temporary) / "self-test.png"
            source.write_bytes(blank_png())
            result = sdk.sign_file(source, Path(temporary) / "signed.png", _SELF_TEST, signer)
    except sdk.SdkError as error:
        return SelfTest(False, f"Self-test failed: {error}")
    validation = result.validation
    outcome = (
        f"{validation.state}, with the signer reported as untrusted, as expected for a local"
        " certificate"
        if validation.signer_untrusted
        else f"{validation.state}"
    )
    return SelfTest(
        True,
        f"Self-test: a blank test image was signed with this certificate and read back: {outcome}.",
    )


def loaded_from_elsewhere(
    site: Path, modules: Mapping[str, object] = sys.modules
) -> dict[str, str]:
    """The wheels' modules that are loaded, but not from ``site``: name -> where from."""
    root = site.resolve()
    found: dict[str, str] = {}
    for name in WATCHED_MODULES:
        location = getattr(modules.get(name), "__file__", None)
        if isinstance(location, str) and not Path(location).resolve().is_relative_to(root):
            found[name] = location
    return found


def megabytes(size: int) -> str:
    return f"{size / 1_000_000:.1f} MB"


def blank_png(size: int = 16) -> bytes:
    """A white square PNG, made here so the plugin needs no image file of its own."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
        )

    header = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)  # 8-bit RGB
    pixels = (b"\x00" + b"\xff\xff\xff" * size) * size
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(pixels))
        + chunk(b"IEND", b"")
    )


_SELF_TEST = mapping.Manifest(
    definition={
        "claim_generator_info": [{"name": mapping.CLAIM_GENERATOR, "version": __version__}],
        "title": "krita-provenance self-test.png",
        "assertions": [
            {
                "label": mapping.ACTIONS,
                "data": {
                    "actions": [{"action": "c2pa.created", "digitalSourceType": mapping.EMPTY}]
                },
            }
        ],
    },
    intent=mapping.CREATE,
    source_type=mapping.EMPTY,
)


def _download_progress(
    wanted: Plan, progress: Progress | None, cancelled: threading.Event | None
) -> installer.Progress:
    """The installer reports per wheel; the dialog shows one figure for all of them."""
    offsets: dict[str, int] = {}
    running = 0
    for wheel in wanted.wheels:
        offsets[wheel.filename] = running
        running += wheel.size

    def report(wheel: Wheel, done: int, expected: int) -> None:
        if cancelled is not None and cancelled.is_set():
            raise Cancelled("Cancelled; nothing was installed.")
        if progress is not None:
            label = (
                f"Downloading {wheel.package} {wheel.version}:"
                f" {megabytes(done)} of {megabytes(expected)}"
            )
            progress(offsets.get(wheel.filename, 0) + done, wanted.total_size, label)

    return report


def _stage(progress: Progress | None, wanted: Plan, label: str) -> None:
    if progress is not None:
        progress(wanted.total_size, wanted.total_size, f"{label}…")
