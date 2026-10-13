"""Make the test signing certificates in tests/fixtures/certs with OpenSSL.

    uv run python scripts/make_test_certs.py

These are for the test suite only. They are what spike S2 asks for: a local root CA and an
end-entity certificate that c2pa-rs accepts, so they double as the reference profile for K1,
however the plugin ends up generating its own. Never use them to sign anything real; the
private keys are public, since they are in this repository.

The profile follows the C2PA specification's certificate requirements, which c2pa-rs enforces
at signing time:

- Root CA: basicConstraints CA:TRUE and keyUsage keyCertSign, both critical.
- End entity: basicConstraints CA:FALSE, keyUsage digitalSignature (critical), extendedKeyUsage
  emailProtection (c2pa-rs accepts emailProtection or documentSigning), and an authority key
  identifier pointing at the root. A self-signed end-entity certificate is refused by c2pa-rs
  ("the certificate was self-signed").
- ECDSA P-256 keys, signature algorithm es256, PKCS#8 keys, 20 years of validity so the golden
  signatures keep validating.

Requires the openssl command line tool (3.x).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CERTS = REPO / "tests" / "fixtures" / "certs"
DAYS = 20 * 365

ROOT_CONFIG = """\
[req]
distinguished_name = dn
prompt = no
x509_extensions = v3_ca
[dn]
CN = krita-provenance test root CA
O = krita-provenance tests
[v3_ca]
basicConstraints = critical, CA:TRUE
keyUsage = critical, keyCertSign, cRLSign
subjectKeyIdentifier = hash
"""

SIGNER_CONFIG = """\
[req]
distinguished_name = dn
prompt = no
[dn]
CN = krita-provenance test signer
O = krita-provenance tests
[v3_signer]
basicConstraints = critical, CA:FALSE
keyUsage = critical, digitalSignature
extendedKeyUsage = emailProtection
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid
"""


def openssl(*args: str, stdin: bytes | None = None) -> bytes:
    result = subprocess.run(["openssl", *args], input=stdin, capture_output=True, check=False)
    if result.returncode != 0:
        raise SystemExit(
            f"openssl {' '.join(args)} failed:\n{result.stderr.decode(errors='replace')}"
        )
    return result.stdout


def ec_key_pkcs8() -> bytes:
    key = openssl("ecparam", "-name", "prime256v1", "-genkey", "-noout")
    return openssl("pkcs8", "-topk8", "-nocrypt", stdin=key)


def make(certs: Path = CERTS) -> list[Path]:
    certs.mkdir(parents=True, exist_ok=True)
    root_config = certs / "root.cnf"
    signer_config = certs / "signer.cnf"
    root_config.write_text(ROOT_CONFIG, encoding="utf-8")
    signer_config.write_text(SIGNER_CONFIG, encoding="utf-8")

    root_key = certs / "root.key"
    root_key.write_bytes(ec_key_pkcs8())
    root_pem = certs / "root.pem"
    root_pem.write_bytes(
        openssl(
            "req",
            "-new",
            "-x509",
            "-key",
            str(root_key),
            "-config",
            str(root_config),
            "-days",
            str(DAYS),
            "-sha256",
        )
    )

    signer_key = certs / "signer.key"
    signer_key.write_bytes(ec_key_pkcs8())
    request = openssl("req", "-new", "-key", str(signer_key), "-config", str(signer_config))
    signer_pem = certs / "signer.pem"
    signer_pem.write_bytes(
        openssl(
            "x509",
            "-req",
            "-CA",
            str(root_pem),
            "-CAkey",
            str(root_key),
            "-CAcreateserial",
            "-days",
            str(DAYS),
            "-sha256",
            "-extfile",
            str(signer_config),
            "-extensions",
            "v3_signer",
            stdin=request,
        )
    )
    (certs / "root.srl").unlink(missing_ok=True)  # the serial file is a by-product
    (certs / "chain.pem").write_bytes(signer_pem.read_bytes() + root_pem.read_bytes())

    openssl("verify", "-CAfile", str(root_pem), str(signer_pem))
    return sorted(path for path in certs.iterdir() if path.is_file())


def main() -> None:
    for path in make():
        print(path.relative_to(REPO))
    print("Fingerprint:", fingerprint(CERTS / "signer.pem"), file=sys.stderr)


def fingerprint(certificate: Path) -> str:
    return (
        openssl("x509", "-in", str(certificate), "-noout", "-fingerprint", "-sha256")
        .decode()
        .strip()
    )


if __name__ == "__main__":
    main()
