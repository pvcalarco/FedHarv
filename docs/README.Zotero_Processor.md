# Zotero PDF Processor

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)
[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)

A companion utility for FedHarv ([src/process_zotero_pdfs.py](../src/process_zotero_pdfs.py)) that merges PDFs exported from Zotero into FedHarv's `Items_Only_Link/` SAF packages and falls back to Unpaywall Green OA downloads when a Zotero attachment is unavailable.

## Workflow

1. Run FedHarv (`python src/run_harvester.py`), which writes missing-PDF citations to `citations.ris` in your output directory.
2. Import `citations.ris` into Zotero, run **Find Available PDFs**, then export the items as **RIS** with **Export Files** checked into `<OutputDir>/Exported Items/`.
3. Run the Zotero processor:

```bash
python src/process_zotero_pdfs.py
# Or specify a custom config file:
python src/process_zotero_pdfs.py --config path/to/config.ini
```

### CLI Options

- `--config PATH` — Path to `config.ini` (defaults to `config.ini` with fallback to `src/config.ini`).
- `--insecure` — Disable TLS certificate verification for Green OA downloads (not recommended; TLS verification is enforced by default).

## Expected Directory Layout

```text
<OutputDir>/
├── Exported Items/
│   ├── Exported Items.ris   # Zotero RIS export containing DO and L1 tags
│   └── files/               # Exported PDF attachments
└── Items_Only_Link/
    └── <Department>/
        └── item_001/
            ├── dublin_core.xml
            └── link.txt     # Contains DOI
```

For each matched item, the processor:
- Validates that the RIS `L1` attachment path stays inside the Zotero export directory (blocking path traversal),
- Copies the Zotero PDF (or downloads a verified Green OA PDF from Unpaywall) to `article.pdf`,
- Writes `article.pdf` to `contents`, and
- Removes `link.txt`.

Items that still lack a PDF are listed in `<OutputDir>/zotero_miss_report.csv`.

## License

Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0). See [LICENSE](../LICENSE).
