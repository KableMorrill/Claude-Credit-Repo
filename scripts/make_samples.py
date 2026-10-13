"""Sign the golden cases into samples/, for checking in other validators.

    uv run python scripts/make_samples.py

Each golden case under tests/fixtures/goldens is signed onto a 16x16 placeholder image with the
public test certificate in tests/fixtures/certs, read back with the SDK, and written to
samples/<case>.png or .jpg. The histories are the goldens' fictional ones; the pixels are
blank. Upload them to Adobe's Verify (https://verify.contentauthenticity.org) or run
``c2patool samples/<file>`` to compare readers: the signer should show as unrecognized, and
nothing else should be flagged.

Labels, instance IDs and thumbnails change on every run, so the files are not byte-for-byte
reproducible; the actions are.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
# The package is not installed into the venv (pyproject: package = false), and goldens.py, the
# golden-case loader, lives with the tests.
sys.path[:0] = [str(REPO), str(REPO / "tests")]

from goldens import CASES, load_case
from krita_provenance import mapping, sdk
from krita_provenance.sdk import IngredientData, SignerConfig

SAMPLES = REPO / "samples"
CERTS = REPO / "tests" / "fixtures" / "certs"
IMAGES = REPO / "tests" / "fixtures" / "images"
PLACEHOLDERS = {
    "image/png": IMAGES / "blank.png",
    "image/jpeg": IMAGES / "blank.jpg",
    mapping.KRITA_DOCUMENT: IMAGES / "blank.kra",
}
SUFFIXES = {"png": ".png", "jpeg": ".jpg"}


def make(samples: Path = SAMPLES) -> list[tuple[Path, sdk.ReadResult]]:
    signer = SignerConfig((CERTS / "chain.pem").read_bytes(), (CERTS / "signer.key").read_bytes())
    samples.mkdir(exist_ok=True)
    made: list[tuple[Path, sdk.ReadResult]] = []
    for name in CASES:
        entry, export, identity = load_case(name)
        manifest = mapping.manifest(entry, export, identity)
        source = samples / f".{name}-source{SUFFIXES[export.format]}"
        shutil.copyfile(PLACEHOLDERS[mapping.FORMATS[export.format]], source)
        ingredients = [
            IngredientData(i.definition["instance_id"], PLACEHOLDERS[i.format or ""].read_bytes())
            for i in manifest.ingredients
        ]
        destination = samples / f"{name}{SUFFIXES[export.format]}"
        try:
            result = sdk.sign_file(source, destination, manifest, signer, ingredients)
        finally:
            source.unlink()
        made.append((destination, result))
    return made


def main() -> None:
    for path, result in make():
        codes = ", ".join(result.validation.failures) or "none"
        print(f"{path.relative_to(REPO)}: {result.validation.state}, failure codes: {codes}")


if __name__ == "__main__":
    main()
