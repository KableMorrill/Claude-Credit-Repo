# krita-provenance

A Krita plugin that records each Krita AI Diffusion generation and each layer edit in a document,
then exports a PNG or JPEG with a signed C2PA manifest and a plain-language AI-use report. It is
for artists who must disclose AI use, and it is the user's portfolio piece for provenance
engineering, so correct, honest claims matter more than speed. The repository is named
Claude-Credit-Repo; the project is krita-provenance and the package `krita_provenance`. Keep
those names.

**Read [docs/spec.md](docs/spec.md) before designing anything.** It is the source of truth for
scope, the log format, the C2PA mapping, the threat model and the milestones. Requirement IDs
(C1, P1, E1, …), spikes (S1–S5) and open questions (Q1–Q11) come from it.

## Where things stand (12 Oct 2026)

- **Done:** the repo scaffold, the spec's "Create the repo" item. The plugin loads in Krita and
  adds Tools ▸ Scripts ▸ Provenance: Check Environment, which reports the Python, Qt, Krita AI
  Diffusion and c2pa-python versions Krita sees. Nothing is captured, logged or exported yet.
- **Not done:** every requirement. Spikes S1–S5 haven't run; the go/no-go note is due 25 Oct 2026.
- **Next:** M1, Core v0.1: C1–C3, P1, E1, E2, E4–E6, D1, V1, K1, I1, I2.

## Layout

```
krita_provenance/          the plugin package; Krita pairs it with krita_provenance.desktop
  __init__.py              entry point; registers the extension only when running inside Krita
  extension.py             Krita glue, the only module allowed to import `krita`
  qt.py                    Qt shim: PyQt5 under Krita 5, PyQt6 under Krita 6
  environment.py           the Check Environment report (Krita-free, tested)
krita_provenance.desktop   plugin manifest Krita reads at startup
scripts/dev_install.py     links this checkout into Krita's pykrita folder
scripts/typeshed/          Krita API stubs, vendored unmodified (see the README there)
tests/                     pytest; nothing needs Krita
docs/spec.md               the project spec
docs/smoke-checklist.md    checks the user runs inside Krita
.github/workflows/ci.yml   CI on every push
```

## Commands

```sh
uv sync              # install the dev tools pinned in uv.lock
uv run ruff format   # format; CI fails on any diff
uv run ruff check    # lint
uv run pyright       # type-check, including calls into Krita's API via the stubs
uv run pytest        # test
```

The four checks must pass before every commit. CI runs them on every push, with tests on Python 3.10
(Windows, macOS, Linux) and 3.14 (Linux). Add dev tools with `uv add --dev <package>` so
`uv.lock` stays in step; CI installs with `--locked`.

## Rules

Enforced by `tests/test_architecture.py`:

- Only `extension.py` imports `krita`. Every other module must import without Krita or Qt; the
  test imports each one in a clean interpreter. Add a new Krita-facing module to
  `ALLOWED_IMPORTERS` on purpose, and keep it thin.
- Qt comes only through `krita_provenance/qt.py`, never from PyQt5 or PyQt6 directly.
- `c2pa` is imported only in `krita_provenance/sdk.py`, lazily, because the wheel arrives at
  first run (I2).
- `ai_diffusion` is imported only in `krita_provenance/adapters/ai_diffusion.py`.
- `version` in pyproject.toml equals `krita_provenance.__version__` (`tests/test_version.py`).

From the spec. Add tests for each as you implement it:

- The honesty rules. Never write "no AI used"; absence reads "No AI generation observed by this
  plugin since" plus the log's start date. Record and disclose every capture gap. Disclose an
  uncertain region as possibly AI, and say why. State facts, not legal conclusions.
- Capture never writes C2PA. The manifest is rebuilt from the log at every export.
- The log is append-only, versioned JSON (`"schema": "krita-provenance/1"`) stored in the
  `krita-provenance.json` annotation. Never rewrite or drop past events.
- E5: never add custom hash exclusions. E6: if signing fails, label the saved file unsigned; it
  must never look signed.
