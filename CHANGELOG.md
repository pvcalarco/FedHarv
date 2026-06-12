# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.1.2] - 2026-06-12

### Added
- Browser-like User-Agent and Accept headers added to default waterfall PDF downloads (in `resolve_url`, `download_file_stream`, and `fetch_html_meta_pdf_link`) to prevent HTTP 403 blocks from Cloudflare or publisher CDN firewalls.
- Detailed `logging.debug(...)` error diagnostics inside caught exception blocks in `pdf.py` for troubleshooting socket, connection, or response failures.

### Changed
- Standardized API client rate-limiting in `api.py` by routing `fetch_unpaywall_data` and `fetch_crossref_data` through the `rate_limited_get` helper, replacing raw session calls.
- Updated automated pytest assertions to match the exponential backoff sleep intervals with jitter in test client queries.
