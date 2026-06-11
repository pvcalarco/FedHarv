# FedHarv — Technical Documentation

FedHarv is a **federated open-access harvester**. For a given institution and date range it
discovers scholarly articles, enriches their metadata, locates open-access PDFs, and packages
everything as **DSpace 7/8 Simple Archive Format (SAF)** for repository ingest.

- **License:** GNU Affero General Public License v3.0 (AGPL-3.0)
- **Version:** 1.0.1 (single-sourced as `fedharv.__version__`)
- **Audience of this doc:** developers and maintainers. For installation and day-to-day use,
  see **[USER_GUIDE.md](USER_GUIDE.md)**.

---

## 1. Architecture at a glance

FedHarv is a single Python package (`fedharv/`) driven by `HarvesterEngine.run()`. The pipeline
is a linear set of stages; per-item work fans out across a thread pool.

```
                 ┌──────────────────────────── HarvesterEngine.run() ───────────────────────────┐
                 │                                                                                │
 config.ini ─▶ DISCOVER ──▶ DEDUPLICATE ──▶  ┌─ per item (15-thread pool) ─────────────────────┐ │
 (+ .env)       │  OpenAlex   by DOI, then    │  ENRICH ─▶ NORMALIZE+GATE OA ─▶ ROUTE ─▶ FETCH   │ │
                │  + Crossref title fallback   │  (Unpaywall,  keep Gold/Hybrid/  folder   PDF    │ │
                │  (concurrent) OpenAlex wins   │   Crossref,   Diamond/Green;     by OA    water- │ │
                │                               │   Sherpa,     skip Closed/       status   fall   │ │
                │                               │   DataCite,   Bronze/Unknown                +    │ │
                │                               │   DOAJ)                                  Playwright│ │
                │                               │                                  ─▶ FINALIZE (SAF)│ │
                │                               └─────────────────────────────────────────────────┘ │
                │                                                                                    │
                └──▶ FINALIZE RUN: harvest_report.csv · citations.ris · department_publisher_report  │
                     · author registry · import_batch.sh · harvest_summary.txt                       │
```

Stage owners: discovery and enrichment live in `fedharv/api.py`; PDF acquisition in
`fedharv/pdf.py`; metadata/SAF/report writing in `fedharv/export.py`; orchestration, OA gating,
routing and finalization in `fedharv/core.py`; config and constant tables in `fedharv/config.py`;
shared helpers in `fedharv/utils.py`.

---

## 2. Module map

| File | Responsibility |
|------|----------------|
| `run_harvester.py` | Root entrypoint: `from fedharv import main; main()`. |
| `fedharv/__init__.py` | Package surface: re-exports `HarvesterEngine`, `main`, `__version__`. |
| `fedharv/config.py` | `ConfigManager` (config.ini + `.env`), API URLs, rate limits, normalization maps, PDF heuristic tables, `__version__`. |
| `fedharv/utils.py` | Decorators (`safe_call`, `cached_api_call`), JSON disk cache, text/JSON normalization, affiliation/department matching. |
| `fedharv/api.py` | `APIClient`: session, `rate_limited_get`, OpenAlex/Crossref discovery, Unpaywall/Sherpa/DataCite/DOAJ enrichment, `check_dspace_duplicate`. |
| `fedharv/pdf.py` | `PDFDownloader`: the PDF waterfall + Playwright fallback; publisher URL heuristics. |
| `fedharv/export.py` | `map_to_dublin_core`, `write_saf`, `generate_ris_block`, `generate_import_scripts`, `MetadataExporter`. |
| `fedharv/core.py` | `HarvesterEngine` orchestrator (discover / merge / process / finalize), thread pools and locks. |
| `process_zotero_pdfs.py` | Standalone companion that backfills PDFs for link-only items from a Zotero RIS export or Unpaywall Green OA. |

---

## 3. The item lifecycle

### 3.1 Discover (`HarvesterEngine.discover`)
Two workers run concurrently (a 2-thread pool):
- **OpenAlex** (`APIClient.harvest_openalex`) — resolves the institution by `Affiliation`
  (falls back to a string match), then cursor-paginates `works` filtered by institution, date
  range, document types and `is_oa:true`. Abstracts are rebuilt from OpenAlex's inverted index.
