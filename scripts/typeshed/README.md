# Krita API stubs

`krita.pyi` describes Krita's Python scripting API, so pyright can check code that imports
`krita`, a module that only exists inside Krita.

- Source: Krita AI Diffusion's `scripts/typeshed/krita.pyi` at tag `v1.53.0`
  (commit `0217cd2197fcadbd70d7e63af25e29cc21cb7c8b`), the release that targets Krita 5 and
  PyQt5. Its `main` branch has since moved to PyQt6 for Krita 6.
- SHA-256: `e3bee76569c3bcc741c168fc3dac82b3c02904f9fa54e3774baa33116564c9cf`
- Licence: GPL v3, as [Krita AI Diffusion](https://github.com/Acly/krita-ai-diffusion).

The file is unmodified, so it can be diffed against upstream and refreshed from it. If a stub is
wrong or missing, fix it upstream where possible, then copy the new version here with its
commit and hash.
