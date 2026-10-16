"""Build the C2PA manifest definition from the log (spec: C2PA mapping).

The manifest is rebuilt from the log at every export, so a fix here applies to older documents
too. Each log event becomes one action in a ``c2pa.actions.v2`` assertion; regions go in the
action's ``changes``; the first action is ``c2pa.created`` or ``c2pa.opened`` and the last is
``c2pa.converted`` for the export itself. The result is scrubbed (D1) before it leaves.

Checked against c2pa-rs 0.91.0, the library inside c2pa-python 0.38.0:

- ``c2pa.created`` must carry a ``digitalSourceType`` or the manifest fails validation.
- With the Create intent a ``parentOf`` ingredient is an error; with the Edit intent the
  ``c2pa.opened`` action must name a parent ingredient that was really added.
- The region ``role`` field the spec's table mentions is deprecated since C2PA 2.1, fails to
  decode in 0.91.0 and is removed in 0.92.0, so regions carry a ``description`` instead.

Honesty rules (spec: Threat model):

1. No "no AI used". ``ai_use_statement`` reads "No AI generation observed by this plugin since"
   the log's start date, marked (UTC) so it isn't read as a local date a day off (user, 14 Oct
   2026).
2. Every capture gap becomes a ``c2pa.unknown`` action that says what was not observed, with no
   ``changes``, which C2PA reads as "anything may have changed".
3. A generation whose layer is hidden, gone or was never identified is described as possibly
   still present, with the reason.
4. Descriptions state what the plugin observed, never a conclusion about authorship.

Decisions of 13 Oct 2026 (CLAUDE.md, Where things stand) and open choices kept in one place:

- A document that already had content when the plugin first saw it (``existing_document``) is
  ``c2pa.opened`` from its saved .kra, the ``parentOf`` ingredient, followed by a ``c2pa.unknown``
  action saying that content from before the log's start was not observed. It is never refused.
- The export title the user typed is kept as typed, reduced to its file name; everything
  captured automatically is scrubbed in full.
- Prompts never leave the log until the disclosure levels land (D2, D3); the AI action's
  parameters are the spec example's: model, seed, steps and strength (S5 settles the keys).
- Earlier ``exported`` events describe other output files and are not actions in this one.
- Any other ``log_started.reason`` is refused: neither first action would be honest for it.

Decisions of 16 Oct 2026:

- Each region is written as a polygon of its four corners, not as the spec table's rectangle.
  Adobe's Verify reads with c2pa-rs 0.85.2, which can't decode a rectangle's optional width
  and height (CLAUDE.md, "Older c2pa-rs can't read rectangle regions"); the area is the same.
- A generation's description doesn't name the layer that received it: Krita AI Diffusion names
  the layers it makes after the prompt. The log keeps the name.
- A document created with content already in it (``started_with_content``) maps like an
  existing document once the log names its saved .kra as the parent; until then it is refused,
  and the export action offers an unsigned file.
"""

from __future__ import annotations

import mimetypes
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from . import __version__, log
from .log import (
    Bounds,
    CaptureGap,
    Exported,
    GenerationApplied,
    IngredientAdded,
    LayerChanged,
    ProvenanceLog,
    UnknownEvent,
    format_time,
)
from .scrub import Identity, file_name, scrub

CLAIM_GENERATOR = "krita-provenance"
ACTIONS = "c2pa.actions.v2"

# Digital source types, as c2pa-rs spells them
EMPTY = "http://c2pa.org/digitalsourcetype/empty"
COMPOSITE_WITH_TRAINED_ALGORITHMIC_MEDIA = (
    "http://cv.iptc.org/newscodes/digitalsourcetype/compositeWithTrainedAlgorithmicMedia"
)

# Builder intents (c2pa-python's C2paBuilderIntent, named here so sdk.py owns the import)
CREATE = "create"
EDIT = "edit"

FORMATS = {"png": "image/png", "jpeg": "image/jpeg"}  # E2; E3 adds WebP later
KRITA_DOCUMENT = "application/x-krita"  # c2pa-rs accepts a .kra, a zip, as an ingredient under it
INGREDIENT_FORMATS = {  # by suffix, before mimetypes, which differs between platforms
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".psd": "image/vnd.adobe.photoshop",
    ".ora": "image/openraster",  # Krita saves working documents as .ora as well as .kra
    ".kra": KRITA_DOCUMENT,
}

TOOL_NAMES = {log.AI_DIFFUSION: "Krita AI Diffusion"}
WORKFLOW_VERBS = {
    "generate": "Generated",
    "inpaint": "Inpainted",
    "refine": "Refined",
    "refine_region": "Refined",
}
GAP_REASONS = {
    log.PLUGIN_OFF: "the plugin was off",
    log.ADAPTER_ERROR: "the Krita AI Diffusion adapter failed",
    log.AI_DIFFUSION_MISSING: "Krita AI Diffusion was not found",
}