- **Crossref** (`APIClient.harvest_crossref`) — cursor-paginates `works` by `query.affiliation`
  and date range; OA status is left for the enrichment stage.

All polite HTTP flows through `rate_limited_get` (`ratelimit` ≈ 10 calls/sec + `backoff`); the
session also retries 5xx.

### 3.2 Deduplicate & merge (`HarvesterEngine.deduplicate_and_merge`)
OpenAlex records are keyed by **DOI**, with a **normalized-title** fallback (`normalize_string`).
Crossref records fill gaps: a Crossref item whose DOI is already present is dropped (OpenAlex is
authoritative); a new DOI is added.

### 3.3 Enrich (`HarvesterEngine.fetch_enrichment_batch`)
For each surviving item, a 4-thread pool fans out (30 s timeout each), combining results into one
`enrich` dict:

| Source | Method | Contributes |
|--------|--------|-------------|
| Crossref | `fetch_crossref_data` | publisher, license, affiliations, funders, authors, direct PDF link |
| Unpaywall | `fetch_unpaywall_data` | `oa_status`, best OA location (`url_for_pdf`, license) |
| Sherpa Romeo | `fetch_sherpa_policy` | self-archiving policy URI (by ISSN; needs `SherpaKey`) |
| DataCite | `fetch_datacite_data` | abstract, related datasets |
| DOAJ | `fetch_doaj_data` | journal in DOAJ? Diamond? (drives status upgrades) |

DOIs missing from discovery are recovered first via `APIClient.lookup_doi_by_title`.

### 3.4 Normalize & gate OA status (`process_item`)
`normalize_oa_status` maps raw values via `OA_STATUS_MAPPINGS` (SciELO `10.1590/` DOIs are forced
to **Diamond**). DOAJ then upgrades: a Diamond journal → `Diamond`; an `Unknown`/`Closed` item in
DOAJ → `Gold`. **The gate:** items whose final status is `Closed`, `Bronze`, or `Unknown` are
skipped (counted in `processed_bronze_closed`). Only **Gold, Hybrid, Diamond, Green** proceed.

### 3.5 Route to a folder (`process_item`)
- `Green` (Unpaywall green) → **`Green/`**; **no PDF is fetched** (metadata + link only).
- `Gold` / `Hybrid` / `Diamond` → **`Items_With_PDF/`** (a PDF fetch is attempted).
- The department sub-folder comes from `determine_primary_department` (longest matching
  `[Mappings]` keyword wins; `Multiple` if several match; the affiliation name if none).

### 3.6 Fetch the PDF (`PDFDownloader.fetch_pdf_with_waterfall`)
Only for `Items_With_PDF`. Ordered attempts, each validated by streaming the bytes:
1. OpenAlex `pdf_url` → 2. Unpaywall best OA → 3. Crossref direct link → 4. Scopus API
(Elsevier/ScienceDirect, needs `ScopusKey`) → 5. publisher heuristics (`apply_publisher_heuristics`)
→ 6. HTML meta-tag scrape (`<meta name="citation_pdf_url">`) → 7. DOI-only heuristics.

