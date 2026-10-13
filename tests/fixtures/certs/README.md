# Test signing certificates

Made by `scripts/make_test_certs.py` with OpenSSL, for the test suite only. The private keys
are public because they are in this repository: never sign anything real with them.

| File | What |
| --- | --- |
| `root.pem`, `root.key` | A local root CA: ECDSA P-256, `CA:TRUE`, `keyCertSign` |
| `signer.pem`, `signer.key` | The end-entity signing certificate it issued: `CA:FALSE`, `digitalSignature`, `emailProtection` |
| `chain.pem` | `signer.pem` followed by `root.pem`, what c2pa-python's `C2paSignerInfo` takes as `sign_cert` |
| `root.cnf`, `signer.cnf` | The OpenSSL configs the script wrote |

This is the certificate profile c2pa-rs 0.91.0 accepts at signing time (spike S2). Validity is
20 years, so the golden signatures keep validating. Regenerate with
`uv run python scripts/make_test_certs.py`; the tests do not depend on the exact keys.
