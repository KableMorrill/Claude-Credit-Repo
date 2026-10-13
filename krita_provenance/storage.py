"""The log's home in the document: the ``krita-provenance.json`` annotation (P1, spike S4).

Krita-free. The glue (extension.py) reads the annotation bytes from a Document, hands them
here, and writes back what ``serialize`` returns. Krita has no before-save signal, so writes
are debounced: every append schedules a write a moment later, as Krita AI Diffusion does
with ``ui.json``, and the glue flushes on its timer. The log itself is append-only (log.py).

How a log starts, from the document's file name alone:

- no file name: a new document, ``new_document``;
- an image file (png, jpg, ...): ``opened_image``, the file is the ``parentOf`` ingredient;
- a .kra or .ora: ``existing_document`` (decision 2 of 13 Oct 2026), the file is the parent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from . import log
from .log import ANNOTATION, IngredientAdded, LogFormatError, ProvenanceLog
from .mapping import ai_use_statement
from .scrub import file_name

DESCRIPTION = "krita-provenance log: AI generations and layer edits observed in this document"
DEBOUNCE_SECONDS = 1.0
DOCUMENT_SUFFIXES = (".kra", ".ora")


class StorageError(Exception):
    """The message is safe to show to the user."""


def read(annotation: bytes | None) -> ProvenanceLog | None:
    """The log in an annotation's bytes; None if the document has none yet.

    A log this version cannot read is an error, never silently replaced: that would drop
    history (P3 migrates older schemas in v1.0).
    """
    if not annotation:
        return None
    try:
        return ProvenanceLog.from_json(annotation)
    except LogFormatError as error:
        raise StorageError(
            f"The document's {ANNOTATION} annotation cannot be read: {error}"
        ) from error


def serialize(entry: ProvenanceLog) -> bytes:
    return entry.to_json().encode("utf-8")


def start(file_name_: str | None, at: datetime | None = None) -> ProvenanceLog:
    """A new log for a document, with its parent ingredient when it came from a file."""
    at = at or log.now()
    name = file_name(file_name_) if file_name_ else ""
    if not name:
        return ProvenanceLog.new(log.NEW_DOCUMENT, at)
    reason = log.EXISTING_DOCUMENT if name.lower().endswith(DOCUMENT_SUFFIXES) else log.OPENED_IMAGE
    entry = ProvenanceLog.new(reason, at)
    entry.append(IngredientAdded(at, log.PARENT_OF, name))
    return entry


def summary(entry: ProvenanceLog) -> str:
    """What the Log Status action shows: enough to see that the log survived a save."""
    kinds: dict[str, int] = {}
    for event in entry.events:
        kinds[log.event_type(event)] = kinds.get(log.event_type(event), 0) + 1
    counts = ", ".join(f"{n} {kind}" for kind, n in sorted(kinds.items())) or "none"
    return "\n".join([
        f"Document id: {entry.document_id}",
        f"Log started: {log.format_time(entry.started.at)} ({entry.started.reason})",
        f"Events: {counts}",
        ai_use_statement(entry),
    ])


@dataclass
class Debounce:
    """Decides when a pending write is due. Pure; the glue drives it from a timer."""

    delay: float = DEBOUNCE_SECONDS
    due_at: float | None = field(default=None, init=False)

    def touch(self, now: float) -> None:
        self.due_at = now + self.delay

    def ready(self, now: float) -> bool:
        return self.due_at is not None and now >= self.due_at

    def clear(self) -> None:
        self.due_at = None
