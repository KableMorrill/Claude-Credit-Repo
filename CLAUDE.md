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

## Handover (13 Oct 2026, from the Fable session, updated by the Opus session)

Read this first, then "Where things stand". Branch `claude/eloquent-hypatia-vzwd8l`, CI green
through `5952830`; every later commit passed all four checks locally, from `c86d349` on in the
user's Windows 11 checkout (Python 3.12 venv; not pushed by Claude, so CI hasn't run them).

Results in (13 Oct 2026, Opus session): smoke rows 1 to 8 pass on Windows 11 (S1's Python
check and S4); the Adobe check (S2) is pending because Adobe's tools failed for every upload.
The user tests each piece below that touches Krita before the next one starts.

Then the rest of step 7, in this order, each piece committed with the four checks passing:

1. Annotation writes. **Built; rows 9 and 10 pass, row 11 awaits a rerun after a fix.**
   Planned as debounced writes flushed on `Notifier.imageSaved`, but that signal fires after
   the file is written, so a debounced event could miss a save. Built instead: every change is
   written at once (`storage.append`; `Debounce` removed; storage.py's docstring has the
   reasoning), and `Notifier.imageCreated` starts the log when Krita creates or opens a
   document. The user agreed to dropping the debounce on 14 Oct 2026. Row 11 found that
   writing an annotation marks the document modified; `extension.ensure_log` now clears the
   mark after starting a log in a document that had no unsaved changes.
2. First run: data folder = a `krita-provenance` subfolder under Krita's app-data location
   (`QStandardPaths.AppDataLocation`), never the plugin folder. `installer.plan()` then
   `installer.install(folder, plan, progress=...)` behind a progress dialog, `installer.activate`
   before any `sdk`/`keys` call, then `keys.ensure(folder / "keys")` and show
   `KeyFiles.info().to_text()`. On a platform where `plan.supports_k1` is False, show the
   message in `plan.unavailable`.
3. Adapter `krita_provenance/adapters/ai_diffusion.py` (the only importer of `ai_diffusion`):
   find the per-document model via `root.model_for_active_document()`, snapshot node unique
   IDs before the apply, connect `jobs.result_used`, copy `JobParams` into a
   `log.GenerationApplied`, and append a `CaptureGap` on any failure (C3). Facts are below.
4. Export action: `Document.exportImage` to a temp PNG/JPEG, `mapping.manifest`,
   `sdk.sign_file`; on `SigningError` save under `sdk.unsigned_name`. For an
   `existing_document` with unsaved changes show "Save and export signed" / "Export unsigned" /
   "Cancel" (never save silently). Label it neutrally, no "Content Credentials".
5. Docker (V1) showing `storage.summary` and the events; release zip (I1).
6. One review at the end, limited to E6 labelling, key creation and storage, the installer
   hash check, and the log never losing events. Budget note: the three adversarial review
   workflows this session cost more than all the implementation; do not run more than that one.

Lessons that cost time this session: libkis wrappers die after the call that received them
(rule below); ruff's preview formatter rewraps lines, so edit files with exact anchors read
from disk, not from memory; `re.sub` interprets backslashes in replacements; pytest's
importlib mode needs shared helpers on `pythonpath` (`tests/goldens.py`), not imported from
test modules.

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
     The Adobe check is pending (S2, under "Not done").
- **Also done (13 Oct, later):** K1 and I2, Krita-free and tested. `keys.py` makes the local
  root CA and signing certificate with `cryptography`; a golden signed with freshly made keys
  validates with the untrusted signer as the only failure code. `wheels.py`, `wheel_pins.py`
  and `installer.py` fetch c2pa-python 0.38.0, cryptography 50.0.2 and cffi 2.1.1 for this
  platform and Python, verify size and SHA-256 against the pins, unpack them into the data
  folder and put them first on `sys.path`; `scripts/update_wheel_pins.py` regenerates the pins
  from PyPI. Real installs on this Linux x86_64 machine, on Python 3.13 and on Python 3.10 (the
  set Krita 5 fetches: c2pa-python manylinux_2_28, cryptography cp39-abi3, cffi cp310),
  downloaded, verified and unpacked all three, and a clean Python imported them from the
  folder. The macOS and Windows wheel sets have not been fetched through the installer.
