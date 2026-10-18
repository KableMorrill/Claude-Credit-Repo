"""The provenance log is append-only, versioned JSON (spec: Provenance log, P1)."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from krita_provenance import log
from krita_provenance.log import (
    SCHEMA,
    CaptureGap,
    Exported,
    GenerationApplied,
    IngredientAdded,
    Job,
    Layer,
    LayerChanged,
    LogFormatError,
    LogStart,
    Model,
    Prompt,
    ProvenanceLog,
    ReadBack,
    Source,
    UnknownEvent,
)

GOLDENS = Path(__file__).parent / "fixtures" / "goldens"
UTC = timezone.utc
T0 = datetime(2026, 11, 2, 18, 4, 11, tzinfo=UTC)


def minutes(n: int) -> datetime:
    return T0 + timedelta(minutes=n)


SKY = Layer("{sky}", "Sky")


def generation(at: datetime, layer: Layer | None = SKY) -> GenerationApplied:
    return GenerationApplied(
        at=at,
        source=Source(log.AI_DIFFUSION, "1.53.0", "local"),
        job=Job("job-1", "diffusion", "inpaint", 2, (512, 128, 768, 640), True, 1234567),
        model=Model(checkpoint="dreamshaper_8.safetensors", steps=24, guidance=4.5, strength=0.65),
        prompt=Prompt("stormy sky over a harbour", "text, watermark"),
        target_layer=layer,
    )


def full_log() -> ProvenanceLog:
    entry = ProvenanceLog("doc-1", LogStart(T0, log.NEW_DOCUMENT))
    entry.append(generation(minutes(16)))
    entry.append(
        LayerChanged(minutes(27), Layer("{line}", "Lineart"), (0, 0, 2048, 2048), "sha256:ab")
    )
    entry.append(
        IngredientAdded(
            minutes(31),
            log.COMPONENT_OF,
            "background.png",
            store="krita-provenance/ingredient-1",
            validation="valid",
            layer=Layer("{bg}", "background.png"),
            bounds=(0, 1024, 2048, 1024),
        )
    )
    entry.append(CaptureGap(minutes(36), minutes(48), log.ADAPTER_ERROR))
    read_back = ReadBack("Valid", ("signingCredential.untrusted",))
    entry.append(Exported(minutes(56), "png", "standard", "urn:c2pa:0f6e1d2c", read_back))
    entry.append(Exported(minutes(57), "jpeg", "standard", None))
    return entry


def test_round_trips_every_event_type():
    original = full_log()
    restored = ProvenanceLog.from_json(original.to_json())
    assert restored == original
    assert [log.event_type(e) for e in restored.events] == [
        "generation_applied",
        "layer_changed",
        "ingredient_added",
        "capture_gap",
        "exported",
        "exported",
    ]


def test_json_is_the_spec_format():
    data = json.loads(full_log().to_json())
    assert data["schema"] == SCHEMA == "krita-provenance/1"
    assert data["document_id"] == "doc-1"
    assert data["log_started"] == {"at": "2026-11-02T18:04:11Z", "reason": "new_document"}
    first = data["events"][0]
    assert first["type"] == "generation_applied"
    assert first["job"]["bounds"] == [512, 128, 768, 640]
    assert first["model"] == {
        "checkpoint": "dreamshaper_8.safetensors",
        "steps": 24,
        "guidance": 4.5,
        "strength": 0.65,
    }
    gap = data["events"][3]
    assert gap == {
        "type": "capture_gap",
        "from": "2026-11-02T18:40:11Z",
        "to": "2026-11-02T18:52:11Z",
        "reason": "adapter_error",
    }
    signed, unsigned = data["events"][4:]
    assert signed["read_back"] == {"state": "Valid", "failures": ["signingCredential.untrusted"]}
    assert "read_back" not in unsigned


def test_an_export_logged_before_read_back_existed_writes_back_as_it_was():
    older = {
        "type": "exported",
        "at": "2026-10-16T16:45:10Z",
        "format": "png",
        "disclosure": "standard",
        "manifest_label": "urn:c2pa:0f6e1d2c",
    }
    event = log.event_from_dict(older)
    assert isinstance(event, Exported) and event.read_back is None
    assert log.event_to_dict(event) == older


def test_the_spec_examples_export_keeps_its_read_back():
    entry = ProvenanceLog.from_json(
        (GOLDENS / "spec_example" / "log.json").read_text(encoding="utf-8")
    )
    exported = entry.events[-1]
    assert isinstance(exported, Exported)
    assert exported.read_back == ReadBack("Valid", ("signingCredential.untrusted",))


def test_reads_the_spec_example():
    entry = ProvenanceLog.from_json(
        (GOLDENS / "spec_example" / "log.json").read_text(encoding="utf-8")
    )
    assert entry.started == LogStart(T0, log.NEW_DOCUMENT)
    kinds = [type(e) for e in entry.events]
    assert kinds == [GenerationApplied, LayerChanged, IngredientAdded, CaptureGap, Exported]
    first = entry.events[0]
    assert isinstance(first, GenerationApplied)
    assert first.job.bounds == (512, 128, 768, 640)
    assert first.model.style == "Cinematic Photo"
    assert first.target_layer == Layer("{3c1f9b7e-2d44-4a0b-9c6e-1f2a3b4c5d6e}", "Sky")


def test_unknown_events_survive_a_round_trip():
    """A log written by a newer plugin must not lose events in an older one."""
    data = json.loads(full_log().to_json())
    data["events"].append({
        "type": "stroke_recorded",
        "at": "2026-11-02T19:10:00Z",
        "pressure": [0.1, 0.9],
    })
    restored = ProvenanceLog.from_json(json.dumps(data))
    assert restored.events[-1] == UnknownEvent(
        "stroke_recorded",
        {"type": "stroke_recorded", "at": "2026-11-02T19:10:00Z", "pressure": [0.1, 0.9]},
    )
    assert json.loads(restored.to_json()) == data


def test_append_only_adds_at_the_end():
    entry = ProvenanceLog("doc-1", LogStart(T0, log.NEW_DOCUMENT))
    before = ProvenanceLog.from_json(full_log().to_json()).events
    for event in before:
        entry.append(event)
    entry.append(CaptureGap(minutes(60), minutes(61), log.PLUGIN_OFF))
    assert entry.events[: len(before)] == before
    assert len(entry.events) == len(before) + 1


def test_new_log_gets_a_uuid_and_a_start():
    entry = ProvenanceLog.new(log.OPENED_IMAGE, at=T0)
    assert len(entry.document_id) == 36
    assert entry.started == LogStart(T0, log.OPENED_IMAGE)
    assert entry.events == []
    assert ProvenanceLog.new(log.NEW_DOCUMENT).started.at.tzinfo is not None


def test_times_are_written_in_utc_with_a_z():
    local = datetime(2026, 11, 2, 20, 4, 11, tzinfo=timezone(timedelta(hours=2)))
    assert log.format_time(local) == "2026-11-02T18:04:11Z"
    assert log.parse_time("2026-11-02T18:04:11Z") == T0
    assert log.parse_time("2026-11-02T20:04:11+02:00") == T0
    with pytest.raises(ValueError, match="timezone"):
        log.format_time(datetime(2026, 11, 2))
    with pytest.raises(ValueError, match="timezone"):
        log.parse_time("2026-11-02T18:04:11")


def test_unknown_layer_is_kept_as_none():
    entry = ProvenanceLog("doc-1", LogStart(T0, log.NEW_DOCUMENT), [generation(minutes(1), None)])
    data = json.loads(entry.to_json())
    assert data["events"][0]["target_layer"] is None
    assert ProvenanceLog.from_json(entry.to_json()) == entry


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda d: d.update(schema="krita-provenance/2"), "schema 'krita-provenance/2'"),
        (lambda d: d.pop("schema"), "schema None"),
        (lambda d: d.pop("log_started"), "malformed"),
        (lambda d: d["events"][0]["job"].update(bounds=[1, 2, 3]), "four integers"),
        (lambda d: d["events"][0]["job"].update(bounds="1,2,3,4"), "four integers"),
        (lambda d: d["events"][0]["job"].update(seed="1234"), "integer"),
        (lambda d: d["events"][0].pop("at"), "malformed"),
        (lambda d: d["events"].append("exported"), "JSON object"),
        (lambda d: d["events"][4].update(read_back="Valid"), "must be an object"),
        (lambda d: d["events"][4]["read_back"].update(failures="none"), "must be a list"),
        (lambda d: d["events"][4]["read_back"].update(state=1), "string"),
    ],
)
def test_rejects_logs_it_cannot_read(change, message):
    data = json.loads(full_log().to_json())
    change(data)
    with pytest.raises(LogFormatError, match=message):
        ProvenanceLog.from_json(json.dumps(data))


def test_rejects_non_json():
    with pytest.raises(LogFormatError, match="not valid JSON"):
        ProvenanceLog.from_json("{")
    with pytest.raises(LogFormatError, match="JSON object"):
        ProvenanceLog.from_json("[]")
