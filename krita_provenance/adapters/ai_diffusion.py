"""Krita AI Diffusion adapter (C1 to C3): the only module that touches ``ai_diffusion``.

Phase A of the spec: read Krita AI Diffusion's internals and change nothing. What this relies
on, read from 1.53.0 (commit 0217cd2; CLAUDE.md, "Facts already checked"):

- ``ai_diffusion.model.root.root`` owns one DocumentModel per document. ``Root.model_created``
  fires for each new one and ``Root.models`` lists those that exist.
- ``DocumentModel.jobs.result_used`` fires with ``Item(job, image)`` after
  ``apply_generated_result`` has applied a result; ``jobs.find(job)`` returns the Job, whose
  ``params`` hold bounds, seed, has_mask, workflow_kind and the metadata keys prompt,
  negative_prompt, strength, style, checkpoint, sampler, steps and guidance.
- Only ``apply_generated_result`` emits it. Live painting, upscaling and control-layer results
  reach the document without it, so this adapter doesn't see them (C7, later).
- Importing ``ai_diffusion`` registers its extension, so a plugin the user disabled would load.
  This module therefore uses it only when Krita has already imported it.

Krita-free: the glue (extension.py) finds the document, snapshots node ids around the apply
and writes the log. This module follows Krita AI Diffusion's models and turns what they hold
into log events, so its logic is tested without Krita (tests/test_ai_diffusion_adapter.py).
"""

from __future__ import annotations

import importlib
import sys
import weakref
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .. import log
from ..log import GenerationApplied, Job, Layer, Model, Prompt, Source

PACKAGE = "ai_diffusion"
TESTED_VERSIONS = ("1.53.0",)
DOCUMENT_ID_ANNOTATION = "ai_diffusion/document_id"  # its own id for a document it has seen
PAINT_LAYER = "paintlayer"  # Krita's Node.type() for the layers results are applied to

# Status.state
NOT_LOADED = "not_loaded"
ATTACHED = "attached"
FAILED = "failed"

OnResult = Callable[[Any, str, int], None]  # model, job id, result index


class CaptureError(Exception):
    """A generation was applied but could not be described; the glue logs a capture gap."""


@dataclass(frozen=True)
class Status:
    state: str  # NOT_LOADED, ATTACHED or FAILED
    version: str | None = None
    detail: str = ""

    def to_text(self) -> str:
        if self.state == ATTACHED:
            text = f"Generation capture: observing Krita AI Diffusion {self.version}."
            if self.version not in TESTED_VERSIONS:
                text += (
                    f" This version is untested (tested: {', '.join(TESTED_VERSIONS)}); capture"
                    " continues and any failure is logged as a capture gap."
                )
            return text
        if self.state == FAILED:
            name = f"Krita AI Diffusion {self.version}" if self.version else "Krita AI Diffusion"
            return (
                f"Generation capture: {name} is running, but this plugin cannot observe it"
                f" ({self.detail}). Its generations are not recorded, and each document's log"
                " says so."
            )
        return (
            "Generation capture: Krita AI Diffusion is not loaded, so there is nothing to observe."
        )


