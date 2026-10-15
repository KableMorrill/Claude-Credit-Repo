"""C1 to C3: the Krita AI Diffusion adapter, with stand-ins shaped like its 1.53.0 objects.

The real ones need Krita; the shapes follow ai_diffusion/model/root.py, model/jobs.py and
image.py at commit 0217cd2 (CLAUDE.md, "Facts already checked"). In Krita, docs/smoke-checklist.md
checks the rest.
"""

from __future__ import annotations

import gc
import weakref
from datetime import datetime, timezone
from enum import Enum
from types import SimpleNamespace
from typing import Any, NamedTuple

import pytest

from krita_provenance import log
from krita_provenance.adapters import ai_diffusion
from krita_provenance.adapters.ai_diffusion import Adapter, CaptureError
from krita_provenance.log import GenerationApplied, Layer

T0 = datetime(2026, 11, 2, 18, 20, 45, tzinfo=timezone.utc)


class Signal:
    def __init__(self) -> None:
        self.slots: list[Any] = []

    def connect(self, slot: Any) -> None:
        self.slots.append(slot)

    def emit(self, *args: Any) -> None:
        for slot in list(self.slots):
            slot(*args)


class Item(NamedTuple):  # JobQueue.Item
    job: str
    image: int


class Bounds(NamedTuple):  # ai_diffusion.image.Bounds
    x: int
    y: int
    width: int
    height: int


class Extent(NamedTuple):
    width: int
    height: int


class JobKind(Enum):
    diffusion = 0
    animation = 6


class WorkflowKind(Enum):
    generate = 0
    inpaint = 1


class ServerMode(Enum):
    managed = 0
    cloud = 2


def make_job(job_id: str = "job-1", **metadata: Any) -> SimpleNamespace:
    params = SimpleNamespace(
        bounds=Bounds(512, 128, 768, 640),
        name="a harbour at dusk",
        metadata={
            "prompt": "a harbour at dusk",
            "negative_prompt": "blurry",
            "checkpoint": "dreamshaper_8.safetensors",
            "style": "cinematic.json",
            "sampler": "DPM++ 2M",
            "steps": 24,
            "guidance": 4.5,
            "strength": 0.65,
            **metadata,
        },
        seed=1234567,
        has_mask=True,
        workflow_kind=WorkflowKind.inpaint,
    )
    results = [SimpleNamespace(extent=Extent(768, 640)), SimpleNamespace(extent=Extent(512, 512))]
    return SimpleNamespace(id=job_id, kind=JobKind.diffusion, params=params, results=results)


class Jobs:
    def __init__(self, *jobs: SimpleNamespace) -> None:
        self.jobs = {job.id: job for job in jobs}
        self.result_used = Signal()

    def find(self, job_id: str) -> SimpleNamespace | None:
        return self.jobs.get(job_id)


class DocumentModel:
    def __init__(self, *jobs: SimpleNamespace, active: bool = True, kad_id: str = "doc-1"):
        self.jobs = Jobs(*jobs)
        self.document = SimpleNamespace(is_active=active, id=kad_id)


class Root:
    def __init__(self, *models: DocumentModel) -> None:
        self.models = list(models)
        self.model_created = Signal()


def modules_with(version: str = "1.53.0") -> dict[str, Any]:
    return {"ai_diffusion": SimpleNamespace(__version__=version)}


def importer(root: Root, server_mode: Any = ServerMode.managed):
    def import_module(name: str) -> Any:
        if name == "ai_diffusion.model.root":
            return SimpleNamespace(root=root)
        if name == "ai_diffusion.settings":
            return SimpleNamespace(settings=SimpleNamespace(server_mode=server_mode))
        raise ImportError(name)

    return import_module


def never_import(name: str) -> Any:
    raise AssertionError(f"imported {name}, which would load a disabled plugin")


def test_without_krita_ai_diffusion_loaded_nothing_is_imported_or_followed():
    adapter = Adapter(lambda *args: None)
    status = adapter.attach(modules={}, import_module=never_import)
    assert status.state == ai_diffusion.NOT_LOADED and adapter.status is status
    assert "not loaded" in status.to_text()


def test_attach_follows_existing_and_new_models_and_reports_each_applied_result():
    seen: list[tuple[Any, str, int]] = []
    first = DocumentModel(make_job())
    root = Root(first)
    adapter = Adapter(lambda *args: seen.append(args))
    status = adapter.attach(modules_with(), importer(root))
    assert status.state == ai_diffusion.ATTACHED and status.version == "1.53.0"
    assert status.to_text() == "Generation capture: observing Krita AI Diffusion 1.53.0."

    first.jobs.result_used.emit(Item("job-1", 0))
    second = DocumentModel(make_job("job-2"))
    root.model_created.emit(second)
    root.model_created.emit(second)  # followed once, however often it is announced
    second.jobs.result_used.emit(Item("job-2", 1))
    assert seen == [(first, "job-1", 0), (second, "job-2", 1)]


