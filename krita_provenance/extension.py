"""Krita-facing glue: the Extension that Krita loads at startup.

Only Krita-facing modules may import `krita`; everything else must run without it
(tests/test_architecture.py). Keep this module thin: it runs only inside Krita, where unit tests
can't reach it.

libkis wrappers (Window, Document, Node) are short-lived: the Window handed to createActions is
deleted once setup is over, and a handler that kept it fails with "wrapped C/C++ object has been
deleted" (seen in Krita 5.3 on Windows, 13 Oct 2026). Handlers therefore take no arguments and
look up the active window and document when they fire; tests/test_architecture.py checks that
no handler closes over the createActions parameter. Notifier.imageCreated deletes the Document
it passes as soon as its handlers return.
"""

from krita import Document, Extension, Krita, Window

from . import storage
from .environment import collect_environment
from .log import ANNOTATION, ProvenanceLog
from .qt import QtCore, QtWidgets

ENVIRONMENT_ACTION_ID = "krita_provenance_environment"
LOG_STATUS_ACTION_ID = "krita_provenance_log_status"


class ProvenanceExtension(Extension):
    def setup(self) -> None:
        notifier = Krita.instance().notifier()
        notifier.setActive(True)
        # The vendored stubs declare Notifier's signals as plain methods.
        notifier.imageCreated.connect(_start_log)  # pyright: ignore[reportAttributeAccessIssue]

    def createActions(self, window: Window) -> None:
        action = window.createAction(
            ENVIRONMENT_ACTION_ID, "Provenance: Check Environment", "tools/scripts"
        )
        action.triggered.connect(_show_environment)
        status = window.createAction(
            LOG_STATUS_ACTION_ID, "Provenance: Log Status", "tools/scripts"
        )
        status.triggered.connect(_show_log_status)


def _parent() -> QtWidgets.QWidget | None:
    """The active window's QWidget for dialogs, looked up now; None when there is none."""
    window = Krita.instance().activeWindow()
    return window.qwindow() if window is not None else None


def _start_log(document: Document) -> None:
    """Notifier.imageCreated, after Krita creates a document or loads one from a file: start its
    log now, so the start time is when the plugin began observing it (storage.py)."""
    if document.rootNode() is None:
        return  # no image yet (Krita.createDocument registers first); setAnnotation needs one
    try:
        ensure_log(document)
    except storage.StorageError as error:
        message = (
            f"{error}\n\nNothing will be recorded in this document, and its log is kept as is."
        )
        # Show it once the document is open; a dialog here would run inside Krita's open.
        QtCore.QTimer.singleShot(
            0, lambda: QtWidgets.QMessageBox.warning(_parent(), "Provenance log", message)
        )


def _show_environment() -> None:
    environment = collect_environment(krita_version=Krita.instance().version())
    QtWidgets.QMessageBox.information(_parent(), "Provenance environment", environment.to_text())


def _show_log_status() -> None:
    """Spike S4: create the log in the active document if absent, then show it. Save and reopen
    the document and run this again: the document id must be the same."""
    document = Krita.instance().activeDocument()
    if document is None:
        QtWidgets.QMessageBox.information(_parent(), "Provenance log", "Open a document first.")
        return
    try:
        entry, created = ensure_log(document)
    except storage.StorageError as error:
        QtWidgets.QMessageBox.warning(_parent(), "Provenance log", str(error))
        return
    text = storage.summary(entry)
    if created:
        text += "\n\nThe log was just created and written to the document; save it to keep it."
    QtWidgets.QMessageBox.information(_parent(), "Provenance log", text)


def ensure_log(document: Document) -> tuple[ProvenanceLog, bool]:
    """The document's log, and True if it had none and one was started and written just now.

    Starting a log is not an edit, but Krita marks a document modified whenever an annotation is
    set (KisImage::addAnnotation). A document that had no unsaved changes is marked unmodified
    again, so Krita doesn't ask to save a file the user only opened; any later change marks it
    modified, and that save includes the log. Events appended later keep Krita's flag.
    """
    entry = read_log(document)
    if entry is not None:
        return entry, False
    entry = storage.start(document.fileName() or None)
    was_modified = document.modified()
    write_log(document, entry)
    if not was_modified:
        document.setModified(False)  # pyright: ignore[reportAttributeAccessIssue] (not in the stubs)
    return entry, True


def read_log(document: Document):
    data = (
        bytes(document.annotation(ANNOTATION)) if ANNOTATION in document.annotationTypes() else None
    )
    return storage.read(data)


def write_log(document: Document, entry) -> None:
    """The document must have an image: Krita 5.3's setAnnotation uses it without a check. The
    write marks the document modified."""
    document.setAnnotation(ANNOTATION, storage.DESCRIPTION, storage.serialize(entry))


def register() -> None:
    app = Krita.instance()
    app.addExtension(ProvenanceExtension(app))
