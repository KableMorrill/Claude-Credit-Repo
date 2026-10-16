# Signed samples

Signed exports made from the golden cases in `tests/fixtures/goldens` by
`scripts/make_samples.py`, for checking this plugin's manifests in other validators, such as
Adobe's [Verify](https://verify.contentauthenticity.org) and `c2patool`.

| File | Case | What its manifest says |
| --- | --- | --- |
| `new_document.png` | A blank canvas, one inpainted region, a layer edit, a capture gap | `c2pa.created` from an empty canvas, then edited, drawing, unknown, converted |
| `nothing_observed.png` | A canvas with one layer edit and no AI | created, drawing, converted; no AI action |
| `opened_image.jpg` | A photo opened in Krita, three generations, a placed logo | `c2pa.opened` from a `parentOf` ingredient, a hidden and a removed layer disclosed, `c2pa.placed` |
| `spec_example.png` | The example log from docs/spec.md | created, edited, drawing, placed, unknown, converted |
| `existing_document.png` | A .kra that had content before the plugin saw it | `c2pa.opened` from the .kra, then `c2pa.unknown` for the unobserved past |

The pixels are a blank 16x16 image; the histories are the goldens' fictional ones. All are
signed with the public test certificate in `tests/fixtures/certs`, so every validator should
report the signer as unrecognized or untrusted and nothing else. In c2pa-python and c2patool
0.28.2 the state is `Valid` with `signingCredential.untrusted` as the only failure code.

Regions are written as four-corner polygons (decision of 16 Oct 2026). Adobe's Verify reads
with c2pa-rs 0.85.2, which can't decode rectangle regions, a bug fixed in c2pa-rs by 0.90.19
(CLAUDE.md, "Older c2pa-rs can't read rectangle regions"). The earlier rectangle samples drew
"Something went wrong" in Verify; these read Valid in c2pa-rs 0.85.1, with the signer untrusted
and nothing else, and await an upload to Verify. Until then, c2patool and c2pa-python are the
validators of record.

Regenerate with `uv run python scripts/make_samples.py` whenever the goldens or the plugin
version change; `tests/test_samples.py` fails until you do. The version in each manifest is the
checkout's (`krita_provenance.__version__`), so it reads `0.1.0.dev0` where the goldens, which
pin `0.1.0`, do not. Labels, instance IDs and thumbnails change on every run; the actions do not.