If all fail **and** a landing URL exists, the item is queued for the **Playwright fallback**
(`process_playwright_queue`) — a single headless-Chromium pass that loads the page, waits for JS,
and tries the citation meta tag then PDF-link/-button selectors. The browser queue runs
**sequentially** (Playwright contexts aren't thread-safe).

### 3.7 Finalize (`HarvesterEngine.finalize_item`)
- **PDF succeeded** → move the temp file to `item_NNN/article.pdf`; `contents` lists `article.pdf`.
- **PDF failed** (was `Items_With_PDF`) → reclassify to **`Items_Only_Link/`**, write `link.txt`
  (`DOI: https://doi.org/…`); counted in `pdf_fail` + `gold_metadata_only`.
- **Green / link-only** → `link.txt` only.
- Always: `write_saf` emits the SAF XML; one row is appended to the harvest CSV; when there is no
  PDF, a RIS block is appended to `citations.ris`. Author identities seen on the item are
  accumulated into the author registry (`extract_authors`).

---

## 4. Concurrency model

| Pool / lock | Where | Purpose |
|-------------|-------|---------|
| 2-thread pool | `discover` | OpenAlex + Crossref in parallel. |
| 15-thread pool | `process_items` | one item per worker through enrich→gate→route→fetch→finalize. |
| 4-thread pool | `fetch_enrichment_batch` | the five enrichment calls per item. |
| sequential | `process_playwright_queue` | browser fallback (contexts not thread-safe). |
| `locks['stats']` | everywhere | guards the `STATS` dict / counters. |
| `locks['csv']` | finalize | guards the CSV writer/handle. |
| `locks['ris']` | finalize | guards the RIS file handle. |
| `locks['author']` | `extract_authors` | guards the author DB. |
| `locks['print']` | finalize | serializes per-item log lines. |

Treat anything touching `STATS`, the CSV, the RIS, or the author DB as needing its lock.

---

## 5. Caching

`@cached_api_call("<prefix>")` on `APIClient` enrichment methods caches by the first argument
(DOI/ISSN) in memory and as JSON on disk. The disk cache lives in **`[General] CacheDir`**
(default `./.fedharv_cache/`, deliberately **outside** `OutputDir` so `run()`'s output cleanup
can't delete it). Entries older than **`[General] CacheMaxAgeDays`** (default 30; `0` = never
expire) are treated as misses. A second run over the same range reuses the cache and makes far
fewer upstream calls.

---

## 6. External API integrations

| API | Endpoint constant | Auth | Used for |
|-----|-------------------|------|----------|
| OpenAlex | `OPENALEX_WORKS_URL`, `OPENALEX_INST_URL` | none (polite `Email`) | discovery, abstracts, OA status |
| Crossref | `CROSSREF_API_URL` | optional `CrossrefPlusToken` | discovery + metadata enrichment |
| Unpaywall | `UNPAYWALL_API` | `Email` required | OA status, best OA PDF location |
| Sherpa Romeo | `SHERPA_API_URL` | `SherpaKey` | self-archiving policy |
| DataCite | `DATACITE_API_URL` | none | abstracts, related datasets |
| DOAJ | `DOAJ_SEARCH_URL` | none | DOAJ/Diamond determination |
| Elsevier Scopus | `api.elsevier.com` | `ScopusKey` | PDF retrieval for Elsevier items |
| DSpace REST | `[DSpace] ApiUrl` | none | optional pre-import duplicate check |

Rate limiting is global via `CALLS` / `RATE_LIMIT_PERIOD` (≈ 10 requests/second) on
`rate_limited_get`.

---

## 7. Configuration reference

`config.ini` (copy from `config.example.FedHarv.ini`) is parsed by `ConfigManager`, which
**preserves key case** (`optionxform = str`). Secrets may also come from `.env`; **config.ini
takes precedence** over env.

### `[Authentication]`
| Key | Type | Default | Purpose |
|-----|------|---------|---------|
| `ScopusKey` | str | `''` (env `SCOPUS_API_KEY`) | Elsevier Scopus PDF retrieval. |
| `SherpaKey` | str | `''` | Sherpa Romeo policy lookups. |
| `CrossrefPlusToken` | str | `''` | Faster/reliable Crossref access. |

### `[General]`
| Key | Type | Default | Purpose |
|-----|------|---------|---------|
| `Email` | str | `''` (env `OPENALEX_EMAIL`) | Polite-pool contact for OpenAlex/Unpaywall/Crossref. |
| `OutputDir` | str | `FedHarv_Output` | Output root. Supports templating (below). |
| `CacheDir` | str | `.fedharv_cache` | Cross-run API cache, kept outside `OutputDir`. |
| `CacheMaxAgeDays` | int | `30` | Cache TTL in days (`0` = never expire). |
| `AuthorRegistryFile` | str | `author_registry.txt` | Author-registry filename (under `OutputDir`). |

`OutputDir` placeholders expanded by `resolve_output_dir_template`: `{StartDate}`, `{EndDate}`,
`{StartYear}`, `{EndYear}`.

### `[Search]`
| Key | Type | Default | Purpose |
|-----|------|---------|---------|
| `StartDate` / `EndDate` | str `YYYY-MM-DD` | required | Harvest window (validated at startup). |
| `Affiliation` | str | required | Target institution; matched via `affiliation_matches_target`. |
| `StrictAffiliationMatch` | bool | `yes` | Skip items with no matching affiliation. |

### `[DSpace]`
| Key | Type | Default | Purpose |
|-----|------|---------|---------|
| `CheckDuplicates` | bool | `no` | Query DSpace REST before packaging an item. |
| `ApiUrl` | str | `''` | DSpace 7/8 REST base URL (for the duplicate check). |
| `AdminEmail` | str | `admin@example.org` | `--eperson` in the import script. |
| `BinPath` | str | `/dspace/bin/dspace` | DSpace CLI path in the import script. |
| `DefaultCollection` | str | `123456789/0` | Fallback collection handle for unmapped departments. |

### `[Mappings]` (optional)
`affiliation keyword = Department_Folder_Name`. Longest matching keyword wins; the value is the
output sub-folder (and the `[Collections]` lookup key).

### `[Collections]` (optional)
`Department_Folder_Name = handle` (case-sensitive). Per-department DSpace collection handle used
by `generate_import_scripts`; folders with no entry fall back to `DefaultCollection`.

---

## 8. Output artifacts (under `OutputDir`)

```
OutputDir/
├── Items_With_PDF/<dept>/item_NNN/   # dublin_core.xml, metadata_*.xml, contents, article.pdf
├── Items_Only_Link/<dept>/item_NNN/  # …, link.txt  (Gold/Hybrid/Diamond whose PDF fetch failed)
├── Green/<dept>/item_NNN/            # …, link.txt  (green OA; no PDF attempted)
├── harvest_report_<start>_<end>.csv  # one row per packaged item
├── citations.ris                     # RIS citations for every link-only item
├── department_publisher_report.csv   # dept × publisher counts
├── author_registry.txt               # target-affiliated authors: name : depts : emails : orcids
├── import_batch.sh                   # DSpace import commands (per-dept --collection)
└── harvest_summary.txt               # dump of the run STATS
```

Per-item SAF files (`write_saf`): `dublin_core.xml` (dc schema) plus `metadata_oaire.xml`,
`metadata_organization.xml`, `metadata_person.xml` as applicable, a `contents` manifest, and the
bitstream (`article.pdf`) or `link.txt`. Out of tree: the cache (`.fedharv_cache/`) and the run
log (`fedharv.log`, cwd).

---

## 9. Extending FedHarv

- **New publisher PDF rule:** add a `DOI prefix → URL template` to `DOI_PDF_PATTERNS`, or a
  `domain → transform` to `DOMAIN_URL_TRANSFORMS` (`config.py`). For one-off/site-specific rules,
  hand-author `learned_patterns.json` (`{ "10.xxxx": "https://…/{doi}.pdf" }`) — it is read at
  startup and tried before the built-in heuristics.
- **New department folder:** add a keyword→folder line under `[Mappings]`.
- **New collection handle:** add a folder→handle line under `[Collections]`.
- **New enrichment source:** add a cached `fetch_*` method to `APIClient` and wire it into
  `fetch_enrichment_batch`.

---

## 10. Testing & CI

- Tests live in `tests/` (pytest), wired via `[tool.pytest.ini_options]` in `pyproject.toml`
  (`pythonpath = ["."]`, so `pytest` resolves the package with no install). Coverage: an import
  **smoke** test (catches packaging/import regressions), pure helpers in `utils.py`/`export.py`,
  and the disk cache.
- Install dev deps with `pip install -r requirements-dev.txt` (runtime deps + `pytest`).
- CI: `.github/workflows/ci.yml` runs `compileall` + `pytest` on Python 3.9 and 3.12 for every
  push/PR to `main`.
