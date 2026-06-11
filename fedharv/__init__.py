"""FedHarv -- a federated open-access harvester.

Discovers scholarly articles for an institution (OpenAlex + Crossref), enriches
their metadata, locates open-access PDFs, and packages everything as DSpace 7/8
Simple Archive Format (SAF) for repository ingest.

Public API:
    HarvesterEngine -- the pipeline orchestrator (see fedharv.core).
    main()          -- console entrypoint used by run_harvester.py and the
                       `fedharv` console-script.
    __version__     -- single source of truth for the package version.

See DOCUMENTATION.md (architecture) and USER_GUIDE.md (install/run).
"""
from .core import HarvesterEngine
from .config import __version__

__all__ = ["HarvesterEngine", "main", "__version__"]


def main():
    """Console entrypoint: run the harvester with the Windows UTF-8 console fix."""
    import sys
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    HarvesterEngine().run()

