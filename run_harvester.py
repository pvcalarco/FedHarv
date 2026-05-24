# SPDX-License-Identifier: AGPL-3.0-only
"""
----------------------------------------------------
Federated OA Harvester (Modular Refactored version)
----------------------------------------------------
See README.FedHarv.md for full description of purpose and functionality.

License: GNU Affero General Public License v3.0 (AGPL-3.0)
Copyright 2026 Pascal V. Calarco <pcalarco@uwindsor.ca>
"""
import sys

# UTF-8 console fix for Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

from fedharv import HarvesterEngine

if __name__ == "__main__":
    harvester = HarvesterEngine()
    harvester.run()
