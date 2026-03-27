# Zotero PDF Processor

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)

A companion utility for FedHarv that integrates Zotero PDF collections with DSpace SAF packages, providing a bridge between reference management and institutional repository workflows.

## Table of Contents

- [Overview](#overview)
- [Key Features](#key-features)
- [Architecture](#architecture)
- [Installation](#installation)
- [Configuration](#configuration)
- [Usage](#usage)
- [Workflow Integration](#workflow-integration)
- [Technical Specifications](#technical-specifications)
- [Troubleshooting](#troubleshooting)
- [Contributing](#contributing)
- [License](#license)

## Overview

The Zotero PDF Processor is a specialized post-processing utility designed to enhance FedHarv outputs by integrating high-quality PDF collections from Zotero reference management software. It addresses the common challenge where institutional repositories need to combine the comprehensive metadata harvesting capabilities of FedHarv with the superior PDF collections maintained by researchers using Zotero.

### Core Functionality

The processor implements a two-tier PDF acquisition strategy:

1. **Primary**: Extract and copy PDFs from Zotero exports that match harvested DOIs
2. **Fallback**: Download open access PDFs directly from Green OA sources when Zotero PDFs are unavailable

This approach ensures maximum PDF coverage while maintaining the integrity of researcher-curated PDF collections.

## Key Features

### 🔗 **Zotero Integration**
- **RIS Export Processing**: Parses Zotero RIS exports with embedded PDF paths
- **DOI Mapping**: Intelligent DOI extraction and normalization from Zotero metadata
- **Path Resolution**: Cross-platform path handling for Zotero's file structure
- **Batch Processing**: Efficient processing of large Zotero collections

### 📄 **PDF Acquisition Pipeline**
- **Zotero-First Priority**: Preserves researcher-curated PDF collections
- **Green OA Fallback**: Automatic download from Unpaywall-identified sources
- **Content Validation**: PDF format verification before copying/downloading
- **Duplicate Prevention**: Intelligent handling of multiple PDF sources

### 🏗️ **SAF Package Enhancement**
- **Seamless Integration**: Works with existing FedHarv SAF package structure
- **Metadata Updates**: Automatic contents file updates for new PDFs
- **Link Removal**: Clean transition from link-only to PDF-complete packages
- **Directory Preservation**: Maintains original folder structure and naming

### 📊 **Quality Assurance**
- **Comprehensive Reporting**: Detailed success/failure tracking
- **Miss Analysis**: CSV reports for items requiring manual intervention
- **Progress Monitoring**: Real-time processing status and statistics
- **Error Resilience**: Graceful handling of missing files and network issues

## Architecture

### Processing Pipeline

```
Zotero Export ──► RIS Parser ──► DOI Mapping ──► PDF Locator
       │                                               │
       └───────────────► Zotero Base Directory ────────┘
                                                       │
FedHarv Output ──► Items_Only_Link Scanner ─────────────┤
       │                                               │
       └───────────────► link.txt DOI Extraction ──────┘
                                                       │
Unpaywall API ──► Green OA URL Discovery ──────────────┤
                                                       │
PDF Downloader ──► Content Validation ──► SAF Enhancement
```

### Data Flow

1. **Zotero Export Analysis** → Parse RIS file for DOI-to-PDF mappings
2. **FedHarv Output Scanning** → Identify link-only SAF packages needing PDFs
3. **DOI Matching** → Cross-reference DOIs between systems
4. **PDF Acquisition** → Zotero copy or Green OA download
5. **SAF Package Updates** → Add PDFs and update metadata
6. **Reporting** → Generate success/failure reports

## Installation

### Prerequisites

- **Python 3.8+**
- **FedHarv Output Directory**: Must contain `Items_Only_Link` folder
- **Zotero RIS Export**: With "Export Files" option enabled
- **Network Access**: For Green OA downloads

### Setup

```bash
# Ensure FedHarv has been run first
python FedHarv-162.py

# Run the Zotero processor
python process_zotero_pdfs.py
```

### Dependencies

The script uses standard Python libraries:
- `requests` - HTTP client for OA downloads
- `configparser` - Configuration file parsing
- `urllib.parse` - URL decoding for file paths

## Configuration

### Integration with FedHarv Config

The processor automatically reads from the same `config.ini` file used by FedHarv:

```ini
[General]
OutputDir=output_2023        # Base directory containing FedHarv output
Email=your-email@university.edu  # For Unpaywall API access

[Search]
Year=2023                    # Year label for file matching
```

### Zotero Export Requirements

#### Export Settings
- **Format**: RIS (Research Information Systems)
- **Options**: ✅ Export Files (critical for PDF paths)
- **Location**: Place in FedHarv output directory

#### Supported Export Structures
```
output_2023/
├── Exported Items/           # Zotero export folder
│   ├── Exported Items.ris   # RIS file with PDF mappings
│   └── files/               # PDF storage directory
│       ├── 123/            # Zotero item folders
│       │   └── paper.pdf
│       └── 456/
│           └── article.pdf
└── Items_Only_Link/         # FedHarv output (target)
    ├── item_001/
    │   └── link.txt        # Contains DOI
    └── item_002/
        └── link.txt
```

## Usage

### Basic Workflow

```bash
# 1. Run FedHarv to generate initial harvest
python FedHarv-162.py

# 2. Export from Zotero with PDFs
# - Select items in Zotero
# - File → Export Library
# - Format: RIS
# - Check: Export Files
# - Save to FedHarv output directory

# 3. Run Zotero processor
python process_zotero_pdfs.py
```

### Output Enhancement

**Before Processing:**
```
Items_Only_Link/item_001/
├── dublin_core.xml
├── contents
└── link.txt (contains DOI)
```

**After Processing:**
```
Items_Only_Link/item_001/
├── dublin_core.xml
├── contents (updated with article.pdf)
├── article.pdf (from Zotero or OA download)
└── link.txt (removed)
```

### Command Line Options

Currently, the script runs with automatic configuration detection. Future versions may support:

```bash
python process_zotero_pdfs.py --config custom-config.ini
python process_zotero_pdfs.py --dry-run
python process_zotero_pdfs.py --verbose
```

## Workflow Integration

### Complete Institutional Workflow

```
Researcher Workflow          │   Repository Workflow
                             │
1. Collect references in Zotero   FedHarv Discovery
2. Download PDFs to Zotero    ├──► OpenAlex harvesting
3. Export with RIS + PDFs     │   └── Metadata enrichment
                             │
4. Run Zotero Processor       PDF Enhancement
   ├──► DOI matching          ├──► Zotero PDF integration
   └──► Green OA fallback     └──► SAF package completion
                             │
5. Manual review of misses    DSpace Import
   └──► Quality assurance     └──► Repository ingestion
```

### Integration Points

- **FedHarv Output**: Reads `Items_Only_Link` directory structure
- **Zotero Export**: Processes RIS files with L1 (file) tags
- **Unpaywall API**: Fallback PDF source for Green OA content
- **SAF Format**: Updates DSpace-compatible package structure

## Technical Specifications

### File Processing

#### RIS Format Support
- **DOI Extraction**: From `DO -` tags
- **PDF Path Mapping**: From `L1 -` tags with URL decoding
- **UTF-8 Handling**: Proper encoding support for international characters

#### PDF Validation
- **Magic Bytes Check**: Verifies `%PDF-` header in first chunk
- **Content-Type Headers**: HTTP content validation for downloads
- **File Size Limits**: Reasonable bounds checking

### Error Handling

#### Graceful Degradation
- **Missing Zotero PDFs**: Automatic fallback to Green OA
- **Network Failures**: Retry logic with exponential backoff
- **Malformed Data**: Skip problematic items with detailed logging
- **Permission Issues**: Clear error messages for file access problems

#### Recovery Mechanisms
- **Partial Success**: Continue processing after individual failures
- **State Preservation**: Idempotent operations allow restart
- **Detailed Logging**: Comprehensive error reporting for troubleshooting

### Performance Characteristics

- **Concurrent Processing**: Sequential processing with polite delays
- **Memory Efficient**: Streaming downloads for large PDFs
- **Rate Limiting**: Built-in delays for API politeness
- **Progress Tracking**: Real-time status updates

## Troubleshooting

### Common Issues

#### No PDF Mappings Found
```
Error: No PDF mappings found in the RIS file
Solution:
1. Ensure "Export Files" was checked in Zotero export
2. Verify RIS format was selected
3. Confirm items actually have attached PDFs in Zotero
4. Check file permissions on export directory
```

#### Zotero Path Resolution Issues
```
Warning: Map exists but file missing
Solution:
1. Ensure complete Zotero export (all subdirectories)
2. Check cross-platform path separator handling
3. Verify Zotero base directory path in config
4. Confirm no file permission issues
```

#### Green OA Download Failures
```
Failed to download PDF from URL
Common causes:
- Geographic access restrictions
- Temporary server issues
- Paywall despite OA status
- Network connectivity problems

Solution:
- Check Unpaywall API email configuration
- Verify network connectivity
- Review download timeout settings
- Consider manual PDF acquisition for critical items
```

#### DOI Parsing Errors
```
Could not parse DOI from link.txt
Solution:
1. Verify FedHarv output format
2. Check for malformed DOI URLs
3. Ensure link.txt files are not corrupted
4. Review DOI extraction regex patterns
```

### Diagnostic Tools

#### Manual Verification
```bash
# Check RIS file structure
head -20 "citations-2023.ris"

# Verify DOI extraction
grep "DO  -" "citations-2023.ris" | head -5

# Check PDF mappings
grep "L1  -" "citations-2023.ris" | head -5
```

#### Configuration Validation
```bash
# Test config file access
python -c "import configparser; c=configparser.ConfigParser(); c.read('config.ini'); print('Config loaded successfully')"
```

### Performance Tuning

#### For Large Collections
```python
# Consider adjusting timeouts in the script
PDF_DOWNLOAD_TIMEOUT = 60  # seconds
NETWORK_RETRY_DELAY = 2    # seconds between downloads
```

#### Memory Considerations
- **Streaming Downloads**: PDFs downloaded in chunks to minimize memory usage
- **File Buffering**: Efficient handling of large files
- **Temporary Files**: No intermediate file creation

## Contributing

### Development Setup

1. **Environment Setup**
   ```bash
   git clone <repository-url>
   cd zotero-pdf-processor
   python -m venv venv
   source venv/bin/activate  # Windows: venv\Scripts\activate
   pip install -r requirements.txt
   ```

2. **Testing Data**
   - Create sample FedHarv output structure
   - Generate test Zotero RIS export
   - Prepare mock Unpaywall API responses

3. **Code Standards**
   - PEP 8 compliance
   - Comprehensive error handling
   - Clear logging messages
   - Type hints where beneficial

### Testing Strategy

```bash
# Unit tests
python -m pytest tests/test_ris_parser.py
python -m pytest tests/test_pdf_downloader.py

# Integration tests
python -m pytest tests/test_workflow_integration.py

# End-to-end testing
python process_zotero_pdfs.py --test-mode
```

### Enhancement Opportunities

- **Batch Processing**: Parallel PDF downloads with rate limiting
- **Quality Validation**: PDF integrity and content checks
- **Metadata Enhancement**: Additional fields from Zotero
- **Progress Persistence**: Resume capability for long-running processes
- **GUI Interface**: Desktop application for easier operation

## License

This project is licensed under the AGPL-v3 License - see the [LICENSE](LICENSE) file for details.

---

**Version**: 1.0.0  
**Last Updated**: March 2026  
**Maintainer**: Pascal V. Calarco         
**Compatibility**: FedHarv v1.0.0+    
**Contact**: pcalarco@uwindsor.ca

---

**Note**: This utility is designed as a companion tool for FedHarv. Ensure FedHarv has been run first to generate the required `Items_Only_Link` directory structure.</content>
<parameter name="filePath">C:\Users\pvcal\Documents\Scripts\ZOTERO_PROCESSOR_README.md
