# krita-provenance

A Krita plugin that records each AI generation and layer edit in a document, then exports a PNG
or JPEG with a signed [C2PA](https://c2pa.org) manifest and a plain-language AI-use report. It is
for artists who have to disclose AI use, such as on a US copyright application, a Steam page or a
crowdfunding campaign, and it works alongside
[Krita AI Diffusion](https://github.com/Acly/krita-ai-diffusion).

> **Status: early development.** Nothing is exported from Krita yet. The plugin loads in Krita,
> starts a provenance log in each document it sees created or opened, and adds two menu
> items under Tools ▸ Scripts: Provenance: Check Environment, which reports the Python, Qt,
> Krita AI Diffusion and c2pa-python versions Krita can see, and Provenance: Log Status, which
> shows the document's log. Generations aren't recorded in the log yet. The
> Krita-free core exists and is tested: the log format, scrubbing, the log-to-C2PA mapping,
> signing with c2pa-python, local signing keys made with cryptography, and the pinned wheel
> installer, with golden manifests that sign and validate. Capture and signed export inside
> Krita are planned for v0.1.

`krita-provenance` is a working title.

## What a manifest will show, and what it won't

A manifest from this plugin states what the plugin observed on the signer's machine. It proves
nothing about what happened elsewhere, and the plugin never claims a work is human-made.

| The manifest shows | It does not show |
| --- | --- |
| Which Krita AI Diffusion generations the plugin saw applied, with parameters and regions | That no other AI was used, outside Krita or before an image was imported |
| Which layers changed between checkpoints | Who the signer is: locally made certificates show as unrecognized in Adobe's tools |
| That the file hasn't changed since signing | That the log wasn't edited before signing, since the signer controls both |

Every manifest and report will follow these rules:

1. Never say "no AI used". When nothing was seen, say "No AI generation observed by this plugin
   since" and the date the log started.
2. Record every gap in capture, such as the plugin being off, and disclose it.
3. When attribution is uncertain, such as after a layer merge, disclose the region as possibly
   AI and say why.
4. State facts, not legal conclusions. The report is not legal advice.

Human edits will be recorded per layer at save and export, not per stroke, because Krita's API
has no per-stroke events.

## Project docs

- [docs/spec.md](docs/spec.md): the project spec, covering goals, requirements, the log format,
  the C2PA mapping, the threat model and milestones.
- [docs/smoke-checklist.md](docs/smoke-checklist.md): checks to run by hand inside Krita.
- [CLAUDE.md](CLAUDE.md): the working brief for Claude, with current state, rules and build order.

## Development

You need [uv](https://docs.astral.sh/uv/). To try the plugin, you also need Krita 5.3 and
Krita AI Diffusion 1.53.0.

```sh
uv sync            # install the dev tools pinned in uv.lock
uv run ruff format # format
uv run ruff check  # lint
uv run pyright     # type-check, including calls into Krita's API
uv run pytest      # test; no test needs Krita
```

CI runs the same checks on every push, with tests on Windows, macOS and Linux.

To run your checkout inside Krita, link it into Krita's plugin folder, restart Krita, and enable
**Provenance (C2PA)** in Settings ▸ Configure Krita ▸ Python Plugin Manager:

```sh
uv run python scripts/dev_install.py
```

On Windows without Developer Mode, this makes a directory junction instead of a symlink. If you
moved Krita's resource folder, pass `--pykrita <folder>`. Run it again with `--remove` before
installing a release zip, so the zip doesn't land in your checkout.

### Layout rules

`tests/test_architecture.py` enforces these:

- Only Krita-facing glue (`extension.py`, for now) imports `krita`. Everything else runs without
  Krita, so it can be unit-tested.
- Qt is imported only through `krita_provenance/qt.py`, which uses PyQt5 under Krita 5 and PyQt6
  under Krita 6.
- c2pa-python is used only through `krita_provenance/sdk.py`, cryptography only through
  `krita_provenance/keys.py`, and Krita AI Diffusion's internals only through
  `krita_provenance/adapters/ai_diffusion.py`, so changes upstream stay in one place.
- The plugin's own copies of c2pa-python, cryptography and cffi will be fetched on first run,
  checked against the sizes and SHA-256 digests pinned in `krita_provenance/wheel_pins.py`,
  and unpacked into the plugin's data folder. The installer and the pin table exist and are
  tested; the Krita glue that runs them at first run is not built yet.
  `scripts/update_wheel_pins.py` regenerates the pins from PyPI.

The test certificates in `tests/fixtures/certs` are made by `scripts/make_test_certs.py` and are
public; never sign anything real with them. `samples/` holds exports signed with them, one per
golden case, for checking the manifests in other validators.

Krita's Python API is described by stubs in `scripts/typeshed`, copied from Krita AI Diffusion
(details in the README there).

## AI assistance

This project is built with AI assistance (Claude). Commits with AI-written changes carry a
`Co-Authored-By:` trailer that names the model.

## Licence

GPL-3.0-or-later; see [LICENSE](LICENSE). `scripts/typeshed/krita.pyi` comes from Krita AI
Diffusion, which is licensed under the GPL v3.
