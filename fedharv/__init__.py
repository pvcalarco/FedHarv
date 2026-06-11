# FedHarv modular package
from .core import HarvesterEngine

__all__ = ["HarvesterEngine", "main"]


def main():
    """Console entrypoint: run the harvester with the Windows UTF-8 console fix."""
    import sys
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    HarvesterEngine().run()

