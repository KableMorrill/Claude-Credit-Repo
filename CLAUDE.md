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

Read this first, then "Where things stand". Branch `claude/eloquent-hypatia-vzwd8l`, pushed at
the user's request on 14, 15, 16, 18 and 19 Oct 2026 and merged into `main` on 19 Oct 2026 (CI
green through `2b62535`; check the run for later pushes). From `c86d349` on, work happens in the user's Windows 11 checkout (Python 3.12 venv,
no c2patool), where all four checks pass.

Results in (13 Oct 2026, Opus session): smoke rows 1 to 8 pass on Windows 11 (S1's Python
check and S4); the Adobe check (S2) is pending because Adobe's tools failed for every upload.
The user tests each piece below that touches Krita before the next one starts.

Then the rest of step 7, in this order, each piece committed with the four checks passing:

1. Annotation writes. **Done 14 Oct 2026; smoke rows 9 to 11 pass.**
   Planned as debounced writes flushed on `Notifier.imageSaved`, but that signal fires after
   the file is written, so a debounced event could miss a save. Built instead: every change is
   written at once (`storage.append`; `Debounce` removed; storage.py's docstring has the
   reasoning), and `Notifier.imageCreated` starts the log when Krita creates or opens a
   document. The user agreed to dropping the debounce on 14 Oct 2026. Row 11 found that
   writing an annotation marks the document modified; `extension.ensure_log` now clears the
   mark after starting a log in a document that had no unsaved changes.
2. First run. **Done 15 Oct 2026; smoke rows 12 to 16 pass, and with them S1 on Windows.** Tools ▸ Scripts ▸
   Provenance: Set Up Signing asks before downloading (`firstrun.consent_text`), then runs
   `firstrun.run` on a Python thread behind a progress dialog: install, `installer.activate`,
   `keys.ensure(folder / "keys")`, and a self-test that signs a blank PNG and reads it back.
   The report shows the certificate (`KeyFiles.info().to_text()`), or `plan.unavailable` where
   K1 isn't possible. Packages join `sys.path` only when signing needs them, not at startup.
3. Adapter. **Done 16 Oct 2026; smoke rows 17 to 21 pass, and S3 with them (rows 18 and 19
   passed on a rerun after a C2 fix).** `krita_provenance/adapters/ai_diffusion.py` (the only importer
   of `ai_diffusion`, Krita-free) follows every model through `root.models` and
   `root.model_created` rather than `model_for_active_document()`. It connects
   `jobs.result_used` and copies `JobParams` into a `log.GenerationApplied`. `extension.py`
   attaches it once all plugins have loaded, snapshots the active document's node ids at each
   mouse press and key press as C2's "before", appends the event at once, and logs a
   `CaptureGap` on any failure (C3).
4. Export action. **Done 16 Oct 2026; smoke rows 23 to 32 pass, pushed as `86ff031`.** A
   4000×4000 export takes about a second from choosing the file (M1's target: 3). Tools ▸ Scripts ▸
   Provenance: Export Signed Image… (neutral label). `extension.py` says first when an export
   can't be signed and offers an unsigned copy. For a document opened from a .kra with unsaved
   changes it shows "Save and export signed" / "Export unsigned" / "Cancel"; it never saves
   silently. It renders with `Document.exportImage` (batch mode, empty `InfoObject`) to a
   temporary file and collects layer visibility and the parent's bytes. `exporter.export`
   (Krita-free, tested with real signing) builds the manifest, signs, reads back (E4) or saves
   `<name>-unsigned.<ext>` with the reason (E6), and returns the `exported` event, which is
   appended to the log.
5. Docker (V1) and release zip (I1). **Done 18 Oct 2026; smoke rows 33 to 40 pass, pushed as
   `582109f`.** With no document open, Krita hides its dockers behind the welcome page.
   Settings ▸ Dockers ▸ Provenance Log: `_LogDocker` (extension.py) shows
   `storage.summary(entry, latest=None)`, the last export's result in this session, and the
   capture status, in a read-only QPlainTextEdit (plain text: layer names hold prompts). A
   child timer re-reads after `canvasChanged` and after every log write (`_Changes.changed`),
   so it never reads inside Krita's open, Krita AI Diffusion's apply, or `unsetCanvas`.
   `scripts/build_release.py` builds `dist/krita-provenance-<version>.zip`, the same bytes for
   the same checkout. Krita's own importer code (plugin_importer.py at bd6e8d0, run outside
   Krita with a stand-in `i18n`) imported it whole.
