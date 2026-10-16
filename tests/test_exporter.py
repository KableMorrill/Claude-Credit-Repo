"""E1, E2, E4 to E6: the export after Krita has rendered, signed for real with the test keys.

A signed export reads back acceptable under the chosen name. Anything that stops the signature
leaves the chosen name alone and saves a file named -unsigned (E6).
"""

from __future__ import annotations

import dataclasses
import shutil
from pathlib import Path

import pytest

from goldens import comparable, load_case, read_actions
from krita_provenance import exporter, mapping, sdk
from krita_provenance.exporter import ExportError
from krita_provenance.log import ProvenanceLog
from krita_provenance.sdk import SignerConfig

FIXTURES = Path(__file__).parent / "fixtures"
CERTS = FIXTURES / "certs"
IMAGES = FIXTURES / "images"


@pytest.fixture
def signer() -> SignerConfig:
    return SignerConfig((CERTS / "chain.pem").read_bytes(), (CERTS / "signer.key").read_bytes())


def rendered(tmp_path: Path, suffix: str = ".png") -> Path:
    """What Krita's exportImage would have written to the temporary folder."""
    path = tmp_path / "render" / f"render{suffix}"
    path.parent.mkdir()
    shutil.copyfile(IMAGES / ("blank.png" if suffix == ".png" else "blank.jpg"), path)
    return path


def test_a_new_documents_export_is_signed_under_the_chosen_name(tmp_path: Path, signer):
    entry, details, identity = load_case("new_document")
    destination = tmp_path / "out" / "harbour.png"
    destination.parent.mkdir()
    outcome = exporter.export(
        entry, rendered(tmp_path), destination, details, signer, None, identity=identity
    )
    assert outcome.signed and outcome.path == destination and outcome.reason is None
    assert outcome.validation is not None and outcome.validation.is_acceptable()
    assert outcome.event.manifest_label == outcome.manifest_label is not None
    assert (outcome.event.format, outcome.event.disclosure) == ("png", "standard")
    result = sdk.read_file(destination)
    assert result is not None and result.manifest_label == outcome.manifest_label
    expected = mapping.manifest(entry, details, identity).definition["assertions"][0]
    assert comparable(read_actions(result)) == comparable(expected["data"]["actions"])
    assert list(destination.parent.iterdir()) == [destination]  # no -unsigned file beside it


def test_jpeg_is_signed_too(tmp_path: Path, signer):
    entry, details, _ = load_case("new_document")
    details = dataclasses.replace(details, format="jpeg", title="harbour.jpg")
    destination = tmp_path / "harbour.jpg"
    outcome = exporter.export(entry, rendered(tmp_path, ".jpg"), destination, details, signer, None)
    assert outcome.signed and sdk.read_file(destination) is not None


def test_an_older_document_is_signed_with_its_saved_kra_as_the_parent(tmp_path: Path, signer):
    entry, details, identity = load_case("existing_document")
    destination = tmp_path / "harbour v3 final.png"
    kra = (IMAGES / "blank.kra").read_bytes()
    outcome = exporter.export(
        entry, rendered(tmp_path), destination, details, signer, kra, identity=identity
    )
    assert outcome.signed
    result = sdk.read_file(destination)
    assert result is not None and "harbour v3.kra" in result.manifest_text


@pytest.mark.parametrize(
    ("case", "parent", "reason"),
    [
        ("started_with_content", False, "close it and open it again"),  # decision 2, 16 Oct
        ("existing_document", False, "isn't available to sign with"),
        ("opened_image", True, "placed files can't be signed into an export yet"),
    ],
)
def test_what_cant_be_signed_honestly_is_saved_unsigned(tmp_path, signer, case, parent, reason):
    entry, details, identity = load_case(case)
    if case == "started_with_content":  # before it was reopened from its saved .kra
        entry = ProvenanceLog(entry.document_id, entry.started, list(entry.events[:1]))
    destination = tmp_path / "out.png"
    data = (IMAGES / "blank.jpg").read_bytes() if parent else None
    outcome = exporter.export(
        entry, rendered(tmp_path), destination, details, signer, data, identity=identity
    )
    assert not outcome.signed and reason in (outcome.reason or "")
    assert outcome.path == tmp_path / "out-unsigned.png" and outcome.path.is_file()
    assert not destination.exists()
    assert sdk.read_file(outcome.path) is None  # it carries no manifest: nothing looks signed
    assert outcome.event.manifest_label is None


def test_a_signing_failure_leaves_the_chosen_name_alone(tmp_path: Path):
    entry, details, _ = load_case("new_document")
    wrong = SignerConfig((CERTS / "chain.pem").read_bytes(), (CERTS / "root.key").read_bytes())
    destination = tmp_path / "harbour.png"
    destination.write_bytes(b"an earlier export")
    outcome = exporter.export(entry, rendered(tmp_path), destination, details, wrong, None)
    assert not outcome.signed and outcome.path.name == "harbour-unsigned.png"
    assert destination.read_bytes() == b"an earlier export"  # left as it was (E6)
    assert "Nothing was written under the name you chose" in outcome.to_text(entry)


def test_no_signer_or_the_users_choice_means_unsigned(tmp_path: Path, signer):
    entry, details, _ = load_case("nothing_observed")
    image = rendered(tmp_path)
    outcome = exporter.export(entry, image, tmp_path / "a.png", details, None, None)
    assert not outcome.signed and outcome.reason == "signing isn't set up yet"
    chosen = exporter.export(
        entry, image, tmp_path / "b.png", details, signer, None, unsigned_reason="you chose it"
    )
    assert not chosen.signed and chosen.reason == "you chose it"
    assert chosen.path.read_bytes() == image.read_bytes()


def test_an_unsigned_name_never_overwrites_an_earlier_file():
    taken = {Path("art/harbour-unsigned.png"), Path("art/harbour-unsigned-2.png")}
    assert exporter.unsigned_path(Path("art/harbour.png"), lambda p: p in taken) == Path(
        "art/harbour-unsigned-3.png"
    )
    assert exporter.unsigned_path(Path("x.jpg"), lambda p: False) == Path("x-unsigned.jpg")


def test_the_format_follows_the_chosen_file_name():
    assert exporter.format_for(Path("a.PNG")) == "png"
    assert exporter.format_for(Path("a.jpeg")) == exporter.format_for(Path("a.jpg")) == "jpeg"
    with pytest.raises(ExportError, match="choose a file name ending in"):
        exporter.format_for(Path("a.webp"))


def test_the_report_says_what_was_signed_and_what_was_observed(tmp_path: Path, signer):
    entry, details, _ = load_case("nothing_observed")
    outcome = exporter.export(entry, rendered(tmp_path), tmp_path / "a.png", details, signer, None)
    text = outcome.to_text(entry)
    assert text.startswith(f"Saved and signed: {tmp_path / 'a.png'}")
    assert "untrusted, as expected for a local certificate" in text
    assert "No AI generation observed by this plugin since" in text
