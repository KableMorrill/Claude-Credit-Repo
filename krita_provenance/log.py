"""The provenance log, the system of record (spec: Provenance log, P1).

Every capture source feeds this one log, and the C2PA manifest is built from it at each export
(mapping.py), so mapping fixes also apply to older documents. It is an append-only list of
events kept as versioned JSON in the document's ``krita-provenance.json`` annotation. The Krita
glue reads and writes the annotation; this module is Krita-free and tested.

Rules, from the spec:

- Append only. Nothing here rewrites or drops a past event. A log round-trips through JSON with
  every event intact, including event types this version doesn't know, so a newer plugin's
  events survive an older one (P3 migrates older schema versions in v1.0). A field added to a
  known event type doesn't yet: a version that doesn't know it drops it when writing back.
- Full prompts stay in the .kra. They are stored here and only the mapping decides what leaves
  (nothing of them, until the disclosure levels D2 and D3 land).
- Every capture gap is an event, so it can be disclosed (honesty rule 2).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

SCHEMA = "krita-provenance/1"
ANNOTATION = "krita-provenance.json"  # no clash: Krita AI Diffusion's start with ai_diffusion/

# log_started.reason
NEW_DOCUMENT = "new_document"  # a blank canvas created in Krita while the plugin watched
OPENED_IMAGE = "opened_image"  # opened from an image file, logged as a parentOf ingredient
EXISTING_DOCUMENT = (
    "existing_document"  # had content before the plugin saw it; its .kra is the parent
)
# Created untitled with content already in it: pasted, from a template, or a file opened as
# untitled (decision 2 of 16 Oct 2026). It has no parent until the plugin sees it reopened from
# a saved .kra or .ora, which logs that file as the parent; it can't be signed before.
STARTED_WITH_CONTENT = "started_with_content"

# capture_gap.reason (C3)
PLUGIN_OFF = "plugin_off"
ADAPTER_ERROR = "adapter_error"  # one applied result that could not be recorded: "to" == "from"
AI_DIFFUSION_MISSING = "ai_diffusion_missing"
# Krita AI Diffusion was running but the adapter couldn't follow it. Logged when the plugin
# begins observing a document in that Krita session, with "to" == "from": the gap lasted until
# Krita closed, which nothing records.
AI_DIFFUSION_UNOBSERVED = "ai_diffusion_unobserved"
# A Krita AI Diffusion result was applied, but the document it went into couldn't be identified.
# Logged in every open document, with "to" == "from": any of them may have received it.
AI_DIFFUSION_UNATTRIBUTED = "ai_diffusion_unattributed"

# source.tool
AI_DIFFUSION = "krita-ai-diffusion"

# ingredient_added.relationship, as C2PA names them
PARENT_OF = "parentOf"
COMPONENT_OF = "componentOf"

Bounds = tuple[int, int, int, int]
"""x, y, width, height in document pixels, as Krita and Krita AI Diffusion report them."""


class LogFormatError(ValueError):
    """The JSON is not a log this version can read."""


@dataclass(frozen=True)
class LogStart:
    at: datetime
    reason: str  # NEW_DOCUMENT, OPENED_IMAGE, EXISTING_DOCUMENT or STARTED_WITH_CONTENT


@dataclass(frozen=True)
class Layer:
    uuid: str  # Krita's node unique ID, braces included
    name: str


@dataclass(frozen=True)
class Source:
    tool: str  # AI_DIFFUSION
    version: str
    backend: str  # "local" or "cloud", as Krita AI Diffusion reports it


@dataclass(frozen=True)
class Job:
    """What Krita AI Diffusion's JobParams says about the applied result (C1)."""

    id: str
    kind: str  # "diffusion" in v1 (C7 adds the others)
    workflow_kind: str  # generate, inpaint, refine, ...
    result_index: int
    bounds: Bounds
    has_mask: bool
    seed: int


@dataclass(frozen=True)
class Model:
    """JobParams.metadata keys; any may be missing from a job."""

    checkpoint: str | None = None
    style: str | None = None
    sampler: str | None = None
    steps: int | None = None
    guidance: float | None = None
    strength: float | None = None


@dataclass(frozen=True)
class Prompt:
    text: str
    negative: str


@dataclass(frozen=True)
class GenerationApplied:
    at: datetime
    source: Source
    job: Job
    model: Model
    prompt: Prompt
    target_layer: Layer | None  # None when C2 could not identify the receiving layer


@dataclass(frozen=True)
class LayerChanged:
    at: datetime
    layer: Layer
    bounds: Bounds
    hash: str  # "sha256:..." of the layer's pixels at the checkpoint (C5)


