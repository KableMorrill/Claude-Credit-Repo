"""Local signing keys (K1): a root CA and an end-entity certificate, made on first run.

Made with the ``cryptography`` package (decision 1 of 13 Oct 2026), imported lazily because it
arrives at first run with c2pa-python (I2). Nothing here imports Krita.

The certificate profile is the one c2pa-rs accepts, checked by signing (tests/test_keys.py) and
mirrored from ``scripts/make_test_certs.py``:

- Root CA: ECDSA P-256, ``basicConstraints CA:TRUE`` and ``keyUsage keyCertSign, cRLSign``, both
  critical, a subject key identifier, 20 years.
- Signer: ECDSA P-256 (``es256``), ``basicConstraints CA:FALSE`` (critical), ``keyUsage
  digitalSignature`` (critical), ``extendedKeyUsage emailProtection``, subject and authority key
  identifiers, 10 years. c2pa-rs refuses a self-signed end-entity certificate, hence the CA.

Validity is long because there is no timestamp authority yet (K3, Q4): without one a signature
is only as valid as its certificate is at the moment someone checks it. K4 adds rotation.

Storage is the data-folder option of Q5, which the spec's threat model backs with file
permissions; the keychain option is open. Four PEM files in a folder the Krita glue chooses,
the private keys unencrypted (K4 adds a passphrase) and created with mode 0600, which Windows
ignores; the folder is per-user there. The root key stays, with the same protection, so K4 can
issue a new signer certificate under the same CA; whether to keep it at all is a K4 question.

Names: the certificate's subject becomes ``signature_info.common_name`` in every manifest, so
it carries no user or machine name (D1). The default is neutral, and K2 lets users bring their
own certificate instead.

Every validator will report this signer as unrecognized or untrusted: that is what a local CA
means, and the fingerprint is how a recipient can pin it (spec: Threat model).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from .sdk import SignerConfig

if TYPE_CHECKING:
    from cryptography import x509

DEFAULT_COMMON_NAME = "krita-provenance local signer"
ROOT_COMMON_NAME = "krita-provenance local root CA"
ORGANIZATION = "krita-provenance"
ROOT_VALIDITY = timedelta(days=20 * 365)
SIGNER_VALIDITY = timedelta(days=10 * 365)
BACKDATE = timedelta(days=1)  # a validator with a slower clock must not read "not yet valid"
ALGORITHM = "es256"

ROOT_CERTIFICATE = "root-ca.pem"
ROOT_KEY = "root-ca.key"
CERTIFICATE = "signer.pem"
KEY = "signer.key"
FILES = (ROOT_CERTIFICATE, ROOT_KEY, CERTIFICATE, KEY)


class KeysError(Exception):
    """The message is safe to show to the user."""


class KeysUnavailable(KeysError):
    """The cryptography package is not installed where this Python can import it (I2)."""


@dataclass(frozen=True)
class GeneratedKeys:
    root_certificate: bytes  # PEM
    root_key: bytes  # PEM, PKCS#8, unencrypted (K4 adds a passphrase)
    certificate: bytes  # PEM
    key: bytes  # PEM, PKCS#8, unencrypted

    @property
    def chain(self) -> bytes:
        return self.certificate + self.root_certificate


@dataclass(frozen=True)
class CertificateInfo:
    common_name: str
    issuer: str
    serial: str  # hex
    not_before: datetime
    not_after: datetime
    fingerprint: str  # SHA-256 of the DER, colon-separated upper-case hex
    algorithm: str

    def to_text(self) -> str:
        return "\n".join([
            f"Signer: {self.common_name}",
            f"Issued by: {self.issuer}",
            f"Valid: {self.not_before:%Y-%m-%d} to {self.not_after:%Y-%m-%d} (UTC)",
            f"Algorithm: {self.algorithm}",
            f"SHA-256 fingerprint: {self.fingerprint}",
            (
                "Validators will report this signer as unrecognized: the certificate comes from"
                " a local root CA on this machine, not from a trust list."
            ),
        ])


@dataclass(frozen=True)
class KeyFiles:
    """The four PEM files in the keys folder."""

    folder: Path

    @property
    def root_certificate(self) -> Path:
        return self.folder / ROOT_CERTIFICATE

    @property
    def root_key(self) -> Path:
        return self.folder / ROOT_KEY

    @property
    def certificate(self) -> Path:
        return self.folder / CERTIFICATE

    @property
    def key(self) -> Path:
        return self.folder / KEY

    @property
    def paths(self) -> tuple[Path, ...]:
        return tuple(self.folder / name for name in FILES)

    def exists(self) -> bool:
        return all(path.is_file() for path in self.paths)

    def signer_config(self) -> SignerConfig:
        """What sdk.sign_file needs: the chain, signer first, and the signer's key."""
        if not self.exists():
            raise KeysError("No signing keys have been made yet")  # no path: a path names the user
        chain = self.certificate.read_bytes() + self.root_certificate.read_bytes()
        return SignerConfig(chain, self.key.read_bytes(), ALGORITHM)

    def info(self) -> CertificateInfo:
        return describe(self.certificate.read_bytes())


def available() -> bool:
    try:
        _require_cryptography()
    except KeysUnavailable:
        return False
    return True


