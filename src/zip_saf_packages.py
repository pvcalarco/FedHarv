# SPDX-License-Identifier: AGPL-3.0-only
"""Package FedHarv SAF department folders into DSpace-ready ZIP archives."""

import argparse
import os
import sys
import zipfile

from fedharv.config import ConfigError, ConfigManager


def load_output_dir(config_path=None):
    return ConfigManager(config_path).OUTPUT_DIR


def zip_saf_packages(output_dir):
    if not os.path.isdir(output_dir):
        print(f"Output directory does not exist: {output_dir}")
        return 0

    packages_dir = os.path.join(output_dir, "SAF_Packages")
    os.makedirs(packages_dir, exist_ok=True)

    zip_count = 0
    for root, dirs, _ in os.walk(output_dir):
        dirs[:] = [d for d in sorted(dirs) if d != "SAF_Packages"]

        item_dirs = [d for d in dirs if d.startswith("item_")]
        if not item_dirs:
            continue

        # Prune item_* directories so os.walk does not redundantly descend into them.
        dirs[:] = [d for d in dirs if not d.startswith("item_")]

        rel_path = os.path.relpath(root, output_dir)
        parts = rel_path.split(os.sep)
        zip_filename = f"{'_'.join(parts)}.zip"
        zip_path = os.path.join(packages_dir, zip_filename)

        print(f"Creating SAF ZIP for: {rel_path} -> {zip_filename}")
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
            for item in sorted(item_dirs):
                item_root = os.path.join(root, item)
                for item_subroot, _, item_subfiles in os.walk(item_root):
                    for filename in sorted(item_subfiles):
                        file_abs_path = os.path.join(item_subroot, filename)
                        arcname = os.path.relpath(file_abs_path, root)
                        zipf.write(file_abs_path, arcname)

        zip_count += 1

    print(f"\nCompleted! Generated {zip_count} SAF ZIP packages in: {packages_dir}")
    return zip_count


def main(argv=None):
    parser = argparse.ArgumentParser(description="Zip FedHarv SAF item folders into DSpace import archives")
    parser.add_argument("--config", default=None, help="Path to config.ini")
    args = parser.parse_args(argv)

    try:
        out_dir = load_output_dir(args.config)
    except ConfigError as exc:
        print(f"Configuration error: {exc}")
        return 1

    print(f"Target Output Directory: {out_dir}")
    zip_saf_packages(out_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
