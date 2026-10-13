"""K1: a local root CA and signing certificate made with cryptography, in the profile c2pa-rs accepts."""

from __future__ import annotations

import json
import shutil
import stat
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from goldens import load_case
from krita_provenance import keys, mapping, sdk
from krita_provenance.keys import KeysError, KeysUnavailable

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 10, 13, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def generated() -> keys.GeneratedKeys:
    return keys.generate(now=NOW)


def load(pem: bytes) -> x509.Certificate:
    return x509.load_pem_x509_certificate(pem)


def common_name(name: x509.Name) -> str:
    value = name.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value
    assert isinstance(value, str)
    return value


def test_root_is_a_ca_that_can_only_sign_certificates(generated: keys.GeneratedKeys):
    root = load(generated.root_certificate)
    assert common_name(root.subject) == keys.ROOT_COMMON_NAME
    assert root.issuer == root.subject
    constraints = root.extensions.get_extension_for_class(x509.BasicConstraints)
    assert constraints.critical and constraints.value.ca and constraints.value.path_length == 0
    usage = root.extensions.get_extension_for_class(x509.KeyUsage)
    assert usage.critical
    assert usage.value.key_cert_sign and usage.value.crl_sign and not usage.value.digital_signature
    root.extensions.get_extension_for_class(x509.SubjectKeyIdentifier)
    assert root.not_valid_before_utc == NOW - keys.BACKDATE
    assert root.not_valid_after_utc == NOW - keys.BACKDATE + keys.ROOT_VALIDITY


def test_signer_has_the_profile_c2pa_requires(generated: keys.GeneratedKeys):
    root, signer = load(generated.root_certificate), load(generated.certificate)
    assert common_name(signer.subject) == keys.DEFAULT_COMMON_NAME
    assert signer.issuer == root.subject
    constraints = signer.extensions.get_extension_for_class(x509.BasicConstraints)
    assert constraints.critical and not constraints.value.ca
    usage = signer.extensions.get_extension_for_class(x509.KeyUsage)
    assert usage.critical and usage.value.digital_signature and not usage.value.key_cert_sign
    extended = signer.extensions.get_extension_for_class(x509.ExtendedKeyUsage)
    assert list(extended.value) == [ExtendedKeyUsageOID.EMAIL_PROTECTION]
    authority = signer.extensions.get_extension_for_class(x509.AuthorityKeyIdentifier).value
    subject_key = root.extensions.get_extension_for_class(x509.SubjectKeyIdentifier).value
    assert authority.key_identifier == subject_key.digest
    assert signer.not_valid_before_utc == NOW - keys.BACKDATE
    assert signer.not_valid_after_utc == NOW - keys.BACKDATE + keys.SIGNER_VALIDITY
    assert signer.not_valid_after_utc < root.not_valid_after_utc


def test_keys_are_p256_and_the_chain_verifies(generated: keys.GeneratedKeys):
    root, signer = load(generated.root_certificate), load(generated.certificate)
    for certificate in (root, signer):
        public_key = certificate.public_key()
        assert isinstance(public_key, ec.EllipticCurvePublicKey)
        assert isinstance(public_key.curve, ec.SECP256R1)
    root_key = root.public_key()
    assert isinstance(root_key, ec.EllipticCurvePublicKey)
    root_key.verify(signer.signature, signer.tbs_certificate_bytes, ec.ECDSA(hashes.SHA256()))
    assert generated.chain == generated.certificate + generated.root_certificate
    assert b"BEGIN PRIVATE KEY" in generated.key and b"ENCRYPTED" not in generated.key


def test_two_generations_differ(generated: keys.GeneratedKeys):
    other = keys.generate(now=NOW)
    assert other.key != generated.key
    assert load(other.certificate).serial_number != load(generated.certificate).serial_number


def test_the_generated_signer_signs_and_validates(generated: keys.GeneratedKeys, tmp_path: Path):
    """The whole point of K1: c2pa-rs accepts the certificate; only the trust list is missing."""
    entry, export, identity = load_case("nothing_observed")
    manifest = mapping.manifest(entry, export, identity)
    source = tmp_path / "export.png"
    shutil.copyfile(FIXTURES / "images" / "blank.png", source)
    result = sdk.sign_file(
        source, tmp_path / "signed.png", manifest, sdk.SignerConfig(generated.chain, generated.key)
    )
    assert result.validation.is_acceptable()
    assert result.validation.failures == (sdk.UNTRUSTED_SIGNER,)
    signature = json.loads(result.manifest_text)["manifests"][result.manifest_label][
        "signature_info"
    ]
    assert signature["common_name"] == keys.DEFAULT_COMMON_NAME
    assert signature["issuer"] == keys.ORGANIZATION
    assert signature["alg"] == "Es256"


def test_the_subject_names_no_one(generated: keys.GeneratedKeys):
    """D1: the subject is in every manifest's signature_info."""
    text = load(generated.certificate).subject.rfc4514_string().lower()
    for forbidden in ("kable", "user", "@", "\\", "/"):
        assert forbidden not in text
    with pytest.raises(KeysError, match="needs a name"):
        keys.generate("   ")


