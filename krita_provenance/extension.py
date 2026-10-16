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

import tempfile
import threading
from pathlib import Path
from typing import Any

from krita import Document, Extension, InfoObject, Krita, Node, Window

from . import exporter, firstrun, installer, keys, log, mapping, sdk, storage
from .adapters import ai_diffusion
from .environment import collect_environment
from .log import ANNOTATION, CaptureGap, ProvenanceLog
from .qt import QtCore, QtWidgets

ENVIRONMENT_ACTION_ID = "krita_provenance_environment"
LOG_STATUS_ACTION_ID = "krita_provenance_log_status"
SET_UP_ACTION_ID = "krita_provenance_set_up_signing"
SET_UP_TITLE = "Provenance: Set Up Signing"
EXPORT_ACTION_ID = "krita_provenance_export_signed"
EXPORT_TITLE = "Provenance: Export Signed Image"  # neutral, no "Content Credentials" (spec)
CAPTURE_TITLE = "Provenance capture"
FLAT_CHECK_BYTES = 8 * 1024 * 1024  # pixel bytes read at a time when checking a canvas is blank
NOT_SET_UP = (
    "signing isn't set up yet; Tools ▸ Scripts ▸ Provenance: Set Up Signing installs the"
    " packages and makes the local signing certificate"
)
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
        export = window.createAction(EXPORT_ACTION_ID, f"{EXPORT_TITLE}…", "tools/scripts")
        export.triggered.connect(_export_signed)


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
        ensure_log(document, opening=True)
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


def _export_signed() -> None:
    """E1, E2, E4 to E6: render the document, sign it with a manifest rebuilt from its log, and
    log the export. When it can't be signed, the user is told why before choosing where to save,
    and the file is saved as -unsigned (exporter.py)."""
    document = Krita.instance().activeDocument()
    if document is None:
        QtWidgets.QMessageBox.information(_parent(), EXPORT_TITLE, "Open a document first.")
        return
    try:
        entry, _ = ensure_log(document)
    except storage.StorageError as error:
        QtWidgets.QMessageBox.warning(_parent(), EXPORT_TITLE, str(error))
        return

    signer, unsigned_reason = _signer()
    if unsigned_reason is None:
        unsigned_reason = _why_unsignable(entry)
    if unsigned_reason is not None and not _confirm_unsigned(unsigned_reason):
        return
    parent = storage.parent(entry)
    if (
        unsigned_reason is None
        and parent is not None
        and storage.is_document_file(parent.title)
        and storage.is_document_file(document.fileName())
        and document.modified()
    ):
        choice = _ask_save_first()  # decision 3 of 13 Oct 2026, second set: never save silently
        if choice is None:
            return
        if choice == "unsigned":
            unsigned_reason = "you chose to export it unsigned rather than save first"
        elif not document.save():
            QtWidgets.QMessageBox.warning(
                _parent(), EXPORT_TITLE, "Krita couldn't save the document; nothing was exported."
            )
            return

    destination = _ask_destination(document)
    if destination is None:
        return
    try:
        format_ = exporter.format_for(destination)
    except exporter.ExportError as error:
        QtWidgets.QMessageBox.warning(_parent(), EXPORT_TITLE, str(error))
        return
    parent_data = _parent_data(document, entry) if unsigned_reason is None else None
    try:
        with tempfile.TemporaryDirectory(
            prefix="krita-provenance-export-", ignore_cleanup_errors=True
        ) as temporary:
            rendered = Path(temporary) / f"render{destination.suffix.lower()}"
            if not _render(document, rendered):
                QtWidgets.QMessageBox.warning(
                    _parent(),
                    EXPORT_TITLE,
                    "Krita couldn't render the document; nothing was saved.",
                )
                return
            details = mapping.Export(
                title=destination.name,
                format=format_,
                at=log.now(),
                krita_version=Krita.instance().version(),
                layers=_layer_states(document),
            )
            outcome = exporter.export(
                entry,
                rendered,
                destination,
                details,
                signer,
                parent_data,
                unsigned_reason=unsigned_reason,
            )
    except OSError as error:
        QtWidgets.QMessageBox.warning(_parent(), EXPORT_TITLE, f"The export failed: {error}")
        return
    text = outcome.to_text(entry)
    try:
        append_event(document, outcome.event)  # marks the document modified (decision 3, 16 Oct)
    except storage.StorageError as error:
        text += f"\n\nThe export isn't recorded in the document's log: {error}"
    show = QtWidgets.QMessageBox.information if outcome.signed else QtWidgets.QMessageBox.warning
    show(_parent(), EXPORT_TITLE, text)


