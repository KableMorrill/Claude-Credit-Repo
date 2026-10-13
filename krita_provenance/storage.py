"""The log's home in the document: the ``krita-provenance.json`` annotation (P1, spike S4).

Krita-free. The glue (extension.py) reads the annotation bytes from a Document, hands them
here, and writes back what ``serialize`` or ``append`` returns. The log itself is append-only
(log.py).

Every change is written back at once, not debounced. P1 asks for writes "debounced and before
save", but Krita has no before-save signal, and ``Notifier.imageSaved`` fires after the file is
written: a write held back for a debounce would be missing from a save made in the meantime,
and the saved .kra would lack the event. Events are rare (one per applied generation or
export) and a write only replaces the annotation in memory, so writing at once costs little,
and a save at any moment includes everything logged before it.

The glue starts the log when Krita creates or opens a document (``Notifier.imageCreated``), so
its start time is when the plugin began observing. How a log starts, from the document's file
name alone:

- no file name: a new document, ``new_document``;
- an image file (png, jpg, ...): ``opened_image``, the file is the ``parentOf`` ingredient;
- a .kra or .ora: ``existing_document`` (decision 2 of 13 Oct 2026), the file is the parent.
"""

from __future__ import annotations

from datetime import datetime

from . import log
from .log import ANNOTATION, Event, IngredientAdded, LogFormatError, ProvenanceLog
from .mapping import ai_use_statement
from .scrub import file_name

DESCRIPTION = "krita-provenance log: AI generations and layer edits observed in this document"
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


def append(annotation: bytes | None, file_name_: str | None, event: Event) -> bytes:
    """The annotation's new bytes, with ``event`` at the end of its log; write them back at once.

    A document without a log gets one first, started now from its file name (``start``). An
    unreadable log raises StorageError and is never replaced (``read``).
    """
    entry = read(annotation)
    if entry is None:
        entry = start(file_name_)
    entry.append(event)
    return serialize(entry)


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
