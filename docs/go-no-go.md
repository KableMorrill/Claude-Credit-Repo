# Go/no-go note: M0 spikes

Draft, 18 October 2026, due 25 October 2026. Spike S5 hasn't run yet; this note becomes final
once it has.

**Recommendation: go.** Four of the five spikes pass on Windows, and the fifth has a workaround.
M1's scope (v0.1) is already built and checked inside Krita on Windows. Of M1's exit gate, only
the Krita AI Diffusion issue with a demo is left.

## Spikes

M0's exit gate: each spike passes or has a written workaround.

| Spike | Question | Result |
| --- | --- | --- |
| S1 | Does c2pa-python load and sign inside Krita's own Python, 3.10 or newer, on each OS? | **Passes on Windows** (15 Oct): Krita 5.3.4.1 runs Python 3.13.5, and c2pa-python 0.38.0 loads from the plugin's verified install and signs. **macOS and Linux are unchecked** (workaround below) |
| S2 | Does the SDK accept a locally made root CA and signing certificate, and does Adobe's Verify show the signer as unrecognized? | **Passes** (14 to 16 Oct). Verify reads full manifests once regions are written as polygons: it runs c2pa-rs 0.85.2, which can't read rectangles |
| S3 | Can a separate plugin catch Krita AI Diffusion's `result_used`, copy the job's parameters and find the layer that received the result? | **Passes** (16 Oct), once the "before" snapshot of the layers was taken at user input instead of from a timer |
| S4 | Does a custom annotation survive save and reopen beside Krita AI Diffusion's `ui.json`? | **Passes** (13 Oct) |
| S5 | How do a Firefly image and a Photoshop Generative Fill export record AI use (Q1)? | **Not run**: it needs those two files (workaround below) |

### Workarounds

- **S1 on macOS and Linux.** The installer's pinned wheels cover macOS arm64 and Linux x86_64,
  and the install has run outside Krita on Linux x86_64 with Python 3.10 and 3.13. Krita's own
  Python on those systems is unchecked. Before v0.1 ships, run smoke rows 1 to 16 on a Mac and on
  Linux; M3's test matrix covers them for v1.0. Intel Macs and Windows on ARM get no local
  signing key, because no current `cryptography` wheel exists for them. That gap is accepted
  (decision 4 of 13 Oct), and K2's bring-your-own certificate (M2) is the way to sign there.
- **S5.** v0.1 records each generation as a `c2pa.edited` action with
  `compositeWithTrainedAlgorithmicMedia` alone, the form the spec builds first, and the choice
  sits in one place in `mapping.py`. Manifests are rebuilt from the log at every export. If S5
  shows that generations should also be `componentOf` ingredients, documents logged before the
  change get the new form at their next export.

## M1, already built

Built during M0 and checked in Krita on Windows (docs/smoke-checklist.md, rows 1 to 42):

- capture of Krita AI Diffusion results (C1 to C3);
- the log kept in the document (P1);
- signed PNG and JPEG export, with read-back and an unsigned fallback (E1, E2, E4 to E6);
- scrubbing of paths and names (D1);
- the log docker (V1);
- the local signing key (K1);
- the release zip (I1) and the verified first-run download (I2).

M1's exit gate:

| Gate item | Status |
| --- | --- |
| Exports validate in c2pa-python and c2patool | **Met.** Every export is read back with c2pa-python (c2pa-rs 0.91.0). On 18 Oct, c2patool 0.27.22 read seven real exports: all Valid, with the untrusted local signer as the only failure code. An unsigned export carries no manifest |
| Adobe's Verify shows the actions | **Met.** Created with krita-provenance, Krita AI Diffusion as the AI model and edited with AI. For a document opened from an image, "Show sources" shows the parent |
| Krita AI Diffusion issue opened with a demo | **Not yet.** Due 22 Nov; the project owner opens it |
| Export overhead at 4K under 3 seconds | **Met.** A 4000×4000 export took about a second, from choosing the file to the result |

## What M0 changed

- Adobe's Verify runs an older c2pa-rs that can't read rectangle regions, so regions are written
  as four-corner polygons covering the same area. Rectangles can return when Verify updates.
- Krita runs Qt events while it waits on the image, so a timer can fire in the middle of Krita
  AI Diffusion's apply. The layer snapshot is taken on mouse and key presses instead.
- Writing any annotation marks a Krita document modified. Starting a log no longer does, so
  opening a file doesn't prompt a save. Recording an export does, so Krita asks to save and the
  record isn't lost.
- Live painting, upscaling and control-layer results don't announce themselves, so v0.1 doesn't
  observe them (C7). The README says so.
- Every export states what the plugin didn't observe: the log's start date, capture gaps,
  content a document already had, and generated layers deleted or hidden before export.

## Risks carried into M1

- Krita AI Diffusion's internals change (high likelihood, high impact). The adapter is
  version-checked, a failure becomes a disclosed capture gap, and Phase B proposes a stable hook
  upstream.
- The local signer shows as unrecognized in every validator, by design. Bring-your-own
  certificates are K2 (M2).
- Without timestamps, Verify shows no signing date (K3, Q4).
- Placed images (`componentOf`) can't be signed yet (C6, M2). Such a document exports unsigned
  and says why.
- If an older version of the plugin rewrites a log, it drops fields it doesn't know from the
  event types it does know. This is to be settled before the first release.

## Open questions

Q1 waits on S5. Q2 and Q10 are answered. Q3 (standard disclosure), Q4 (no timestamps in v0.1),
Q5 (keys in the data folder) and Q6 (GPL-3.0-or-later is compatible) are decided for v0.1. Q7
(Krita 6), Q8 (model identity), Q9 (reference images and prompts as ingredients) and Q11
(`c2pa.ai-disclosure`) stay open for M2.

## To finish M0

1. S5: dump and compare a Firefly image and a Photoshop Generative Fill export, then decide Q1.
2. S1 on macOS and Linux, or accept the workaround above for now.
3. The go/no-go decision.
