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
its start time is when the plugin began observing. How a log starts:

- no file name, blank canvas: a new document, ``new_document``;
- no file name, content already in it (pasted, from a template, a file opened as untitled):
  ``started_with_content``, with no parent yet (decision 2 of 16 Oct 2026); ``observation``
  adds the parent when the plugin first sees it reopened from a saved .kra or .ora;
- an image file (png, jpg, ...): ``opened_image``, the file is the ``parentOf`` ingredient, and
  a copy of its bytes is kept in the ``PARENT_COPY`` annotation for signing (decision 1 of
  16 Oct 2026);
- a .kra or .ora: ``existing_document`` (decision 2 of 13 Oct 2026), the file is the parent.
"""

from __future__ import annotations

from datetime import datetime

from . import log
from .log import (
    ANNOTATION,
    CaptureGap,
    Event,
    Exported,
    GenerationApplied,
    IngredientAdded,
    LayerChanged,
    LogFormatError,
    ProvenanceLog,
    ReadBack,
)
from .mapping import ai_use_statement
from .scrub import file_name
from .sdk import UNTRUSTED_SIGNER

DESCRIPTION = "krita-provenance log: AI generations and layer edits observed in this document"
DOCUMENT_SUFFIXES = (".kra", ".ora")
SUMMARY_EVENTS = 8  # how many recent events Log Status lists
PARENT_COPY = "krita-provenance/ingredient-1"  # the spec's name for an ingredient's bytes (P2)
PARENT_COPY_DESCRIPTION = "krita-provenance: a copy of the image this document was opened from"
MAX_PARENT_COPY = (
    256 * 1024 * 1024
)  # bytes; a larger image isn't copied, and signing then needs it on disk


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


def start(
    file_name_: str | None,
    at: datetime | None = None,
    *,
    blank: bool = True,
    parent_copy: str | None = None,
) -> ProvenanceLog:
    """A new log for a document, with its parent ingredient when it came from a file.

    ``blank`` says whether an untitled document's canvas was blank when Krita created it; the
    glue checks. ``parent_copy`` names the annotation that holds an opened image's bytes, when
    the glue kept them (``keeps_parent_copy``).
    """
    at = at or log.now()
    name = file_name(file_name_) if file_name_ else ""
    if not name:
        return ProvenanceLog.new(log.NEW_DOCUMENT if blank else log.STARTED_WITH_CONTENT, at)
    if is_document_file(name):
        entry = ProvenanceLog.new(log.EXISTING_DOCUMENT, at)
        entry.append(IngredientAdded(at, log.PARENT_OF, name))
        return entry
    entry = ProvenanceLog.new(log.OPENED_IMAGE, at)
    entry.append(IngredientAdded(at, log.PARENT_OF, name, store=parent_copy))
    return entry


def is_document_file(file_name_: str) -> bool:
    """A Krita working document (.kra, .ora) rather than an image."""
    return file_name_.lower().endswith(DOCUMENT_SUFFIXES)


def keeps_parent_copy(file_name_: str | None) -> bool:
    """Whether a new log for this file is an opened image, whose bytes the glue keeps."""
    name = file_name(file_name_) if file_name_ else ""
    return bool(name) and not is_document_file(name)


def observation(
    entry: ProvenanceLog, file_name_: str | None, at: datetime | None = None
) -> list[Event]:
    """Events to log as the plugin begins observing a document that already has a log.

    A ``started_with_content`` document gets its parent when the plugin first sees it opened
    from a saved .kra or .ora (decision 2 of 16 Oct 2026); from then on it can be signed.
    """
    name = file_name(file_name_) if file_name_ else ""
    if entry.started.reason != log.STARTED_WITH_CONTENT or parent(entry) is not None:
        return []
    if not name or not is_document_file(name):
        return []
    return [IngredientAdded(at or log.now(), log.PARENT_OF, name)]


def parent(entry: ProvenanceLog) -> IngredientAdded | None:
    """The log's parentOf ingredient, if it has one."""
    return next(
        (
            e
            for e in entry.events
            if isinstance(e, IngredientAdded) and e.relationship == log.PARENT_OF
        ),
        None,
    )


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


def summary(entry: ProvenanceLog, latest: int | None = SUMMARY_EVENTS) -> str:
    """What the Log Status action shows: enough to see that the log survived a save, and what
    the last few events recorded. With ``latest`` None it lists every event, oldest first, as
    the docker's timeline (V1). Local text: it may show layer names, which can hold prompts."""
    kinds: dict[str, int] = {}
    for event in entry.events:
        kinds[log.event_type(event)] = kinds.get(log.event_type(event), 0) + 1
    counts = ", ".join(f"{n} {kind}" for kind, n in sorted(kinds.items())) or "none"
    lines = [
        f"Document id: {entry.document_id}",
        f"Log started: {log.format_time(entry.started.at)} ({entry.started.reason})",
        f"Events: {counts}",
        ai_use_statement(entry),
    ]
    if entry.events:
        count = len(entry.events)
        shown = entry.events[count - min(count, latest) :] if latest is not None else entry.events
        heading = "Latest events:" if len(shown) < len(entry.events) else "Events, oldest first:"
        lines += ["", heading, *(event_line(event) for event in shown)]
    return "\n".join(lines)


def event_line(event: Event) -> str:
    """One event in a line, for Log Status."""
    if isinstance(event, GenerationApplied):
        layer = event.target_layer
        where = f"on layer {layer.name}" if layer else "layer not identified"
        return (
            f"{log.format_time(event.at)}  AI generation: {event.source.tool}"
            f" {event.source.version}, {event.job.workflow_kind}, seed {event.job.seed}, {where}"
        )
    if isinstance(event, CaptureGap):
        return f"{log.format_time(event.start)}  capture gap: {event.reason}"
    if isinstance(event, IngredientAdded):
        return f"{log.format_time(event.at)}  {event.relationship}: {event.title}"
    if isinstance(event, LayerChanged):
        return f"{log.format_time(event.at)}  layer changed: {event.layer.name}"
    if isinstance(event, Exported):
        signed = "signed" if event.manifest_label else "unsigned"
        line = f"{log.format_time(event.at)}  exported {event.format}, {signed}"
        return line + (f", read back {_read_back(event.read_back)}" if event.read_back else "")
    return f"event of a newer type: {log.event_type(event)}"


def _read_back(read_back: ReadBack) -> str:
    """E4's result in a few words: "Valid, signer untrusted"."""
    others = [code for code in read_back.failures if code != UNTRUSTED_SIGNER]
    text = read_back.state or "with no state"
    if UNTRUSTED_SIGNER in read_back.failures:
        text += ", signer untrusted"
    return text + (f" ({', '.join(others)})" if others else "")