- **Decisions of 13 Oct 2026, second set**, from the user, for step 7:
  1. **Root key:** discarded after issuing the signer certificate. Rotation (K4, later) means a
     new root and signer with a new fingerprint. Built: `keys.py` writes three files.
  2. **Key storage:** unencrypted PEM in the data folder is fine for v0.1, with restrictive
     permissions where the OS supports them; claim no more on Windows than the profile folder
     gives. Validity stays 20y/10y with the one-day backdate; revisit with K3.
  3. **Older document with unsaved changes at export:** a dialog with "Save and export signed",
     "Export unsigned" and "Cancel". Never save silently.
  4. **Intel Mac and Windows ARM:** accept the gap; don't pin an old cryptography. Show that key
     creation isn't available on this platform yet and point to K2. Built: `installer.plan`.
  5. **Data folder:** a `krita-provenance` subfolder in Krita's app-data location, never inside
     the plugin folder, so reinstalling can't delete keys.
  6. **Timestamps** stay off for v0.1; opt-in later under K3.
  7. **Review:** one review at the end of step 7, limited to E6 labelling, key creation and
     storage, the installer hash check, and the log never losing events.
  8. **Order:** annotation storage first, then stop for the user to test it in Krita (S4) while
     they run S1 and upload `samples/` to Adobe Verify.
- **Step 7, first part, done (13 Oct, later still):** `storage.py` reads and writes the log as
  the `krita-provenance.json` annotation, chooses the start reason from the file name (none:
  `new_document`; image: `opened_image` with the file as parent; .kra or .ora:
  `existing_document` with the file as parent), summarises the log and holds the debounce
  policy. `extension.py` adds Tools ▸ Scripts ▸ Provenance: Log Status, which creates the log
  if absent and shows the summary; docs/smoke-checklist.md rows 4 to 8 are the S4 check.
  First Krita run (Windows 11, Krita 5.3): both actions failed because their handlers kept
  the createActions Window; fixed by looking the window up when the action fires (ee9c96c).
  **S4 passes** (13 Oct 2026; Windows 11, Krita 5.3.4.1, Python 3.13.5, PyQt5, Krita AI
  Diffusion 1.53.0): smoke rows 1 to 8 pass. The log keeps its id and start time through save
  and reopen, Krita AI Diffusion's history survives beside it, and opened PNGs and older .kra
  files start with the right reason and parent.
