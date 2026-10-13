"""Golden tests for the log-to-manifest mapping (spec: C2PA mapping, Testing and validation).

The cases live under tests/fixtures/goldens; see tests/goldens.py for their layout and for
UPDATE_GOLDENS=1, which rewrites the expected manifests after a deliberate change.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import pytest

from goldens import CASES, GOLDENS, load_case, read_json
from krita_provenance import __version__, log, mapping
from krita_provenance.log import (
    CaptureGap,
    GenerationApplied,
    IngredientAdded,
    Job,
    Layer,
    LogStart,
    Model,
    Prompt,
    ProvenanceLog,
    Source,
    UnknownEvent,
)
from krita_provenance.mapping import Export, MappingError
from krita_provenance.scrub import Identity

NO_ONE = Identity()
T0 = datetime(2026, 11, 2, 18, 4, 11, tzinfo=timezone.utc)
T1 = datetime(2026, 11, 2, 19, 0, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("name", CASES)
def test_golden_manifest(name: str):
    entry, export, identity = load_case(name)
    actual = mapping.manifest(entry, export, identity, plugin_version="0.1.0").to_dict()
    expected_path = GOLDENS / name / "manifest.json"
    if os.environ.get("UPDATE_GOLDENS"):
        expected_path.write_text(json.dumps(actual, indent=2) + "\n", encoding="utf-8")
    assert actual == read_json(expected_path)


@pytest.mark.parametrize("name", CASES)
def test_golden_manifests_keep_prompts_paths_and_names_in_the_kra(name: str):
    entry, export, identity = load_case(name)
    text = json.dumps(mapping.manifest(entry, export, identity).to_dict())
    for event in entry.events:
        if isinstance(event, GenerationApplied):
            assert event.prompt.text not in text
            if event.prompt.negative:
                assert event.prompt.negative not in text
    for name_ in (
        identity.user_names + identity.host_names + ((identity.home,) if identity.home else ())
    ):
        assert name_.lower() not in text.lower()
    assert "\\\\" not in text and "/home/" not in text


@pytest.mark.parametrize("name", CASES)
def test_golden_manifests_never_say_no_ai_used(name: str):
    entry, export, identity = load_case(name)
    text = json.dumps(mapping.manifest(entry, export, identity).to_dict()).lower()
    statement = mapping.ai_use_statement(entry).lower()
    for phrase in ("no ai used", "no ai was used", "human-made", "made by hand"):
        assert phrase not in text
        assert phrase not in statement


def test_first_and_last_actions_frame_the_history():
    entry, export, identity = load_case("new_document")
    actions = mapping.manifest(entry, export, identity).definition["assertions"][0]["data"][
        "actions"
    ]
    assert actions[0]["action"] == "c2pa.created"
    assert actions[0]["digitalSourceType"] == mapping.EMPTY
    assert actions[-1]["action"] == "c2pa.converted"
    assert actions[-1]["description"] == "Exported from .kra to PNG"
    assert [a["action"] for a in actions] == [
        "c2pa.created",
        "c2pa.edited",
        "c2pa.drawing",
        "c2pa.unknown",
        "c2pa.converted",
    ]


def test_a_generation_maps_to_an_ai_edit_with_its_region_and_without_the_prompt():
    entry, export, identity = load_case("new_document")
    actions = mapping.manifest(entry, export, identity).definition["assertions"][0]["data"][
        "actions"
    ]
    edited = actions[1]
    assert edited["digitalSourceType"] == mapping.COMPOSITE_WITH_TRAINED_ALGORITHMIC_MEDIA
    assert edited["softwareAgent"] == {"name": "Krita AI Diffusion", "version": "1.53.0"}
    assert edited["parameters"] == {
        "seed": 1234567,
        "model": "dreamshaper_8.safetensors",  # the path to the checkpoint was scrubbed
        "steps": 24,
        "strength": 0.65,
    }
    assert edited["changes"] == [
        {
            "region": [mapping.rectangle((512, 128, 768, 640))],
            "description": "Inpainted region on layer Sky (Krita AI Diffusion, inpaint)",
        }
    ]
    assert "role" not in edited["changes"][0]  # deprecated in C2PA 2.1; c2pa-rs 0.91 rejects it
    assert "prompt" not in json.dumps(edited)


def test_a_capture_gap_is_an_unknown_action_without_changes():
    entry, export, identity = load_case("new_document")
    actions = mapping.manifest(entry, export, identity).definition["assertions"][0]["data"][
        "actions"
    ]
    gap = actions[3]
    assert gap["action"] == "c2pa.unknown"
    assert "changes" not in gap
    assert gap["description"] == (
        "Capture gap from 2026-11-02T18:40:00Z to 2026-11-02T18:52:17Z (the Krita AI Diffusion"
        " adapter failed): AI generations in this period were not observed by this plugin."
    )


def test_uncertain_attribution_is_disclosed_with_the_reason():
    """Honesty rule 3: hidden, removed or unidentified layers read as possibly AI."""
    entry, export, identity = load_case("opened_image")
    actions = mapping.manifest(entry, export, identity).definition["assertions"][0]["data"][
        "actions"
    ]
    descriptions = [a["changes"][0]["description"] for a in actions if a["action"] == "c2pa.edited"]
    assert descriptions[0].startswith(
        "Refined region on layer Retouch (Krita AI Diffusion, refine); the layer is hidden"
    )
    assert "may still contain AI-generated content" in descriptions[0]
    assert descriptions[1].startswith(
        "Generated image on layer Clouds (Krita AI Diffusion, generate); the layer no longer exists"
    )
    assert "merged" in descriptions[1]
    assert descriptions[2].startswith(
        "Inpainted region (Krita AI Diffusion, inpaint); the layer that received it could not be identified"
    )


def test_an_opened_image_is_the_parent_and_a_placed_image_a_component():
    entry, export, identity = load_case("opened_image")
    result = mapping.manifest(entry, export, identity)
    assert result.intent == mapping.EDIT
    assert result.source_type is None
    parent, component = result.ingredients
    assert parent.definition["relationship"] == "parentOf"
    assert parent.definition["title"] == "harbour.jpg"
    assert parent.format == "image/jpeg"
    assert parent.store == "krita-provenance/ingredient-1"
    assert component.definition["relationship"] == "componentOf"
    assert component.definition["title"] == "logo.png"
    assert component.validation == "valid"
    actions = result.definition["assertions"][0]["data"]["actions"]
    assert actions[0]["action"] == "c2pa.opened"
    assert actions[0]["parameters"] == {"ingredientIds": [parent.definition["instance_id"]]}
    placed = next(a for a in actions if a["action"] == "c2pa.placed")
    assert placed["parameters"] == {"ingredientIds": [component.definition["instance_id"]]}
    assert placed["changes"][0]["region"] == [mapping.rectangle((1400, 1000, 180, 180))]
    assert placed["changes"][0]["description"] == "Placed logo.png on layer Logo"
    assert parent.definition["instance_id"] != component.definition["instance_id"]
    again = mapping.manifest(entry, export, identity)
    assert again.ingredients == result.ingredients  # ids are stable across exports


def test_ai_use_statement_reads_absence_as_not_observed():
    entry, _, _ = load_case("nothing_observed")
    assert (
        mapping.ai_use_statement(entry)
        == "No AI generation observed by this plugin since 4 November 2026."
    )
    entry, _, _ = load_case("new_document")
    assert mapping.ai_use_statement(entry) == (
        "1 AI generation observed by this plugin since 2 November 2026."
        " Capture was interrupted 1 time; see the log for when."
    )
    entry, _, _ = load_case("opened_image")
    assert mapping.ai_use_statement(entry).startswith(
        "3 AI generations observed by this plugin since 3 November 2026."
    )


def test_unknown_events_become_unknown_actions():
    entry = ProvenanceLog("doc", LogStart(T0, log.NEW_DOCUMENT))
    entry.append(UnknownEvent("stroke_recorded", {"type": "stroke_recorded"}))
    actions = mapping.manifest(entry, Export("x.png", "png", T1), NO_ONE).definition["assertions"][
        0
    ]["data"]["actions"]
    assert actions[1]["action"] == "c2pa.unknown"
    assert "stroke_recorded" in actions[1]["description"]
    assert "newer version" in actions[1]["description"]


def test_the_plugin_is_the_claim_generator():
    entry = ProvenanceLog("doc", LogStart(T0, log.NEW_DOCUMENT))
    definition = mapping.manifest(entry, Export("x.png", "png", T1), NO_ONE).definition
    assert definition["claim_generator_info"] == [
        {"name": "krita-provenance", "version": __version__}
    ]
    assert definition["title"] == "x.png"
    assert [a["label"] for a in definition["assertions"]] == ["c2pa.actions.v2"]


def parent_event(at: datetime) -> IngredientAdded:
    return IngredientAdded(at, log.PARENT_OF, "photo.jpg")


def test_refuses_what_it_cannot_map_honestly():
    fresh = ProvenanceLog("doc", LogStart(T0, log.NEW_DOCUMENT))
    with pytest.raises(MappingError, match="not supported"):
        mapping.manifest(fresh, Export("x.webp", "webp", T1), NO_ONE)

    fresh.append(parent_event(T0))
    with pytest.raises(MappingError, match="new document"):
        mapping.manifest(fresh, Export("x.png", "png", T1), NO_ONE)

    opened = ProvenanceLog("doc", LogStart(T0, log.OPENED_IMAGE))
    with pytest.raises(MappingError, match="parentOf"):
        mapping.manifest(opened, Export("x.png", "png", T1), NO_ONE)
    opened.append(parent_event(T0))
    opened.append(parent_event(T1))
    with pytest.raises(MappingError, match="more than one"):
        mapping.manifest(opened, Export("x.png", "png", T1), NO_ONE)

    other = ProvenanceLog("doc", LogStart(T0, "existing_document"))
    with pytest.raises(MappingError, match="existing_document"):
        mapping.manifest(other, Export("x.png", "png", T1), NO_ONE)


def test_uses_the_machine_identity_by_default(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(Identity, "current", classmethod(lambda cls: Identity(("someone",))))
    entry = ProvenanceLog("doc", LogStart(T0, log.NEW_DOCUMENT))
    entry.append(
        GenerationApplied(
            T0,
            Source(log.AI_DIFFUSION, "1.53.0", "local"),
            Job("j", "diffusion", "inpaint", 0, (0, 0, 10, 10), True, 1),
            Model(checkpoint="someone.safetensors"),
            Prompt("", ""),
            Layer("{l}", "someone's layer"),
        )
    )
    text = json.dumps(mapping.manifest(entry, Export("x.png", "png", T1)).definition)
    assert "someone" not in text
    assert "[user]" in text


def test_gap_statement_keeps_unknown_reasons_verbatim():
    gap = CaptureGap(T0, T1, "signal_disconnected")
    assert "(signal_disconnected)" in mapping.gap_statement(gap)