@dataclass(frozen=True)
class IngredientAdded:
    at: datetime
    relationship: str  # PARENT_OF or COMPONENT_OF
    title: str  # the file's name; the mapping scrubs it before it leaves the .kra
    store: str | None = None  # annotation holding the file's bytes (P2), if kept
    validation: str | None = None  # the imported manifest's state as the SDK reported it
    layer: Layer | None = None  # the layer that received it, when placed (C6)
    bounds: Bounds | None = None  # where it was placed


@dataclass(frozen=True)
class CaptureGap:
    start: datetime  # JSON "from"
    end: datetime  # JSON "to"
    reason: str  # one of the reasons above, or a free-form message


@dataclass(frozen=True)
class ReadBack:
    """E4: what reading a signed export back with the SDK reported."""

    state: str | None  # "Valid" or "Trusted", as the SDK reports it
    failures: tuple[str, ...] = ()  # its validation codes; a local signer is "untrusted"


@dataclass(frozen=True)
class Exported:
    at: datetime
    format: str  # "png" or "jpeg"
    disclosure: str  # "standard" until D2 lands
    manifest_label: str | None  # None when the file was saved unsigned (E6)
    # Kept since 18 Oct 2026 (the user's decision), so the log shows each export's read-back
    # for good. None when unsigned, and in exports logged before then. Not in the manifest.
    read_back: ReadBack | None = None


@dataclass(frozen=True)
class UnknownEvent:
    """An event type this version doesn't know, kept verbatim so it survives a round trip."""

    type: str
    data: Mapping[str, Any]


Event = GenerationApplied | LayerChanged | IngredientAdded | CaptureGap | Exported | UnknownEvent

EVENT_TYPES: dict[type, str] = {
    GenerationApplied: "generation_applied",
    LayerChanged: "layer_changed",
    IngredientAdded: "ingredient_added",
    CaptureGap: "capture_gap",
    Exported: "exported",
}


def event_type(event: Event) -> str:
    if isinstance(event, UnknownEvent):
        return event.type
    return EVENT_TYPES[type(event)]


@dataclass
class ProvenanceLog:
    document_id: str
    started: LogStart
    events: list[Event] = field(default_factory=list)

    @classmethod
    def new(cls, reason: str, at: datetime | None = None) -> ProvenanceLog:
        return cls(str(uuid.uuid4()), LogStart(at or now(), reason))

    def append(self, event: Event) -> None:
        """The only way the log grows. Past events are never changed."""
        self.events.append(event)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "document_id": self.document_id,
            "log_started": {"at": format_time(self.started.at), "reason": self.started.reason},
            "events": [event_to_dict(event) for event in self.events],
        }

    @classmethod
    def from_json(cls, text: str | bytes) -> ProvenanceLog:
        try:
            data = json.loads(text)
        except ValueError as error:
            raise LogFormatError(f"The log is not valid JSON: {error}") from error
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: Any) -> ProvenanceLog:
        if not isinstance(data, Mapping):
            raise LogFormatError("The log must be a JSON object")
        schema = data.get("schema")
        if schema != SCHEMA:
            raise LogFormatError(
                f"The log has schema {schema!r}; this version reads {SCHEMA!r} only"
            )
        try:
            started = data["log_started"]
            log = cls(
                document_id=_string(data["document_id"]),
                started=LogStart(parse_time(started["at"]), _string(started["reason"])),
            )
            for item in data["events"]:
                log.append(event_from_dict(item))
        except LogFormatError:
            raise
        except (KeyError, TypeError, ValueError) as error:
            raise LogFormatError(f"The log is malformed: {error!r}") from error
        return log


def now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def format_time(at: datetime) -> str:
    """UTC with a Z suffix and whole seconds, as the spec's examples show."""
    if at.tzinfo is None:
        raise ValueError("Log times must be timezone-aware")
    return at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_time(text: Any) -> datetime:
    """Read an ISO 8601 time with a Z or an offset. Python 3.10 rejects Z on its own."""
    text = _string(text)
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    at = datetime.fromisoformat(text)
    if at.tzinfo is None:
        raise ValueError(f"Time {text!r} has no timezone")
    return at.astimezone(timezone.utc)


def event_to_dict(event: Event) -> dict[str, Any]:
    if isinstance(event, UnknownEvent):
        return dict(event.data)
    data: dict[str, Any] = {"type": event_type(event)}
    if isinstance(event, GenerationApplied):
        data["at"] = format_time(event.at)
        data["source"] = _fields(event.source)
        data["job"] = {**_fields(event.job), "bounds": list(event.job.bounds)}
        data["model"] = _fields(event.model, drop_none=True)
        data["prompt"] = _fields(event.prompt)
        data["target_layer"] = _fields(event.target_layer) if event.target_layer else None
    elif isinstance(event, LayerChanged):
        data["at"] = format_time(event.at)
        data["layer"] = _fields(event.layer)
        data["bounds"] = list(event.bounds)
        data["hash"] = event.hash
    elif isinstance(event, IngredientAdded):
        data["at"] = format_time(event.at)
        data["relationship"] = event.relationship
        data["title"] = event.title
        data["store"] = event.store
        data["validation"] = event.validation
        if event.layer is not None:
            data["layer"] = _fields(event.layer)
        if event.bounds is not None:
            data["bounds"] = list(event.bounds)
    elif isinstance(event, CaptureGap):
        data["from"] = format_time(event.start)
        data["to"] = format_time(event.end)
        data["reason"] = event.reason
    elif isinstance(event, Exported):
        data["at"] = format_time(event.at)
        data["format"] = event.format
        data["disclosure"] = event.disclosure
        data["manifest_label"] = event.manifest_label
        if event.read_back is not None:  # left out otherwise, so older events write back as read
            data["read_back"] = {
                "state": event.read_back.state,
                "failures": list(event.read_back.failures),
            }
    return data