class Adapter:
    """Follows every Krita AI Diffusion document model and reports each applied result."""

    def __init__(self, on_result: OnResult) -> None:
        self._on_result = on_result
        self._followed: weakref.WeakSet[Any] = weakref.WeakSet()
        self._settings: Any = None
        self.status = Status(NOT_LOADED)

    def attach(
        self,
        modules: Mapping[str, Any] = sys.modules,
        import_module: Callable[[str], Any] = importlib.import_module,
    ) -> Status:
        """Start following, if Krita has loaded Krita AI Diffusion. Call once all plugins have
        loaded; it never imports the package itself."""
        package = modules.get(PACKAGE)
        if package is None:
            self.status = Status(NOT_LOADED)
            return self.status
        version = getattr(package, "__version__", None)
        try:
            root = import_module(f"{PACKAGE}.model.root").root
            root.model_created.connect(self._follow)
            for model in root.models:
                self._follow(model)
        except Exception as error:  # noqa: BLE001 - its internals changed: report, don't crash
            self.status = Status(FAILED, version, f"{type(error).__name__}: {error}")
            return self.status
        try:
            self._settings = import_module(f"{PACKAGE}.settings").settings
        except Exception:  # noqa: BLE001 - only the backend name is lost
            self._settings = None
        self.status = Status(ATTACHED, version)
        return self.status

    def generation(
        self, model: Any, job_id: str, index: int, target: Layer | None, at: datetime
    ) -> GenerationApplied:
        """C1: the applied job's params, copied now: Krita AI Diffusion prunes its history."""
        job = model.jobs.find(job_id)
        if job is None:
            raise CaptureError(f"job {job_id} is no longer in Krita AI Diffusion's history")
        return generation_applied(job, index, self.status.version, self._backend(), target, at)

    def _follow(self, model: Any) -> None:
        if model in self._followed:
            return
        reference = weakref.ref(model)  # a closed document's model must be free to go

        def result_used(item: Any) -> None:
            followed = reference()
            if followed is not None:
                self._on_result(followed, str(item.job), int(item.image))

        model.jobs.result_used.connect(result_used)
        self._followed.add(model)

    def _backend(self) -> str:
        mode = getattr(self._settings, "server_mode", None)
        return str(getattr(mode, "name", mode or "unknown"))


def generation_applied(
    job: Any,
    index: int,
    version: str | None,
    backend: str,
    target: Layer | None,
    at: datetime,
) -> GenerationApplied:
    """A Krita AI Diffusion Job, as the log's event. Full prompts are kept: they stay in the .kra
    and the mapping decides what leaves (nothing of them, until D2 and D3)."""
    params = job.params
    metadata: Mapping[str, Any] = params.metadata or {}
    bounds = params.bounds
    width, height = bounds.width, bounds.height
    if 0 <= index < len(job.results):  # apply_result places the image at the job's offset
        extent = job.results[index].extent
        width, height = extent.width, extent.height
    return GenerationApplied(
        at=at,
        source=Source(log.AI_DIFFUSION, version or "unknown", backend),
        job=Job(
            id=str(job.id),
            kind=_name(job.kind),
            workflow_kind=_name(params.workflow_kind),
            result_index=index,
            bounds=(int(bounds.x), int(bounds.y), int(width), int(height)),
            has_mask=bool(params.has_mask),
            seed=int(params.seed),
        ),
        model=Model(
            checkpoint=_text(metadata.get("checkpoint")),
            style=_text(metadata.get("style")),
            sampler=_text(metadata.get("sampler")),
            steps=_whole(metadata.get("steps")),
            guidance=_number(metadata.get("guidance")),
            strength=_number(metadata.get("strength")),
        ),
        prompt=Prompt(
            text=_text(metadata.get("prompt")) or "",
            negative=_text(metadata.get("negative_prompt")) or "",
        ),
        target_layer=target,
    )


def target_layer(before: Collection[str], after: Mapping[str, tuple[str, str]]) -> Layer | None:
    """C2: the one paint layer that appeared during the apply, else None (disclosed as unknown).

    ``before`` holds the node ids from before the apply; ``after`` maps the ids after it to
    (name, Node.type()). Applying to several regions, or a layered result, adds several paint
    layers; which of them holds what isn't known here, so None.
    """
    new = [
        (node_id, name)
        for node_id, (name, kind) in after.items()
        if node_id not in before and kind == PAINT_LAYER
    ]
    return Layer(new[0][0], new[0][1]) if len(new) == 1 else None


def document_is_active(model: Any) -> bool:
    return bool(model.document.is_active)


def document_id(model: Any) -> str:
    """The id Krita AI Diffusion keeps in the document's ``ai_diffusion/document_id``."""
    return str(model.document.id)


def _name(value: Any) -> str:
    return str(getattr(value, "name", value))


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _whole(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)