- D1: scrub file paths, user names and machine names from every field that leaves the .kra.
- Read imported manifests only through the SDK, cap their size, and show their text as plain text.
- Keep prompt text out of exports for now. Disclosure levels and prompt commitments (D2, D3) are
  v1.0 work, and until then full prompts stay in the .kra.
- Label the export action neutrally, without "Content Credentials" or the CR icon, until the user
  has checked C2PA's brand terms (spec: Distribution).

Runtime:

- The plugin runs on Krita's embedded Python with only the standard library and Krita's PyQt.
  Assume Python 3.10 until S1 says otherwise; pyright targets 3.10 and CI tests on it.
- The only third-party package the plugin may use is c2pa-python 0.38.x, through `sdk.py`,
  unless the user agrees to another (see K1 below). Dev and test tools can be anything.
- v1 targets Krita 5.3 and PyQt5; Krita 6 and PyQt6 depend on Q7. Type-checking uses PyQt5.
- Put logic in Krita-free modules that take plain data; keep the Krita glue thin.
- Never commit real keys or certificates. Test material goes in `tests/fixtures`.

## Suggested order for M1

Testable, Krita-free work first; Krita glue last. Commit after each step with all checks passing.

1. Log model: P1's format, the event types from the spec's example, JSON round-trip and schema
   version. Annotation storage comes in step 7.
2. Scrubbing (D1).
3. Mapping from log to manifest definition, following the spec's C2PA mapping table. Golden
   tests: fixture logs map to expected JSON.
4. `sdk.py`: sign PNG and JPEG, read the result back and report validation (E4), with E5 and E6.
   Add `c2pa-python==0.38.0` as a dev dependency for tests. Golden manifests are signed and read
   back; the only allowed warning is an untrusted signer.
5. Keys (K1): local root CA, end-entity certificate, fingerprint. This needs a decision first
   (Open technical questions).
6. Installer (I2): download the platform's c2pa-python 0.38.0 wheel, check its SHA-256 against a
   pinned table, unpack it into the plugin's data folder and import it lazily. Test with fake
   downloads.
7. Krita glue: annotation storage (P1), the Krita AI Diffusion adapter (C1–C3), the export
   action through `Document.exportImage` (E1), the docker (V1) and the release zip (I1).
   Type-check against the stubs, and add steps to docs/smoke-checklist.md.

## Facts already checked

Rely on these; check anything else against source.

**Krita AI Diffusion v1.53.0** (commit `0217cd2`, the release for Krita 5):