def _signer() -> tuple[sdk.SignerConfig | None, str | None]:
    """The local signing key, activating the plugin's packages first; or why there is none."""
    try:
        folder = _data_folder()
        files = keys.KeyFiles(folder / firstrun.KEYS)
        if installer.activate(folder) is None or not files.exists():
            return None, NOT_SET_UP
        return files.signer_config(), None
    except (firstrun.FirstRunError, keys.KeysError, OSError) as error:
        return None, f"the signing key couldn't be read ({error})"


def _why_unsignable(entry: ProvenanceLog) -> str | None:
    """A log the mapping refuses, such as a document that started with content and hasn't been
    reopened from its saved .kra, is reported before the user picks a file."""
    probe = mapping.Export(title="probe.png", format="png", at=log.now())
    try:
        mapping.manifest(entry, probe)
    except mapping.MappingError as error:
        return str(error)
    return None


def _confirm_unsigned(reason: str) -> bool:
    answer = QtWidgets.QMessageBox.question(
        _parent(),
        EXPORT_TITLE,
        f"This export can't be signed: {reason}\n\nSave an unsigned copy instead? Its name will"
        " end in -unsigned.",
        QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.Cancel,
        QtWidgets.QMessageBox.Cancel,
    )
    return answer == QtWidgets.QMessageBox.Yes


def _ask_save_first() -> str | None:
    """ "save", "unsigned", or None to cancel."""
    box = QtWidgets.QMessageBox(_parent())
    box.setWindowTitle(EXPORT_TITLE)
    box.setIcon(QtWidgets.QMessageBox.Question)
    box.setText(
        "This document has unsaved changes. A signed export names its saved .kra as the file it"
        " was opened from, so the .kra has to be saved first. Krita won't save it unless you"
        " choose to."
    )
    save = box.addButton("Save and export signed", QtWidgets.QMessageBox.AcceptRole)
    unsigned = box.addButton("Export unsigned", QtWidgets.QMessageBox.ActionRole)
    box.addButton(QtWidgets.QMessageBox.Cancel)
    box.exec()
    clicked = box.clickedButton()
    if clicked is save:
        return "save"
    if clicked is unsigned:
        return "unsigned"
    return None


def _ask_destination(document: Document) -> Path | None:
    """Where to save, as PNG or JPEG. The default sits beside the document and never is the
    document's own file, which may be the image it was opened from."""
    current = document.fileName()
    if current:
        default = Path(current).with_suffix(".png")
        if default == Path(current):
            default = default.with_name(f"{default.stem} export.png")
    else:
        pictures = QtCore.QStandardPaths.writableLocation(QtCore.QStandardPaths.PicturesLocation)
        default = Path(pictures) / "untitled.png"
    chosen, _ = QtWidgets.QFileDialog.getSaveFileName(
        _parent(), EXPORT_TITLE, str(default), "PNG image (*.png);;JPEG image (*.jpg *.jpeg)"
    )
    return Path(chosen) if chosen else None


def _parent_data(document: Document, entry: ProvenanceLog) -> bytes | None:
    """The bytes of the file the document came from, for its parentOf ingredient: the copy kept
    when an image was opened (decision 1 of 16 Oct 2026), else the document's own file when it
    is that file. A .kra or .ora counts even if renamed: c2pa-rs records its title and format,
    not its bytes (CLAUDE.md, "Facts already checked")."""
    parent = storage.parent(entry)
    if parent is None:
        return None
    if parent.store and parent.store in document.annotationTypes():
        return bytes(document.annotation(parent.store))
    current = document.fileName()
    if not current:
        return None
    file = Path(current)
    both_documents = storage.is_document_file(parent.title) and storage.is_document_file(file.name)
    if not both_documents and file.name != parent.title:
        return None
    try:
        if file.stat().st_size > exporter.MAX_PARENT_BYTES:
            return None
        return file.read_bytes()
    except OSError:
        return None


def _render(document: Document, path: Path) -> bool:
    """E1: Krita renders the document to a temporary PNG or JPEG, without option dialogs."""
    was_batch = document.batchmode()
    document.setBatchmode(True)
    try:
        # libkis takes InfoObject(parent=None); the vendored stub gives it a Window's signature.
        done = document.exportImage(str(path), InfoObject())  # pyright: ignore[reportCallIssue]
    finally:
        document.setBatchmode(was_batch)
    return bool(done) and path.is_file() and path.stat().st_size > 0


