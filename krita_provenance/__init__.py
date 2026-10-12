"""Record AI generations and layer edits in Krita and export them in a signed C2PA manifest.

A manifest from this plugin states what it observed on the signer's machine; it never claims a
work is human-made. See README.md for what a manifest does and does not show.
"""

__version__ = "0.1.0.dev0"

import importlib.util

# Krita imports this package at startup. Only Krita-facing glue is registered from here, so every
# other module stays importable, and unit-testable, without Krita (tests/test_architecture.py).
if importlib.util.find_spec("krita") is not None:
    from .extension import register

    register()
