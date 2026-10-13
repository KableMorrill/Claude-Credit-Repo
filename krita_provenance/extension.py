"""Krita-facing glue: the Extension that Krita loads at startup.

Only Krita-facing modules may import `krita`; everything else must run without it
(tests/test_architecture.py). Keep this module thin: it runs only inside Krita, where unit tests
can't reach it.
"""

from krita import Document, Extension, Krita, Window

from . import storage
from .environment import collect_environment
from .log import ANNOTATION
from .qt import QtWidgets

ENVIRONMENT_ACTION_ID = "krita_provenance_environment"
LOG_STATUS_ACTION_ID = "krita_provenance_log_status"


class ProvenanceExtension(Extension):
    def setup(self) -> None:
        pass

    def createActions(self, window: Window) -> None:
        action = window.createAction(
            ENVIRONMENT_ACTION_ID, "Provenance: Check Environment", "tools/scripts"
        )
        action.triggered.connect(lambda: _show_environment(window))
        status = window.createAction(
            LOG_STATUS_ACTION_ID, "Provenance: Log Status", "tools/scripts"
        )
        status.triggered.connect(lambda: _show_log_status(window))


def _show_environment(window: Window) -> None:
    environment = collect_environment(krita_version=Krita.instance().version())
    QtWidgets.QMessageBox.information(
        window.qwindow(), "Provenance environment", environment.to_text()
    )


def _show_log_status(window: Window) -> None:
    """Spike S4: create the log in the active document if absent, then show it. Save and reopen
    the document and run this again: the document id must be the same."""
    document = Krita.instance().activeDocument()
    if document is None:
        QtWidgets.QMessageBox.information(
            window.qwindow(), "Provenance log", "Open a document first."
        )
        return
    try:
        entry = read_log(document)
        created = entry is None
        if entry is None:
            entry = storage.start(document.fileName() or None)
            write_log(document, entry)
    except storage.StorageError as error:
        QtWidgets.QMessageBox.warning(window.qwindow(), "Provenance log", str(error))
        return
    text = storage.summary(entry)
    if created:
        text += "\n\nThe log was just created and written to the document; save it to keep it."
    QtWidgets.QMessageBox.information(window.qwindow(), "Provenance log", text)


def read_log(document: Document):
    data = (
        bytes(document.annotation(ANNOTATION)) if ANNOTATION in document.annotationTypes() else None
    )
    return storage.read(data)


def write_log(document: Document, entry) -> None:
    document.setAnnotation(ANNOTATION, storage.DESCRIPTION, storage.serialize(entry))


def register() -> None:
    app = Krita.instance()
    app.addExtension(ProvenanceExtension(app))
