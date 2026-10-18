"""Golden manifests are signed and read back; the only allowed warning is an untrusted signer.

Spec: Testing and validation; E4, E5, E6. These tests need c2pa-python, a dev dependency.
"""

from __future__ import annotations

import json
import stat
import sys
from pathlib import Path

import pytest

from goldens import CASES, GOLDENS, comparable, load_case, read_actions
from krita_provenance import mapping, sdk
from krita_provenance.mapping import Manifest
from krita_provenance.sdk import IngredientData, ReadError, SignerConfig, SigningError

FIXTURES = Path(__file__).parent / "fixtures"
CERTS = FIXTURES / "certs"
IMAGES = {
    "image/png": FIXTURES / "images" / "blank.png",
    "image/jpeg": FIXTURES / "images" / "blank.jpg",
    mapping.KRITA_DOCUMENT: FIXTURES / "images" / "blank.kra",  # a .kra-shaped zip
}
SIGNER = SignerConfig((CERTS / "chain.pem").read_bytes(), (CERTS / "signer.key").read_bytes())
SELF_SIGNED = SignerConfig((CERTS / "root.pem").read_bytes(), (CERTS / "root.key").read_bytes())


def golden(name: str) -> tuple[Manifest, str]:
    entry, export, identity = load_case(name)
    return mapping.manifest(entry, export, identity, plugin_version="0.1.0"), export.format


def ingredient_data(manifest: Manifest) -> list[IngredientData]:
    """Stand-in bytes for each ingredient: a blank image in the ingredient's format."""
    return [
        IngredientData(i.definition["instance_id"], IMAGES[i.format or "image/png"].read_bytes())
        for i in manifest.ingredients
    ]


def source_for(format_: str, tmp_path: Path) -> Path:
    source = tmp_path / f"export.{format_}"
    source.write_bytes(IMAGES[mapping.FORMATS[format_]].read_bytes())
    return source


@pytest.mark.parametrize("name", CASES)
def test_golden_manifests_sign_and_read_back(name: str, tmp_path: Path):
    manifest, format_ = golden(name)
    destination = tmp_path / f"signed.{format_}"
    result = sdk.sign_file(
        source_for(format_, tmp_path), destination, manifest, SIGNER, ingredient_data(manifest)
    )

    assert destination.is_file()
    assert result.validation.state == "Valid"
    assert result.validation.failures == (sdk.UNTRUSTED_SIGNER,)  # the only allowed warning
    assert set(result.validation.informational) <= sdk.ACCEPTED_INFORMATIONAL
    assert result.validation.is_acceptable() and result.validation.signer_untrusted
    assert result.manifest_label.startswith("urn:c2pa:")
    assert not result.truncated

    expected = json.loads((GOLDENS / name / "manifest.json").read_text(encoding="utf-8"))
    assert comparable(read_actions(result)) == comparable(
        expected["definition"]["assertions"][0]["data"]["actions"]
    )
    again = sdk.read_file(destination)
    assert again is not None and again.manifest_label == result.manifest_label
    data = json.loads(again.manifest_text)
    active = data["manifests"][again.manifest_label]
    assert active["title"] == expected["definition"]["title"]
    generator = active["claim_generator_info"][0]
    assert (generator["name"], generator["version"]) == ("krita-provenance", "0.1.0")
    assert [i["relationship"] for i in active.get("ingredients", [])] == [
        i.definition["relationship"] for i in manifest.ingredients
    ]
    assert [i["title"] for i in active.get("ingredients", [])] == [
        i.definition["title"] for i in manifest.ingredients
    ]
    assert [i["format"] for i in active.get("ingredients", [])] == [
        i.format for i in manifest.ingredients
    ]
    assert next(a["action"] for a in read_actions(result)) in ("c2pa.created", "c2pa.opened")
    assert (
        sdk.data_hash_exclusions(destination) and len(sdk.data_hash_exclusions(destination)) == 1
    )  # E5


def test_the_signed_file_has_the_source_file_permissions(tmp_path: Path):
    """mkstemp makes a private file; an export must be as readable as any other export."""
    manifest, format_ = golden("nothing_observed")
    source = source_for(format_, tmp_path)
    source.chmod(0o644)
    destination = tmp_path / "signed.png"
    sdk.sign_file(source, destination, manifest, SIGNER)
    assert stat.S_IMODE(destination.stat().st_mode) == stat.S_IMODE(source.stat().st_mode)


def test_jpeg_and_png_both_sign(tmp_path: Path):
    manifest, _ = golden("nothing_observed")
    for format_ in ("png", "jpeg"):
        result = sdk.sign_file(
            source_for(format_, tmp_path), tmp_path / f"out.{format_}", manifest, SIGNER
        )
        assert result.validation.is_acceptable()


def test_refuses_a_definition_that_brings_its_own_hash_assertion(tmp_path: Path):
    """E5: hash exclusions come from the SDK alone."""
    manifest, format_ = golden("nothing_observed")
    definition = json.loads(json.dumps(manifest.definition))
    definition["assertions"].append({
        "label": "c2pa.hash.data",
        "data": {"exclusions": [{"start": 0, "length": 50}]},
    })
    tampered = Manifest(definition, manifest.intent, manifest.source_type)
    destination = tmp_path / "signed.png"
    with pytest.raises(SigningError, match=r"c2pa\.hash\.data"):
        sdk.sign_file(source_for(format_, tmp_path), destination, tampered, SIGNER)
    assert not destination.exists()