- `JobQueue.result_used` is `pyqtSignal(Item)`, where `Item` is `(job: str, image: int)`
  ([jobs.py L143–154](https://github.com/Acly/krita-ai-diffusion/blob/0217cd2197fcadbd70d7e63af25e29cc21cb7c8b/ai_diffusion/model/jobs.py#L143-L154)),
  and `JobQueue.find(id)` returns the job
  ([L183](https://github.com/Acly/krita-ai-diffusion/blob/0217cd2197fcadbd70d7e63af25e29cc21cb7c8b/ai_diffusion/model/jobs.py#L183-L184)).
  `JobParams` holds bounds, name, regions, metadata, seed, has_mask, workflow_kind and more
  ([L51–65](https://github.com/Acly/krita-ai-diffusion/blob/0217cd2197fcadbd70d7e63af25e29cc21cb7c8b/ai_diffusion/model/jobs.py#L51-L65)).
- `Model.apply_generated_result` applies the result first and then calls `jobs.notify_used`,
  which emits `result_used`
  ([model.py L847–868](https://github.com/Acly/krita-ai-diffusion/blob/0217cd2197fcadbd70d7e63af25e29cc21cb7c8b/ai_diffusion/model/model.py#L847-L868)).
  The new layer already exists when the signal arrives, so C2's "before" snapshot of node IDs
  must be taken earlier. The "replace" apply behaviour also adds a node: it creates a same-named
  copy of the active layer and removes the original a moment later
  ([layer.py](https://github.com/Acly/krita-ai-diffusion/blob/0217cd2197fcadbd70d7e63af25e29cc21cb7c8b/ai_diffusion/layer.py)).
- Per-document models come from `root.models()` and `root.model_for_active_document()`
  ([root.py](https://github.com/Acly/krita-ai-diffusion/blob/0217cd2197fcadbd70d7e63af25e29cc21cb7c8b/ai_diffusion/model/root.py#L71-L84));
  each has `.jobs` and `.document`.
- It keeps its state in a `ui.json` annotation, rewritten about a second after the last change
  (including after `result_used`), and result images in `result{slot}.webp` annotations
  ([persistence.py](https://github.com/Acly/krita-ai-diffusion/blob/0217cd2197fcadbd70d7e63af25e29cc21cb7c8b/ai_diffusion/persistence.py)).
- Its LICENSE is the stock GPLv3 text with no "or later" grant, so GPL-3.0-or-later here is
  compatible (Q6).

**c2pa-python 0.38.0** (commit `192023c`, bundling c2pa-rs v0.91.0):

- API: `Builder`, `Reader`, `Signer`, `C2paSignerInfo`, `C2paSigningAlg`, `C2paBuilderIntent`
  (CREATE, EDIT, UPDATE), `C2paDigitalSourceType`, `Context` and `Settings`.
- `Signer.from_info(C2paSignerInfo(alg, sign_cert, private_key, ta_url))` signs in the native
  library ([used in its tests](https://github.com/contentauth/c2pa-python/blob/192023c3edc1cd74962650e0fa0f1ec431304e9c/tests/test_unit_tests.py#L1354-L1360)).
  [examples/sign.py](https://github.com/contentauth/c2pa-python/blob/192023c3edc1cd74962650e0fa0f1ec431304e9c/examples/sign.py)
  instead uses `Signer.from_callback` with the `cryptography` package and a DigiCert timestamp
  URL. Timestamps are undecided (K3, Q4) and need the network, so leave the timestamp authority
  unset.
- The package's own modules import only the standard library. Its declared dependencies
  (`cryptography`, `requests`, `toml`) serve the examples and its build script.
- It has no certificate-generation API.

**Krita API stubs** (`scripts/typeshed/krita.pyi`): `Document` has `annotation`,
`setAnnotation`, `annotationTypes`, `removeAnnotation`, `exportImage`, `topLevelNodes` and
`nodeByUniqueID`; `Node` has `uniqueId`, `childNodes` and `bounds`. `Notifier.imageSaved` fires
after a save; there is no before-save signal.

## Open technical questions

Raise these with the user rather than settling them silently.

- K1 needs X.509 and ECDSA, which neither Krita's Python nor c2pa-python provides. One option is
  fetching `cryptography` beside c2pa-python, at the cost of more wheels to pin and hash. Spike
  S2 decides.
- I2: c2pa-python works without its declared dependencies, so the plugin can unpack just its
  wheel, unless K1's answer brings `cryptography` in anyway.
- P1 says "before save", but Krita has no before-save signal. Debounced writes that keep the
  annotation current, as Krita AI Diffusion does, may be the answer; S4 checks it in Krita.
- Q1 (generation as an action only, or also as an ingredient) waits on S5. Build the action-only
  form, with the choice in one place.
- Where the spec proposes a default for an open question, use it (Q3: standard disclosure).
  Otherwise choose the most reversible option, keep it in one place, and list it in your summary.

## What can't be verified without Krita

Cloud sessions have no Krita, so `krita` exists there only as stubs. Code that calls Krita's API,
uses Krita AI Diffusion's live objects or relies on annotations surviving save and reopen is
type-checked in the cloud and verified by the user in Krita with docs/smoke-checklist.md. Adobe's
Verify and Inspect need the user to upload a file, and S5 needs Firefly and Photoshop sample files
from the user. Everything else runs anywhere, including c2pa-python, c2patool
(`cargo install c2patool`) and test certificates.

## Working with the user

- docs/spec.md is the user's document. Change it only when asked.
- Keep "Where things stand" in this file current as work lands; the next session starts from it.
- End each session with what works and how you checked it, what is unverified and why, choices
  you made provisionally, and questions for the user.
- Don't open upstream issues or pull requests, publish releases or post anywhere; outreach and
  Phase B are the user's.
- Commits with AI-written changes carry a `Co-Authored-By:` trailer that names the model (README:
  AI assistance).
