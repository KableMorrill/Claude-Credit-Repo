"""The one module that talks to c2pa-python (spec: Risks, "wrap the SDK in one module").

c2pa-python 0.38.x arrives at first run (I2), so ``c2pa`` is imported lazily, inside the
functions that need it; importing this module never imports it. Everything else in the plugin
works with plain data and this module's results.

What it guarantees:

- E4: every signed file is read back with the SDK and its validation state is reported.
- E5: no custom hash exclusions. A definition carrying a ``c2pa.hash.*`` assertion is refused,
  and after signing the data-hash assertion must list exactly one exclusion, the manifest
  store itself, or the output is discarded.
- E6: a file either carries a manifest that validated on read-back or does not exist. Signing
  goes to a temporary file beside the destination, which is only moved into place at the end;
  on any failure the temporary file is removed and ``SigningError`` says why. The caller then
  saves the plain export under ``unsigned_name()``.
- Imported manifests are read only through the SDK, files over a size cap are refused, and the
  manifest text comes back as plain JSON for display as plain text.

Checked against c2pa-python 0.38.0 (c2pa-rs 0.91.0): with a signer the SDK does not trust,
``validation_state`` is "Valid" and ``validation_results.activeManifest.failure`` holds one
entry, ``signingCredential.untrusted``; ``signingCredential.ocsp.skipped`` appears under
``informational`` because no network call is made. Those two are the only codes a fresh
export may carry.
"""

from __future__ import annotations

import io
import json
import os
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import mapping
from .mapping import Manifest

SDK_DISTRIBUTION = "c2pa-python"
SDK_VERSIONS = "0.38."  # the pinned minor; upgrade on purpose (spec: Risks)

UNTRUSTED_SIGNER = "signingCredential.untrusted"
ACCEPTED_FAILURES = frozenset({UNTRUSTED_SIGNER})  # a local CA is unknown to every trust list
ACCEPTED_INFORMATIONAL = frozenset({"signingCredential.ocsp.skipped"})  # no network at export

MAX_READ_BYTES = 512 * 1024 * 1024  # files above this are not opened at all
MAX_MANIFEST_TEXT = 4 * 1024 * 1024  # characters of manifest JSON kept for display

MIME_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}  # E2

UNSIGNED_SUFFIX = "-unsigned"


class SdkError(Exception):
    """Base class; the message is safe to show to the user."""


class SdkMissing(SdkError):
    """c2pa-python is not installed where this Python can import it (I2 fetches it)."""


class SigningError(SdkError):
    """Nothing was written to the destination."""


class ReadError(SdkError):
    """The file could not be read as an asset."""


@dataclass(frozen=True)
class SignerConfig:
    certificate_chain: bytes  # PEM, the signing certificate first, then its issuers
    private_key: bytes  # PEM, PKCS#8
    algorithm: str = "es256"
    timestamp_url: str | None = None  # K3; None means no timestamp and no network call


@dataclass(frozen=True)
class IngredientData:
    """The bytes behind one of the manifest's ingredients, resolved by the exporter."""

    instance_id: str
    data: bytes


@dataclass(frozen=True)
class Validation:
    state: str | None  # "Trusted", "Valid" or "Invalid", as the SDK reports it
    failures: tuple[str, ...]  # validation codes
    informational: tuple[str, ...]

    def is_acceptable(self) -> bool:
        """Valid, with at most an untrusted signer. A fresh export must pass this."""
        return self.state in ("Valid", "Trusted") and set(self.failures) <= ACCEPTED_FAILURES

    @property
    def signer_untrusted(self) -> bool:
        return UNTRUSTED_SIGNER in self.failures


@dataclass(frozen=True)
class ReadResult:
    manifest_label: str
    validation: Validation
    manifest_text: str  # Reader.json(), to show as plain text; never render it
    truncated: bool  # manifest_text was cut at MAX_MANIFEST_TEXT


def available() -> bool:
    try:
        _c2pa()
    except SdkMissing:
        return False
    return True


def version() -> tuple[str, str]:
    """(c2pa-python version, c2pa-rs version)."""
    c2pa = _c2pa()
    return c2pa.__version__, c2pa.sdk_version()


def version_problem() -> str | None:
    """Why the installed SDK is not the one this plugin was tested with, if it isn't."""
    python_version, _ = version()
    if not python_version.startswith(SDK_VERSIONS):
        return (
            f"{SDK_DISTRIBUTION} {python_version} is installed; this plugin needs {SDK_VERSIONS}x"
        )
    return None


def unsigned_name(path: Path) -> Path:
    """Where the plain export goes when signing fails (E6): harbour.png -> harbour-unsigned.png."""
    return path.with_name(f"{path.stem}{UNSIGNED_SUFFIX}{path.suffix}")


