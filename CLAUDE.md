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

## Where things stand (13 Oct 2026)

- **Done:** the repo scaffold and Tools ▸ Scripts ▸ Provenance: Check Environment. M1 steps 1–4
  of the order below, all Krita-free and tested: the log model (`log.py`), scrubbing (`scrub.py`),
  the log-to-manifest mapping with golden fixtures (`mapping.py`, `tests/fixtures/goldens`), and
  `sdk.py`, which signs PNG and JPEG with c2pa-python 0.38.0, reads them back and keeps E5 and E6.
  Golden manifests sign and validate with an untrusted signer as the only failure code, in
  c2pa-python and in c2patool 0.28.2 (G3's two readers; `cargo install c2patool --locked`).
  `scripts/make_test_certs.py` makes a root CA and signing certificate with OpenSSL that c2pa-rs
  accepts (spike S2's profile); the test copies are in `tests/fixtures/certs`.
- **Decisions of 13 Oct 2026**, from the user, to build on without reopening:
  1. **K1:** "fetch the cryptography wheel alongside c2pa-python at first run, pinned and
     SHA-256 checked the same way as I2. No openssl or hand-written crypto. No further
     exploration needed." Consequence (Claude's, not the user's): `cryptography`'s wheels need
     `cffi` on CPython, so I2 fetches three wheels, c2pa-python, `cryptography` and `cffi`, each
     pinned and hashed. S1, which loads c2pa-python inside Krita, should load these too.
  2. **Documents that predate their log** (`log_started.reason` = `existing_document`): "treat
     the saved .kra as the parentOf ingredient with c2pa.opened, and disclose that content
     before the log's start date was not observed by this plugin. Don't refuse to sign." Built
     in `mapping.py` (golden case `existing_document`): the start reason is never a ground for
     refusal; the mapping still needs exactly one `parentOf` event, the saved .kra, which the
     Krita glue must log before export. What the export action does for a document that has
     never been saved is open (below).
  3. **The export title the user typed is kept as typed**, reduced to its file name, never the
     folder; a title with no file name is refused. Everything captured automatically is still
     scrubbed in full (D1). Built.
  4. **Signed samples** for other validators live in `samples/`, made by
     `scripts/make_samples.py` from the goldens; `tests/test_samples.py` fails when they drift.
     The Adobe Verify result is not yet recorded; the user uploads them.
- **Also done (13 Oct, later):** K1 and I2, Krita-free and tested. `keys.py` makes the local
  root CA and signing certificate with `cryptography`; a golden signed with freshly made keys
  validates with the untrusted signer as the only failure code. `wheels.py`, `wheel_pins.py`
  and `installer.py` fetch c2pa-python 0.38.0, cryptography 50.0.2 and cffi 2.1.1 for this
  platform and Python, verify size and SHA-256 against the pins, unpack them into the data
  folder and put them first on `sys.path`; `scripts/update_wheel_pins.py` regenerates the pins
  from PyPI. A real install on this Linux x86_64 machine imported all three from the unpacked
  folder.
- **Not done:** everything that touches Krita. Nothing is captured, stored in a .kra or exported
  from Krita yet; keys are not yet made, and wheels not yet fetched, from inside Krita. Spikes
  S1, S3, S4 and S5 haven't run; S2 is half done (c2pa-rs accepts both the OpenSSL-made test
  certificate and the `cryptography`-made one; the Adobe Verify check waits on the samples).
  The go/no-go note is due 25 Oct 2026.
- **Next:** step 7, the Krita glue: annotation storage, the adapter, first-run install and key
  creation, the export action, the docker and the release zip.

## Layout

```
krita_provenance/          the plugin package; Krita pairs it with krita_provenance.desktop
  __init__.py              entry point; registers the extension only when running inside Krita
  extension.py             Krita glue, the only module allowed to import `krita`
  qt.py                    Qt shim: PyQt5 under Krita 5, PyQt6 under Krita 6
  environment.py           the Check Environment report (Krita-free, tested)
  log.py                   the provenance log: P1's format, event types, JSON round trip
  scrub.py                 D1: paths, user names and machine names out of everything that leaves
  mapping.py               log -> C2PA manifest definition, after the spec's mapping table
  sdk.py                   the only module that imports c2pa; sign, read back, E5, E6
  keys.py                  K1: root CA and signing certificate, the only importer of cryptography
  wheels.py                I2: which pinned wheel fits a platform and Python (stdlib only)
  wheel_pins.py            I2: GENERATED pins: file names, URLs, sizes, SHA-256 (do not edit)
  installer.py             I2: fetch, verify, unpack, record and activate the pinned wheels
krita_provenance.desktop   plugin manifest Krita reads at startup
scripts/dev_install.py     links this checkout into Krita's pykrita folder
scripts/make_test_certs.py makes the test certificates in tests/fixtures/certs with OpenSSL
scripts/make_samples.py    signs the goldens into samples/ for other validators
scripts/update_wheel_pins.py regenerates wheel_pins.py from PyPI; review the diff it makes
scripts/typeshed/          Krita API stubs, vendored unmodified (see the README there)
samples/                   signed sample exports, one per golden case (see the README there)
tests/                     pytest; nothing needs Krita. goldens.py loads tests/fixtures/goldens
tests/fixtures/goldens/    per case: log.json, export.json (what the exporter knew), manifest.json
tests/fixtures/certs/      test root CA and signing certificate; never sign anything real with them
tests/fixtures/images/     16x16 PNG and JPEG placeholders and a .kra-shaped zip for ingredients
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
  first run (I2). c2pa-python 0.38.0 is a dev dependency so the signing tests run in CI.
- `cryptography` is imported only in `krita_provenance/keys.py`, lazily, for the same reason;
  cryptography 50.0.2 is a dev dependency, the version the plugin fetches.
- `wheel_pins.py` is generated. To change a pinned version, edit `PINS` in
  `scripts/update_wheel_pins.py`, run it, bump the dev dependency to match, and review the
  diff: those digests are what every download is checked against.
- `ai_diffusion` is imported only in `krita_provenance/adapters/ai_diffusion.py`.
- `version` in pyproject.toml equals `krita_provenance.__version__` (`tests/test_version.py`).

From the spec. Add tests for each as you implement it:

- The honesty rules. Never write "no AI used"; absence reads "No AI generation observed by this
  plugin since" plus the log's start date. Record and disclose every capture gap. Disclose an
  uncertain region as possibly AI, and say why. State facts, not legal conclusions.
- Capture never writes C2PA. The manifest is rebuilt from the log at every export.
- The log is append-only, versioned JSON (`"schema": "krita-provenance/1"`) stored in the
  `krita-provenance.json` annotation. Never rewrite or drop past events. Unknown event types
  survive a round trip and map to `c2pa.unknown`.
- Golden tests: `tests/fixtures/goldens/<case>/` holds a log, `export.json` and the expected
  `manifest.json`. After a deliberate mapping change run `UPDATE_GOLDENS=1 uv run pytest` and
  review the diff; never update goldens to make a test pass.
- E5: never add custom hash exclusions. E6: if signing fails, label the saved file unsigned; it
  must never look signed.
- D1: scrub file paths, user names and machine names from every field that leaves the .kra.
  The one exemption is the export title the user typed, kept as typed minus its folder.
- Read imported manifests only through the SDK, cap their size, and show their text as plain text.
- Keep prompt text out of exports for now. Disclosure levels and prompt commitments (D2, D3) are
  v1.0 work, and until then full prompts stay in the .kra.
- Label the export action neutrally, without "Content Credentials" or the CR icon, until the user
  has checked C2PA's brand terms (spec: Distribution).

Runtime:

- The plugin runs on Krita's embedded Python with only the standard library and Krita's PyQt.
  Assume Python 3.10 until S1 says otherwise; pyright targets 3.10 and CI tests on it.
- The third-party packages the plugin may use are c2pa-python 0.38.x, through `sdk.py`, and
  `cryptography` with its dependency `cffi` for K1 (decision 1 above), all fetched at first run.
  Dev and test tools can be anything.
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
5. Keys (K1): local root CA, end-entity certificate, fingerprint, made with `cryptography`
   (decision 1) in the profile `scripts/make_test_certs.py` shows c2pa-rs accepts.
6. Installer (I2): download the platform's c2pa-python 0.38.0, `cryptography` and `cffi` wheels,
   check each SHA-256 against a pinned table, unpack them into the plugin's data folder and
   import them lazily. Test with fake downloads.
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

**c2pa-python 0.38.0 behaviour, checked by signing** (tests/test_sdk.py reproduces these):

- `c2pa.created` must carry a `digitalSourceType`; without one the manifest fails validation
  at signing (`assertion.action.malformed`). With intent Create a `parentOf` ingredient is an
  error; with intent Edit, `c2pa.opened` must name a parent that was really added through
  `Builder.add_ingredient`, else "Action ingredientId not found".
- The region `role` field in the spec's mapping table is deprecated since C2PA 2.1, fails CBOR
  decoding in c2pa-rs 0.91.0 ("expected enum Role") and is removed in 0.92.0. Regions carry a
  `description` instead.
- With a signer on no trust list, `validation_state` is `Valid`, `failure` holds exactly
  `signingCredential.untrusted`, and `informational` holds `signingCredential.ocsp.skipped`.
  Those are the only codes a fresh export may carry.
- The data-hash assertion has one exclusion, the manifest store (`Reader.detailed_json()`,
  `assertion_store["c2pa.hash.data"].exclusions`). A definition that includes a
  `c2pa.hash.data` assertion is rejected by the Builder; sdk.py refuses it first anyway (E5).
- A failed `Builder.sign` leaves a zero-byte destination, so sdk.py signs to a temporary file.
- `Signer.from_info` refuses a self-signed end-entity certificate and a malformed key with
  `C2paError.Signature`. The accepted certificate profile is in `scripts/make_test_certs.py`.
- `Reader` raises `C2paError.ManifestNotFound` on a file without a manifest and
  `C2paError.NotSupported` on a non-asset. `import c2pa` emits no warnings, so pytest's
  `filterwarnings = error` is safe.
- Regions come back with float coordinates; `ingredientIds` in action parameters are rewritten
  to hashed `ingredients` URIs; `claim_generator_info` gains `org.contentauth.c2pa_rs`.
- A .kra (a zip) is accepted as a `parentOf` ingredient under `application/x-krita`, but its
  `c2pa.ingredient.v3` assertion holds only the title, format, relationship and instance id: no
  hash of the bytes and no thumbnail. The parent names the file; it does not bind to its bytes.

**Wheels on PyPI, checked 13 Oct 2026** (`scripts/update_wheel_pins.py` reads the same data):

- c2pa-python 0.38.0: `py3-none` wheels for macOS 10.9 x86_64 and universal2, macOS 11
  arm64, manylinux_2_28 x86_64 and aarch64, win_amd64 and win_arm64; 15 MB each, 88 MB on
  Windows. It needs glibc 2.28 on Linux.
- cryptography 50.0.2: `cp39-abi3` and `cp311-abi3` wheels (one file covers every CPython from
  that minor on) for manylinux_2_17 x86_64 and aarch64, macOS 11 arm64 and win_amd64; about
  4 MB. No macOS Intel, no Windows ARM, no 32-bit.
- cffi 2.1.1: one wheel per CPython minor (3.10 to 3.15) for manylinux_2_17, macOS 10.15 x86_64
  and 11 arm64, win_amd64 and, from 3.11, win_arm64; about 200 KB.
- Free-threaded (`cp314t`) and PyPy wheels exist and are skipped: Krita's Python is neither.

**Krita API stubs** (`scripts/typeshed/krita.pyi`): `Document` has `annotation`,
`setAnnotation`, `annotationTypes`, `removeAnnotation`, `exportImage`, `topLevelNodes` and
`nodeByUniqueID`; `Node` has `uniqueId`, `childNodes` and `bounds`. `Notifier.imageSaved` fires
after a save; there is no before-save signal.

## Open technical questions

Raise these with the user rather than settling them silently.

- K1 and I2 are built. Pinned: cryptography 50.0.2 and cffi 2.1.1, the newest at the time.
  cryptography has shipped no macOS Intel wheel since 46.0.4 and no Windows ARM wheel since
  46.0.3, so on those machines the installer fetches c2pa-python alone, K1 is unavailable and
  K2's own certificate is the way to sign. The spec's matrix (Windows x64, macOS arm64, Linux
  x64) is fully covered.
- Where the data folder lives, and what the first-run dialog looks like, is the glue's (step
  7): `installer.install` takes any folder, reports progress per wheel, and `keys.ensure`
  takes any folder.
- P1 says "before save", but Krita has no before-save signal. Debounced writes that keep the
  annotation current, as Krita AI Diffusion does, may be the answer; S4 checks it in Krita.
- Q1 (generation as an action only, or also as an ingredient) waits on S5. Build the action-only
  form, with the choice in one place.
- A document the plugin first sees after it already has content is decided (decision 2 above).
  Still open for the Krita glue: what the export action does when such a document has never
  been saved, since there is no .kra to be the parent. Saving first is the likely answer.
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
