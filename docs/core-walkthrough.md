# HarvesterEngine Class Walkthrough

Here is a detailed walkthrough of the HarvesterEngine class (located in src/fedharv/core.py), explaining what it does and how it orchestrates the overall harvesting process:

1. Purpose & Initialization (__init__, setup_logging, init_stats)
The HarvesterEngine is the central orchestration class of the script. When initialized in run_harvester.py, it pulls together the various modular components of the application:

Configuration Management: It loads the ConfigManager, providing access to output directories, target affiliations, dates, and API endpoints.

Dependencies: It instantiates the APIClient (for fetching metadata), the PDFDownloader (for downloading PDFs using waterfall logic and learned patterns), and the MetadataExporter (for writing CSV, RIS, and SAF outputs).

Concurrency Setup: It initializes thread-safe locks (self.locks) to safely update global statistics, CSV files, and RIS files when multiple threads run simultaneously.

Statistics Initialization: It prepares a large internal dictionary (self.STATS) to track metrics like duplicates, Open Access (OA) types (Gold, Green, Hybrid, Diamond), and API success rates.

1. Main Entry Point (run method)
The run(dry_run=False, resume=False) method acts as the control center:

Directory Preparation: Depending on the --resume flag passed from run_harvester.py, it either preserves the existing output directory or performs a robust cleanup to start fresh.

DSpace Scripts Generation: It automatically writes .sh and .bat batch import scripts for DSpace using the output directory configurations.

Discovery & Deduplication: It invokes discover() and then deduplicate_and_merge().

Dry Run Check: If the --dry-run flag is active, execution cleanly halts here before any downloading or heavy processing happens.

Processing: If proceeding, it triggers process_items().

1. API Discovery & Merging (discover, deduplicate_and_merge)
discover(): Queries the OpenAlex API as the primary discovery engine for records matching the target affiliation within the configured date range (using OpenAlex's automated ROR/institution mapping). If EnableCrossrefDiscovery is enabled in configuration, it queries both OpenAlex and Crossref concurrently using a ThreadPoolExecutor.

deduplicate_and_merge(): Deduplicates candidate records by DOI (falling back to normalized titles). If Crossref discovery is enabled, it merges both source lists without duplicate records.

1. Concurrent Enrichment & Processing (process_items, process_item, fetch_enrichment_batch)

process_items(final_list): Iterates over the deduplicated list using a ThreadPoolExecutor with 15 concurrent threads, feeding each item into process_item(idx, item).

process_item() Logic per Paper:

DSpace Check: Pings the local DSpace instance to see if the item already exists in the repository (discarding it if found).

Enrichment: Calls fetch_enrichment_batch(), which uses another nested 4-worker thread pool to query Crossref, Unpaywall, Sherpa, DataCite, and DOAJ in parallel for missing metadata, licenses, and OA status.

Filtering: Examines the OA status. If a paper is marked as "Closed," "Bronze," or "Unknown," it is skipped.

Target Affiliation Verification: Calls extract_authors to rigorously verify that at least one author genuinely matches the target university affiliation. It also uses determine_primary_department to place the item into the correct department bucket.

PDF Fetching: Determines which folder the item belongs to (e.g., Items_With_PDF, Green). If applicable, it triggers self.pdf_downloader.fetch_pdf_with_waterfall().

Playwright Fallback: If the standard Python HTTP requests fail to download the PDF, the engine appends the item to a playwright_queue to be handled by a headless browser later, rather than blocking the fast processing threads.

1. Finalizing Output (finalize_item)
Whether called immediately upon a successful/failed HTTP download or later by the Playwright fallback queue, finalize_item() finishes the job:

Writing Metadata: It uses MetadataExporter to write DSpace Simple Archive Format (SAF) metadata via write_saf().

Logging CSV & RIS: It securely locks the csv and ris threads, appending rows indicating success or failure, metadata, and citation format.

Folder Moving: Depending on the PDF success state, it appropriately moves the item to Items_With_PDF or Items_Only_Link.

1. Cleanup & Summary (cleanup_empty_collection_dirs, generate_summary)

Once all items and the headless Playwright queue are finished, the engine runs final cleanup tasks:

Pruning: It scans the output directories and removes any generated department folders that ended up empty.

Reporting: It calls generate_summary() which outputs a harvest_summary.txt of all execution stats (e.g., success rates, OA types found). It also outputs an Author Registry and a
Department-Publisher breakdown report.

How it interacts with the rest of the script:
run_harvester.py manages the User Interface/CLI. It parses commands (--config, --dry-run) and boots up the HarvesterEngine.

The Engine completely abstracts away the APIs (api.py), filesystem operations (export.py), and PDF scrapers (pdf.py), acting as a highly concurrent traffic controller that feeds data down the pipeline while ensuring thread safety.