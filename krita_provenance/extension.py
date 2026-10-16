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

Capture (C1 to C3): once every plugin has loaded, the Krita AI Diffusion adapter follows its
document models. Krita AI Diffusion adds the result's layer before it emits result_used, so C2's
"before" is a snapshot of the active document's node ids, taken at each mouse press and key
press. An apply only ever follows user input, and Krita handles no user input during it. A timer
can't be used: libkis waits on the image while inserting the layer, and Krita's busy-wait runs
QApplication::processEvents meanwhile, so a timer fires in mid-apply (smoke rows 18 and 19,
16 Oct 2026). The one paint layer that appeared since the snapshot is the target.
"""

import threading
from pathlib import Path
from typing import Any

from krita import Document, Extension, Krita, Node, Window

from . import firstrun, installer, keys, log, sdk, storage
from .adapters import ai_diffusion
from .environment import collect_environment
from .log import ANNOTATION, CaptureGap, ProvenanceLog
from .qt import QtCore, QtWidgets

ENVIRONMENT_ACTION_ID = "krita_provenance_environment"
LOG_STATUS_ACTION_ID = "krita_provenance_log_status"
SET_UP_ACTION_ID = "krita_provenance_set_up_signing"
SET_UP_TITLE = "Provenance: Set Up Signing"
CAPTURE_TITLE = "Provenance capture"
_INPUT_EVENTS = frozenset({
    QtCore.QEvent.MouseButtonPress,
    QtCore.QEvent.MouseButtonDblClick,
    QtCore.QEvent.KeyPress,
})


class ProvenanceExtension(Extension):
    def setup(self) -> None:
        notifier = Krita.instance().notifier()
        notifier.setActive(True)
        # The vendored stubs declare Notifier's signals as plain methods.
        notifier.imageCreated.connect(_start_log)  # pyright: ignore[reportAttributeAccessIssue]
        QtCore.QTimer.singleShot(0, _attach_adapter)  # after every plugin has loaded

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


class _InputWatcher(QtCore.QObject):
    """Takes C2's snapshot as user input arrives, before Krita delivers it. Krita's busy-wait
    excludes user input, but the dialog it shows after a second of waiting doesn't, so nothing
    is taken while a modal dialog is up."""

    def __init__(self) -> None:
        super().__init__()
        self._last: object = None  # (event type, timestamp) of the last input that took one

    def eventFilter(self, a0: QtCore.QObject | None, a1: QtCore.QEvent | None) -> bool:
        if a1 is None or a1.type() not in _INPUT_EVENTS:
            return False
        timestamp = getattr(a1, "timestamp", None)  # QInputEvent's, in milliseconds
        stamp = (a1.type(), timestamp()) if callable(timestamp) else None
        if (stamp is None or stamp != self._last) and (
            QtWidgets.QApplication.activeModalWidget() is None
        ):
            self._last = stamp  # a press reaches each widget it propagates to; one snapshot
            _take_snapshot()
        return False  # never consumes the event


_first_run: "tuple[_Relay, QtWidgets.QProgressDialog] | None" = None  # alive while it runs
_snapshot: "tuple[str, frozenset[str]] | None" = None  # (active document's root id, node ids)
_input_watcher: _InputWatcher | None = None
_warned: set[str] = set()  # capture warnings already shown this session


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
        ensure_log(document, _observation_gaps())
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
    environment = collect_environment(
        krita_version=Krita.instance().version(),
        installed=installed,
        capture=_adapter.status.to_text(),
    )
    QtWidgets.QMessageBox.information(_parent(), "Provenance environment", environment.to_text())


def _attach_adapter() -> None:
    """C1: follow Krita AI Diffusion if Krita loaded it. When it runs but can't be followed, say
    so, and log the gap in every document observed in this session (C3)."""
    global _input_watcher
    status = _adapter.attach()
    if status.state == ai_diffusion.ATTACHED:
        app = QtWidgets.QApplication.instance()
        if app is not None:
            _input_watcher = _InputWatcher()
            app.installEventFilter(_input_watcher)
        _take_snapshot()
    elif status.state == ai_diffusion.FAILED:
        for document in Krita.instance().documents():
            _start_log(document)  # documents opened before this ran get the gap too
        QtWidgets.QMessageBox.warning(_parent(), CAPTURE_TITLE, status.to_text())


def _observation_gaps() -> list[log.Event]:
    """What the log must say when the plugin begins observing a document in this session."""
    if _adapter.status.state != ai_diffusion.FAILED:
        return []
    at = log.now()
    return [CaptureGap(at, at, log.AI_DIFFUSION_UNOBSERVED)]


def _take_snapshot() -> None:
    """C2's "before": the active document's node ids, taken as user input arrives and after
    each recorded apply. It runs inside an event filter, so it never raises: a failure leaves no
    snapshot, and the next apply is logged with its layer unidentified, which the manifest
    discloses."""
    global _snapshot
    try:
        document = Krita.instance().activeDocument()
        root = document.rootNode() if document is not None else None
        _snapshot = None if root is None else (_node_id(root), _node_ids(root))
    except Exception:  # noqa: BLE001 - an event filter that raised would break input handling
        _snapshot = None


def _record_generation(model: Any, job_id: str, index: int) -> None:
    """C1 to C3: an applied result goes into its document's log at once, or a gap if it can't be
    described. Runs inside Krita AI Diffusion's apply, so warnings wait for the event loop."""
    at = log.now()
    try:
        document = _document_for(model)
    except Exception as error:  # noqa: BLE001 - its internals changed; nothing to write into
        document, problem = None, f"{type(error).__name__}: {error}"
    else:
        problem = "the document wasn't found among Krita's open documents"
    if document is None:
        _warn_capture(f"A Krita AI Diffusion result was applied but not recorded: {problem}.")
        return
    try:
        root = document.rootNode()
        before = _snapshot[1] if _snapshot is not None and _snapshot[0] == _node_id(root) else None
        target = None if before is None else ai_diffusion.target_layer(before, _nodes(root))
        event: log.Event = _adapter.generation(model, job_id, index, target, at)
    except Exception as error:  # noqa: BLE001 - any failure to describe it is a capture gap (C3)
        event = CaptureGap(at, at, log.ADAPTER_ERROR)
        _warn_capture(
            f"A Krita AI Diffusion result was applied but couldn't be described"
            f" ({type(error).__name__}: {error}); the log records a capture gap instead."
        )
    try:
        append_event(document, event)
    except storage.StorageError as error:
        _warn_capture(f"{error}\n\nThe applied Krita AI Diffusion result is not recorded.")
    _take_snapshot()


