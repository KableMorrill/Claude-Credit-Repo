"""The signed export (E1, E2, E4 to E6), from the rendered image onwards.

Krita-free, so it is tested with real signing (tests/test_exporter.py). The export action in
extension.py asks where to save, renders the document to a temporary file with Krita's
``Document.exportImage`` (E1), gathers the layer states and the parent's bytes, and calls
``export``. The manifest is rebuilt from the log here, at every export.

E6: if anything stops the signature (signing not set up, a log that can't be described
honestly, a parent file that isn't available, the SDK refusing or failing its read-back), the
rendered image is saved as ``<name>-unsigned.<ext>`` beside the name the user chose, and nothing
is written under that name. A file that isn't signed never takes the name of a signed export.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from . import log, mapping, sdk
from .log import Exported, ProvenanceLog
from .mapping import Export, Manifest
from .scrub import Identity
from .sdk import IngredientData, SignerConfig, Validation

FORMATS = {".png": "png", ".jpg": "jpeg", ".jpeg": "jpeg"}  # E2; mapping.FORMATS has the MIME types
DISCLOSURE = "standard"  # Q3's proposed default; D2 adds full and minimal
MAX_PARENT_BYTES = sdk.MAX_READ_BYTES  # a larger parent file isn't read into memory


class ExportError(Exception):
    """The message is safe to show to the user."""


@dataclass(frozen=True)
class Outcome:
    path: Path  # where the image was saved
    signed: bool
    validation: Validation | None  # E4: what reading the signed file back reported
    manifest_label: str | None
    reason: str | None  # why it isn't signed
    event: Exported  # for the document's log

    def to_text(self, entry: ProvenanceLog) -> str:
        return f"{self.result_text()}\n\n{mapping.ai_use_statement(entry)}"

    def result_text(self) -> str:
        """Where the image went and whether it is signed, without the AI-use statement."""
        if self.signed and self.validation is not None:
            untrusted = (
                ", with the signer reported as untrusted, as expected for a local certificate"
                if self.validation.signer_untrusted
                else ""
            )
            lines = [
                f"Saved and signed: {self.path}",
                "",
                f"Read back: {self.validation.state}{untrusted}.",
                f"Manifest: {self.manifest_label}",
            ]
        else:
            lines = [
                f"Saved without a signature: {self.path}",
                "",
                f"Why: {self.reason}",
                "",
                (
                    "Its name ends in -unsigned so it can't be mistaken for a signed export."
                    " Nothing was written under the name you chose; a file already there was"
                    " left as it was."
                ),
            ]
        return "\n".join(lines)


def format_for(path: Path) -> str:
    """E2: the export format for a file name the user chose."""
    format_ = FORMATS.get(path.suffix.lower())
    if format_ is None:
        raise ExportError(f"{path.name}: choose a file name ending in .png, .jpg or .jpeg")
    return format_


def unsigned_path(destination: Path, exists: Callable[[Path], bool] = Path.exists) -> Path:
    """Where an unsigned export goes: harbour-unsigned.png, or harbour-unsigned-2.png and on if
    that is taken, so an earlier file is never overwritten without the user choosing it."""
    first = sdk.unsigned_name(destination)
    candidate, number = first, 2
    while exists(candidate):
        candidate = first.with_name(f"{first.stem}-{number}{first.suffix}")
        number += 1
    return candidate


def export(
    entry: ProvenanceLog,
    rendered: Path,
    destination: Path,
    details: Export,
    signer: SignerConfig | None,
    parent_data: bytes | None,
    *,
    identity: Identity | None = None,
    unsigned_reason: str | None = None,
) -> Outcome:
    """Sign ``rendered`` into ``destination`` with a manifest built from the log, or, when that
    can't be done or ``unsigned_reason`` says not to, save it as unsigned (E6).

    ``parent_data`` is the bytes of the file the document came from, for its parentOf
    ingredient: the kept copy of an opened image, or the saved .kra. Raises OSError only if even
    the unsigned copy can't be written.
    """
    reason = unsigned_reason
    if reason is None:
        try:
            if signer is None:
                raise ExportError("signing isn't set up yet")
            manifest = mapping.manifest(entry, details, identity)
            ingredients = _ingredient_data(manifest, parent_data)
            result = sdk.sign_file(rendered, destination, manifest, signer, ingredients)
        except (mapping.MappingError, sdk.SdkError, ExportError) as error:
            reason = str(error)
        else:
            label = result.manifest_label
            event = Exported(details.at, details.format, DISCLOSURE, label)
            return Outcome(destination, True, result.validation, label, None, event)
    target = unsigned_path(destination)
    shutil.copyfile(rendered, target)
    event = Exported(details.at, details.format, DISCLOSURE, None)
    return Outcome(target, False, None, None, reason, event)


def _ingredient_data(manifest: Manifest, parent_data: bytes | None) -> list[IngredientData]:
    found: list[IngredientData] = []
    for ingredient in manifest.ingredients:
        title = ingredient.definition["title"]
        if ingredient.definition["relationship"] != log.PARENT_OF:
            raise ExportError(
                f"it includes a placed file, {title}, and placed files can't be signed into an"
                " export yet"
            )
        if parent_data is None:
            raise ExportError(
                f"the file this document was opened from, {title}, isn't available to sign with"
            )
        found.append(IngredientData(ingredient.definition["instance_id"], parent_data))
    return found