def sign_file(
    source: Path,
    destination: Path,
    manifest: Manifest,
    signer: SignerConfig,
    ingredients: Sequence[IngredientData] = (),
) -> ReadResult:
    """Write ``source`` to ``destination`` with the manifest embedded and signed.

    Returns the read-back result (E4). Raises SigningError with nothing at ``destination``
    (E6); a file already there is left as it was.
    """
    mime_type = MIME_TYPES.get(source.suffix.lower())
    if mime_type is None:
        raise SigningError(f"{source.name} is not a PNG or JPEG; only those can be signed")
    refuse_hash_assertions(manifest.definition)
    data_by_id = {item.instance_id: item.data for item in ingredients}
    for ingredient in manifest.ingredients:
        instance_id = ingredient.definition["instance_id"]
        if instance_id not in data_by_id:
            raise SigningError(f"No data for ingredient {ingredient.definition['title']!r}")
        if ingredient.format is None:
            raise SigningError(f"Unknown format for ingredient {ingredient.definition['title']!r}")

    c2pa = _c2pa()
    destination.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".part", dir=destination.parent
    )
    os.close(handle)
    temporary = Path(temporary_name)
    try:
        native_signer = _signer(c2pa, signer)
        builder = c2pa.Builder(manifest.definition)
        _set_intent(c2pa, builder, manifest)
        for ingredient in manifest.ingredients:
            instance_id = ingredient.definition["instance_id"]
            builder.add_ingredient(
                ingredient.definition, ingredient.format, io.BytesIO(data_by_id[instance_id])
            )
        with open(source, "rb") as source_file, open(temporary, "w+b") as output:
            builder.sign(native_signer, mime_type, source_file, output)

        result = read_file(temporary)
        if result is None:
            raise SigningError("The SDK wrote no manifest into the output")
        if not result.validation.is_acceptable():
            raise SigningError(
                "The signed output did not validate on read-back:"
                f" {result.validation.state}, {', '.join(result.validation.failures) or 'no codes'}"
            )
        exclusions = data_hash_exclusions(temporary)
        if len(exclusions) != 1:
            raise SigningError(
                f"The data hash has {len(exclusions)} exclusions; exactly one is allowed"
            )
    except SigningError:
        _remove(temporary)
        raise
    except Exception as error:  # the SDK's errors, I/O errors: nothing may be left behind (E6)
        _remove(temporary)
        raise SigningError(f"Signing failed: {error}") from error
    os.replace(temporary, destination)
    return result


def read_file(path: Path, max_bytes: int = MAX_READ_BYTES) -> ReadResult | None:
    """Read the manifest in a file through the SDK. None if the file carries none (E4, V2)."""
    c2pa = _c2pa()
    size = path.stat().st_size
    if size > max_bytes:
        raise ReadError(f"{path.name} is {size} bytes; files over {max_bytes} bytes are not read")
    try:
        with c2pa.Reader(path) as reader:
            text = reader.json()
    except c2pa.C2paError.ManifestNotFound:
        return None
    except c2pa.C2paError as error:
        raise ReadError(f"Could not read {path.name}: {error}") from error
    data = json.loads(text)
    results = (data.get("validation_results") or {}).get("activeManifest") or {}
    return ReadResult(
        manifest_label=str(data.get("active_manifest", "")),
        validation=Validation(
            state=data.get("validation_state"),
            failures=_codes(results.get("failure")),
            informational=_codes(results.get("informational")),
        ),
        manifest_text=text[:MAX_MANIFEST_TEXT],
        truncated=len(text) > MAX_MANIFEST_TEXT,
    )


def data_hash_exclusions(path: Path) -> list[dict[str, Any]]:
    """The exclusions in the active manifest's data-hash assertion (E5)."""
    c2pa = _c2pa()
    try:
        with c2pa.Reader(path) as reader:
            detailed = json.loads(reader.detailed_json())
    except c2pa.C2paError as error:
        raise ReadError(f"Could not read {path.name}: {error}") from error
    active = detailed["manifests"][detailed["active_manifest"]]
    store = active.get("assertion_store") or {}
    hashes = [store[label] for label in store if label.startswith("c2pa.hash.")]
    if len(hashes) != 1 or not isinstance(hashes[0].get("exclusions"), list):
        raise ReadError("Could not find the data hash assertion and its exclusions")
    return hashes[0]["exclusions"]


def refuse_hash_assertions(definition: dict[str, Any]) -> None:
    """E5: hash assertions, and so exclusions, come from the SDK alone."""
    for assertion in definition.get("assertions", []):
        label = str(assertion.get("label", ""))
        if label.startswith("c2pa.hash."):
            raise SigningError(f"The manifest definition must not contain {label}")


def _c2pa() -> Any:
    try:
        import c2pa
    except ImportError as error:
        raise SdkMissing(f"{SDK_DISTRIBUTION} is not installed") from error
    return c2pa


def _signer(c2pa: Any, config: SignerConfig) -> Any:
    info = c2pa.C2paSignerInfo(
        config.algorithm, config.certificate_chain, config.private_key, config.timestamp_url
    )
    return c2pa.Signer.from_info(info)


def _set_intent(c2pa: Any, builder: Any, manifest: Manifest) -> None:
    if manifest.intent == mapping.CREATE:
        if manifest.source_type != mapping.EMPTY:
            raise SigningError(f"Unsupported digital source type {manifest.source_type!r}")
        builder.set_intent(c2pa.C2paBuilderIntent.CREATE, c2pa.C2paDigitalSourceType.EMPTY)
    elif manifest.intent == mapping.EDIT:
        builder.set_intent(c2pa.C2paBuilderIntent.EDIT)
    else:
        raise SigningError(f"Unknown builder intent {manifest.intent!r}")


def _codes(entries: Any) -> tuple[str, ...]:
    if not isinstance(entries, list):
        return ()
    return tuple(str(entry.get("code", "")) for entry in entries if isinstance(entry, dict))


def _remove(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