def test_the_adapter_does_not_keep_a_closed_documents_model_alive():
    model = DocumentModel(make_job())
    root = Root(model)
    Adapter(lambda *args: None).attach(modules_with(), importer(root))
    reference = weakref.ref(model)
    root.models.clear()
    del model
    gc.collect()
    assert reference() is None


def test_a_krita_ai_diffusion_it_cannot_follow_is_reported_not_raised():
    def broken(name: str) -> Any:
        raise AttributeError("module 'ai_diffusion.model.root' has no attribute 'root'")

    adapter = Adapter(lambda *args: None)
    status = adapter.attach(modules_with("1.60.0"), broken)
    assert status.state == ai_diffusion.FAILED and status.version == "1.60.0"
    assert "AttributeError" in status.detail
    text = status.to_text()
    assert "1.60.0 is running, but this plugin cannot observe it" in text
    assert "not recorded" in text


def test_an_untested_version_is_followed_with_a_warning():
    status = Adapter(lambda *args: None).attach(modules_with("1.54.0"), importer(Root()))
    assert status.state == ai_diffusion.ATTACHED
    assert "untested" in status.to_text() and "capture gap" in status.to_text()


def test_generation_copies_the_jobs_params_into_the_log_event():
    model = DocumentModel(make_job())
    adapter = Adapter(lambda *args: None)
    adapter.attach(modules_with(), importer(Root(model), ServerMode.cloud))
    target = Layer("{6a5d9c40-1111-4222-8333-944455556666}", "[Generated] a harbour (1234567)")
    event = adapter.generation(model, "job-1", 0, target, T0)

    assert event == GenerationApplied(
        at=T0,
        source=log.Source(log.AI_DIFFUSION, "1.53.0", "cloud"),
        job=log.Job("job-1", "diffusion", "inpaint", 0, (512, 128, 768, 640), True, 1234567),
        model=log.Model("dreamshaper_8.safetensors", "cinematic.json", "DPM++ 2M", 24, 4.5, 0.65),
        prompt=log.Prompt("a harbour at dusk", "blurry"),
        target_layer=target,
    )
    assert log.event_from_dict(log.event_to_dict(event)) == event  # it survives the .kra


def test_the_applied_results_own_size_sets_the_bounds():
    event = ai_diffusion.generation_applied(make_job(), 1, "1.53.0", "managed", None, T0)
    assert event.job.bounds == (512, 128, 512, 512) and event.job.result_index == 1


def test_metadata_of_an_unexpected_type_is_left_out_not_guessed():
    job = make_job(steps="24", guidance=True, strength=None, checkpoint=7, prompt=None)
    event = ai_diffusion.generation_applied(job, 0, None, "unknown", None, T0)
    assert event.model == log.Model(None, "cinematic.json", "DPM++ 2M", None, None, None)
    assert event.prompt.text == "" and event.source.version == "unknown"


def test_a_job_already_pruned_from_history_cannot_be_described():
    model = DocumentModel()
    adapter = Adapter(lambda *args: None)
    adapter.attach(modules_with(), importer(Root(model)))
    with pytest.raises(CaptureError, match="no longer in Krita AI Diffusion's history"):
        adapter.generation(model, "gone", 0, None, T0)


def test_the_backend_is_named_as_krita_ai_diffusion_names_it():
    def without_settings(name: str) -> Any:
        if name == "ai_diffusion.model.root":
            return SimpleNamespace(root=Root(DocumentModel(make_job())))
        raise ImportError(name)

    adapter = Adapter(lambda *args: None)
    adapter.attach(modules_with(), without_settings)
    model = DocumentModel(make_job())
    assert adapter.generation(model, "job-1", 0, None, T0).source.backend == "unknown"


@pytest.mark.parametrize(
    ("after", "expected"),
    [
        ({"{a}": ("Sky", "paintlayer"), "{new}": ("[Generated] x (1)", "paintlayer")}, "{new}"),
        ({"{a}": ("Sky", "paintlayer")}, None),  # nothing appeared
        (
            {"{new}": ("one", "paintlayer"), "{new2}": ("two", "paintlayer")},
            None,  # several regions: which holds what isn't known
        ),
        (
            {"{g}": ("Sky Group", "grouplayer"), "{new}": ("[Generated] x", "paintlayer")},
            "{new}",  # a region apply that also made a group
        ),
    ],
)
def test_the_target_is_the_one_paint_layer_that_appeared(after, expected):
    target = ai_diffusion.target_layer({"{a}"}, after)
    assert (target.uuid if target else None) == expected
    if target is not None:
        assert target.name == after[expected][0]


def test_document_helpers_read_krita_ai_diffusions_document():
    model = DocumentModel(active=False, kad_id="3f2a")
    assert not ai_diffusion.document_is_active(model)
    assert ai_diffusion.document_id(model) == "3f2a"
