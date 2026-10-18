# In-Krita smoke checklist

The tests can't reach Krita's API, so anything that touches Krita is checked here, by hand, in
Krita. Run it before each release and whenever a change touches Krita-facing code. Record the OS,
Krita, Python and Krita AI Diffusion versions with the results.

## Setup, once per machine

1. Install Krita 5.3 and Krita AI Diffusion 1.53.0 with the local managed server.
2. From this checkout, run `uv run python scripts/dev_install.py`.
3. Restart Krita. In Settings ▸ Configure Krita ▸ Python Plugin Manager, enable
   **Provenance (C2PA)**, then restart Krita again.

## Checks

| # | In Krita | Expected |
| --- | --- | --- |
| 1 | Tools ▸ Scripts ▸ Provenance: Check Environment | A dialog lists the krita-provenance, Krita, Python, Qt binding, Krita AI Diffusion and c2pa-python versions, with no Problems section |
| 2 | Read the Python line in that dialog | 3.10 or newer (spike S1) |
| 3 | Read the Krita AI Diffusion line | `1.53.0 (loaded)` |

## Adding checks

Each change that touches Krita adds its checks here, in the same commit: what to do in Krita and
what should happen.

### Annotation storage (spike S4)

| # | In Krita | Expected |
| --- | --- | --- |
| 4 | New document, Tools ▸ Scripts ▸ Provenance: Log Status | A dialog with a document id, "Log started … (new_document)", "Events: none" and "No AI generation observed by this plugin since …". Since piece 1 (rows 9 to 11) there is no "just created" note: the log started with the document |
| 5 | Save as .kra, close, reopen, run Log Status again | The same document id and start time; no "just created" note |
| 6 | With Krita AI Diffusion active, generate once, save, reopen, run Log Status | Same document id; `ui.json` still works for Krita AI Diffusion (its history is intact) |
| 7 | File ▸ Open a PNG or JPEG, run Log Status | "(opened_image)" and "Events: 1 ingredient_added" |
| 8 | Open an older .kra made before the plugin, run Log Status | "(existing_document)" and "Events: 1 ingredient_added" |

Record which Krita version and OS you used; S4 passes when 5 and 6 hold.

### The log starts with the document (step 7, piece 1)

The plugin starts a document's log when Krita creates it or opens it, without Log Status, and
writes every change to the document at once. Log times are in UTC.

| # | In Krita | Expected |
| --- | --- | --- |
| 9 | Note the time, File ▸ New, paint a stroke, wait a minute, save as .kra, close, reopen, then run Log Status (not before) | "(new_document)", started when you created the document, not when you saved it; no "just created" note |
| 10 | Open a PNG or JPEG, save it as .kra without running Log Status, close, reopen the .kra, run Log Status | "(opened_image)" and "Events: 1 ingredient_added", started when you opened the image |
| 11 | Open an older .kra made before the plugin and close it straight away | Krita doesn't ask to save: starting the log isn't an edit, so the plugin clears the "modified" mark that writing it sets. Reopen it and run Log Status: "(existing_document)", started at the reopening, since the first log was never saved |

Krita AI Diffusion writes its own document id into a document it hasn't seen before, and that
also marks the document modified. If row 11 asks to save, run it again with Krita AI Diffusion
disabled in the Python Plugin Manager (restart Krita, and re-enable it afterwards). If Krita
then doesn't ask, the prompt comes from Krita AI Diffusion, not this plugin.

### First run: packages and signing key (step 7, piece 2; spike S1)

Needs an internet connection; it downloads about 92 MB on Windows, 19 MB elsewhere.

| # | In Krita | Expected |
| --- | --- | --- |
| 12 | Tools ▸ Scripts ▸ Provenance: Set Up Signing, then Cancel | A dialog names c2pa-python 0.38.0, cryptography 50.0.2 and cffi 2.1.1 with their sizes, PyPI as the source, the SHA-256 check, and the folder: `%APPDATA%\krita\krita-provenance` on Windows, not inside `pykrita`. After Cancel nothing is downloaded |
| 13 | Run it again and choose Yes | A progress dialog counts through the three downloads, then "Making the local signing certificate…" and "Signing a test image…". The report reads "Signing is set up.", lists the three packages, "Loaded: c2pa-python 0.38.0 with c2pa-rs 0.91.0", the certificate (signer "krita-provenance local signer", issued by "krita-provenance local root CA", 10 years, a SHA-256 fingerprint) and a self-test that read back Valid with the signer untrusted. No line says a package was loaded from outside the verified install |
| 14 | Tools ▸ Scripts ▸ Provenance: Check Environment | "c2pa-python: 0.38.0" |
| 15 | Restart Krita, run Check Environment, then Set Up Signing | Check Environment: "0.38.0 (installed by this plugin; loads when signing)". Set Up Signing asks nothing, downloads nothing ("Already installed"), and shows the same fingerprint as row 13 |
| 16 | Optional: move the folder from row 12 aside, run Set Up Signing, choose Yes, then Cancel during the download | "Cancelled; nothing was installed." Afterwards put the folder back (it holds the key from row 13) |