6. One review at the end, limited to E6 labelling, key creation and storage, the installer
   hash check, and the log never losing events. Budget note: the three adversarial review
   workflows this session cost more than all the implementation; do not run more than that one.
   **Done 18 Oct 2026**, inline and in those four areas only: three fixes and a docstring
   ("Where things stand", piece 6). Step 7 is complete.

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
  folder. On 14 Oct 2026 `firstrun.run` did the same on the user's Windows 11 machine, outside
  Krita, in a Python 3.12 venv: c2pa-python win_amd64, cryptography cp311-abi3 win_amd64 and
  cffi cp312 (91.9 MB, 6.3 s). It loaded c2pa and cryptography from the data folder, made the
  keys, and the self-test read back Valid with only the untrusted signer. A second run took
  0.3 s and kept the key. Krita's Python 3.13 differs only in taking cffi cp313. The macOS set
  hasn't been fetched through the installer.
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
  one isn't an edit). Events appended later keep the flag Krita sets. The rerun of row 11
  passes for this plugin: Krita still asks while Krita AI Diffusion is enabled, because it
  writes its own document id into a document it hasn't seen, and doesn't ask with it disabled.
- **Step 7, piece 2 (14–15 Oct 2026), verified in Krita:** `firstrun.py` (Krita-free, tested
  with faked downloads and the real c2pa-python and cryptography) holds the data folder rule
  (refuses a folder inside the plugin), the consent text, overall progress and cancellation,
  install → activate → keys → self-test, and the report. The report flags any of c2pa,
  cryptography or cffi loaded from outside the verified install, because then the running code
  isn't the code whose SHA-256 was checked. `extension.py` adds Provenance: Set Up Signing; a
  QObject made in the GUI thread carries the worker's progress and result back to it. Check
  Environment reports the plugin's own install even before it joins `sys.path`. Smoke rows 12
  to 16 pass (15 Oct 2026): inside Krita 5.3.4.1 on Windows, the real download verified and
  installed, c2pa-python 0.38.0 loaded in Krita's Python 3.13.5 from the data folder, the keys
  were made and kept across a restart, and the self-test signed and read back. Cancelling
  mid-download installs nothing; it leaves the data folder and an empty `downloads` folder,
  which installer.py keeps on purpose (removing it could break a second install running at the
  same time).
- **Step 7, piece 3 (15 Oct 2026), unverified in Krita:** the Krita AI Diffusion adapter
  (C1 to C3). It never imports `ai_diffusion` unless Krita already has (importing it registers
  the extension, which would revive a disabled plugin). It follows each model weakly, so a
  closed document's model can go. Each applied result becomes a `generation_applied` event with
  the job's params, full prompts included (they stay in the .kra), the applied image's size as
  bounds, and the target layer. The target is the one paint layer that appeared since the last
  snapshot; with none or several (regions, layered results), it is None, which the manifest
  discloses. A result that can't be described becomes `capture_gap` `adapter_error`. If
  Krita AI Diffusion runs but can't be followed, a warning says so, and each document observed
  in that session gets `capture_gap` `ai_diffusion_unobserved`, whose statement runs "until
  Krita was closed". Log Status lists the latest events; Check Environment and Log Status show
  the capture status. Tested with stand-ins shaped like 1.53.0's objects. In Krita (16 Oct
  2026): capture works, and rows 17, 20 and 21 pass. Rows 18 and 19 recorded every apply,
  including a re-applied older result as its own event, but all as "layer not identified".
  The cause: the snapshot came from a half-second timer, and Krita's busy-wait processes
  events inside the apply (facts, below), so the timer could fire after the new layer existed.
  Now the snapshot is taken by an application event filter on mouse and key presses, which
  Krita doesn't process during the apply, and never while a modal dialog is up. Row 22's two
  applies were ordinary ones (the region was dropped), and their layers were named.
- **Decisions of 14 Oct 2026**, from the user: (1) P1's debounce is dropped; every change is
  written at once. (2) Plain-language statements give dates in UTC and say so: "since
  15 October 2026 (UTC)" (`mapping.ai_use_statement`). (3) Pushing to the branch so CI runs is
  approved for these commits; ask again before pushing later work.