def ensure(folder: Path, common_name: str = DEFAULT_COMMON_NAME) -> KeyFiles:
    """The keys in ``folder``, made on first run. Existing keys are never touched (K4 rotates)."""
    files = KeyFiles(folder)
    if files.exists():
        return files
    present = [path.name for path in files.paths if path.exists()]
    if present:
        raise KeysError(
            f"The keys folder holds {', '.join(present)} but not all of {', '.join(FILES)}; move"
            " them aside before new keys can be made"
        )
    generated = generate(common_name)
    folder.mkdir(parents=True, exist_ok=True)
    _write_private(files.root_key, generated.root_key)
    _write_private(files.key, generated.key)
    files.root_certificate.write_bytes(generated.root_certificate)
    files.certificate.write_bytes(generated.certificate)
    return files


def generate(common_name: str = DEFAULT_COMMON_NAME, now: datetime | None = None) -> GeneratedKeys:
    """A new root CA and a signer certificate it issued, both ECDSA P-256."""
    _require_cryptography()
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    if not common_name.strip():
        raise KeysError("The signer needs a name")
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(microsecond=0)
    start = now - BACKDATE

    def name(cn: str) -> x509.Name:
        return x509.Name([
            x509.NameAttribute(NameOID.COMMON_NAME, cn),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, ORGANIZATION),
        ])

    def key_usage(*, sign: bool, certify: bool) -> x509.KeyUsage:
        return x509.KeyUsage(
            digital_signature=sign,
            content_commitment=False,
            key_encipherment=False,
            data_encipherment=False,
            key_agreement=False,
            key_cert_sign=certify,
            crl_sign=certify,
            encipher_only=False,
            decipher_only=False,
        )

    root_key = ec.generate_private_key(ec.SECP256R1())
    root_subject_key_id = x509.SubjectKeyIdentifier.from_public_key(root_key.public_key())
    root = (
        x509
        .CertificateBuilder()
        .subject_name(name(ROOT_COMMON_NAME))
        .issuer_name(name(ROOT_COMMON_NAME))
        .public_key(root_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(start)
        .not_valid_after(start + ROOT_VALIDITY)
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(key_usage(sign=False, certify=True), critical=True)
        .add_extension(root_subject_key_id, critical=False)
        .sign(root_key, hashes.SHA256())
    )

    signer_key = ec.generate_private_key(ec.SECP256R1())
    signer = (
        x509
        .CertificateBuilder()
        .subject_name(name(common_name.strip()))
        .issuer_name(root.subject)
        .public_key(signer_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(start)
        .not_valid_after(start + SIGNER_VALIDITY)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(key_usage(sign=True, certify=False), critical=True)
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.EMAIL_PROTECTION]), critical=False
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(signer_key.public_key()), critical=False
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_subject_key_identifier(root_subject_key_id),
            critical=False,
        )
        .sign(root_key, hashes.SHA256())
    )

    def pem(certificate: x509.Certificate) -> bytes:
        return certificate.public_bytes(serialization.Encoding.PEM)

    def private_pem(key: ec.EllipticCurvePrivateKey) -> bytes:
        return key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )

    return GeneratedKeys(pem(root), private_pem(root_key), pem(signer), private_pem(signer_key))


def describe(certificate_pem: bytes) -> CertificateInfo:
    """The facts about a certificate that the user sees (K1: "shows its fingerprint")."""
    certificate = _load_certificate(certificate_pem)  # checks that cryptography is there first
    from cryptography.hazmat.primitives import hashes
    from cryptography.x509.oid import NameOID

    return CertificateInfo(
        common_name=_attribute(certificate.subject, NameOID.COMMON_NAME),
        issuer=_attribute(certificate.issuer, NameOID.COMMON_NAME),
        serial=format(certificate.serial_number, "x"),
        not_before=certificate.not_valid_before_utc,
        not_after=certificate.not_valid_after_utc,
        fingerprint=certificate.fingerprint(hashes.SHA256()).hex(":").upper(),
        algorithm=ALGORITHM if _is_p256(certificate) else certificate.signature_algorithm_oid._name,
    )


def fingerprint(certificate_pem: bytes) -> str:
    """SHA-256 of the certificate's DER, as OpenSSL and browsers show it."""
    return describe(certificate_pem).fingerprint


def _require_cryptography() -> None:
    try:
        import cryptography  # noqa: F401 - only its presence matters here
    except ImportError as error:
        raise KeysUnavailable("The cryptography package is not installed") from error


def _load_certificate(certificate_pem: bytes) -> x509.Certificate:
    _require_cryptography()
    from cryptography import x509

    try:
        return x509.load_pem_x509_certificate(certificate_pem)
    except ValueError as error:
        raise KeysError(f"Not a PEM certificate: {error}") from error


def _is_p256(certificate: x509.Certificate) -> bool:
    from cryptography.hazmat.primitives.asymmetric import ec

    public_key = certificate.public_key()
    return isinstance(public_key, ec.EllipticCurvePublicKey) and isinstance(
        public_key.curve, ec.SECP256R1
    )


def _attribute(name: x509.Name, oid: x509.ObjectIdentifier) -> str:
    attributes = name.get_attributes_for_oid(oid)
    value = attributes[0].value if attributes else ""
    return value if isinstance(value, str) else value.decode(errors="replace")


def _write_private(path: Path, data: bytes) -> None:
    """Create a file only this user can read, as bytes. Fails rather than overwrite."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)  # no CRLF on Windows
    flags |= getattr(os, "O_NOFOLLOW", 0)
    with os.fdopen(os.open(path, flags, 0o600), "wb") as output:
        output.write(data)