S1 passes on Windows when row 13 holds: c2pa-python loads in Krita's own Python and signs. If
a download fails with an SSL or certificate error, note the message: Krita's Python may lack
the certificates urllib needs.

### Generation capture (step 7, piece 3; spike S3)

Krita AI Diffusion enabled and connected to its server.

| # | In Krita | Expected |
| --- | --- | --- |
| 17 | Restart Krita, then Tools ▸ Scripts ▸ Provenance: Check Environment | The last line reads "Generation capture: observing Krita AI Diffusion 1.53.0." and no capture warning appeared at startup |
| 18 | New document. Generate with Krita AI Diffusion and apply one result (the default puts it on a new layer). Run Log Status | "Events: 1 generation_applied" and "1 AI generation observed by this plugin since … (UTC)". The event line names the workflow (generate), the seed, and "on layer [Generated] … (seed)", the layer that just appeared. No warning dialog |
| 19 | Select an area, generate with the strength slider at 100% and apply that result. Then click row 18's result in Krita AI Diffusion's history and apply it again. Run Log Status | Three generation_applied lines in all. The second says inpaint (refine_region if the strength was below 100%). The third repeats row 18's workflow and seed: re-applying an older result is a new event, on the new layer it made |
| 20 | Save as .kra, close, reopen, run Log Status | The same three events after reopening |
| 21 | Optional, Live: in the AI Image Generation docker, click the workspace button (an icon with a small arrow, left of the style selector) and choose Live. Press "Start/stop live preview", paint a few strokes until a result shows, click "Create a new layer with the current result", then run Log Status | No new event. Krita AI Diffusion 1.53.0 doesn't announce live results (Q10, from its source), so v0.1 doesn't record them |
| 22 | Optional, regional prompts: in the Generate workspace, first type a prompt for the whole picture in the usual prompt box (before any region exists, that box is the prompt common to all regions). Add a paint layer and paint a large blob, at least a tenth of the canvas; with that layer active, click "Add Region" (beside the strength slider) and type the region's own prompt. Generate, apply, run Log Status | The preview covers the whole canvas, as usual: regions steer what appears where, and the area outside them is generated from the common prompt. After applying, the region's result sits in a new group with the blob layer hidden, and the rest is a new background layer, so Log Status shows one generation_applied ending "layer not identified". If it names a layer instead, Krita AI Diffusion dropped the region (it ignores regions under 2% of the canvas) and did an ordinary apply. (Two areas lassoed into one selection are an ordinary inpaint too: one new layer, identified) |

S3 passes when rows 18 to 20 hold: the plugin catches `result_used`, copies the job's
parameters and identifies the layer that received the result.

### Signed export (step 7, piece 4; E1, E2, E4 to E6)

Signing set up (rows 12 and 13). Use Tools ▸ Scripts ▸ Provenance: Export Signed Image….

| # | In Krita | Expected |
| --- | --- | --- |
| 23 | New document, generate and apply a result, export it as test.png | A save dialog offers PNG and JPEG. Then "Saved and signed: …test.png", "Read back: Valid, with the signer reported as untrusted, as expected for a local certificate.", a manifest label and the AI-use statement. Log Status ends "exported png, signed" |
| 24 | Upload test.png to Adobe's Verify | Credentials: created with krita-provenance, Krita AI Diffusion as the AI model, edited with AI, the signer unrecognized |
| 25 | Export the same document as test.jpg | Signed, as in row 23 |
| 26 | Close the document | Krita asks to save, because recording the export changed the log (decision 3 of 16 Oct 2026) |
| 27 | Open a PNG or JPEG, apply a generation, save as .kra, close, reopen the .kra, export | Signed: the copy of the opened image kept in the .kra is the parent (decision 1 of 16 Oct 2026). In Verify the credentials list the image it was opened from |
| 28 | Open an older .kra, paint a stroke, export | A dialog offers "Save and export signed", "Export unsigned" and "Cancel". Export unsigned saves …-unsigned.png with no credentials; Save and export signed saves the .kra, then signs |
| 29 | Copy an image, File ▸ New ▸ Create from Clipboard, run Log Status, then export | Log Status shows "(started_with_content)". Export says it can't be signed because the document was created with content already in it, and offers an unsigned copy (decision 2 of 16 Oct 2026) |
| 30 | Save that document as .kra, close it, reopen it, export | Signed. Log Status lists "parentOf: ….kra", added when it was reopened |
| 31 | File ▸ New with the default white background, run Log Status | "(new_document)": a flat background counts as a blank canvas |
| 32 | Optional: export a 4000×4000 document and time it | Note the seconds from choosing the file to the result dialog; the M1 target is under 3 seconds of overhead at 4K |

