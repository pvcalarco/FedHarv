# FedHarv: Federated Open Access Harvester

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)

FedHarv is a sophisticated, production-ready federated harvester for open access academic content, designed to automatically discover, enrich, and harvest scholarly articles with PDF availability from multiple sources. 

The problem we are trying to provide a solution for is to to the extent possible, identify Creative Commons-licensed scholarly works (journal articles, letters to the editor, retractions, errata, book chapters, conference proceedings, and open access books) that are authored by researchers, faculty and students of an institution of higher education or research, harvest the metadata and associated PDF from a variety of API services. Where we can't find a non-paywalled version, we use Unpaywall 
to identify author manuscripts and preprints that can be deposited.

The script then provides these metadata and PDFs in a series of folders for the repository manager to quickly check (for departmental and institutional affiliation and CC license correctness), package these up into Simple Archive Format (SAF), ready for batch ingest into DSpace institutional repositories.

The harvester isn't perfect and you should still check to make sure closed or bronze OA items were not harvested in error, but the author has made every effort to do so and has encountered few such errors after much iteration over this.

With this tool, you'll be able to gather together as much of the Open Access scholarly works that your community has formally written and legally deposit these into your organization's institutional repository. If you find this software useful, please drop me an email! 

## Table of Contents

- [Overview](#overview)
- [Key Features](#key-features)
- [Architecture](#architecture)
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

1. **Discovers** open access content using federated search across APIs
2. **Enriches** metadata through cross-referencing multiple data sources
3. **Validates** open access status and licensing information
4. **Locates** PDF files through a multi-tier waterfall approach
5. **Generates** DSpace-compatible SAF (Simple Archive Format) packages

The system is designed for institutional repositories and digital libraries that need to systematically harvest and preserve open access scholarly content.

## Key Features

### 🔍 **Federated Discovery**
- **OpenAlex API**: Primary discovery source with comprehensive metadata
- **Date Range Queries**: Precise temporal filtering using YYYY-MM-DD ranges
- **Institutional Affiliation**: Target-specific institution content harvesting
- **Duplicate Prevention**: Built-in deduplication across sources

### 📊 **Metadata Enrichment**
- **CrossRef**: Author affiliations, funding information, and publisher data
- **DataCite**: Abstracts, datasets, and extended metadata
- **DOAJ**: Journal classification and APC information
- **Sherpa Romeo**: Copyright and self-archiving policies
- **Unpaywall**: Comprehensive OA status and licensing data

### 📄 **PDF Discovery Pipeline**
- **Multi-tier Waterfall**: OpenAlex → Unpaywall → CrossRef TDM → Publisher Heuristics
- **Publisher-Specific Rules**: 25+ publisher-specific URL transformation patterns
- **DOI-Based Patterns**: Automatic PDF URL generation from DOI prefixes
- **HTML Scraping**: Meta-tag extraction for PDF links
- **Learning System**: Adaptive pattern recognition for new publishers

### 🏗️ **Output Management**
- **Split Output Strategy**:
  - `Items_With_PDF/`: Complete SAF packages with PDF files
  - `Items_Only_Link/`: SAF packages with DOI links (no PDF)
  - `citations.ris`: RIS format citations for link-only items
- **DSpace Compatibility**: Full SAF (Simple Archive Format) support
- **Batch Import Scripts**: Automated DSpace import script generation

### 🔒 **Quality Assurance**
- **Strict OA Filtering**: Gold, Hybrid, Diamond, and Green OA only
- **License Validation**: Creative Commons and publisher license verification
- **Metadata Completeness**: Guaranteed minimum metadata requirements
- **Error Resilience**: Comprehensive error handling and recovery

## Architecture

### Core Components

```
FedHarv
├── Discovery Engine (OpenAlex)
├── Enrichment Pipeline
│   ├── CrossRef API
│   ├── Unpaywall API
│   ├── DataCite API
│   ├── DOAJ API
│   └── Sherpa Romeo API
├── PDF Discovery Waterfall
│   ├── OpenAlex Links
│   ├── Unpaywall OA Locations
│   ├── CrossRef TDM
│   └── Publisher Heuristics
├── Output Generation
│   ├── SAF Package Builder
│   ├── RIS Citation Export
│   └── DSpace Import Scripts
└── Quality Control
    ├── OA Status Validation
    ├── License Verification
    └── Metadata Completeness Checks
```

### Data Flow

1. **Query Generation** → OpenAlex API with date range and affiliation filters
2. **Initial Filtering** → OA status validation and duplicate removal
3. **Metadata Enrichment** → Parallel API calls for comprehensive metadata
4. **PDF Discovery** → Multi-tier waterfall PDF location
5. **Output Generation** → SAF packages and citation exports
6. **Quality Assurance** → Final validation and cleanup

## Installation

### Prerequisites

- **Python 3.8+**
- **Virtual Environment** (recommended)
- **API Keys** (see Configuration section)

### Setup

```bash
# Clone repository
git clone <repository-url>
cd fedharv

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp config.ini.example config.ini
# Edit config.ini with your settings
```

### Dependencies

Key packages include:
- `requests` - HTTP client with retry logic
- `ratelimit` - API rate limiting
- `backoff` - Exponential backoff for retries
- `tqdm` - Progress bars
- `concurrent.futures` - Parallel processing
- `configparser` - Configuration management

## Configuration

### Environment Variables (.env)

```bash
# Required API Keys
OPENALEX_EMAIL=your-email@example.com
SCOPUS_API_KEY=your-scopus-key  # Optional

# CrossRef Plus API Token (Optional - enhances rate limits)
CROSSREF_TOKEN=your-crossref-token
```

### Configuration File (config.ini)

```ini
[GENERAL]
# Target institution for harvesting
TARGET_AFFIL=Your university here

# Date range for harvesting (YYYY-MM-DD format)
START_DATE=2023-01-01
END_DATE=2023-12-31

# Output directory
OUTPUT_DIR=output

# Cache directory
CACHE_DIR=cache

[API]
# API endpoints and settings
OPENALEX_EMAIL=your-email@example.com
CROSSREF_TOKEN=
SHERPA_KEY=

[OUTPUT]
# DSpace import settings
DSPACE_BIN=/path/to/dspace
DSPACE_EMAIL=admin@example.com
COLLECTION_ID=123456789/0

# Duplicate checking
DSPACE_CHECK_DUPLICATES=true
DSPACE_API_URL=https://example.com/rest

[ADVANCED]
# Performance tuning
MAX_WORKERS=10
BATCH_SIZE=100

# PDF download settings
PDF_TIMEOUT=60
PDF_MAX_SIZE=50MB
```

# ==============================================================================
# INSTITUTIONAL UNIT MAPPINGS
# ==============================================================================
# Format: keyword_to_find = Exact_Folder_Name
# The script checks these in order. Put specific institutes ABOVE generic faculties.
### Department Mapping (unit_map.json)

```json
{
  # --- Research Institutes & Centers ---
  "argumentation": "Centre_for_Research_in_Reasoning_Argumentation_and_Rhetoric",
  "glier": "Great_Lakes_Institute_for_Environmental_Research",
  "great lakes institute": "Great_Lakes_Institute_for_Environmental_Research",
  "we-spark": "WE_SPARK_Health_Institute",
  "cross-border": "Cross_Border_Institute",
  "fluid dynamics": "Fluid_Dynamics_Research_Institute",
  "automotive": "Automotive_Research_and_Development_Centre"
  # --- Schools (Distinct Units) ---
  "odette": "Odette_School_of_Business",
  "business": "Odette_School_of_Business",
  "creative arts":  "School_of_Creative_Arts",
  "soca": "School_of_Creative_Arts",
  "dramatic art": "School_of_Dramatic_Art",
  "drama": "School_of_Dramatic_Art",
  "computer science": "School_of_Computer_Science"
  "social work": "School_of_Social_Work"
  "environment": "School_of_the_Environment",
  "medical education": "Schulich_School_of_Medicine_Dentistry_Windsor_Campus"
  # --- Faculty of Human Kinetics ---
  "human kinetics": "Faculty_of_Human_Kinetics",
  "kinesiology": "Faculty_of_Human_Kinetics"
  # --- Faculty of Law ---
  "law": "Faculty_of_Law",
  # --- Faculty of Nursing ---
  "nursing": "Faculty_of_Nursing",
  # --- Faculty of Education ---
  "faculty of education": "Faculty_of_Education"
  # --- Faculty of Engineering ---
  "civil": "Faculty_of_Engineering_Civil_Environmental",
  "electrical": "Faculty_of_Engineering_Electrical_Computer",
  "mechanical": "Faculty_of_Engineering_Mechanical_Automotive_Materials"
  # --- Faculty of Science ---
  "biomedical sciences": "Faculty_of_Science_Biomedical_Sciences",
  "integrative biology": "Faculty_of_Science_Integrative_Biology",
  "biology": "Faculty_of_Science_Biological_Sciences",
  "chemistry": "Faculty_of_Science_Chemistry_Biochemistry",
  "economics": "Faculty_of_Science_Economics",
  "mathematics": "Faculty_of_Science_Mathematics_Statistics",
  "physics": "Faculty_of_Science_Physics",
  "earth": "Faculty_of_Science_Earth_and_Environmental_Sciences"    
  # --- Faculty of Arts, Humanities, and Social Sciences (FAHSS) ---
  "communication": "FAHSS_Communication_Media_Film",
  "english": "FAHSS_English_and_Creative_Writing",
  "history": "FAHSS_History",
  "languages": "FAHSS_Languages_Literatures_Cultures",
  "philosophy": "FAHSS_Philosophy",
  "political science": "FAHSS_Political_Science",
  "psychology": "FAHSS_Psychology",
  "sociology": "FAHSS_Sociology_Anthropology_Criminology",
  "women": "FAHSS_Womens_and_Gender_Studies"
  # --- Library ---
  "leddy": "Leddy_Library",
  "library": "Leddy_Library"
}
```

## Usage

### Basic Usage

```bash
# Run with default configuration
python FedHarv-162.py

# Specify custom config file
python FedHarv-162.py --config my-config.ini
```

### Advanced Usage

```bash
# Dry run (no actual harvesting)
python FedHarv-162.py --dry-run

# Limit results for testing
python FedHarv-162.py --max-results 100

# Verbose logging
python FedHarv-162.py --verbose
```

### Output Structure

```
output/
├── Items_With_PDF/
│   ├── item_000/
│   │   ├── dublin_core.xml
│   │   ├── metadata_oaire.xml
│   │   ├── contents
│   │   └── document.pdf
│   └── item_001/
│       └── ...
├── Items_Only_Link/
│   ├── item_100/
│   │   ├── dublin_core.xml
│   │   └── contents
│   └── ...
├── citations.ris
├── harvest_report_20230101_20231231.csv
├── import_batch.sh
└── department_publisher_report.csv
```

## API Integration

### OpenAlex API
- **Purpose**: Primary discovery and initial metadata
- **Rate Limit**: 10 requests/second
- **Data Retrieved**: Title, authors, DOI, OA status, publication date

### CrossRef API
- **Purpose**: Author affiliations and funding data
- **Rate Limit**: 10 requests/second (50 with Plus token)
- **Data Retrieved**: Affiliations, funders, license URLs, PDF links

### Unpaywall API
- **Purpose**: OA status validation and PDF locations
- **Rate Limit**: 10 requests/second
- **Data Retrieved**: OA status, license info, PDF URLs

### DataCite API
- **Purpose**: Extended metadata and abstracts
- **Rate Limit**: 10 requests/second
- **Data Retrieved**: Abstracts, related datasets, descriptions

### DOAJ API
- **Purpose**: Journal classification and APC data
- **Rate Limit**: 10 requests/second
- **Data Retrieved**: Diamond OA status, license information

### Sherpa Romeo API
- **Purpose**: Copyright and self-archiving policies
- **Rate Limit**: 10 requests/second
- **Data Retrieved**: SHERPA policy URIs

## PDF Discovery Pipeline

### Waterfall Strategy

1. **OpenAlex Links** (Fastest)
   - Direct PDF URLs from OpenAlex metadata
   - Highest success rate for recent publications

2. **Unpaywall OA Locations** (Most Comprehensive)
   - Repository and publisher-hosted PDFs
   - Includes embargoed content when available

3. **CrossRef TDM** (Text Mining Access)
   - Publisher-authorized text mining interfaces
   - High-quality, authorized access

4. **Publisher Heuristics** (Fallback)
   - DOI prefix-based URL patterns
   - Domain-specific URL transformations
   - HTML meta-tag scraping

### Publisher-Specific Rules

The system includes transformation rules for 25+ publishers:

- **Wiley**: `/full/` → `/pdf/` transformations
- **Springer**: `/article/` → `/content/pdf/` patterns
- **IEEE**: Document ID to stamp.jsp URLs
- **PLOS**: `/article?` → `/article/file?` with printable type
- **Cambridge**: Core content view to PDF conversion

### Learning System

FedHarv includes an adaptive learning system that:
- Discovers new PDF URL patterns
- Stores successful patterns in `learned_patterns.json`
- Improves success rates over time
- Adapts to new publishers automatically

## Output Structure

### SAF Package Format

Each item directory contains:

```
item_XXX/
├── dublin_core.xml      # Core Dublin Core metadata
├── metadata_oaire.xml   # OAI-ORE extension metadata
├── contents            # Bitstream manifest
└── document.pdf        # PDF file (if available)
```

### Metadata Standards

- **Dublin Core**: Title, authors, dates, identifiers
- **OAI-ORE**: Citation metadata, pagination, ISSN
- **Custom Extensions**: OA status, license URIs, funder info

### Citation Export

RIS format citations for link-only items include:
- Complete bibliographic information
- DOI and URL links
- Abstract text when available
- Proper RIS type classification

## Technical Specifications

### Performance Characteristics

- **Concurrent Processing**: ThreadPoolExecutor with configurable workers
- **Rate Limiting**: 10 requests/second per API with exponential backoff
- **Caching**: Dual-layer (memory + file) with thread-safe operations
- **Memory Management**: Efficient streaming for large PDF downloads

### Error Handling

- **Graceful Degradation**: Continues processing despite individual failures
- **Comprehensive Logging**: Detailed error reporting and progress tracking
- **Retry Logic**: Exponential backoff for transient failures
- **Data Validation**: Strict type checking and null handling

### Data Quality

- **OA Status Validation**: Multi-source verification
- **License Normalization**: Creative Commons URI standardization
- **Metadata Completeness**: Guaranteed minimum field requirements
- **Duplicate Prevention**: DOI-based deduplication

## Troubleshooting

### Common Issues

#### API Rate Limits
```
Solution: Configure API tokens for higher limits
- CrossRef Plus Token: Increases limit from 10 to 50 req/sec
- Sherpa API Key: Required for Sherpa Romeo access
```

#### Configuration Errors
```
Error: Configuration file not found
Solution: Ensure config.ini exists in working directory
```

#### PDF Download Failures
```
Common causes:
- Publisher paywalls (despite OA status)
- Geographic restrictions
- Temporary server issues

Solution: Check PDF waterfall logs for specific failure points
```

#### Memory Issues
```
Solution: Reduce MAX_WORKERS in config
- Default: 10 workers
- Recommended for low memory: 3-5 workers
```

### Logging

Enable verbose logging for debugging:

```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

### Performance Tuning

```ini
[ADVANCED]
MAX_WORKERS=5          # Reduce for memory constraints
BATCH_SIZE=50          # Smaller batches for stability
PDF_TIMEOUT=30         # Faster timeout for unreliable networks
```

## Contributing

### Development Setup

1. Fork the repository
2. Create a feature branch
3. Install development dependencies
4. Run tests and linting
5. Submit pull request

### Code Standards

- **PEP 8** compliance
- **Type hints** for function parameters
- **Docstrings** for all public functions
- **Error handling** with appropriate logging
- **Thread safety** for shared resources

### Testing

```bash
# Run unit tests
python -m pytest tests/

# Run integration tests
python -m pytest tests/integration/

# Check code quality
flake8 FedHarv-162.py
black FedHarv-162.py
```

## License

This project is licensed under the AGPL-v3 License - see the [LICENSE](LICENSE) file for details.

---

**Version**: 1.0 (Public Release)  
**Last Updated**: March 2026  
**Maintainer**: Pascal V. Calarco
**Contact**: pcalarco@uwindsor.ca</content>
<parameter name="filePath">C:\Users\pvcal\Documents\Scripts\README-FedHarv.md