def _layer_states(document: Document) -> dict[str, mapping.LayerState]:
    """Each node's visibility at export, counting hidden groups: the mapping says when the layer
    that received a generation is gone or hidden (honesty rule 3)."""
    states: dict[str, mapping.LayerState] = {}
    root = document.rootNode()
    pending = [(child, True) for child in root.childNodes()] if root is not None else []
    while pending:
        node, parent_visible = pending.pop()
        visible = parent_visible and node.visible()
        states[_node_id(node)] = mapping.LayerState(visible)
        pending.extend((child, visible) for child in node.childNodes())
    return states


def ensure_log(document: Document, *, opening: bool = False) -> tuple[ProvenanceLog, bool]:
    """The document's log, and True if it had none and one was started and written just now.

    A new log starts from the file name (storage.start). An untitled document's canvas is first
    checked for content (decision 2 of 16 Oct 2026), and an opened image's bytes are kept in
    their own annotation for signing (decision 1). ``opening`` says Krita has just created or
    opened the document: then the log also gets what it must say as the plugin begins
    observing, such as this session's capture gap, or the saved .kra of a document that started
    with content (storage.observation).

    None of that is an edit, but Krita marks a document modified whenever an annotation is set
    (KisImage::addAnnotation). A document that had no unsaved changes is marked unmodified again,
    so Krita doesn't ask to save a file the user only opened; any later change marks it modified,
    and that save includes the log. Events appended later (append_event) keep Krita's flag.
    """
    path = document.fileName() or None
    entry = read_log(document)
    created = entry is None
    copy: bytes | None = None
    if entry is None:
        if storage.keeps_parent_copy(path):
            copy = _read_parent_file(path)
        entry = storage.start(
            path,
            blank=path is not None or _canvas_is_blank(document),
            parent_copy=storage.PARENT_COPY if copy is not None else None,
        )
    observed: list[log.Event] = []
    if opening:
        observed = _observation_gaps() + ([] if created else storage.observation(entry, path))
    if not created and not observed:
        return entry, False
    for event in observed:
        entry.append(event)
    was_modified = document.modified()
    if copy is not None:
        document.setAnnotation(storage.PARENT_COPY, storage.PARENT_COPY_DESCRIPTION, copy)
    write_log(document, entry)
    if not was_modified:
        document.setModified(False)  # pyright: ignore[reportAttributeAccessIssue] (not in the stubs)
    return entry, created


def _read_parent_file(path: str | None) -> bytes | None:
    """The opened image's bytes, to keep in the document (decision 1 of 16 Oct 2026); None when
    it can't be read or is over storage.MAX_PARENT_COPY, and then signing needs the file."""
    if not path:
        return None
    try:
        file = Path(path)
        if file.stat().st_size > storage.MAX_PARENT_COPY:
            return None
        return file.read_bytes()
    except OSError:
        return None


def _canvas_is_blank(document: Document) -> bool:
    """Decision 2 of 16 Oct 2026: an untitled document is a blank canvas only if every layer is
    a paint layer that is empty or one flat colour. Pasted content, a template's layers or an
    image opened as untitled aren't; anything else, a vector layer or a mask, counts as content."""
    root = document.rootNode()
    pending = list(root.childNodes()) if root is not None else []
    while pending:
        node = pending.pop()
        kind = node.type()
        if kind == "":
            continue  # Krita's own helper nodes, such as the decorations wrapper, have no type
        if kind == "grouplayer":
            pending.extend(node.childNodes())
        elif kind != "paintlayer" or node.childNodes() or not _is_flat(node):
            return False
    return True


def _is_flat(node: Node) -> bool:
    """Whether every pixel of a paint layer is the same, read a strip at a time."""
    rect = node.bounds()
    if rect.isEmpty():
        return True
    x, y, width, height = rect.x(), rect.y(), rect.width(), rect.height()
    first = bytes(node.pixelData(x, y, 1, 1))
    if not first:
        return True
    rows = max(1, FLAT_CHECK_BYTES // (width * len(first)))
    for top in range(y, y + height, rows):
        strip = bytes(node.pixelData(x, top, width, min(rows, y + height - top)))
        if strip != first * (len(strip) // len(first)):
            return False
    return True


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