- **Step 7, piece 1 (13–14 Oct 2026):** `extension.py` connects `Notifier.imageCreated` and
  starts the log when Krita creates or opens a document (skipping a document with no image
  yet), so `log_started.at` is when the plugin began observing it, not when Log Status first
  ran. An unreadable log is left alone, with a warning shown after the open. `storage.append`
  loads or starts the log, appends one event and returns the bytes to write at once; nothing
  calls it yet (the adapter and the export will). Smoke rows 9 and 10 pass. Row 11 failed:
  Krita asked to save an older .kra that was only opened, because setting an annotation marks
  the document modified. `ensure_log` now restores the flag after starting a log (starting
  one isn't an edit). Events appended later keep the flag Krita sets. Row 11 awaits a rerun.
- **Not done:** the rest of step 7: the Krita AI Diffusion adapter
  (C1–C3), first-run install and key creation in Krita, the export action with the dialog of
  decision 3, the docker (V1) and the release zip (I1). S1 is half done: the Python version is
  checked on Windows, but loading c2pa-python inside Krita and signing from it wait on first
  run (piece 2 of the handover). S3 and S5 haven't run. S2: c2pa-python and c2patool accept the
  certificate profile and validate the samples, but the Adobe check is **pending**. On 13 Oct
  2026 Adobe's Verify (verify.contentauthenticity.org) answered "Something went wrong" for
  every upload, unrelated images included. Adobe Content Authenticity Inspect
  (contentauthenticity.adobe.com/inspect) said "We couldn't verify how this was made" for every
  upload, and for our samples only, also popped "Something went wrong". On 14 Oct a bisection
  ruled out size: unsigned 16 px and 512 px PNGs drew only the generic message, and the same
  512 px PNG signed with the `nothing_observed` or `new_document` golden popped "Something went
  wrong". So Inspect fails on our signed files, or on every test-signed file. Our files differ
  from c2pa-python's sample `C.jpg` (claim v1, PS256, timestamped 2023) in claim version (v2)
  and in having no timestamp. A second round is with the user: a minimal manifest (one
  `c2pa.created`), then that manifest with an IPTC source type, with C2PA's public test
  certificate, with a DigiCert timestamp, as claim v1, as JPEG, and `C.jpg` itself. Until an
  Adobe tool reads them, c2patool and c2pa-python read-back are the verification of record.
  The go/no-go note is due 25 Oct 2026.
- **Next:** the rest of step 7 in the handover's order, stopping after each piece that touches
  Krita for the user to test it.

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
  storage.py               P1: the log as the krita-provenance.json annotation; start reason
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
  `scripts/update_wheel_pins.py`, run it, bump the dev dependency to match
  (`tests/test_wheel_pins.py` checks that pyproject's pins equal `PINS`), update the versions
  and the K1 gaps that `tests/test_wheel_pins.py` and `tests/test_installer.py` spell out, and
  review the diff: those digests are what every download is checked against.
- `ai_diffusion` is imported only in `krita_provenance/adapters/ai_diffusion.py`.
- libkis wrappers (Window, Document, Node) are never kept beyond the call that received them.
  Action handlers take no arguments and look up `Krita.instance().activeWindow()` and
  `activeDocument()` when they fire; the Window passed to `createActions` is deleted after
  setup, and a handler that captured it failed in Krita 5.3 with "wrapped C/C++ object of type
  Window has been deleted" (13 Oct 2026). `tests/test_architecture.py` checks for the capture.
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
  S1 (13 Oct 2026, smoke row 2): Krita 5.3.4.1 on Windows 11 runs Python 3.13.5, which the
  pins cover (cffi cp313, cryptography cp311-abi3, c2pa-python win_amd64). Keep targeting
  3.10: pyright and CI stay on it, so the plugin also runs on older Krita builds and the pins
  cover 3.10 to 3.14. macOS and Linux Krita builds are unchecked.
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
  The annotation types carry an `ai_diffusion/` prefix (`ai_diffusion/ui.json`), added by
  `KritaDocument.annotate`
  ([document.py L258–266](https://github.com/Acly/krita-ai-diffusion/blob/0217cd2197fcadbd70d7e63af25e29cc21cb7c8b/ai_diffusion/document.py#L258-L266)).
  The first time it wraps a document with no `ai_diffusion/document_id` annotation, it writes
  one ([L120–130](https://github.com/Acly/krita-ai-diffusion/blob/0217cd2197fcadbd70d7e63af25e29cc21cb7c8b/ai_diffusion/document.py#L120-L130)),
  which marks the document modified, as any annotation write does (Krita 5.3 source, below).
  It gives a copy of an open document (same id) a new id.
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
  4 MB. No macOS Intel, no Windows ARM, no win32 or i686. PyPI's release files, read on 13 Oct
  2026, show 46.0.3 (Oct 2025) as the last release with a macOS Intel (`macosx_10_9_x86_64`)
  wheel and a `win_arm64` wheel; 46.0.4 onwards has neither.
- cffi 2.1.1: one wheel per CPython minor (3.10 to 3.15) for manylinux_2_17, macOS 10.15 x86_64
  and 11 arm64, win_amd64 and, from 3.11, win_arm64; about 200 KB.
- Free-threaded (`cp314t`) and PyPy wheels exist and are skipped: Krita's Python is neither.

**Krita API stubs** (`scripts/typeshed/krita.pyi`): `Document` has `annotation`,
`setAnnotation`, `annotationTypes`, `removeAnnotation`, `exportImage`, `topLevelNodes` and
`nodeByUniqueID`; `Node` has `uniqueId`, `childNodes` and `bounds`. `Notifier.imageSaved` fires
after a save; there is no before-save signal. The stubs declare Notifier's signals as plain
methods, so `.connect` on them needs a `pyright: ignore`.

**Krita 5.3 source** (commit `bd6e8d0`, the build the user runs, read 13 Oct 2026):

- `Document::setAnnotation` uses `d->document->image()` without a null check, so it must never
  be called on a document with no image; `rootNode()` and `width()` do check, and return None
  or 0. It calls `KisImage::addAnnotation`, which replaces the annotation and calls
  `setModifiedWithoutUndo()`, and so does `removeAnnotation`. That reaches
  `KisDocument::setModified(true)` synchronously: the image and its signal router live in the
  GUI thread (`moveToThread(qApp->thread())` in KisImage's constructor). So every annotation
  write marks the document modified; smoke row 11 showed it on 14 Oct 2026, after this file
  had wrongly said otherwise. `Document.setModified(bool)` is in 5.3's bindings
  (Document.sip) but not in our stubs. `KisDocument::setModified(false)` also clears
  `imageModifiedWithoutUndo`, and autosave skips an unmodified document.
- `Notifier::imageCreated` follows `KisPart::sigDocumentAdded`. It wraps the KisDocument in a
  new `Document`, emits, and deletes the wrapper as soon as the handlers return. For a local
  file, `KisMainWindow::openDocumentInternal` adds the document after `openPath` (or
  `importDocument`) returns. libkis `Krita::createDocument` adds it before `newImage`, so a
  script-made document has no image when the signal fires. An autosave being recovered still
  has its autosave path then; Krita renames it afterwards.
- `Notifier::setActive(false)` blocks the notifier's signals; it is shared, so the plugin only
  ever sets it active.
- The PNG exporter writes only the `kpp_version` and `kpp_preset` annotations, and the JPEG
  exporter writes none: ordinary PNG and JPEG exports carry neither our log nor `ui.json`.

## Open technical questions

Raise these with the user rather than settling them silently.

- K1 and I2 are built. Pinned: cryptography 50.0.2 and cffi 2.1.1, the newest at the time.
  cryptography 50.0.2 ships no macOS Intel and no Windows ARM wheel (46.0.3 was the last that
  did), so on those machines the installer fetches c2pa-python alone, K1 is unavailable and
  K2's own certificate is the way to sign. The spec's matrix (Windows x64, macOS arm64, Linux
  x64) is fully covered.
- Q5, K4 and the data folder are decided (second set above). What the first-run dialog looks
  like is the glue's; `installer.install` reports progress per wheel.
- P1's debounce is dropped (user, 14 Oct 2026): Krita has no before-save signal and
  `imageSaved` fires after the file is written, so every change is written at once.
- An `exported` event (piece 4) is appended to a document that may have no unsaved changes, and
  the write marks it modified, so Krita will ask to save on close. Keeping that prompt lets the
  user keep the event; clearing the mark would lose the event when they close without saving.
  Decide with the user in piece 4.
- A document with no file name may already have content when Krita creates it: Create from
  Clipboard, a template, Open existing Document as Untitled, an unnamed autosave recovered.
  Its log starts as `new_document`, which the mapping turns into `c2pa.created` from an empty
  canvas, and that is false for it. Not settled; ask the user before the export action
  (piece 4) ships. One option: at creation, treat the canvas as blank only if every layer is
  empty or one flat colour, and otherwise use a start reason that the mapping discloses like
  `existing_document`.
- Q1 (generation as an action only, or also as an ingredient) waits on S5. Build the action-only
  form, with the choice in one place.
- A document the plugin first sees after it already has content is decided (decision 2 above),
  and an unsaved one at export gets the save/unsigned/cancel dialog (second set, 3).
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