- **Decisions of 16 Oct 2026**, from the user: (1) regions are written as four-corner polygons,
  not rectangles, so Adobe's Verify (c2pa-rs 0.85.2) can read them; the spec's table says
  "Rectangle", and the area is the same. (2) Manifests don't name the layer that received a
  generation, since Krita AI Diffusion names it after the prompt; the log keeps the name.
  (3) Piece 3 was pushed (`772bb37`); ask again before pushing later work.
- **Decisions of 16 Oct 2026, second set**, from the user, for the export action: (1) an
  opened image's bytes are copied into the document, in the `krita-provenance/ingredient-1`
  annotation, when the log starts, so a signed export can include its parent even after the
  document is saved as .kra (up to 256 MB; costs .kra size). (2) An untitled document whose
  canvas isn't blank when Krita creates it starts as `started_with_content`. It can't be
  signed until the plugin sees it reopened from a saved .kra, which logs that file as its
  parent, and then it maps like an existing document. (3) Logging an export marks the document
  modified, so Krita asks to save on close and the event isn't lost.
- **Step 7, piece 4 (16 Oct 2026), verified in Krita (smoke rows 23 to 32):** the export action (handover item 4)
  and the three decisions above. `_canvas_is_blank` reads each paint layer a strip at a time;
  any vector layer, mask or other node counts as content. `_parent_data` uses the kept copy,
  else the document's own file when it is the parent (any .kra or .ora, since c2pa-rs doesn't
  bind those bytes). Golden case `started_with_content` and `tests/test_exporter.py` cover
  the Krita-free side. Placed files (componentOf, C6) can't be signed yet and export unsigned.
  In Krita: PNG and JPEG signed and read back Valid with the signer untrusted, Verify showed
  the actions, an image reopened from its .kra signed with the kept copy as parent, a pasted
  document refused to sign until saved and reopened, and a 4000×4000 export took about a
  second. Not reported: Verify's view of row 27's parent and what row 28's two buttons did.
- **Step 7, piece 5 (16 Oct 2026), verified in Krita (smoke rows 33 to 40, 18 Oct 2026):** the
  docker and the release zip (handover item 5). `tests/test_release.py` checks the zip against
  the importer's rules, its contents against the package, and that two builds match byte for
  byte. In Krita the zip imported through Import Python Plugin from File, and the data folder,
  with its packages and key, came through the install untouched (decision 5).
- **Decisions of 18 Oct 2026**, from the user: (1) keep each export's read-back result in the
  log for good, adding a field, provided it doesn't interfere with C2PA. Built: `exported`
  events carry `read_back` (`log.ReadBack`: the SDK's state and failure codes), left out when
  unsigned and in exports logged earlier, so those write back unchanged. The spec's log
  example doesn't show it. The mapping skips `exported` events, so manifests are unchanged
  (the goldens still match). Log Status and the docker end the line "read back Valid, signer
  untrusted"; the docker's "Last export in this session" still adds the path and, for an
  unsigned export, the reason, which the log doesn't keep. (2) Piece 5 was pushed (`582109f`);
  ask again before pushing later work.
- **Step 7, piece 6 (18 Oct 2026): the review**, done once, inline, in its four areas.
  - E6 labelling, fixed: when the signed file couldn't be moved into place (a destination
    another program holds open on Windows), `sdk.sign_file` left its signed `.part` file beside
    it and the export failed with no unsigned copy. Now the temporary file is removed and the
    export saves `-unsigned` with the reason. Otherwise sound: signing goes to a temporary
    file that moves into place only after the read-back validates; every failure removes it;
    the fallback name never takes or overwrites the chosen one.
  - Key creation and storage: no change. The root key is never written. The signer key is
    created exclusively (owner-only on POSIX, the profile folder on Windows). Existing keys are
    never replaced, a partial set stops with "move them aside", and no user or machine name is
    in the certificates.
  - Installer hash check: no defect. Size and SHA-256 must match the pin before anything is
    unpacked, and unpacking refuses unsafe paths. The docstring now states the boundary:
    what's unpacked isn't hashed again, since anyone who can write to the data folder can
    change `site-packages` anyway.
  - The log never losing events, fixed twice. (1) When a Krita AI Diffusion result's document
    couldn't be identified (possible if an untested version changes its internals), nothing
    was logged, though the capture status promises a gap for any failure. Now every open
    document's log gets `capture_gap` `ai_diffusion_unattributed`, disclosed as possibly this
    document. (2) A document the plugin first meets in `storage.append` (one made by a script,
    which has no image when Krita announces it) started as `new_document`, a blank-canvas
    claim for a canvas the plugin never saw; it now starts as `started_with_content`. Checked
    and kept: observation events at open are saved with the next change and re-added at each
    open until then, and a declined save prompt discards new events with everything else
    (smoke row 37).
