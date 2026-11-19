# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.2.0] - 2026-11-19

### Added
- Interactive duplicate reporting during processing: when a DOI already exists in DSpace, the harvester now logs `[DUPLICATE] Found in DSpace: <doi> - <title>...`.
- Optional CrossRef discovery toggle (`[Search] EnableCrossrefDiscovery`, default `no`) prioritizing OpenAlex for primary discovery, plus exact-phrase affiliation and date-range pre-filtering when CrossRef discovery is enabled.
- Package metadata via `pyproject.toml`, installable `fedharv` CLI (`fedharv.cli:main` / `python -m fedharv`), and `Dockerfile` container support.
- Per-host sliding-window rate limiter with RFC 7231 `Retry-After` support (numeric seconds and HTTP-date).
- OpenAlex API key support (`[Authentication] OpenAlexKey` / `OPENALEX_API_KEY`) and `.env` fallback for `CROSSREF_TOKEN`.
- Companion utilities (`src/zip_saf_packages.py`, `src/process_zotero_rdf_to_folders.py`) and expanded unit test suite (43 tests).

### Security
- Untracked local `src/config.ini` and ignored `.env`, `config.ini`, `src/config.ini`, `learned_patterns.json`, and `*.code-workspace`.
- Quoted all arguments in generated `import_batch.sh` via `shlex.quote` (with `set -euo pipefail` and Unix LF line endings).
- Blocked path traversal in Zotero RIS `L1` attachment paths and changed Zotero PDF downloads to fail closed on TLS verification errors (opt-in `--insecure`).
- Validated `http`/`https` schemes, added a 100 MB PDF download size cap with atomic `.part` writes, neutralized CSV formula prefixes, and replaced `xml.dom.minidom` re-parsing with native `ET.indent`.

### Fixed
- Generated `import_batch.sh` after `process_items()` populates item directories, and made `--dry-run` strictly read-only without cleaning or writing files.
- Fixed Green OA Unpaywall PDF detection to read `best_oa_location.url_for_pdf` (and omit `link.txt` when a PDF succeeds).
- Fixed double-`/pdf/pdf/` publisher URL transforms (Wiley, SAGE, CDN Science Pub), SciELO query separators, and Cambridge trailing-slash handling.
- Fixed `.env` fallback precedence when INI keys are blank, stripped brackets from `Email`, and validated `StartDate <= EndDate`.
- Fixed case-insensitive DOI deduplication, percent-encoded DOIs in API URLs, preserved math comparisons while unescaping HTML entities in `clean_text`, guarded null/empty API fields, stopped fabricating CC-BY 4.0 URIs for Diamond OA without an explicit license, and prevented Playwright mid-batch errors from double-finalizing items.
- Tightened `affiliation_matches_target` to reject generic substring false positives and prevented `learn_pattern_from_url` from overriding curated DOI prefixes or learning issue-specific URLs.

### Changed
- Removed nested `ThreadPoolExecutor` pools, routed all API calls through pooled `requests.Session`, staged enrichment to reuse CrossRef payloads and skip unnecessary DataCite calls, and isolated bounded LRU + atomic disk caching per `cache_dir`.
- Removed sunsetted Sherpa Romeo integration (`SherpaKey`, `SHERPA_API_URL`, `fetch_sherpa_policy`, and `Sherpa_Policy` CSV column) ahead of future OpenPolicyFinder support.
- Unified config and PDF utilities across `run_harvester.py`, `process_zotero_pdfs.py`, and `zip_saf_packages.py`; moved runtime `learned_patterns.json` into `CACHE_DIR`; consolidated license to root `LICENSE` and removed stale/duplicate files (`docs/requirements.txt`, `docs/README.FedHarv.md`, stray `.code-workspace`).

## [1.1.2] - 2026-06-12

### Added
- Browser-like User-Agent and Accept headers added to default waterfall PDF downloads (in `resolve_url`, `download_file_stream`, and `fetch_html_meta_pdf_link`) to prevent HTTP 403 blocks from Cloudflare or publisher CDN firewalls.
- Detailed `logging.debug(...)` error diagnostics inside caught exception blocks in `pdf.py` for troubleshooting socket, connection, or response failures.
- Dynamic pattern learning mechanism that generalizes successful meta-scraped PDF URLs (by checking for DOI or DOI suffix matches) and writes them to `learned_patterns.json`.
- A unit test coverage suite for dynamic pattern learning (`test_learn_pattern_from_url_deduces_doi_placeholder`).

### Changed
- Standardized API client rate-limiting in `api.py` by routing `fetch_unpaywall_data` and `fetch_crossref_data` through the `rate_limited_get` helper, replacing raw session calls.
- Updated automated pytest assertions to match the exponential backoff sleep intervals with jitter in test client queries.
- Consolidated orchestrator state locks: merged `locks['author']` into `locks['stats']` and removed redundant `locks['print']` wrappers.
- Optimized Playwright fallback timeouts (reduced goto timeout from 60s to 30s) and replaced static 5-second sleeps with a dynamic 3-second `networkidle` state wait.
