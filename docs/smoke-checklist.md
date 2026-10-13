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
| 11 | Open an older .kra made before the plugin and close it straight away | Krita doesn't ask to save: starting the log doesn't mark the document changed. Reopen it and run Log Status: "(existing_document)", started at the reopening, since the first log was never saved |

## Results

| Date | Setup | Rows | Result |
| --- | --- | --- | --- |
| 13 Oct 2026 | Windows 11, Krita 5.3.4.1 (git bd6e8d0), Python 3.13.5, PyQt5, Krita AI Diffusion 1.53.0 (loaded), c2pa-python not installed (the installer isn't wired yet) | 1–8 | All pass, after the window fix in ee9c96c. S1's Python check passes on Windows (3.13.5) and S4 passes: the log kept its id and start time through save and reopen, and Krita AI Diffusion's history survived beside it |