_INSTANCE_NAMESPACE = uuid.UUID("5a3c0b8e-6b7f-4b1a-9d2e-7c4f1e8a9b10")  # for ingredient ids

STARTED_WITH_CONTENT_UNSIGNED = (
    "This document was created with content already in it (pasted, from a template or a file"
    " opened as untitled), which this plugin didn't see being made. To export it signed, save it"
    " as .kra, close it and open it again; until then it can only be exported unsigned."
)


class MappingError(ValueError):
    """The log cannot be described honestly as a C2PA manifest."""


@dataclass(frozen=True)
class LayerState:
    visible: bool


@dataclass(frozen=True)
class Export:
    """What the exporter knows at export time, beyond the log."""

    title: str  # the output file's name as the user typed it; kept minus its folder, not scrubbed
    format: str  # a key of FORMATS
    at: datetime
    krita_version: str | None = None
    layers: Mapping[str, LayerState] | None = None  # node uuid -> state now; None if unknown


@dataclass(frozen=True)
class Ingredient:
    """An ingredient the signer adds with its bytes (sdk.py), beside the definition."""

    definition: dict[str, Any]  # title, relationship, instance_id
    format: str | None  # MIME type guessed from the title, None if unknown
    store: str | None  # annotation holding the bytes (P2), if kept
    validation: str | None  # the imported manifest's state as the SDK reported it


@dataclass(frozen=True)
class Manifest:
    definition: dict[str, Any]
    intent: str  # CREATE or EDIT
    source_type: str | None  # with CREATE
    ingredients: tuple[Ingredient, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "source_type": self.source_type,
            "ingredients": [vars(ingredient) for ingredient in self.ingredients],
            "definition": self.definition,
        }


def manifest(
    entry: ProvenanceLog,
    export: Export,
    identity: Identity | None = None,
    plugin_version: str = __version__,
) -> Manifest:
    """The manifest definition for this export, scrubbed and ready for the signer."""
    if export.format not in FORMATS:
        raise MappingError(
            f"Format {export.format!r} is not supported; use one of {sorted(FORMATS)}"
        )
    title = typed_file_name(export.title)

    krita = _agent("Krita", export.krita_version)
    parents = [
        e
        for e in entry.events
        if isinstance(e, IngredientAdded) and e.relationship == log.PARENT_OF
    ]
    if len(parents) > 1:
        raise MappingError("The log names more than one parentOf ingredient; a manifest has one")

    ingredients: list[Ingredient] = []
    actions: list[dict[str, Any]] = []

    reason = entry.started.reason
    if reason == log.NEW_DOCUMENT:
        if parents:
            raise MappingError("A new document cannot also have been opened from a file")
        intent, source_type = CREATE, EMPTY
        actions.append(_action("c2pa.created", entry.started.at, krita, digitalSourceType=EMPTY))
    elif reason in (log.OPENED_IMAGE, log.EXISTING_DOCUMENT, log.STARTED_WITH_CONTENT):
        if not parents:
            if reason == log.STARTED_WITH_CONTENT:
                raise MappingError(STARTED_WITH_CONTENT_UNSIGNED)
            raise MappingError(f"A log started as {reason!r} needs a parentOf ingredient event")
        intent, source_type = EDIT, None
        parent = _ingredient(entry, parents[0])
        ingredients.append(parent)
        opened = _action(
            "c2pa.opened",
            entry.started.at,
            krita,
            parameters={"ingredientIds": [parent.definition["instance_id"]]},
        )
        actions.append(opened)
        if reason in (log.EXISTING_DOCUMENT, log.STARTED_WITH_CONTENT):
            # It already had content: say so on the opened action, and add an action with no
            # changes, which C2PA reads as "anything may have changed", for the unobserved past.
            if reason == log.EXISTING_DOCUMENT:
                opened["description"] = (
                    f"Began observing {file_name(parents[0].title)} on"
                    f" {format_time(entry.started.at)}, when it already had content; this plugin"
                    " did not see it being created or opened."
                )
            else:
                opened["description"] = (
                    f"Began observing this document, saved as {file_name(parents[0].title)}, on"
                    f" {format_time(entry.started.at)}, when Krita created it with content already"
                    " in it (pasted, from a template or a file opened as untitled); this plugin did"
                    " not see that content being made."
                )
            actions.append(
                _action("c2pa.unknown", None, None, description=unobserved_statement(entry))
            )
    else:
        raise MappingError(f"Log start reason {reason!r} has no honest C2PA first action")

    for event in entry.events:
        if isinstance(event, GenerationApplied):
            actions.append(_generation(event, export))
        elif isinstance(event, LayerChanged):
            actions.append(
                _action(
                    "c2pa.drawing",
                    event.at,
                    krita,
                    changes=[_change(event.bounds, f"Layer {event.layer.name} changed")],
                )
            )
        elif isinstance(event, IngredientAdded):
            if event.relationship == log.PARENT_OF:
                continue  # the first action covers it
            component = _ingredient(entry, event)
            ingredients.append(component)
            actions.append(_placed(event, component, krita))
        elif isinstance(event, CaptureGap):
            actions.append(
                _action("c2pa.unknown", event.start, None, description=gap_statement(event))
            )
        elif isinstance(event, Exported):
            continue  # describes another output file
        elif isinstance(event, UnknownEvent):
            actions.append(
                _action(
                    "c2pa.unknown",
                    None,
                    None,
                    description=(
                        f"An event of type {event.type!r} was recorded by a newer version of"
                        f" {CLAIM_GENERATOR} and could not be described by version {plugin_version}."
                    ),
                )
            )

    actions.append(
        _action(
            "c2pa.converted",
            export.at,
            _agent(CLAIM_GENERATOR, plugin_version),
            description=f"Exported from .kra to {export.format.upper()}",
        )
    )

    definition = {
        "claim_generator_info": [{"name": CLAIM_GENERATOR, "version": plugin_version}],
        "title": export.title,
        "assertions": [{"label": ACTIONS, "data": {"actions": actions}}],
    }
    identity = identity or Identity.current()
    definition = scrub(definition, identity)
    definition["title"] = title  # typed by the user: kept as typed, minus its folder (decision 3)
    return Manifest(
        definition=definition,
        intent=intent,
        source_type=source_type,
        ingredients=tuple(
            Ingredient(scrub(i.definition, identity), i.format, i.store, i.validation)
            for i in ingredients
        ),
    )


