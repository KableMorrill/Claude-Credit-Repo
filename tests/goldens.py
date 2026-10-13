"""The golden cases under tests/fixtures/goldens, shared by the mapping and signing tests.

Each case directory holds a log, what the exporter knew (export.json, with the identity to
scrub) and the expected manifest. UPDATE_GOLDENS=1 rewrites the expected files after a
deliberate mapping change, for review in the diff.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from krita_provenance import log
from krita_provenance.log import ProvenanceLog
from krita_provenance.mapping import Export, LayerState
from krita_provenance.scrub import Identity

GOLDENS = Path(__file__).parent / "fixtures" / "goldens"
CASES = sorted(path.name for path in GOLDENS.iterdir() if (path / "log.json").is_file())


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_case(name: str) -> tuple[ProvenanceLog, Export, Identity]:
    folder = GOLDENS / name
    entry = ProvenanceLog.from_json((folder / "log.json").read_text(encoding="utf-8"))
    data = read_json(folder / "export.json")
    layers = data["layers"]
    export = Export(
        title=data["title"],
        format=data["format"],
        at=log.parse_time(data["at"]),
        krita_version=data["krita_version"],
        layers=None if layers is None else {k: LayerState(**v) for k, v in layers.items()},
    )
    who = data["identity"]
    identity = Identity(tuple(who["user_names"]), who["home"], tuple(who["host_names"]))
    return entry, export, identity