def event_from_dict(data: Any) -> Event:
    if not isinstance(data, Mapping):
        raise LogFormatError(f"An event must be a JSON object, not {type(data).__name__}")
    kind = _string(data["type"])
    if kind == "generation_applied":
        job = data["job"]
        model = data.get("model") or {}
        return GenerationApplied(
            at=parse_time(data["at"]),
            source=Source(**_strings(data["source"], "tool", "version", "backend")),
            job=Job(
                id=_string(job["id"]),
                kind=_string(job["kind"]),
                workflow_kind=_string(job["workflow_kind"]),
                result_index=_int(job["result_index"]),
                bounds=parse_bounds(job["bounds"]),
                has_mask=bool(job["has_mask"]),
                seed=_int(job["seed"]),
            ),
            model=Model(
                checkpoint=_optional(model.get("checkpoint"), _string),
                style=_optional(model.get("style"), _string),
                sampler=_optional(model.get("sampler"), _string),
                steps=_optional(model.get("steps"), _int),
                guidance=_optional(model.get("guidance"), _number),
                strength=_optional(model.get("strength"), _number),
            ),
            prompt=Prompt(**_strings(data["prompt"], "text", "negative")),
            target_layer=_optional(data.get("target_layer"), _layer),
        )
    if kind == "layer_changed":
        return LayerChanged(
            at=parse_time(data["at"]),
            layer=_layer(data["layer"]),
            bounds=parse_bounds(data["bounds"]),
            hash=_string(data["hash"]),
        )
    if kind == "ingredient_added":
        return IngredientAdded(
            at=parse_time(data["at"]),
            relationship=_string(data["relationship"]),
            title=_string(data["title"]),
            store=_optional(data.get("store"), _string),
            validation=_optional(data.get("validation"), _string),
            layer=_optional(data.get("layer"), _layer),
            bounds=_optional(data.get("bounds"), parse_bounds),
        )
    if kind == "capture_gap":
        return CaptureGap(
            start=parse_time(data["from"]),
            end=parse_time(data["to"]),
            reason=_string(data["reason"]),
        )
    if kind == "exported":
        return Exported(
            at=parse_time(data["at"]),
            format=_string(data["format"]),
            disclosure=_string(data["disclosure"]),
            manifest_label=_optional(data.get("manifest_label"), _string),
            read_back=_optional(data.get("read_back"), _read_back),
        )
    return UnknownEvent(kind, dict(data))


def parse_bounds(value: Any) -> Bounds:
    if not isinstance(value, Iterable) or isinstance(value, (str, bytes)):
        raise TypeError(f"Bounds must be a list of four integers, not {value!r}")
    items = [_int(item) for item in value]
    if len(items) != 4:
        raise ValueError(f"Bounds must have four integers, not {len(items)}")
    return items[0], items[1], items[2], items[3]


def _fields(value: Any, drop_none: bool = False) -> dict[str, Any]:
    data = dict(vars(value))
    return {k: v for k, v in data.items() if v is not None} if drop_none else data


def _layer(data: Any) -> Layer:
    return Layer(**_strings(data, "uuid", "name"))


def _read_back(data: Any) -> ReadBack:
    if not isinstance(data, Mapping):
        raise TypeError(f"read_back must be an object, not {data!r}")
    failures = data.get("failures", [])
    if not isinstance(failures, list):
        raise TypeError(f"read_back failures must be a list, not {failures!r}")
    return ReadBack(_optional(data.get("state"), _string), tuple(_string(f) for f in failures))


def _strings(data: Mapping[str, Any], *keys: str) -> dict[str, str]:
    return {key: _string(data[key]) for key in keys}


def _string(value: Any) -> str:
    if not isinstance(value, str):
        raise TypeError(f"Expected a string, got {value!r}")
    return value


def _int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"Expected an integer, got {value!r}")
    return value


def _number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"Expected a number, got {value!r}")
    return float(value)


def _optional(value: Any, parse: Any) -> Any:
    return None if value is None else parse(value)