def ai_use_statement(entry: ProvenanceLog) -> str:
    """Honesty rule 1: absence is "not observed since", never "no AI used"."""
    generations = [e for e in entry.events if isinstance(e, GenerationApplied)]
    gaps = [e for e in entry.events if isinstance(e, CaptureGap)]
    since = f"{_date(entry.started.at)} (UTC)"
    if not generations:
        text = f"No AI generation observed by this plugin since {since}."
    else:
        count = len(generations)
        plural = "s" if count != 1 else ""
        text = f"{count} AI generation{plural} observed by this plugin since {since}."
    if entry.started.reason == log.EXISTING_DOCUMENT:
        text += " The document already had content on that date; earlier AI use, if any, is not recorded."
    elif entry.started.reason == log.STARTED_WITH_CONTENT:
        text += (
            " The document was created with content already in it; how that content was made is"
            " not recorded."
        )
    if gaps:
        plural = "s" if len(gaps) != 1 else ""
        text += f" Capture was interrupted {len(gaps)} time{plural}; see the log for when."
    return text


def unobserved_statement(entry: ProvenanceLog) -> str:
    """For a document that predates its log: what the manifest cannot say about it.

    Names no file, so it is safe to show anywhere; the opened action beside it names the parent.
    """
    return (
        f"Content from before {format_time(entry.started.at)}, when this plugin began observing"
        " this document, was not observed by this plugin as it was made; AI use before then, if"
        " any, is not recorded here."
    )


def typed_file_name(title: str) -> str:
    """The file name in a title the user typed (decision 3): the name, never its folder.

    Raises MappingError when there is no file name to keep, such as a bare folder, a drive or a
    UNC host, which would otherwise leave unscrubbed.
    """
    stripped = title.strip()
    parts = [part for part in _SEPARATORS.split(stripped) if part]
    if stripped.startswith(("\\\\", "//")):
        parts = parts[2:]  # host and share are not file names
    if not parts or stripped.endswith(("/", "\\")) or _DRIVE.fullmatch(parts[-1]):
        raise MappingError(f"The export title {title!r} has no file name")
    return parts[-1]


_SEPARATORS = re.compile(r"[\\/]+")
_DRIVE = re.compile(r"[A-Za-z]:")


def _date(at: datetime) -> str:
    """2 November 2026, the UTC date, independent of the locale and of Windows' strftime."""
    at = at.astimezone(timezone.utc)
    return f"{at.day} {_MONTHS[at.month - 1]} {at.year}"


_MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


