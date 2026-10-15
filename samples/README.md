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

Adobe's tools haven't read them yet. On 13 Oct 2026 Verify answered "Something went wrong" for
every upload, unrelated images included, and Content Authenticity Inspect
(contentauthenticity.adobe.com/inspect) found no credentials in any upload and, for these
samples only, also showed "Something went wrong". On 14 Oct the same happened with these
manifests signed onto a 512 px image, while unsigned 16 px and 512 px images drew no error, so
the image size isn't the cause. Until an Adobe tool reads them, c2patool and c2pa-python are
the validators of record.

Regenerate with `uv run python scripts/make_samples.py` whenever the goldens or the plugin
version change; `tests/test_samples.py` fails until you do. The version in each manifest is the
checkout's (`krita_provenance.__version__`), so it reads `0.1.0.dev0` where the goldens, which
pin `0.1.0`, do not. Labels, instance IDs and thumbnails change on every run; the actions do not.