- **Results, 18 Oct 2026:** smoke row 42 passes (a script-made document starts as
  `started_with_content`). Row 41 passes on a rerun; its first export ran in a Krita session
  started before decision 1 reached the checkout. Row 37's missing events came from closing row 30's
  document without saving: the user had taken the save prompts for Krita AI Diffusion's. The
  prompt after an export is this plugin's (decision 3 of 16 Oct). **M1's c2patool gate item is
  met:** c2patool 0.27.22 (GitHub's Windows build, SHA-256 checked, run from the scratchpad, not
  installed) read seven real exports from Krita as Valid, with `signingCredential.untrusted` the
  only failure code and the expected actions. It found no claim in the unsigned export.
- **Decisions of 18 Oct 2026, second set**, from the user: (1) no compatibility with older logs
  or older plugin versions is needed while this is a dev build only the user runs; settle the
  open question about unknown fields closer to a release. (2) `ecb48ab` and `c31ab29` were
  pushed; ask again before pushing later work.
- **Decisions of 18 Oct 2026, third set**, from the user: (1) the export result doesn't add
  "save to keep this export in the log": clutter, since anyone who wants the record saves
  their work. (2) docs/spec.md's log example now shows `read_back`, at the user's request, and
  the `spec_example` golden log matches it (its manifest is unchanged). (3) `fe60895` was
  pushed; ask again before pushing later work.