def gap_statement(gap: CaptureGap) -> str:
    if gap.reason == log.AI_DIFFUSION_UNOBSERVED:  # its end isn't known: it ran until Krita closed
        return (
            f"Capture gap from {format_time(gap.start)} until Krita was closed (Krita AI Diffusion"
            " was running, but this plugin could not observe it): AI generations in this period"
            " were not observed by this plugin."
        )
    reason = GAP_REASONS.get(gap.reason, gap.reason)
    return (
        f"Capture gap from {format_time(gap.start)} to {format_time(gap.end)} ({reason}):"
        " AI generations in this period were not observed by this plugin."
    )


def region(bounds: Bounds) -> dict[str, Any]:
    """A spatial region over x, y, width, height in pixels, written as the polygon of its four
    corners (decision of 16 Oct 2026, module docstring): no optional floats for old readers."""
    x, y, width, height = bounds
    corners = [(x, y), (x + width, y), (x + width, y + height), (x, y + height)]
    return {
        "type": "spatial",
        "shape": {
            "type": "polygon",
            "unit": "pixel",
            "origin": {"x": x, "y": y},
            "vertices": [{"x": cx, "y": cy} for cx, cy in corners],
        },
    }


def _generation(event: GenerationApplied, export: Export) -> dict[str, Any]:
    tool = TOOL_NAMES.get(event.source.tool, event.source.tool)
    verb = WORKFLOW_VERBS.get(event.job.workflow_kind, "AI-generated")
    what = f"{verb} region" if verb != "Generated" else "Generated image"
    how = f"{tool}, {event.job.workflow_kind}"

    # The layer is never named: Krita AI Diffusion names it after the prompt (decision of 16 Oct).
    layer = event.target_layer
    if layer is None:
        description = (
            f"{what} ({how}); the layer that received it could not be identified, so this"
            " AI-generated content may be present anywhere it was moved or merged."
        )
    elif export.layers is None or (
        layer.uuid in export.layers and export.layers[layer.uuid].visible
    ):
        description = f"{what} ({how})"
    elif layer.uuid not in export.layers:
        description = (
            f"{what} ({how}); the layer that received it no longer exists at export, so it may"
            " have been merged into other layers and this region may still contain"
            " AI-generated content."
        )
    else:
        description = (
            f"{what} ({how}); the layer that received it is hidden at export, but copies or"
            " merges of it may remain, so this region may still contain AI-generated content."
        )

    parameters: dict[str, Any] = {"seed": event.job.seed}
    if event.model.checkpoint is not None:
        parameters["model"] = event.model.checkpoint
    if event.model.steps is not None:
        parameters["steps"] = event.model.steps
    if event.model.strength is not None:
        parameters["strength"] = event.model.strength

    return _action(
        "c2pa.edited",
        event.at,
        _agent(tool, event.source.version),
        digitalSourceType=COMPOSITE_WITH_TRAINED_ALGORITHMIC_MEDIA,
        parameters=parameters,
        changes=[_change(event.job.bounds, description)],
    )


def _placed(event: IngredientAdded, component: Ingredient, krita: dict[str, Any]) -> dict[str, Any]:
    where = f" on layer {event.layer.name}" if event.layer else ""
    description = f"Placed {file_name(event.title)}{where}"  # a path with spaces would survive prose scrubbing
    changes = [_change(event.bounds, description)] if event.bounds else None
    return _action(
        "c2pa.placed",
        event.at,
        krita,
        parameters={"ingredientIds": [component.definition["instance_id"]]},
        changes=changes,
        description=None if changes else description,
    )


def _ingredient(entry: ProvenanceLog, event: IngredientAdded) -> Ingredient:
    index = [e for e in entry.events if isinstance(e, IngredientAdded)].index(event)
    instance_id = f"xmp.iid:{uuid.uuid5(_INSTANCE_NAMESPACE, f'{entry.document_id}/{index}')}"
    return Ingredient(
        definition={
            "title": event.title,
            "relationship": event.relationship,
            "instance_id": instance_id,
        },
        format=_ingredient_format(event.title),
        store=event.store,
        validation=event.validation,
    )


def _ingredient_format(title: str) -> str | None:
    suffix = "." + file_name(title).rsplit(".", 1)[-1].lower() if "." in file_name(title) else ""
    return INGREDIENT_FORMATS.get(suffix) or mimetypes.guess_type(file_name(title))[0]


def _agent(name: str, version: str | None) -> dict[str, Any]:
    return {"name": name, "version": version} if version else {"name": name}


def _change(bounds: Bounds, description: str) -> dict[str, Any]:
    return {"region": [region(bounds)], "description": description}


def _action(
    name: str,
    when: datetime | None,
    agent: dict[str, Any] | None,
    **fields: Any,
) -> dict[str, Any]:
    action: dict[str, Any] = {"action": name}
    if when is not None:
        action["when"] = format_time(when)
    if agent is not None:
        action["softwareAgent"] = agent
    action.update((key, value) for key, value in fields.items() if value is not None)
    return action
