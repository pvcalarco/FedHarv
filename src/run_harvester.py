# SPDX-License-Identifier: AGPL-3.0-only
"""
Federated OA Harvester CLI script wrapper.
See README.md for full description of purpose and functionality.

License: GNU Affero General Public License v3.0 (AGPL-3.0)
Copyright 2026 Pascal V. Calarco <pcalarco@uwindsor.ca>
"""
import sys

from fedharv.cli import main

if __name__ == "__main__":
    sys.exit(main())
