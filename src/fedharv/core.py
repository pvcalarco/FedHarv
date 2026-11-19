# SPDX-License-Identifier: AGPL-3.0-only
import json
import logging
import os
import re
import shutil
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

from .api import APIClient, check_dspace_duplicate, parse_crossref_message
from .config import ConfigManager
from .export import MetadataExporter, generate_import_scripts, map_to_dublin_core, write_saf
from .pdf import PDFDownloader, resolve_url
from .utils import (
    affiliation_matches_target,
    deep_get,
    determine_primary_department,
    ensure_list_of_dicts,
    extract_all_affiliations,
    normalize_doi,
    normalize_license_uri,
    normalize_oa_status,
    normalize_string,
)


def robust_cleanup(path):
    if not os.path.exists(path):
        return
    for i in range(5):
        try:
            shutil.rmtree(path)
            return
        except Exception as e:
            logging.warning(f"Cleanup Warning: Could not delete {path} (Attempt {i + 1}/5). Is a PDF open? {e}")
            time.sleep(2)
    raise RuntimeError(f"Failed to clean output directory {path}. Please close any open files.")


def robust_cleanup_output_dir(output_dir, preserve_path=None):
    """Clean output directory while optionally preserving a nested path (e.g., cache)."""
    if not os.path.exists(output_dir):
        return

    if not preserve_path:
        robust_cleanup(output_dir)
        return

    output_abs = os.path.abspath(output_dir)
    preserve_abs = os.path.abspath(preserve_path)
    prefix = output_abs + os.sep
    if not (preserve_abs == output_abs or preserve_abs.startswith(prefix)):
        robust_cleanup(output_dir)
        return

    for entry in os.listdir(output_dir):
        candidate = os.path.abspath(os.path.join(output_dir, entry))
        if preserve_abs == candidate or preserve_abs.startswith(candidate + os.sep):
            continue
        if os.path.isdir(candidate):
            robust_cleanup(candidate)
        else:
            robust_remove_file(candidate)


def robust_remove_file(path):
    if not path or not os.path.exists(path):
        return
    for _ in range(5):
        try:
            os.remove(path)
            return
        except OSError:
            time.sleep(1)


def robust_move_file(src, dst):
    if not src or not os.path.exists(src):
        return
    for _ in range(5):
        try:
            shutil.move(src, dst)
            return
        except OSError:
            time.sleep(1)
    try:
        shutil.move(src, dst)
    except Exception as exc:
        logging.warning(f"Failed to move {src} -> {dst}: {exc}")


def _clean_orcid(raw_orcid):
    if not raw_orcid:
        return None
    return re.sub(r"^https?://orcid\.org/", "", str(raw_orcid).strip(), flags=re.IGNORECASE) or None


def _format_crossref_author(auth):
    family = (auth.get("family") or "").strip()
    given = (auth.get("given") or "").strip()
    if family and given:
        return f"{family}, {given}"
    return family or given or None


