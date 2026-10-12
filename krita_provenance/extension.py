"""Krita-facing glue: the Extension that Krita loads at startup.

Only Krita-facing modules may import `krita`; everything else must run without it
(tests/test_architecture.py). Keep this module thin: it runs only inside Krita, where unit tests
can't reach it.
"""

from krita import Extension, Krita, Window

from .environment import collect_environment
from .qt import QtWidgets

ENVIRONMENT_ACTION_ID = "krita_provenance_environment"


class ProvenanceExtension(Extension):
    def setup(self) -> None:
        pass

    def createActions(self, window: Window) -> None:
        action = window.createAction(
            ENVIRONMENT_ACTION_ID, "Provenance: Check Environment", "tools/scripts"
        )
        action.triggered.connect(lambda: _show_environment(window))


def _show_environment(window: Window) -> None:
    environment = collect_environment(krita_version=Krita.instance().version())
    QtWidgets.QMessageBox.information(
        window.qwindow(), "Provenance environment", environment.to_text()
    )


def register() -> None:
    app = Krita.instance()
    app.addExtension(ProvenanceExtension(app))
