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