_adapter = ai_diffusion.Adapter(_record_generation)


def _document_for(model: Any) -> Document | None:
    """The Krita document a Krita AI Diffusion model belongs to: the active one when it applies."""
    app = Krita.instance()
    if ai_diffusion.document_is_active(model):
        return app.activeDocument()
    wanted = ai_diffusion.document_id(model)
    key = ai_diffusion.DOCUMENT_ID_ANNOTATION
    for document in app.documents():
        if (
            key in document.annotationTypes()
            and bytes(document.annotation(key)).decode("utf-8", "replace") == wanted
        ):
            return document
    return None


def _nodes(root: Node) -> dict[str, tuple[str, str]]:
    """Every node under the root: unique id (braces included) -> (name, Node.type())."""
    found: dict[str, tuple[str, str]] = {}
    pending = list(root.childNodes())
    while pending:
        node = pending.pop()
        found[_node_id(node)] = (node.name(), node.type())
        pending.extend(node.childNodes())
    return found


def _node_ids(root: Node) -> frozenset[str]:
    """The unique ids of every node under the root; cheaper than _nodes, for every input event."""
    found: set[str] = set()
    pending = list(root.childNodes())
    while pending:
        node = pending.pop()
        found.add(_node_id(node))
        pending.extend(node.childNodes())
    return frozenset(found)


def _node_id(node: Node) -> str:
    return node.uniqueId().toString()


def _warn_capture(message: str) -> None:
    """Once per message and session, after the current call: a dialog inside Krita AI Diffusion's
    apply would run a nested event loop in the middle of it."""
    if message in _warned:
        return
    _warned.add(message)
    QtCore.QTimer.singleShot(
        0, lambda: QtWidgets.QMessageBox.warning(_parent(), CAPTURE_TITLE, message)
    )


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
    text += f"\n\n{_adapter.status.to_text()}"
    QtWidgets.QMessageBox.information(_parent(), "Provenance log", text)


def ensure_log(
    document: Document, observation: list[log.Event] | None = None
) -> tuple[ProvenanceLog, bool]:
    """The document's log, and True if it had none and one was started and written just now.

    ``observation`` is what the log must say as the plugin begins observing the document, such
    as a capture gap for this session; it is appended and written with the start. Neither is an
    edit, but Krita marks a document modified whenever an annotation is set
    (KisImage::addAnnotation). A document that had no unsaved changes is marked unmodified again,
    so Krita doesn't ask to save a file the user only opened; any later change marks it modified,
    and that save includes the log. Events appended later (append_event) keep Krita's flag.
    """
    entry = read_log(document)
    created = entry is None
    if entry is None:
        entry = storage.start(document.fileName() or None)
    elif not observation:
        return entry, False
    for event in observation or []:
        entry.append(event)
    was_modified = document.modified()
    write_log(document, entry)
    if not was_modified:
        document.setModified(False)  # pyright: ignore[reportAttributeAccessIssue] (not in the stubs)
    return entry, created


def append_event(document: Document, event: log.Event) -> None:
    """Append one event and write the log back at once (storage.py says why not later). Krita
    marks the document modified, so closing it asks to save, and the save keeps the event."""
    data = storage.append(_annotation(document), document.fileName() or None, event)
    document.setAnnotation(ANNOTATION, storage.DESCRIPTION, data)


def read_log(document: Document):
    return storage.read(_annotation(document))


def _annotation(document: Document) -> bytes | None:
    if ANNOTATION not in document.annotationTypes():
        return None
    return bytes(document.annotation(ANNOTATION))


def write_log(document: Document, entry) -> None:
    """The document must have an image: Krita 5.3's setAnnotation uses it without a check. The
    write marks the document modified."""
    document.setAnnotation(ANNOTATION, storage.DESCRIPTION, storage.serialize(entry))


def register() -> None:
    app = Krita.instance()
    app.addExtension(ProvenanceExtension(app))