def test_a_custom_name_is_used_as_typed():
    signer = load(keys.generate("Studio Nine", now=NOW).certificate)
    assert common_name(signer.subject) == "Studio Nine"


def test_describe_and_fingerprint(generated: keys.GeneratedKeys):
    info = keys.describe(generated.certificate)
    assert info.common_name == keys.DEFAULT_COMMON_NAME
    assert info.issuer == keys.ROOT_COMMON_NAME
    assert info.algorithm == "es256"
    assert (
        info.not_before == NOW - keys.BACKDATE
        and info.not_after == info.not_before + keys.SIGNER_VALIDITY
    )
    assert int(info.serial, 16) == load(generated.certificate).serial_number
    expected = load(generated.certificate).fingerprint(hashes.SHA256()).hex(":").upper()
    assert info.fingerprint == expected == keys.fingerprint(generated.certificate)
    assert len(info.fingerprint) == 95 and info.fingerprint.count(":") == 31
    lines = info.to_text().splitlines()
    assert lines[0] == f"Signer: {keys.DEFAULT_COMMON_NAME}"
    assert lines[4] == f"SHA-256 fingerprint: {expected}"
    assert "unrecognized" in lines[5]
    with pytest.raises(KeysError, match="Not a PEM certificate"):
        keys.describe(b"not a certificate")


def test_ensure_makes_the_keys_once_and_keeps_them_private(tmp_path: Path):
    folder = tmp_path / "keys"
    files = keys.ensure(folder)
    assert files.exists()
    assert sorted(path.name for path in folder.iterdir()) == sorted(keys.FILES)
    if sys.platform != "win32":
        assert stat.S_IMODE(files.key.stat().st_mode) == 0o600
    assert not (folder / "root-ca.key").exists()  # discarded after issuing (decision 1)
    before = {path.name: path.read_bytes() for path in files.paths}
    again = keys.ensure(folder)
    assert again == files
    assert {path.name: path.read_bytes() for path in again.paths} == before  # never regenerated


def test_ensure_refuses_to_touch_a_partial_key_folder(tmp_path: Path):
    folder = tmp_path / "keys"
    folder.mkdir()
    (folder / keys.KEY).write_bytes(b"old key")
    with pytest.raises(KeysError, match="move them aside") as error:
        keys.ensure(folder)
    assert str(folder) not in str(error.value)
    assert (folder / keys.KEY).read_bytes() == b"old key"
    assert not (folder / keys.CERTIFICATE).exists()


def test_key_files_give_the_signer_config_and_info(tmp_path: Path):
    files = keys.ensure(tmp_path / "keys")
    config = files.signer_config()
    assert (
        config.certificate_chain
        == files.certificate.read_bytes() + files.root_certificate.read_bytes()
    )
    assert config.private_key == files.key.read_bytes()
    assert config.algorithm == "es256" and config.timestamp_url is None
    assert files.info().common_name == keys.DEFAULT_COMMON_NAME
    with pytest.raises(KeysError, match="No signing keys") as error:
        keys.KeyFiles(tmp_path / "nowhere").signer_config()
    assert str(tmp_path) not in str(error.value)  # a path would name the user (D1)


def test_without_cryptography_the_error_is_clear(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """As in a Krita before the wheel arrived: no cryptography module cached anywhere."""
    for name in [
        name for name in sys.modules if name == "cryptography" or name.startswith("cryptography.")
    ]:
        monkeypatch.delitem(sys.modules, name)
    monkeypatch.setitem(sys.modules, "cryptography", None)
    assert not keys.available()
    with pytest.raises(KeysUnavailable, match="cryptography package is not installed"):
        keys.generate()
    with pytest.raises(KeysUnavailable):
        keys.describe(b"")
    with pytest.raises(KeysUnavailable):
        keys.fingerprint(b"")
    with pytest.raises(KeysUnavailable):
        keys.ensure(tmp_path / "keys")
    assert not (tmp_path / "keys").exists()


def test_files_hold_exactly_the_generated_bytes_and_sign(
    tmp_path: Path, generated: keys.GeneratedKeys, monkeypatch
):
    """The on-disk path the glue uses: no text-mode rewriting on Windows, and the files sign."""
    monkeypatch.setattr(
        keys, "generate", lambda common_name=keys.DEFAULT_COMMON_NAME, now=None: generated
    )
    files = keys.ensure(tmp_path / "keys")
    assert files.key.read_bytes() == generated.key
    assert files.certificate.read_bytes() == generated.certificate
    assert files.root_certificate.read_bytes() == generated.root_certificate
    assert b"\r" not in files.key.read_bytes()
    entry, export, identity = load_case("nothing_observed")
    manifest = mapping.manifest(entry, export, identity)
    source = tmp_path / "export.png"
    shutil.copyfile(FIXTURES / "images" / "blank.png", source)
    result = sdk.sign_file(source, tmp_path / "signed.png", manifest, files.signer_config())
    assert result.validation.failures == (sdk.UNTRUSTED_SIGNER,)


def test_cryptography_is_available_here():
    assert keys.available()
    assert keys.SIGNER_VALIDITY < keys.ROOT_VALIDITY
    assert keys.BACKDATE == timedelta(days=1)  # public CAs backdate for slow clocks too
