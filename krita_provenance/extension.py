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

The first run's download runs on a Python thread. Its progress and result reach the GUI thread
through the signals of a QObject made in the GUI thread, so every dialog call happens there.
"""

import threading
from pathlib import Path

from krita import Document, Extension, Krita, Window

from . import firstrun, installer, keys, sdk, storage
from .environment import collect_environment
from .log import ANNOTATION, ProvenanceLog
from .qt import QtCore, QtWidgets

ENVIRONMENT_ACTION_ID = "krita_provenance_environment"
LOG_STATUS_ACTION_ID = "krita_provenance_log_status"
SET_UP_ACTION_ID = "krita_provenance_set_up_signing"
SET_UP_TITLE = "Provenance: Set Up Signing"


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
        set_up = window.createAction(SET_UP_ACTION_ID, SET_UP_TITLE, "tools/scripts")
        set_up.triggered.connect(_set_up_signing)


class _Relay(QtCore.QObject):
    """Carries the first run's progress and result from its thread to the GUI thread."""

    progressed = QtCore.pyqtSignal(int, int, str)  # bytes done, bytes in all, what's happening
    finished = QtCore.pyqtSignal(object)  # a firstrun.Report, or the exception that stopped it


_first_run: "tuple[_Relay, QtWidgets.QProgressDialog] | None" = None  # alive while it runs


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
    try:
        installed = installer.installed(_data_folder())
    except firstrun.FirstRunError:
        installed = None
    environment = collect_environment(krita_version=Krita.instance().version(), installed=installed)
    QtWidgets.QMessageBox.information(_parent(), "Provenance environment", environment.to_text())


def _data_folder() -> Path:
    """Decision 5 of the second set: a krita-provenance folder in Krita's app-data location."""
    location = QtCore.QStandardPaths.writableLocation(QtCore.QStandardPaths.AppDataLocation)
    return firstrun.data_folder(location)


def _set_up_signing() -> None:
    """I2 and K1: after the user agrees, fetch the pinned wheels, make the local keys and sign a
    test image (spike S1). Nothing is downloaded without asking; running it again only checks."""
    global _first_run
    if _first_run is not None:
        _first_run[1].show()  # already running
        return
    try:
        folder = _data_folder()
        wanted = installer.plan()
    except (firstrun.FirstRunError, installer.InstallError) as error:
        QtWidgets.QMessageBox.warning(_parent(), SET_UP_TITLE, str(error))
        return
    if not installer.is_current(folder, wanted):
        answer = QtWidgets.QMessageBox.question(
            _parent(),
            SET_UP_TITLE,
            firstrun.consent_text(wanted, folder),
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.Cancel,
            QtWidgets.QMessageBox.Cancel,
        )
        if answer != QtWidgets.QMessageBox.Yes:
            return

    dialog = QtWidgets.QProgressDialog("Starting…", "Cancel", 0, 1000, _parent())
    dialog.setWindowTitle(SET_UP_TITLE)
    dialog.setWindowModality(QtCore.Qt.WindowModal)
    dialog.setMinimumDuration(0)
    dialog.setAutoClose(False)
    dialog.setAutoReset(False)
    relay = _Relay()  # made here, in the GUI thread, so its signals are delivered here
    cancelled = threading.Event()

    def show_progress(done: int, total: int, label: str) -> None:
        if cancelled.is_set():
            return  # Qt re-shows a cancelled dialog on setValue; the worker stops at its next step
        dialog.setValue(1000 * done // total if total else 1000)
        dialog.setLabelText(label)

    def work() -> None:
        try:
            result: object = firstrun.run(
                folder, wanted, progress=relay.progressed.emit, cancelled=cancelled
            )
        except Exception as error:  # noqa: BLE001 - an escape would leave the dialog up forever
            result = error  # _first_run_finished shows it
        relay.finished.emit(result)

    relay.progressed.connect(show_progress)
    relay.finished.connect(_first_run_finished)
    dialog.canceled.connect(cancelled.set)
    _first_run = (relay, dialog)
    dialog.show()
    threading.Thread(target=work, name="krita-provenance first run", daemon=True).start()


def _first_run_finished(result: object) -> None:
    global _first_run
    if _first_run is not None:
        dialog = _first_run[1]
        _first_run = None
        dialog.hide()
        dialog.deleteLater()
    if isinstance(result, firstrun.Report):
        show = QtWidgets.QMessageBox.information if result.ready else QtWidgets.QMessageBox.warning
        show(_parent(), SET_UP_TITLE, result.to_text())
    elif isinstance(result, firstrun.Cancelled):
        QtWidgets.QMessageBox.information(_parent(), SET_UP_TITLE, str(result))
    elif isinstance(
        result, (installer.InstallError, keys.KeysError, firstrun.FirstRunError, sdk.SdkError)
    ):
        QtWidgets.QMessageBox.warning(_parent(), SET_UP_TITLE, str(result))
    else:
        QtWidgets.QMessageBox.warning(
            _parent(), SET_UP_TITLE, f"Setting up signing failed unexpectedly: {result!r}"
        )


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
