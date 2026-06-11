# SPDX-License-Identifier: AGPL-3.0-only
"""
----------------------------------------------------
Federated OA Harvester (Modular Refactored version)
----------------------------------------------------
See README.FedHarv.md for full description of purpose and functionality.

License: GNU Affero General Public License v3.0 (AGPL-3.0)
Copyright 2026 Pascal V. Calarco <pcalarco@uwindsor.ca>
"""
import argparse
import sys

# UTF-8 console fix for Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

from fedharv import HarvesterEngine

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run FedHarv harvest")
    parser.add_argument("--config", default="config.ini", help="Path to config file")
    parser.add_argument("--dry-run", action="store_true", help="Discover and deduplicate only, without writing output")
    parser.add_argument("--resume", action="store_true", help="Resume from existing output without cleanup")
    args = parser.parse_args()

    harvester = HarvesterEngine(config_path=args.config)
    harvester.run(dry_run=args.dry_run, resume=args.resume)