class HarvesterEngine:
    """The central orchestration engine for the Federated OA Harvester."""

    def __init__(self, config_path=None):
        self.config = ConfigManager(config_path)
        self.setup_logging()
        self.api_client = APIClient(self.config)
        self.patterns_file = os.path.join(self.config.CACHE_DIR, "learned_patterns.json")
        self.pdf_downloader = PDFDownloader(self.api_client, patterns_file=self.patterns_file)
        self._sorted_unit_keys = tuple(sorted(self.config.UNIT_MAP.keys(), key=len, reverse=True))

        self.init_stats()

        self.metadata_exporter = MetadataExporter(
            output_dir=self.config.OUTPUT_DIR,
            csv_file=self.csv_file,
            ris_file=self.ris_file,
            author_file=self.author_file,
            publisher_report_file=self.publisher_report_file,
            institution_name=self.config.TARGET_AFFIL,
        )

        self.locks = {
            "stats": threading.Lock(),
            "csv": threading.Lock(),
            "ris": threading.Lock(),
        }

    def setup_logging(self):
        logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

    def init_stats(self):
        self.STATS = {
            "openalex_raw_total": 0,
            "openalex_oa_kept": 0,
            "crossref_raw_total": 0,
            "dspace_duplicates": 0,
            "processed_gold": 0,
            "processed_hybrid": 0,
            "processed_diamond": 0,
            "processed_green": 0,
            "processed_bronze_closed": 0,
            "gold_metadata_only": 0,
            "skipped_no_target_affiliation": 0,
            "pdf_sources": Counter(),
            "pdf_success": 0,
            "pdf_fail": 0,
            "pdf_skipped_existing": 0,
            "enriched_crossref": 0,
            "enriched_fundref": 0,
            "enriched_datacite": 0,
            "enriched_orcid": 0,
            "enriched_doaj": 0,
            "dept_breakdown": defaultdict(Counter),
            "dept_publisher_breakdown": defaultdict(Counter),
            "author_db": defaultdict(lambda: {"depts": set(), "emails": set(), "orcids": set()}),
        }

        safe_start = self.config.START_DATE.replace("-", "")
        safe_end = self.config.END_DATE.replace("-", "")

        self.csv_file = os.path.join(self.config.OUTPUT_DIR, f"harvest_report_{safe_start}_{safe_end}.csv")
        self.ris_file = os.path.join(self.config.OUTPUT_DIR, "citations.ris")
        self.author_file = os.path.join(self.config.OUTPUT_DIR, self.config.AUTHOR_REGISTRY_FILE)
        self.publisher_report_file = os.path.join(self.config.OUTPUT_DIR, "department_publisher_report.csv")

    def increment_stat(self, stat_name, amount=1):
        with self.locks["stats"]:
            current = self.STATS.get(stat_name)
            if isinstance(current, Counter):
                return
            self.STATS[stat_name] = (current or 0) + amount

    def discover(self):
        if getattr(self.config, "ENABLE_CROSSREF_DISCOVERY", False):
            logging.info(
                f"Starting concurrent API discovery (OpenAlex + Crossref) "
                f"from {self.config.START_DATE} to {self.config.END_DATE}..."
            )

            with ThreadPoolExecutor(max_workers=2) as executor:
                f_oa = executor.submit(
                    self.api_client.harvest_openalex,
                    self.config.START_DATE,
                    self.config.END_DATE,
                    self.config.TARGET_AFFIL,
                    self.increment_stat,
                )
                f_cr = executor.submit(
                    self.api_client.harvest_crossref,
                    self.config.START_DATE,
                    self.config.END_DATE,
                    self.config.TARGET_AFFIL,
                    self.increment_stat,
                )
                oa_list = f_oa.result()
                cr_list = f_cr.result()
            return oa_list, cr_list

        logging.info(
            f"Starting API discovery (OpenAlex) "
            f"from {self.config.START_DATE} to {self.config.END_DATE}..."
        )
        oa_list = self.api_client.harvest_openalex(
            self.config.START_DATE,
            self.config.END_DATE,
            self.config.TARGET_AFFIL,
            stats_callback=self.increment_stat,
        )
        return oa_list, []

    def deduplicate_and_merge(self, openalex_list, crossref_list):
        if crossref_list:
            logging.info(f"--- Merging: {len(openalex_list)} OpenAlex items + {len(crossref_list)} Crossref items ---")
        else:
            logging.info(f"--- OpenAlex Discovery Complete: {len(openalex_list)} items found ---")

        unique_map = {}
        seen_titles = set()
        for item in openalex_list:
            doi = normalize_doi(item.get("doi"))
            title_key = normalize_string(item.get("title"))
            if title_key:
                seen_titles.add(title_key)
            if doi:
                item["doi"] = doi
                unique_map[doi] = item
            elif title_key:
                unique_map[f"title:{title_key}"] = item

        crossref_new = 0
        for item in crossref_list:
            doi = normalize_doi(item.get("doi"))
            title_key = normalize_string(item.get("title"))
            if doi:
                item["doi"] = doi
                if doi not in unique_map:
                    unique_map[doi] = item
                    if title_key:
                        seen_titles.add(title_key)
                    crossref_new += 1
            elif title_key and title_key not in seen_titles:
                unique_map[f"title:{title_key}"] = item
                seen_titles.add(title_key)
                crossref_new += 1

        logging.info(f"--- Deduplication Complete: {crossref_new} new items added from Crossref ---")
        return list(unique_map.values())

    def _iter_target_authors(self, item, cr):
        """Yield (name, orcid, dept) tuples for authors matching TARGET_AFFIL across OpenAlex and CrossRef."""
        target_affil = self.config.TARGET_AFFIL

        if item.get("source") == "openalex":
            for ship in ensure_list_of_dicts(deep_get(item, ["raw", "authorships"])):
                inst_names = [
                    inst["display_name"]
                    for inst in ensure_list_of_dicts(ship.get("institutions"))
                    if inst.get("display_name")
                ]
                raw_affs = [s for s in (ship.get("raw_affiliation_strings") or []) if isinstance(s, str)]
                raw_single = ship.get("raw_affiliation_string")
                if isinstance(raw_single, str) and raw_single:
                    raw_affs.append(raw_single)

                if not affiliation_matches_target(inst_names + raw_affs, target_affil):
                    continue

                dept_found = None
                for raw_aff in raw_affs:
                    match = re.search(r"(Department of [^,]+|School of [^,]+|Faculty of [^,]+)", raw_aff, re.IGNORECASE)
                    if match:
                        dept_found = match.group(1).strip()
                        break

                yield (
                    deep_get(ship, ["author", "display_name"]),
                    _clean_orcid(deep_get(ship, ["author", "orcid"])),
                    dept_found,
                )

        if cr and cr.get("raw_message"):
            for auth in ensure_list_of_dicts(deep_get(cr, ["raw_message", "author"])):
                aff_names = [
                    aff["name"]
                    for aff in ensure_list_of_dicts(auth.get("affiliation"))
                    if aff.get("name")
                ]
                if not affiliation_matches_target(aff_names, target_affil):
                    continue

                yield (
                    _format_crossref_author(auth),
                    _clean_orcid(auth.get("ORCID")),
                    aff_names[-1] if aff_names else None,
                )

    def _register_authors_and_orcids(self, item, cr):
        orcids = set()
        records = list(self._iter_target_authors(item, cr))
        with self.locks["stats"]:
            for name, orcid, dept in records:
                if orcid:
                    orcids.add(orcid)
                if not name:
                    continue
                if "," not in name and " " in name:
                    parts = name.split()
                    name = f"{parts[-1]}, {' '.join(parts[:-1])}" if len(parts) > 1 else name
                entry = self.STATS["author_db"][name]
                if orcid:
                    entry["orcids"].add(orcid)
                    self.STATS["enriched_orcid"] += 1
                if dept:
                    entry["depts"].add(dept)
        return sorted(orcids)

    def _resolve_license(self, item, cr, upw, doaj_data):
        final_license = (
            deep_get(upw, ["best_oa_location", "license"])
            or deep_get(item, ["raw", "primary_location", "license", "url"])
            or deep_get(item, ["raw", "primary_location", "license"])
        )
        if not final_license and item.get("oa_status") in ("Hybrid", "Gold", "Diamond"):
            crossref_licenses = cr.get("license")
            if isinstance(crossref_licenses, list) and crossref_licenses:
                first = crossref_licenses[0]
                final_license = first.get("URL") if isinstance(first, dict) else first
            elif isinstance(crossref_licenses, str):
                final_license = crossref_licenses
            if final_license:
                logging.info(f"Using CrossRef license for {item.get('doi')}: {final_license}")

        if not final_license and doaj_data.get("license"):
            final_license = doaj_data["license"]

        if isinstance(final_license, str) and final_license and not final_license.startswith("http"):
            final_license = normalize_license_uri(final_license)
        return final_license if isinstance(final_license, str) else None

    def _fetch_primary_enrichment(self, item):
        """Fetch CrossRef (reusing raw payload when already from CrossRef), Unpaywall, and DOAJ."""
        doi = item.get("doi")
        issn = item.get("issn")

        if item.get("source") == "crossref" and isinstance(item.get("raw"), dict):
            cr = parse_crossref_message(item["raw"])
        else:
            cr = self.api_client.fetch_crossref_data(doi) if doi else {}

        upw = self.api_client.fetch_unpaywall_data(doi) if doi else {}
        doaj_data = self.api_client.fetch_doaj_data(issn) if issn else {"is_doaj": False}
        return cr, upw, doaj_data

    def _fetch_secondary_enrichment(self, item):
        """Fetch DataCite only for kept items that need an abstract or dataset metadata."""
        doi = item.get("doi")
        needs_datacite = bool(doi) and (not item.get("openalex_abstract") or item.get("doctype") == "Dataset")
        return self.api_client.fetch_datacite_data(doi) if needs_datacite else {}

    def _classify_route(self, item, upw):
        """Determine output folder and whether Green OA has a direct PDF URL."""
        upw_status = (upw.get("oa_status") or "").lower()
        if upw_status == "green":
            with self.locks["stats"]:
                self.STATS["processed_green"] += 1
            unpaywall_pdf = (
                deep_get(upw, ["best_oa_location", "url_for_pdf"])
                or deep_get(upw, ["best_oa_location", "pdf_url"])
            )
            is_green_with_pdf = bool(
                unpaywall_pdf
                and not unpaywall_pdf.lower().startswith(("https://doi.org/", "http://doi.org/"))
            )
            return "Green", is_green_with_pdf

        status = item.get("oa_status")
        if status in ("Gold", "Hybrid", "Diamond"):
            with self.locks["stats"]:
                if status == "Hybrid":
                    self.STATS["processed_hybrid"] += 1
                elif status == "Diamond":
                    self.STATS["processed_diamond"] += 1
                else:
                    self.STATS["processed_gold"] += 1
            return "Items_With_PDF", False

        return None, False

    def _acquire_pdf(self, idx, item, enrich, upw, item_dir, target_folder, is_green_with_pdf):
        """Resolve landing URL and attempt waterfall PDF download.

        Returns (pdf_success, source, landing_url, temp_pdf, dest_pdf, should_defer).
        """
        if not (target_folder == "Items_With_PDF" or is_green_with_pdf):
            return False, "None", None, None, None, False

        dest_pdf = os.path.join(item_dir, "article.pdf")
        temp_pdf = os.path.join(self.config.OUTPUT_DIR, f"temp_{idx}.pdf")

        if os.path.exists(dest_pdf):
            with self.locks["stats"]:
                self.STATS["pdf_skipped_existing"] += 1
            return True, "Existing (Cached)", None, temp_pdf, dest_pdf, False

        landing_url = deep_get(item, ["raw", "primary_location", "landing_page_url"])
        if not landing_url and item.get("doi"):
            landing_url = resolve_url(f"https://doi.org/{item['doi']}", self.api_client.SESSION)
        if landing_url and ("doi.org" in landing_url or "handle.net" in landing_url):
            landing_url = resolve_url(landing_url, self.api_client.SESSION)

        pdf_success, source = self.pdf_downloader.fetch_pdf_with_waterfall(
            item, enrich, upw, landing_url, temp_pdf
        )
        should_defer = (not pdf_success) and bool(landing_url)
        return pdf_success, source, landing_url, temp_pdf, dest_pdf, should_defer

    def process_item(self, idx, item):
        try:
            doctype = item.get("doctype", "Article")
            title_preview = (item.get("title") or "Untitled")[:60]

            if check_dspace_duplicate(
                item.get("doi"),
                self.config.CHECK_DSPACE,
                self.config.DSPACE_API,
                session=self.api_client.SESSION,
            ):
                with self.locks["stats"]:
                    self.STATS["dspace_duplicates"] += 1
                logging.info(f"[DUPLICATE] Found in DSpace: {item.get('doi')} - {title_preview}...")
                return None

            if not item.get("doi") and item.get("title"):
                rec_doi = self.api_client.lookup_doi_by_title(item["title"])
                if rec_doi:
                    item["doi"] = rec_doi
                    logging.info(f"Recovered DOI for '{title_preview[:30]}...': {rec_doi}")

            # Stage 1: Primary enrichment (CrossRef, Unpaywall, DOAJ)
            cr, upw, doaj_data = self._fetch_primary_enrichment(item)

            with self.locks["stats"]:
                if cr:
                    self.STATS["enriched_crossref"] += 1
                if cr.get("funders"):
                    self.STATS["enriched_fundref"] += 1
                if doaj_data.get("is_doaj"):
                    self.STATS["enriched_doaj"] += 1

            upw_status = upw.get("oa_status")
            if upw_status:
                item["oa_status"] = normalize_oa_status(upw_status, item.get("doi"))

            if doaj_data.get("is_diamond"):
                item["oa_status"] = "Diamond"
            elif item.get("oa_status") in ("Unknown", "Closed") and doaj_data.get("is_doaj"):
                item["oa_status"] = "Gold"

            if item.get("oa_status") in ("Closed", "Bronze", "Unknown"):
                with self.locks["stats"]:
                    self.STATS["processed_bronze_closed"] += 1
                return None

            all_affs = extract_all_affiliations(item, cr)
            if self.config.STRICT_AFFILIATION_MATCH and not affiliation_matches_target(
                all_affs, self.config.TARGET_AFFIL
            ):
                with self.locks["stats"]:
                    self.STATS["skipped_no_target_affiliation"] += 1
                return None

            target_folder, is_green_with_pdf = self._classify_route(item, upw)
            if not target_folder:
                return None

            # Stage 2: Secondary enrichment only for kept items
            dc_data = self._fetch_secondary_enrichment(item)
            if dc_data.get("abstract"):
                with self.locks["stats"]:
                    self.STATS["enriched_datacite"] += 1

            enrich = {
                "publisher": cr.get("publisher"),
                "license": self._resolve_license(item, cr, upw, doaj_data),
                "abstract": dc_data.get("abstract") or item.get("openalex_abstract"),
                "funders": cr.get("funders", []),
                "authors": cr.get("authors", []),
                "crossref_pdf": cr.get("crossref_pdf"),
            }

            paper_orcids = self._register_authors_and_orcids(item, cr)
            dept = determine_primary_department(
                all_affs, self.config.UNIT_MAP, self.config.TARGET_AFFIL, sorted_keys=self._sorted_unit_keys
            )
            pub_name = enrich.get("publisher") or item.get("publisher") or "Unknown"

            with self.locks["stats"]:
                self.STATS["dept_breakdown"][dept][item.get("oa_status")] += 1
                self.STATS["dept_publisher_breakdown"][dept][pub_name] += 1

            md = map_to_dublin_core(item, enrich, all_affs, paper_orcids)
            item_dir = os.path.join(self.config.OUTPUT_DIR, target_folder, dept, f"item_{str(idx).zfill(3)}")

            pdf_success, source, landing_url, temp_pdf, dest_pdf, should_defer = self._acquire_pdf(
                idx, item, enrich, upw, item_dir, target_folder, is_green_with_pdf
            )
            task_info = {
                "idx": idx,
                "item": item,
                "landing_url": landing_url,
                "temp_pdf": temp_pdf,
                "dest_pdf": dest_pdf,
                "item_dir": item_dir,
                "target_folder": target_folder,
                "is_green_with_pdf": is_green_with_pdf,
                "dept": dept,
                "md": md,
                "enrich": enrich,
                "doctype": doctype,
            }
            if should_defer:
                with self.locks["stats"]:
                    self.playwright_queue.append(task_info)
                return None

            self.finalize_item(task_info, pdf_success, source)
        except Exception as e:
            logging.error(f"Error processing item {idx}: {e}")

    def finalize_item(self, task, pdf_success, source):
        idx = task["idx"]
        item = task["item"]
        target_folder = task["target_folder"]
        dept = task["dept"]
        temp_pdf = task["temp_pdf"]
        dest_pdf = task["dest_pdf"]
        item_dir = task["item_dir"]
        md = task["md"]
        enrich = task["enrich"]
        doctype = task["doctype"]
        is_green_with_pdf = task.get("is_green_with_pdf", False)

        os.makedirs(item_dir, exist_ok=True)

        if target_folder == "Items_With_PDF":
            if pdf_success:
                robust_move_file(temp_pdf, dest_pdf)
                with self.locks["stats"]:
                    self.STATS["pdf_success"] += 1
                    self.STATS["pdf_sources"][source] += 1
            else:
                robust_remove_file(temp_pdf)
                try:
                    shutil.rmtree(item_dir)
                except OSError:
                    pass
                target_folder = "Items_Only_Link"
                item_dir = os.path.join(self.config.OUTPUT_DIR, target_folder, dept, f"item_{str(idx).zfill(3)}")
                os.makedirs(item_dir, exist_ok=True)
                with self.locks["stats"]:
                    self.STATS["pdf_fail"] += 1
                    self.STATS["gold_metadata_only"] += 1
        elif target_folder == "Green" and is_green_with_pdf:
            if pdf_success:
                robust_move_file(temp_pdf, dest_pdf)
                with self.locks["stats"]:
                    self.STATS["pdf_success"] += 1
                    self.STATS["pdf_sources"][source] += 1
            else:
                robust_remove_file(temp_pdf)
                with self.locks["stats"]:
                    self.STATS["pdf_fail"] += 1

        if not pdf_success:
            doi = item.get("doi")
            link_text = f"DOI: https://doi.org/{doi}\n" if doi else f"Title: {item.get('title') or 'Untitled'}\n"
            with open(os.path.join(item_dir, "link.txt"), "w", encoding="utf-8") as f:
                f.write(link_text)

        write_saf(md, item_dir, "article.pdf" if pdf_success else None)

        with self.locks["csv"]:
            self.metadata_exporter.write_csv_row({
                "DOI": item.get("doi"),
                "Title": item.get("title"),
                "ISSN": item.get("issn"),
                "Doc_Type": doctype,
                "Source": item.get("source"),
                "Folder_Type": target_folder,
                "Dept": dept,
                "PDF_Status": "OK" if pdf_success else "Missing",
                "PDF_Source": source,
                "OA_Status": item.get("oa_status"),
            })

        if not pdf_success:
            with self.locks["ris"]:
                self.metadata_exporter.write_ris_entry(item, enrich)

        logging.info(f"[{idx}] {(item.get('title') or 'Untitled')[:40]}... -> {target_folder}")

    def process_items(self, final_list):
        self.playwright_queue = []
        with self.metadata_exporter:
            logging.info(f"Processing {len(final_list)} unique items with 15 threads...")
            with ThreadPoolExecutor(max_workers=15) as executor:
                futures = [executor.submit(self.process_item, idx, item) for idx, item in enumerate(final_list)]
                for future in as_completed(futures):
                    exc = future.exception()
                    if exc:
                        logging.error(f"Unhandled worker exception: {exc}")

            if self.playwright_queue:
                logging.info(f"Processing {len(self.playwright_queue)} items via Playwright fallback...")
                self.pdf_downloader.process_playwright_queue(self.playwright_queue, self.finalize_item)

        self.cleanup_empty_collection_dirs()
        generate_import_scripts(
            self.config.OUTPUT_DIR,
            self.config.DSPACE_BIN,
            self.config.DSPACE_EMAIL,
            collection_map=self.config.COLLECTION_MAP,
            default_collection_handle=self.config.DEFAULT_COLLECTION_HANDLE,
        )
        self.generate_summary()
        self.metadata_exporter.generate_publisher_report(self.STATS["dept_publisher_breakdown"])

    def _serialize_stats(self):
        return {
            k: (
                dict(v)
                if isinstance(v, Counter)
                else {
                    dk: (
                        {ak: sorted(av) for ak, av in dv.items()}
                        if isinstance(dv, dict)
                        else dict(dv)
                    )
                    for dk, dv in v.items()
                }
                if isinstance(v, defaultdict)
                else v
            )
            for k, v in self.STATS.items()
            if k != "author_db"
        }

    def generate_summary(self):
        logging.info(
            f"HARVEST COMPLETE | Unique Depts: {len(self.STATS['dept_breakdown'])} | "
            f"PDFs: {self.STATS['pdf_success']} | Sources: {dict(self.STATS['pdf_sources'])}"
        )
        summary_payload = self._serialize_stats()
        summary_payload["unique_authors"] = len(self.STATS["author_db"])
        try:
            with open(os.path.join(self.config.OUTPUT_DIR, "harvest_summary.txt"), "w", encoding="utf-8") as f:
                json.dump(summary_payload, f, indent=2, sort_keys=True)
                f.write("\n")
        except OSError as exc:
            logging.warning(f"Failed to write harvest summary: {exc}")

        self.metadata_exporter.generate_author_registry(self.STATS["author_db"])

    def cleanup_empty_item_dirs(self):
        """Remove stale empty item_* directories from prior interrupted runs."""
        if not os.path.isdir(self.config.OUTPUT_DIR):
            return

        removed = 0
        for root, dirs, files in os.walk(self.config.OUTPUT_DIR, topdown=False):
            if not os.path.basename(root).startswith("item_"):
                continue
            if dirs or files:
                continue
            try:
                os.rmdir(root)
                removed += 1
            except OSError:
                pass

        if removed:
            logging.info(f"Removed {removed} empty stale item directories before run start.")

    def cleanup_empty_collection_dirs(self):
        """Prune empty directories beneath output collection roots after harvesting."""
        collection_roots = ("Green", "Items_With_PDF", "Items_Only_Link")
        removed = 0

        for collection in collection_roots:
            root = os.path.join(self.config.OUTPUT_DIR, collection)
            if not os.path.isdir(root):
                continue

            for current_root, dirs, files in os.walk(root, topdown=False):
                if current_root == root:
                    continue
                if dirs or files:
                    continue
                try:
                    os.rmdir(current_root)
                    removed += 1
                except OSError:
                    pass

        if removed:
            logging.info(f"Pruned {removed} empty output directories after harvest.")

    def run(self, dry_run=False, resume=False):
        try:
            oa_list, cr_list = self.discover()
            final_list = self.deduplicate_and_merge(oa_list, cr_list)

            if dry_run:
                logging.info(
                    f"Dry-run mode: discovery complete with {len(final_list)} unique candidate items. "
                    "No output files were written."
                )
                return

            if resume:
                logging.info("Resume mode enabled: preserving existing output directory content.")
                os.makedirs(self.config.OUTPUT_DIR, exist_ok=True)
            else:
                robust_cleanup_output_dir(self.config.OUTPUT_DIR, preserve_path=self.config.CACHE_DIR)
                os.makedirs(self.config.OUTPUT_DIR, exist_ok=True)
                self.cleanup_empty_item_dirs()
                with open(self.ris_file, "w", encoding="utf-8"):
                    pass

            self.process_items(final_list)
        finally:
            self.api_client.close()