- **S5 passes, with Firefly standing in for Photoshop (18 Oct 2026).** The user has no Adobe
  subscription, and no public Generative Fill sample turned up (the C2PA public test files hold
  Adobe's synthetic 2022 samples and Nikon and Truepic camera images). Firefly edited the
  user's signed S36.png twice (facts, below). Adobe records each AI edit as a `c2pa.edited`
  action with `compositeWithTrainedAlgorithmicMedia` and the model as software agent, never as
  a `componentOf` ingredient. That is v0.1's form, and Q1 keeps it (fifth set, below). The
  user's first file came from Google's Gemini Flash partner model and carries only Google's
  manifest. docs/go-no-go.md is updated.
- **Decisions of 18 Oct 2026, fourth set**, from the user: (1) an opened image's statement ends
  "The document began as an image opened from a file; how that image was made is not recorded
  by this plugin." ("for now"); built, smoke row 43 passes. (2) `b6e015c` was pushed; ask again
  before pushing later work.
- **Decisions of 18 Oct 2026, fifth set**, from the user: (1) **Q1:** generations stay
  `c2pa.edited` actions with `compositeWithTrainedAlgorithmicMedia` alone, as Adobe records
  them; no `componentOf` ingredient. (2) **No network at export:** sdk.py runs every SDK call
  with remote manifest fetching off (`sdk.SETTINGS`, `_context`); `tests/test_sdk.py` signs
  with a parent linked to a local server and checks that nothing was requested. Fetching an
  opened image's online manifest with consent at open time is C6 and P2's (v1.0). (3) The
  thumbnail of an opened image in exports stays for now and becomes a choice of D2's
  disclosure levels (v1.0): useful, but not wanted by everyone, and it takes space.
  (4) `10b4aae` was pushed; ask again before pushing later work.
- **Decisions of 18 Oct 2026, sixth set**, from the user: (1) docs/spec.md ticks Q1 (settled
  by S5) and the finished items under "This week" (all but S1, which macOS and Linux still
  lack); its snapshot header now names the edits made at the user's request. The live Claude
  Doc it was exported from got the same edits on 19 Oct 2026 (revision 22). (2) `2b62535` was pushed; ask again before
  pushing later work. (3) Claude drafted the Krita AI Diffusion issue for the user to post
  (Phase B: one documented signal for applied results, carrying params, layer IDs and kind).
  The draft lives in the session's scratchpad, not the repo.
- **Decision of 18 Oct 2026, seventh set**, from the user: no contact with Krita AI Diffusion
  until v1.0 (no issue, Phase B hook proposal or Discussions post); the user doesn't want a
  first open-source contribution to reach a major project unfinished. Meanwhile the user posts
  progress on LinkedIn, starting with the demo. This moves M1's "Krita AI Diffusion issue
  opened with a demo" past v1.0, beside M4's upstream PRs and write-up. At v1.0, redraft the
  issue against the Krita AI Diffusion of the day; the proposal's substance is in the sixth
  set, above.
- **S1 on macOS and Linux:** the user has neither, but may have a spare desktop for Linux.
  Options given: WSL on the Windows 11 machine, or the spare PC booted from a USB stick, with
  Krita's AppImage and the release zip. For a Mac: borrow one, or rent an Apple-silicon Mac by
  the hour (Scaleway; Apple's licence sets a 24-hour minimum). Possibly CI on GitHub's macOS and
  Linux runners, if Krita's downloads can run Python without the interface (unchecked).
- **Decisions of 19 Oct 2026**, from the user: (1) the README's "Platforms" note says the plugin
  is tested inside Krita on Windows 11 only, asks macOS and Linux users for reports (smoke rows
  1–16), points Mac users to krita.org's build rather than the Mac App Store's, and says Intel
  Macs and Windows on ARM can't sign yet. Mac testing isn't required by Krita AI Diffusion, only
  by the spec's own S1 and v1.0 matrix, which the user hasn't asked to change. (2) Linux is
  tested inside Krita as v1.0 approaches, not before. (3) `dc1e39b`, `9235678` and `b25c242`
  were pushed with the README change, and this branch was merged into `main` (a fast-forward),
  so `main` now holds the plugin. (4) The live Claude Doc got this week's spec edits on 19 Oct
  2026 (revision 22): `read_back` in the log example and its bullet, Q1 settled and ticked, and
  the finished "This week" items ticked.
- **Go/no-go note:** drafted in [docs/go-no-go.md](docs/go-no-go.md) on 18 Oct 2026; final
  once S5 runs. Recommendation: go.
- **Not done:** S1 inside Krita on Linux (as v1.0 approaches) and macOS (by users' reports).
  **S1 passes on Windows** (15 Oct 2026): Python 3.13.5, and c2pa-python loads and signs
  inside Krita; macOS and Linux are unchecked. **S3 passes** (16 Oct 2026, smoke rows 18 to 20).
  S5 hasn't run. **S2 passes** (16 Oct 2026): Verify reads the polygon samples and shows
  their actions (created with krita-provenance, Krita AI Diffusion as the AI model, edited
  with AI). History: **S2's Adobe check passes for a
  minimal manifest** (14 Oct 2026). Adobe's Verify (verify.contentauthenticity.org), which
  answered "Something went wrong" to every upload on 13 Oct, now reads a PNG carrying one
  `c2pa.created` action, signed with our test certificate (the profile `keys.py` makes). It
  shows "Created with krita-provenance" and "These Content Credentials were issued by an
  unrecognized source", as S2 expects. It reads the same with an IPTC source type, with C2PA's
  public test certificate, as JPEG and as claim v1 (plus "This file uses a legacy version of
  the C2PA Technical Specification"). With a DigiCert timestamp it also shows the signing
  date; without one it shows no date. c2pa-python's sample `C.jpg` reads too. **Full
  manifests fail in Verify, and the cause is found** (15 Oct 2026). Every file whose actions
  carry `changes` with a rectangle region draws "Something went wrong" in Verify: all of
  `samples/`, and regions inside or outside the image alike; region-free actions read. Verify
  reads with c2pa-rs 0.85.2, which can't decode a rectangle's optional `width` and `height`
  (facts, below), even in a file it signed itself. On 16 Oct the user chose polygons:
  `mapping.region` writes each region as its four corners, the goldens and `samples/` were
  regenerated, and all five samples read Valid in c2pa-rs 0.85.1 with every action. On 16 Oct
  the user uploaded them to Verify: no errors, and the actions show. The go/no-go note is due
  25 Oct 2026.
- **Next:** the go/no-go call (25 Oct 2026); the user's LinkedIn posts, demo first (Claude may
  draft them; never posts); then M2.

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
  firstrun.py              I2 + K1 at first run: data folder, consent text, install, keys, self-test
  storage.py               P1: the log as the krita-provenance.json annotation; start reason
  exporter.py              E1-E6 after rendering: sign under the chosen name or save -unsigned
  adapters/ai_diffusion.py C1-C3: follows Krita AI Diffusion's models; job -> generation_applied
krita_provenance.desktop   plugin manifest Krita reads at startup
scripts/dev_install.py     links this checkout into Krita's pykrita folder
scripts/make_test_certs.py makes the test certificates in tests/fixtures/certs with OpenSSL
scripts/make_samples.py    signs the goldens into samples/ for other validators
scripts/build_release.py   builds the release zip (I1) into dist/
scripts/update_wheel_pins.py regenerates wheel_pins.py from PyPI; review the diff it makes
scripts/typeshed/          Krita API stubs, vendored unmodified (see the README there)
samples/                   signed sample exports, one per golden case (see the README there)
tests/                     pytest; nothing needs Krita. goldens.py loads tests/fixtures/goldens
tests/fixtures/goldens/    per case: log.json, export.json (what the exporter knew), manifest.json
tests/fixtures/certs/      test root CA and signing certificate; never sign anything real with them
tests/fixtures/images/     16x16 PNG and JPEG placeholders and a .kra-shaped zip for ingredients
docs/spec.md               the project spec
docs/smoke-checklist.md    checks the user runs inside Krita
docs/go-no-go.md           the go/no-go note that closes M0 (draft until S5 runs)
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
- Read 15 Oct 2026 for the adapter: `root = Root()` is a module-level singleton in
  `model/root.py`; `AIToolsExtension.__init__` calls `root.init()`, and `extension.py` calls
  `Krita.instance().addExtension(...)` at import, so importing `ai_diffusion` loads the plugin.
  `Root.model_created` is `pyqtSignal(DocumentModel)`. `KritaDocument` keeps its Krita
  document private (`_doc`) but exposes `is_active` and `id`, the value of its
  `ai_diffusion/document_id` annotation.
- Only `apply_generated_result` emits `result_used` (model.py L847–868). Live painting's
  `LiveWorkspace.apply_result` (L1312–1325), upscaling's `add_upscale_layer` and control layers'
  `add_control_layer`, both applied when their job finishes, don't. That answers Q10: applying
  a live result does not fire `result_used`.
- Generated layers are named after the prompt: `job_name` is the evaluated prompt (L316) and
  the layer is `"[Generated] {trim_text(params.name, 200)} ({seed})"` (L769); a region's layer
  is named after the region's prompt (L790). `metadata["loras"]` holds `{"name", "weight"}`
  for each LoRA (L324), so S3's question about LoRAs is answered: they're in the metadata.
- `LayerManager` polls the node tree every 500 ms, and its `create()` registers a new layer at
  once (`self.updated().wrap(node)`), so by `result_used` it already knows the new layer.
- `eventloop.run` only schedules a task on its own asyncio loop and never processes Qt events.
  But Krita does, inside `apply_generated_result`: creating and removing layers goes through
  libkis `Node.addChildNode` and `Node.remove`, which wait on the image (Krita 5.3 source,
  below), and that wait runs `QApplication::processEvents`. So Qt timers can fire in mid-apply,
  and C2's snapshot can't come from a timer (smoke rows 18 and 19, 16 Oct 2026).

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

**Older c2pa-rs can't read rectangle regions** (checked 15 Oct 2026 by reading signed files
with older c2pa-python releases in a scratch venv):

- c2pa-rs 0.85.1, 0.88.0, 0.89.0, 0.90.0, 0.90.8 and 0.90.16 fail on any `c2pa.actions.v2`
  whose `changes` hold a rectangle: "could not decode assertion c2pa.actions.v2 … Syntax error:
  Invalid simple type in option". 0.85.1 fails even on a rectangle it signed itself. 0.90.19
  and 0.91.0 read them. The changelog for 0.90.17 to 0.90.19 doesn't name the fix.
- The failing fields are the rectangle's `width` and `height`, `Option<f64>` in
  `region_of_interest.rs` (unchanged between 0.85.2 and 0.91.0), which every version encodes
  as CBOR doubles. A polygon (`origin` plus `vertices`, all plain `f64`) reads in 0.85.1, and
  so do floats in action `parameters`.
- Adobe's Verify (contentauth/verify-site, SvelteKit) depends on `@contentauth/c2pa-web`
  `^0.11.0`, which bundles c2pa-rs `=0.85.2` (c2pa-js Cargo.toml at that tag). c2pa-web 0.15.0
  bundles 0.90.22 and 0.15.3 (30 Sept 2026) bundles 0.91.0. Verify turns any read error into
  "Something went wrong" and logs it to the browser console from `createC2paReader.read()`.

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
- libkis `Node::addChildNode` and `Node::remove` run a single-command stroke and then
  `KisImage::waitForDone()`, so the node tree is current when they return. `waitForDone()`
  calls `KisBusyWaitBroker::notifyWaitOnImageStarted`, whose feedback callback (KisPart.cpp,
  `busyWaitWithFeedback`) runs `KisDelayedSaveDialog::blockIfImageIsBusy()`. While the image
  is busy, that loops on `QApplication::processEvents(QEventLoop::ExcludeUserInputEvents)` for
  up to a second, then shows a modal dialog with its own event loop. So timers and posted
  events run inside any libkis call that waits on the image; user input doesn't, except in
  that dialog.
- `Notifier::setActive(false)` blocks the notifier's signals; it is shared, so the plugin only
  ever sets it active.
- The PNG exporter writes only the `kpp_version` and `kpp_preset` annotations, and the JPEG
  exporter writes none: ordinary PNG and JPEG exports carry neither our log nor `ui.json`.
- Read 16 Oct 2026 for piece 5. Import Python Plugin from File
  (`plugins/python/plugin_importer/plugin_importer.py`) takes `X-KDE-Library` from each
  .desktop file in the zip and needs a directory entry `<name>/` holding `<name>/__init__.py`
  (files alone don't count). It asks before overwriting, `shutil.rmtree`s `pykrita/<name>`,
  copies the directory and the .desktop file, and ignores the rest. Then it asks "Enable
  plugins now? (Requires restart)" and writes the `enable_<name>` setting.
- macOS (read 19 Oct 2026, `packaging/macos/entitlements.plist`): krita.org's signed build has
  `com.apple.security.cs.disable-library-validation` (and `allow-jit`), so its Python may load
  native libraries signed by others, such as the wheels I2 downloads. The Mac App Store build
  (`macStore-entitlements.plist`) is sandboxed and doesn't disable library validation, so it
  may refuse them. Neither has been tried.
- libkis `DockWidget::canvas()` returns the docker's current `Canvas`; `unsetCanvas` calls
  `canvasChanged(None)` and only then deletes that Canvas, so `canvas()` still returns the
  dying one during the call. `Canvas.view()` and `View.document()` return None when there is
  no view. `DockWidgetFactory(id, position, cls)` is Python (`krita/dockwidgetfactory.py`);
  neither it nor the `DockPosition` enum is in our stubs.

**Images from other generators** (18 Oct 2026, c2patool 0.27.22 and c2pa-python 0.38.0; the
user's files, kept out of the repo):

- A Firefly download made with Google's Gemini Flash partner model holds one manifest,
  Google's: "Google C2PA Core Generator Library", claim v2, signed by Google LLC as "Google
  Media Processing Services", with a Google timestamp. Its actions are `c2pa.created` with
  `trainedAlgorithmicMedia` ("Created by Google Generative AI."), citing an `inputTo`
  ingredient holding only a description ("Input ingredient 0"), then `c2pa.edited` with
  `trainedAlgorithmicMedia` ("Applied imperceptible SynthID watermark."). Its XMP says the
  same in IPTC terms (DigitalSourceType `trainedAlgorithmicMedia`, credit "Made with Google
  AI"). Nothing in the file is Adobe's, and nothing links to a manifest elsewhere. c2patool's
  default trust settings report Google's signer as untrusted.
- An export with that file as its `parentOf` ingredient (a scratch run with the test
  certificate) validates with only our untrusted signer, and the store keeps Google's manifest
  as the parent's, beneath ours; c2pa-python and c2patool agree. An opened image that carries
  credentials keeps them through an export from Krita, even in v0.1.
- Firefly's own edits of the user's signed S36.png keep their manifests online: the PNG holds
  only an XMP `dcterms:provenance` link to `cai-manifests.adobe.com`. c2patool couldn't fetch
  it on this machine (TLS `UnknownIssuer`, as uv had), so the stores were fetched with
  PowerShell and read with c2pa-python's `Reader(format, stream, manifest_data)`. Each Firefly
  step is its own manifest, signed by Adobe Inc. ("Adobe C2PA" or "Adobe Firefly C2PA") with a
  timestamp.
  - The upload adds `c2pa.opened`, with S36.png's manifest as `parentOf`, and `c2pa.redacted`
    of our `c2pa.thumbnail.claim`, reason `c2pa.PII.present`.
  - A prompt edit adds `c2pa.edited`, `compositeWithTrainedAlgorithmicMedia`, software agent
    "Adobe Firefly Image 5", with `com.adobe.firefly.operation: text_to_image` and a version.
  - A Generative Fill adds `c2pa.edited`, `compositeWithTrainedAlgorithmicMedia`, software
    agent "Generate Fill 1", with the selected zone as an `inputTo` ingredient that has no
    manifest.
  - No action carries `changes` (regions), and no generated content is a `componentOf`
    ingredient. Our manifest survives at the root of both chains. All validate; c2pa-python's
    default trust settings report Adobe's signer as untrusted.
- With such a file as our parent, the export signs and validates, but the parent ingredient
  has no manifest: `manifest.inaccessible`, "Remote manifest not fetched". c2pa-rs tried to
  fetch it while signing (the entry quotes the failed request): a network call at export. A
  `c2pa.Context` built with `Settings.from_dict({"verify": {"remote_manifest_fetch": False}})`,
  passed to `Builder.from_json` and `Reader`, stops the request, and the entry then records
  only the URL. sdk.py has done so for every call since 18 Oct 2026 (fifth set).
- An export of a document opened from an image embeds a thumbnail of that image
  (`c2pa.thumbnail.ingredient`); Verify's "Show sources" shows it (smoke row 27).

**Windows paths** (14 Oct 2026): unpacking cryptography failed with "No such file or
directory" when the data folder sat about 150 characters deep, because a staged file's path
passed 260 characters. `installer.write_failure` now names the limit. The real data folder
(`%APPDATA%\krita\krita-provenance`) keeps the longest path near 150 characters.

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
- Prompt text in layer names: settled 16 Oct 2026. Krita AI Diffusion names the layers it
  creates after the prompt, so manifests no longer name the layer that received a generation;
  the log keeps the name, and a test checks no golden manifest carries it. Still to watch: C5's
  `layer_changed` (v1.0) names its layer, which may be one Krita AI Diffusion named.
- **Results v0.1 doesn't observe** (C7): live painting, upscaling and control-layer images
  reach the document without `result_used`. The manifest can only say what was observed, and
  the README should name these. Capturing upscaling and control layers through
  `jobs.job_finished` is a possible next step.
- Regions are polygons (user, 16 Oct 2026), because Adobe's Verify can't read a rectangle until
  it moves to c2pa-web 0.15.0 or later (facts, above). Reporting that dependency upstream is
  the user's call. Going back to rectangles later is one function (`mapping.region`) plus
  `UPDATE_GOLDENS=1` and `scripts/make_samples.py`.
- Without a timestamp, Adobe's Verify shows no date for a file's credentials; with one it shows
  the signing date (14 Oct 2026). Timestamps stay off for v0.1 (decision 6 of the second set);
  this is input for K3 and Q4.
- Settled 16 Oct 2026 (second set, above): an export keeps Krita's save prompt; an untitled
  document with content starts as `started_with_content`. An unnamed autosave being recovered
  still opens under its autosave path, so its log starts as `existing_document` with that file
  as parent; it isn't claimed blank.
- Q1 (generation as an action only, or also as an ingredient) is decided: actions only, as
  Adobe records them (S5; fifth set of 18 Oct 2026). The choice stays in one place.
- A document the plugin first sees after it already has content is decided (decision 2 above),
  and an unsaved one at export gets the save/unsigned/cancel dialog (second set, 3).
- A known event type drops fields this version doesn't know when the log is written back;
  only unknown event types survive whole (`log.UnknownEvent`). `read_back` (18 Oct 2026) is
  the first added field. The user deferred this until closer to a release (18 Oct 2026): then
  decide whether known events keep unknown fields, so fields added later survive older readers.
- For v1.0, from S5 (fifth set of 18 Oct 2026): D2's disclosure levels decide whether an
  export carries a thumbnail of the image it was opened from (it does now, so a source painted
  over still shows in small). C6 and P2 can fetch an opened image's online manifest at open
  time, with the user's consent, and keep it for export; exports themselves never fetch.
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