def test_a_failed_signature_leaves_nothing_behind(tmp_path: Path):
    """E6: never a file that looks signed."""
    manifest, format_ = golden("nothing_observed")
    source = source_for(format_, tmp_path)
    destination = tmp_path / "out" / "signed.png"
    with pytest.raises(SigningError, match="self-signed"):
        sdk.sign_file(source, destination, manifest, SELF_SIGNED)  # c2pa-rs refuses a bare CA cert
    assert not destination.exists()
    assert list(destination.parent.iterdir()) == []  # no temporary file either

    broken_key = SignerConfig(
        SIGNER.certificate_chain, b"-----BEGIN PRIVATE KEY-----\nnope\n-----END PRIVATE KEY-----\n"
    )
    with pytest.raises(SigningError):
        sdk.sign_file(source, destination, manifest, broken_key)
    assert not destination.exists()


def test_a_signed_file_that_cannot_be_moved_into_place_leaves_nothing_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """E6: on Windows another program can hold the destination open. Without this, the signed
    temporary file stayed beside it and no unsigned copy was saved (review, 18 Oct 2026)."""
    manifest, format_ = golden("nothing_observed")
    source = source_for(format_, tmp_path)
    destination = tmp_path / "out" / "signed.png"

    def locked(source: object, target: object) -> None:
        raise PermissionError(13, "The process cannot access the file", str(target))

    monkeypatch.setattr(sdk.os, "replace", locked)
    with pytest.raises(SigningError, match=r"couldn't be saved as signed\.png"):
        sdk.sign_file(source, destination, manifest, SIGNER)
    assert list(destination.parent.iterdir()) == []


def test_a_failed_signature_keeps_the_previous_file(tmp_path: Path):
    manifest, format_ = golden("nothing_observed")
    destination = tmp_path / "signed.png"
    destination.write_bytes(b"previous export")
    with pytest.raises(SigningError):
        sdk.sign_file(source_for(format_, tmp_path), destination, manifest, SELF_SIGNED)
    assert destination.read_bytes() == b"previous export"


def test_an_opened_image_needs_its_parent_bytes(tmp_path: Path):
    manifest, format_ = golden("opened_image")
    destination = tmp_path / "signed.jpg"
    with pytest.raises(SigningError, match=r"No data for ingredient 'harbour\.jpg'"):
        sdk.sign_file(source_for(format_, tmp_path), destination, manifest, SIGNER)
    assert not destination.exists()


def test_only_png_and_jpeg_are_signed(tmp_path: Path):
    manifest, _ = golden("nothing_observed")
    source = tmp_path / "export.webp"
    source.write_bytes(b"RIFF")
    with pytest.raises(SigningError, match="not a PNG or JPEG"):
        sdk.sign_file(source, tmp_path / "signed.webp", manifest, SIGNER)


def test_unsigned_name_labels_the_fallback_file():
    assert sdk.unsigned_name(Path("/art/harbour.png")) == Path("/art/harbour-unsigned.png")
    assert sdk.unsigned_name(Path("harbour.final.jpg")) == Path("harbour.final-unsigned.jpg")


def test_reading_a_file_without_a_manifest_gives_none():
    assert sdk.read_file(IMAGES["image/png"]) is None
    assert sdk.read_file(IMAGES["image/jpeg"]) is None


def test_reading_refuses_what_is_not_an_asset_or_too_large(tmp_path: Path):
    with pytest.raises(ReadError, match="Could not read"):
        sdk.read_file(CERTS / "root.pem")
    with pytest.raises(ReadError, match="over 10 bytes"):
        sdk.read_file(IMAGES["image/png"], max_bytes=10)


def test_manifest_text_is_capped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    manifest, format_ = golden("nothing_observed")
    destination = tmp_path / "signed.png"
    sdk.sign_file(source_for(format_, tmp_path), destination, manifest, SIGNER)
    monkeypatch.setattr(sdk, "MAX_MANIFEST_TEXT", 100)
    result = sdk.read_file(destination)
    assert result is not None and result.truncated and len(result.manifest_text) == 100


def test_reports_the_sdk_version():
    assert sdk.available()
    python_version, rust_version = sdk.version()
    assert python_version.startswith("0.38.")
    assert rust_version.startswith("0.91.")
    assert sdk.version_problem() is None


def test_without_the_sdk_the_error_is_clear(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setitem(sys.modules, "c2pa", None)  # makes "import c2pa" raise ImportError
    assert not sdk.available()
    manifest, format_ = golden("nothing_observed")
    with pytest.raises(sdk.SdkMissing, match="c2pa-python is not installed"):
        sdk.sign_file(source_for(format_, tmp_path), tmp_path / "signed.png", manifest, SIGNER)
    with pytest.raises(sdk.SdkMissing):
        sdk.read_file(IMAGES["image/png"])


def test_version_problem_names_an_unexpected_sdk(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(sdk, "version", lambda: ("0.40.0", "0.95.0"))
    assert sdk.version_problem() == "c2pa-python 0.40.0 is installed; this plugin needs 0.38.x"
