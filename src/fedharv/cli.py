# SPDX-License-Identifier: AGPL-3.0-only
"""Command-line entrypoint for FedHarv."""

import argparse
import logging
import sys

from .config import ConfigError
from .core import HarvesterEngine


def _configure_console_encoding():
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass


def main(argv=None):
    _configure_console_encoding()
    parser = argparse.ArgumentParser(description="Run FedHarv harvest")
    parser.add_argument("--config", default="config.ini", help="Path to config file")
    parser.add_argument("--dry-run", action="store_true", help="Discover and deduplicate only, without writing output")
    parser.add_argument("--resume", action="store_true", help="Resume from existing output without cleanup")
    args = parser.parse_args(argv)

    try:
        harvester = HarvesterEngine(config_path=args.config)
        harvester.run(dry_run=args.dry_run, resume=args.resume)
    except (ConfigError, RuntimeError) as exc:
        logging.error(str(exc))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

