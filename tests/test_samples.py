"""samples/ mirrors the goldens (decision 4 of 13 Oct 2026), and the generator runs everywhere.

A sample that no longer matches the mapping means the goldens or the version changed:
regenerate with ``uv run python scripts/make_samples.py`` and commit the new files.
"""

import json
from pathlib import Path

import pytest

import make_samples
from goldens import CASES, comparable, load_case, read_actions
from krita_provenance import mapping, sdk

ROOT = Path(__file__).resolve().parents[1]
SUFFIXES = make_samples.SUFFIXES


@pytest.mark.parametrize("name", CASES)
def test_committed_sample_matches_its_golden(name: str):
    entry, export, identity = load_case(name)
    expected = mapping.manifest(
        entry, export, identity
    )  # this checkout's version, as the generator
    result = sdk.read_file(ROOT / "samples" / f"{name}{SUFFIXES[export.format]}")
    assert result is not None
    assert result.validation.is_acceptable()
    assert result.validation.failures == (sdk.UNTRUSTED_SIGNER,)
    assert comparable(read_actions(result)) == comparable(
        expected.definition["assertions"][0]["data"]["actions"]
    )
    active = json.loads(result.manifest_text)["manifests"][result.manifest_label]
    assert active["title"] == expected.definition["title"]
    assert [
        (i["title"], i["relationship"], i["format"]) for i in active.get("ingredients", [])
    ] == [
        (i.definition["title"], i.definition["relationship"], i.format)
        for i in expected.ingredients
    ]


def test_generator_makes_a_valid_sample_per_case(tmp_path: Path):
    made = make_samples.make(tmp_path)
    assert [path.name for path, _ in made] == [
        f"{name}{SUFFIXES[load_case(name)[1].format]}" for name in CASES
    ]
    for path, result in made:
        assert path.is_file()
        assert result.validation.is_acceptable()
    assert not [p.name for p in tmp_path.iterdir() if p.name.startswith(".")]  # sources removed
