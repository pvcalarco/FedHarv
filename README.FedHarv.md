# FedHarv: Federated Open Access Harvester

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)
[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)

A sophisticated, production-ready federated harvester for open access academic content. Designed to automatically discover, enrich, and harvest scholarly articles with PDF availability from multiple sources, it is structured as a clean, modular Python package.

## Table of Contents

- [Overview](#overview)
- [Key Features](#key-features)
- [Architecture & Modularization](#architecture--modularization)
- [Installation](#installation)
- [Configuration](#configuration)
- [Usage](#usage)
- [API Integration](#api-integration)
- [PDF Discovery Pipeline](#pdf-discovery-pipeline)
- [Output Structure](#output-structure)
- [Technical Specifications](#technical-specifications)
- [Troubleshooting](#troubleshooting)
- [Contributing](#contributing)
- [License](#license)

## Overview

FedHarv is a comprehensive solution for harvesting open access scholarly content from multiple academic sources. It implements a sophisticated multi-stage pipeline that:

1. **Discovers** open access content using federated search across APIs (OpenAlex and CrossRef).
2. **Enriches** metadata through cross-referencing multiple data sources.
3. **Validates** open access status and licensing information.
4. **Locates** PDF files through a multi-tier waterfall approach.
5. **Generates** DSpace-compatible SAF (Simple Archive Format) packages.

The system is designed for institutional repositories and digital libraries that need to systematically harvest and preserve open access scholarly content.

## Key Features

### 🔍 **Federated Discovery**

- **OpenAlex API**: Primary discovery source with comprehensive metadata.
- **Date Range Queries**: Precise temporal filtering using `YYYY-MM-DD` ranges.
- **Institutional Affiliation**: Target-specific institution content harvesting.
- **Duplicate Prevention**: Built-in deduplication across sources.

### 📊 **Metadata Enrichment**

- **CrossRef**: Author affiliations, funding information, and publisher data.
- **DataCite**: Abstracts, datasets, and extended metadata.
- **DOAJ**: Journal classification and APC information.
- **Sherpa Romeo**: Copyright and self-archiving policies.
- **Unpaywall**: Comprehensive OA status and licensing data.

### 📄 **PDF Discovery Pipeline**

- **Multi-tier Waterfall**: OpenAlex → Unpaywall → CrossRef TDM → Scopus API (Elsevier) → Heuristics → HTML Meta-Scraper → DOI Heuristics.
- **Publisher-Specific Rules**: 25+ publisher-specific URL transformation patterns.
- **DOI-Based Heuristics**: Automatic PDF URL generation from DOI prefixes.
- **Browser Automation Fallback**: Playwright browser automation to scrape dynamic landing pages and handle complex Javascript PDF downloads.
- **Custom PDF Patterns** *(optional)*: Supply your own publisher rules in `learned_patterns.json` — a `DOI-prefix → URL template` map using `{doi}`/`{doi_suffix}` placeholders. It is loaded at startup and tried ahead of the built-in heuristics. Patterns are user-supplied (hand-authored), not automatically learned.

### 🏗️ **Output Management**

- **Split Output Strategy**:
  - `Items_With_PDF/`: Complete SAF packages with PDF files.
  - `Items_Only_Link/`: SAF packages with DOI links (no PDF).
  - `Green/`: Green-OA items as metadata + DOI link only (no PDF is fetched for green items).
  - `citations.ris`: RIS format citations for link-only items.
- **DSpace Compatibility**: Full SAF (Simple Archive Format) support.
- **Batch Import Scripts**: Automated DSpace import script generation.

### 🔒 **Quality Assurance**
- **Strict OA Filtering**: Gold, Hybrid, Diamond, and Green OA only (Closed/Bronze skipped).
- **License Validation**: Creative Commons and publisher license verification.
- **Metadata Completeness**: Guaranteed minimum metadata requirements.
- **Error Resilience**: Comprehensive error handling and recovery.

---

## Documentation

- **[USER_GUIDE.md](USER_GUIDE.md)** — install, configure, run a harvest, and import into DSpace.
- **[DOCUMENTATION.md](DOCUMENTATION.md)** — architecture, the item pipeline, the full configuration reference, and output artifacts.
- **[README.Zotero_Processor.md](README.Zotero_Processor.md)** — the Zotero PDF-backfill companion.

## Architecture & Modularization

FedHarv is structured as a modular Python package divided into separate layers of concern:

```
FedHarv/
├── run_harvester.py         # Entrypoint script
├── learned_patterns.json    # optional user-supplied PDF URL patterns (DOI-prefix → template)
├── config.ini               # User configurations
├── fedharv/                 # Package directory
│   ├── __init__.py          # Exposes the main HarvesterEngine
│   ├── config.py            # CLI parser, dotenv loader, and config mappings
│   ├── utils.py             # File, string helpers, and CacheManager
│   ├── api.py               # Consolidated API Client (HTTP & sessions)
│   ├── pdf.py               # PDF Downloader and Playwright fallback
│   ├── export.py            # Dublin Core & SAF export utilities
│   └── core.py              # HarvesterEngine orchestration logic
```

### Module Responsibilities

1. **`config.py` (`ConfigManager`)**
   - Loads `.env` file and CLI arguments.
   - Parses the `config.ini` configuration.
   - Contains global normalization maps like `DOCTYPE_MAPPINGS`, `OA_STATUS_MAPPINGS`, `LICENSE_URI_MAPPINGS`, and `CC_LICENSE_NAMES`.
   - Defines domain URL transformers (`DOMAIN_URL_TRANSFORMS`) and hardcoded prefix heuristics (`DOI_PDF_PATTERNS`).

2. **`utils.py`**
   - Implements text cleaning and text index helpers (`clean_text`, `reconstruct_openalex_abstract`).
   - Implements thread-safe caching decorators (`cached_api_call`, `load_from_cache`, `save_to_cache`) supporting local JSON serialization.
   - Implements primary department and affiliation matcher (`determine_primary_department`).

3. **`api.py` (`APIClient`)**
   - Sets up a robust `requests.Session` with mountable retry policies.
   - Implements the global `@limits` rate-limited and `@backoff` decorated HTTP GET function (`rate_limited_get`).
   - Bundles queries to OpenAlex, CrossRef, Unpaywall, Sherpa Romeo, DataCite, and DOAJ.
   - Implements DSpace duplicate checking (`check_dspace_duplicate`).

4. **`pdf.py` (`PDFDownloader`)**
   - Implements the waterfall download workflow.
   - Runs publisher URL transformations and HTML meta-tag scrapers.
   - Houses the `PlaywrightFallback` automation logic, executing Chromium in headless mode to click PDF links and handle redirects.

5. **`export.py` (`MetadataExporter`)**
   - Implements Dublin Core schema mapper (`map_to_dublin_core`).
   - Generates SAF XML packages (`dublin_core.xml`, `metadata_oaire.xml`, `contents`).
   - Outputs reports: CSV log files, RIS citation records, Windsor author registries, and publisher breakdowns.
   - Creates DSpace shell batch import scripts.

6. **`core.py` (`HarvesterEngine`)**
   - Acts as the main controller/orchestrator.
   - Initiates thread executors for discovery and processing.
   - Maintains the central state machine and program statistics.

---

## Installation

### Prerequisites

- **Python 3.8+**
- **Virtual Environment** (recommended)
- **Playwright dependencies** (for browser-based fallback harvesting)

### Setup

```text
# Clone the repository
git clone <repository-url>
cd FedHarv

# Create a virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Install Playwright browser dependencies
playwright install chromium

# Copy config template
cp config.example.FedHarv.ini config.ini
```

---

## Configuration

### Environment Variables (`.env`)

Create a `.env` file in the root directory for secure credentials:

```bash
# Core authentication keys
OPENALEX_EMAIL=your-email@example.com
SCOPUS_API_KEY=your-elsevier-scopus-key  # Optional: For Scopus PDF extraction

# CrossRef Plus API Token (Optional: increases query limits)
CROSSREF_TOKEN=your-crossref-token
```

### Configuration File (`config.ini`)

Customize options in `config.ini`:

```ini
[Search]
StartDate=2025-01-01
EndDate=2025-03-31
Affiliation=University of Windsor

[Authentication]
SherpaKey=your-sherpa-key
ScopusKey=  # Overrides environment variable if set
CrossrefPlusToken=

[General]
Email=pcalarco@uwindsor.ca
OutputDir=FedHarv_Output

[DSpace]
CheckDuplicates=true
ApiUrl=https://scholar.uwindsor.ca/server/api
AdminEmail=admin@uwindsor.ca
BinPath=/dspace/bin/dspace

[Mappings]
# Mappings map affiliations keywords to target folders
glier = Great_Lakes_Institute_for_Environmental_Research
odette = Odette_School_of_Business
computer science = School_of_Computer_Science
chemistry = Faculty_of_Science_Chemistry_Biochemistry
```

---

## Usage

### Run Harvester

Execute the modular entrypoint script:
```bash
python run_harvester.py
```
To specify a custom configuration file:

```bash
python run_harvester.py --config custom_config.ini
```

### Dagster Integration
If using Dagster for pipeline automation, configure the run parameters to execute `HarvesterEngine` programmatic asset definitions. The new codebase natively supports programmatic instantiation:
```python
from fedharv import HarvesterEngine

harvester = HarvesterEngine(config_path="config.ini")
harvester.run()
```

---

## PDF Discovery Pipeline

### Waterfall Execution Priority
When resolving an item's PDF, FedHarv attempts the following sources sequentially:
1. **OpenAlex Direct PDF** link.
2. **Unpaywall best OA location** PDF link.
3. **CrossRef Direct PDF** link (if text-mining permissions allow).
4. **Scopus PDF API** (if an Elsevier item and Scopus key is configured).
5. **Publisher Heuristics** (Domain-specific URL replacement rules).
6. **Meta-Tag Scraper** (Grabs `<meta name="citation_pdf_url">` via static requests).
7. **DOI-Only Heuristics** (Prefixed publisher patterns).
8. **Playwright Browser Automation** (As a final fallback, launches chromium to simulate clicks and extract browser downloads).

---

## Output Structure

The output directory contains:

```
FedHarv_Output/
├── Items_With_PDF/                      # Successful downloads sorted by dept
│   └── School_of_Computer_Science/
│       └── item_001/
│           ├── dublin_core.xml          # Core metadata
│           ├── metadata_oaire.xml       # Citation & pagination mappings
│           ├── contents                 # SAF bitstream manifest
│           └── article.pdf              # Harvested PDF file
├── Items_Only_Link/                     # PDF-missing items (links only)
│   └── School_of_Computer_Science/
│       └── item_002/
│           ├── dublin_core.xml
│           ├── contents
│           └── link.txt                 # Contains target DOI URI
├── citations.ris                        # RIS block references for Items_Only_Link
├── harvest_report_YYYYMMDD_YYYYMMDD.csv # Comprehensive spreadsheet mapping
├── import_batch.sh                      # Shell DSpace importing utility
├── windsor_authors.txt                  # Windsor author affiliation database
└── department_publisher_report.csv      # Departmental output counts
```

---

## Technical Specifications

- **Parallel Processing**: Uses a `ThreadPoolExecutor` with up to 15 parallel workers for metadata processing and waterfall downloads.
- **Thread Safety**: Access to output files (CSV, RIS) and engine logs are safeguarded by individual thread locks.
- **Dual-Layer Caching**: Shared cache dictionaries are held in memory and serialized locally to JSON in `OutputDir/cache/` to prevent redundant API queries.
- **Rate-Limiting Compliance**: Handles 429 response limits gracefully with automatic backing-off and polite pool headers.

---

## Troubleshooting

- Ensure `config.ini` and `.env` are present and correctly populated.
- If browser fallback fails, reinstall Playwright dependencies with `playwright install chromium`.
- Verify API credentials (OpenAlex email, CrossRef token, Scopus key, Sherpa key) when metadata or PDF discovery is incomplete.

---

## Contributing

Contributions are welcome! Please follow these guidelines:
- Fork the repository and create a feature branch.
- Write clear commit messages and update documentation.
- Submit pull requests with detailed descriptions of changes.

---

## License

This project is licensed under the GNU Affero General Public License v3.0 (AGPL-3.0) - see the `GNU AFFERO GENERAL PUBLIC LICENSE.md` file for details.

---
**Version**: 1.0.1 (Modular Release)  
**Maintainer**: Pascal V. Calarco  
**Contact**: pcalarco@uwindsor.ca