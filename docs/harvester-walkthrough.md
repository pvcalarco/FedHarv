# run_harvester.py Walkthrough

Here is a detailed walkthrough of run_harvester.py:

1. Script Metadata & License (Lines 1–10)
License: Released under the GNU Affero General Public License v3.0 (AGPL-3.0).
Description: The script serves as the main entry point to run the Federated OA Harvester (Modular Refactored version).

1. Imports and Environment Setup (Lines 11–21)
Standard Library Imports:
argparse is imported to parse command-line arguments.
sys is imported to inspect the execution environment platform.
Windows console fix (Lines 14–19): If the system platform is "win32", the script attempts to reconfigure standard output (sys.stdout) to use utf-8 encoding. This prevents encoding crashes when printing special characters on Windows consoles.
Core Import (Line 21): Imports HarvesterEngine from the fedharv package, which contains the main harvesting orchestration logic.

1. Command-Line Argument Parsing (Lines 23–28)
When executed directly as a script (if __name__ == "__main__":):

Creates an argparse.ArgumentParser to define and process the command-line options:
--config: The path to the configuration file (defaults to config.ini).
--dry-run: A boolean flag that, when active, instructs the harvester to discover and deduplicate records without writing output.
--resume: A boolean flag that, when active, resumes harvesting from existing output files without executing cleanup processes first.

1. Engine Initialization & Execution (Lines 30–31)
Engine Initialization (Line 30): Instantiates the HarvesterEngine by passing the configuration path determined by args.config.
Harvest Execution (Line 31): Calls the run() method on the engine instance, passing in the dry_run and resume status options to kick off the harvesting process.