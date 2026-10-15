"""P1 and spike S4: the log lives in the krita-provenance.json annotation and never loses events."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from krita_provenance import log, storage
from krita_provenance.log import CaptureGap, IngredientAdded, ProvenanceLog
from krita_provenance.storage import StorageError

T0 = datetime(2026, 11, 2, 18, 4, 11, tzinfo=timezone.utc)


def test_round_trip_through_annotation_bytes():
    entry = storage.start(None, T0)
    entry.append(CaptureGap(T0, T0, log.PLUGIN_OFF))
    data = storage.serialize(entry)
    assert data.startswith(b'{"schema": "krita-provenance/1"')
    assert storage.read(data) == entry
    assert storage.read(None) is None and storage.read(b"") is None


def test_an_unreadable_log_is_an_error_not_a_fresh_start():
    with pytest.raises(StorageError, match="cannot be read"):
        storage.read(b'{"schema": "krita-provenance/9"}')
    with pytest.raises(StorageError):
        storage.read(b"not json")


@pytest.mark.parametrize(
    ("file_name", "reason", "parent"),
    [
        (None, log.NEW_DOCUMENT, None),
        ("", log.NEW_DOCUMENT, None),
        ("/home/kable/photos/harbour.jpg", log.OPENED_IMAGE, "harbour.jpg"),
        ("C:\\Users\\Kable\\Art\\harbour v3.kra", log.EXISTING_DOCUMENT, "harbour v3.kra"),
        ("sketch.ORA", log.EXISTING_DOCUMENT, "sketch.ORA"),
    ],
)
def test_start_reason_follows_the_file_name(file_name, reason, parent):
    entry = storage.start(file_name, T0)
    assert entry.started.reason == reason and entry.started.at == T0
    if parent is None:
        assert entry.events == []
    else:
        assert entry.events == [IngredientAdded(T0, log.PARENT_OF, parent)]
    assert len(entry.document_id) == 36


def test_summary_names_the_document_and_counts_events():
    entry = storage.start("harbour.kra", T0)
    entry.append(CaptureGap(T0, T0, log.PLUGIN_OFF))
    lines = storage.summary(entry).splitlines()
    assert lines[0] == f"Document id: {entry.document_id}"
    assert lines[1] == "Log started: 2026-11-02T18:04:11Z (existing_document)"
    assert lines[2] == "Events: 1 capture_gap, 1 ingredient_added"
    assert lines[3].startswith(
        "No AI generation observed by this plugin since 2 November 2026 (UTC)."
    )


def test_append_keeps_every_past_event_and_adds_one_at_the_end():
    entry = storage.start("harbour.png", T0)
    entry.append(log.UnknownEvent("from_a_newer_version", {"type": "from_a_newer_version", "x": 1}))
    gap = CaptureGap(T0, T0, log.ADAPTER_ERROR)
    after = storage.read(storage.append(storage.serialize(entry), "ignored.kra", gap))
    assert after is not None
    assert after.document_id == entry.document_id and after.started == entry.started
    assert after.events == [*entry.events, gap]


def test_append_starts_a_log_from_the_file_name_when_there_is_none():
    gap = CaptureGap(T0, T0, log.ADAPTER_ERROR)
    for annotation in (None, b""):
        after = storage.read(storage.append(annotation, "C:\\Art\\old.kra", gap))
        assert after is not None and after.started.reason == log.EXISTING_DOCUMENT
        assert after.events == [IngredientAdded(after.started.at, log.PARENT_OF, "old.kra"), gap]


def test_append_never_replaces_a_log_it_cannot_read():
    """Starting afresh would drop the history this version can't read (P3 migrates in v1.0)."""
    for unreadable in (b'{"schema": "krita-provenance/9", "events": []}', b"{not json"):
        with pytest.raises(StorageError, match="cannot be read"):
            storage.append(unreadable, None, CaptureGap(T0, T0, log.ADAPTER_ERROR))


def test_new_log_is_a_fresh_document_with_no_events():
    assert isinstance(storage.start(None), ProvenanceLog)
