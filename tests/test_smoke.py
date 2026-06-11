"""Import smoke tests.

These are deliberately trivial: importing the package and every submodule. They
would have caught both P0 bugs that shipped on `main` — the missing `fedharv/`
package (`ModuleNotFoundError: No module named 'fedharv'`) and the missing
top-level `import re` in core.py — because both fail at import time.
"""
import importlib

import pytest

SUBMODULES = [
    "fedharv",
    "fedharv.config",
    "fedharv.utils",
    "fedharv.api",
    "fedharv.pdf",
    "fedharv.export",
    "fedharv.core",
]


@pytest.mark.parametrize("name", SUBMODULES)
def test_submodule_imports(name):
    assert importlib.import_module(name) is not None


def test_package_exposes_engine_and_main():
    import fedharv

    assert hasattr(fedharv, "HarvesterEngine")
    assert callable(fedharv.main)