### Log docker (step 7, piece 5; V1)

Settings ▸ Dockers ▸ Provenance Log turns the docker on; Krita keeps it on across restarts.

| # | In Krita | Expected |
| --- | --- | --- |
| 33 | With a document open, Settings ▸ Dockers ▸ Provenance Log | The docker shows what Log Status shows, but with every event, oldest first, and ends "Generation capture: observing Krita AI Diffusion 1.53.0." |
| 34 | Open a second document and switch between the two | The docker follows the active document. With no document open, Krita shows its welcome page and hides the dockers |
| 35 | New document, generate and apply a result | Within a second, without reopening anything, the docker lists the generation_applied line |
| 36 | Export it with Provenance: Export Signed Image… | The docker adds "exported png, signed" and, under "Last export in this session:", the "Saved and signed" and "Read back" lines from the result dialog (this is also row 23's Log Status check) |
| 37 | Open the .kra from row 30 | The docker lists "parentOf: ….kra", logged when it was reopened (row 30's Log Status check) |

### Release zip (step 7, piece 5; I1)

| # | In Krita | Expected |
| --- | --- | --- |
| 38 | In the checkout, run `uv run python scripts/build_release.py`, then `uv run python scripts/dev_install.py --remove`. Remove the dev link first: Krita's importer replaces the folder it installs into, and the link points at this checkout. In Krita, Tools ▸ Scripts ▸ Import Python Plugin from File…, choose `dist\krita-provenance-0.1.0.dev0.zip` | Krita lists "Provenance (C2PA)" as imported and asks "Enable plugins now? (Requires restart)". Choose Yes and restart Krita |
| 39 | Check Environment, then Set Up Signing | "krita-provenance 0.1.0.dev0" with no Problems section. Set Up Signing downloads nothing ("Already installed; nothing was downloaded.") and shows row 13's fingerprint: installing left the data folder alone |
| 40 | Back to the checkout: quit Krita, delete `krita_provenance` and `krita_provenance.desktop` from `%APPDATA%\krita\pykrita`, run `uv run python scripts/dev_install.py`, start Krita | Check Environment works as before |

### Read-back kept in the log (decision of 18 Oct 2026)

| # | In Krita | Expected |
| --- | --- | --- |
| 41 | Export a document with Provenance: Export Signed Image…, save it as .kra, close it, reopen it | Log Status and the docker list "exported png, signed, read back Valid, signer untrusted": the read-back stays in the log. Exports logged before this change keep the shorter line |

### Review fixes (step 7, piece 6)

| # | In Krita | Expected |
| --- | --- | --- |
| 42 | Optional: Tools ▸ Scripts ▸ Scripter, run `d = Krita.instance().createDocument(512, 512, "scripted", "RGBA", "U8", "", 72.0)` and then `Krita.instance().activeWindow().addView(d)`. Without running Log Status first, generate and apply a result in the new view, then run Log Status | "(started_with_content)" and "Events: 1 generation_applied". Krita announces a script-made document before it has an image, so the plugin first meets it at the apply and doesn't claim it began as a blank canvas |

## Results

| Date | Setup | Rows | Result |
| --- | --- | --- | --- |
| 13 Oct 2026 | Windows 11, Krita 5.3.4.1 (git bd6e8d0), Python 3.13.5, PyQt5, Krita AI Diffusion 1.53.0 (loaded), c2pa-python not installed (the installer isn't wired yet) | 1–8 | All pass, after the window fix in ee9c96c. S1's Python check passes on Windows (3.13.5) and S4 passes: the log kept its id and start time through save and reopen, and Krita AI Diffusion's history survived beside it |
| 14 Oct 2026 (local; the log's UTC dates read 15 Oct) | Same | 9–11, on 7b379b0 | 9 and 10 pass: the logs started at 04:49:08Z and 04:53:19Z, when the documents were created and opened, not when they were saved. 11 fails: Krita asked to save old-test.kra on close, every time. Writing an annotation marks a document modified (KisImage::addAnnotation); the next commit clears the mark after starting a log. Rerun 11 |
| 14 Oct 2026 | Same | 11, on 0d5c44f | Passes for this plugin. With Krita AI Diffusion enabled Krita still asked to save old-test.kra; with it disabled in the Python Plugin Manager, Krita didn't ask. The remaining prompt is Krita AI Diffusion writing its `ai_diffusion/document_id` into a document it hasn't seen before |
| 15 Oct 2026 | Same, Krita AI Diffusion enabled | 12–16, on 49c8e53 | All pass. Row 12 named the three packages (91.9 MB), PyPI and `C:\Users\kable\AppData\Roaming\krita\krita-provenance`. Row 13: "Signing is set up.", c2pa-python 0.38.0 with c2pa-rs 0.91.0 loaded in Krita's Python 3.13.5, a certificate and fingerprint, a self-test read back Valid with the signer untrusted, no SSL error and nothing loaded from outside the install. Row 15 showed the same fingerprint after a restart. Row 16's cancel left only the data folder and an empty `downloads` folder. **S1 passes on Windows** |
| 16 Oct 2026 | Same | 17–22, on 17e5039 | 17 passes. 18 and 19 recorded every apply with its workflow and seed, row 18's result re-applied as an event of its own, but each read "layer not identified": the snapshot came from a timer, which Krita's busy-wait lets fire in mid-apply. Fixed in the next commit; rerun 18 and 19. 20 passes: the three events survived save and reopen. 21: no event, as expected, but the live result looked unchanged, so it's unclear a live result was applied. 22: both applies were ordinary ones (the region was dropped), and their layers were named after the prompt |
| 16 Oct 2026 | Same | 18–19 rerun, on 2f4c0f7 | Pass: every event names the layer it landed on, including an apply made twice by mistake (two events, two layers) and row 18's result re-applied. **S3 passes.** A layer deleted afterwards keeps its event, by design: at export the manifest says that layer no longer exists and the region may still hold AI content |
| 16 Oct 2026 | Same, signing set up | 23–32, on 86ff031 | Pass. 23 and 25: the PNG and the JPEG were saved and signed, read back Valid with the signer untrusted, with "1 AI generation observed by this plugin since 16 October 2026 (UTC)." 24: Verify shows created with krita-provenance, Krita AI Diffusion as the AI model and edited with AI. 26: Krita asked to save after the export. 27: a document opened from an image, saved and reopened as .kra, signed, so the kept copy served as its parent. 28: the save-first dialog appeared. 29: "(started_with_content)"; the export said why it couldn't sign and offered an unsigned copy. 30: signed after saving and reopening, with "The document was created with content already in it; how that content was made is not recorded." 31: "(new_document)". 32: a 4000×4000 export took about a second from choosing the file to the result dialog, under M1's 3 seconds. Not reported: row 23's Log Status line and row 30's (rows 36 and 37 cover them), row 27's Verify view of the parent, and what row 28's two export buttons did |
| 18 Oct 2026 | Same | 27–28 completed; 33–40, on 582109f | Pass. 27: Verify offers "Show sources (1)", which shows the parent image. 28: both export buttons did what the row says. 33: the docker showed old-test.kra's log with every event, the unsigned export's reason under "Last export in this session:", and the capture status. 34: it follows the active document; with no document open Krita hides its dockers behind the welcome page, so "No document is open." never shows. 35: the generation appeared as soon as it was applied. 36: "exported png, signed" and the read-back lines. 37: "parentOf: S30_test.kra", logged at this reopening; row 30's export isn't in the log, as when the document isn't saved after an export. 38 to 40: the zip imported, Check Environment read 0.1.0.dev0, Set Up Signing found the install ("Already installed; nothing was downloaded.") and the 14 Oct certificate, and the dev link went back in. **V1 and I1 pass** |
| 18 Oct 2026 | Same | 41–42, on c31ab29 | 42 passes: a script-made document logged its first generation under "(started_with_content)". 41's export (19:50 UTC) ran in a Krita session started before decision 1 reached the checkout (19:20 UTC), so its line is the older form, as the row allows; rerun 41 after restarting Krita (`tests/test_exporter.py` now builds 41's line from a real export). Row 37's missing events: the document wasn't saved after row 30, so the reopened .kra held the log as it was saved before |
| 18 Oct 2026 | Same, Krita restarted | 41 rerun, on fe60895 | Passes: "exported png, signed, read back Valid, signer untrusted" after save and reopen |
