# SPDX-License-Identifier: AGPL-3.0-only
import argparse
from .core import HarvesterEngine

def main():
    parser = argparse.ArgumentParser(description="Run FedHarv harvest")
    parser.add_argument("--config", default="config.ini", help="Path to config file")
    parser.add_argument("--dry-run", action="store_true", help="Discover and deduplicate only, without writing output")
    parser.add_argument("--resume", action="store_true", help="Resume from existing output without cleanup")
    args = parser.parse_args()

    harvester = HarvesterEngine(config_path=args.config)
    harvester.run(dry_run=args.dry_run, resume=args.resume)

if __name__ == "__main__":
    main()
