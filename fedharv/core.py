# SPDX-License-Identifier: AGPL-3.0-only
import os
import sys
import time
import shutil
import logging
import threading
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
import concurrent.futures

from .config import ConfigManager
from .api import APIClient, check_dspace_duplicate
from .pdf import PDFDownloader, resolve_url
from .export import (
    MetadataExporter, write_saf, generate_import_scripts,
    generate_ris_block, map_to_dublin_core
)
from .utils import (
    normalize_oa_status, normalize_doctype, normalize_string,
    sanitize_filename, determine_primary_department, extract_all_affiliations,
    deep_get, ensure_list_of_dicts, normalize_license_uri,
    affiliation_matches_target
)

# Robust filesystem helper functions
def robust_cleanup(path):
    if not os.path.exists(path): return
    for i in range(5):
        try:
            shutil.rmtree(path)
            return
        except Exception as e:
            print(f"Cleanup Warning: Could not delete {path} (Attempt {i+1}/5). Is a PDF open? {e}")
            time.sleep(2)
    print(f"CRITICAL ERROR: Failed to clean output directory {path}. Please close open files.")
    sys.exit(1)

def robust_remove_file(path):
    if not os.path.exists(path): return
    for i in range(5):
        try:
            os.remove(path)
            return
        except OSError:
            time.sleep(1) # Wait for handle release

def robust_move_file(src, dst):
    if not os.path.exists(src): return
    for i in range(5):
        try:
            shutil.move(src, dst)
            return
        except OSError:
            time.sleep(1) # Wait for handle release
    try: 
        shutil.move(src, dst)
    except Exception: 
        pass

class HarvesterEngine:
    """The central orchestration engine for the Federated OA Harvester."""
    def __init__(self, config_path=None):
        self.config = ConfigManager(config_path)
        self.setup_logging()
        self.api_client = APIClient(self.config)
        self.patterns_file = "learned_patterns.json"
        self.pdf_downloader = PDFDownloader(self.api_client, patterns_file=self.patterns_file)
        
        self.init_stats()
        
        self.metadata_exporter = MetadataExporter(
            output_dir=self.config.OUTPUT_DIR,
            csv_file=self.csv_file,
            ris_file=self.ris_file,
            author_file=self.author_file,
            publisher_report_file=self.publisher_report_file
        )
        
        self.locks = {
            'stats': threading.Lock(),
            'csv': threading.Lock(),
            'author': threading.Lock(),
            'print': threading.Lock(),
            'ris': threading.Lock() 
        }

    def setup_logging(self):
        logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

    def init_stats(self):
        self.STATS = {
            'openalex_total_est': 0, 'openalex_raw_total': 0, 'openalex_oa_kept': 0,
            'crossref_total_est': 0, 'crossref_raw_total': 0, 'crossref_oa_kept': 0,
            'dspace_duplicates': 0, 
            'processed_gold': 0, 'processed_hybrid': 0, 'processed_diamond': 0,
            'processed_green': 0, 'processed_bronze_closed': 0, 'gold_metadata_only': 0,
            'skipped_no_license': 0, 'skipped_closed': 0, 'skipped_bronze': 0, 'skipped_green': 0,
            'skipped_no_target_affiliation': 0,
            'pdf_sources': Counter(), 'pdf_success': 0, 'pdf_fail': 0, 'pdf_skipped_existing': 0,
            'enriched_crossref': 0, 'enriched_fundref': 0, 
            'enriched_datacite': 0, 'enriched_orcid': 0, 'enriched_doaj': 0,
            'dept_breakdown': defaultdict(Counter),
            'dept_publisher_breakdown': defaultdict(Counter),
            'windsor_author_db': defaultdict(lambda: {'depts': set(), 'emails': set(), 'orcids': set()}),
        }
        
        safe_start = self.config.START_DATE.replace('-', '')
        safe_end = self.config.END_DATE.replace('-', '')
        
        csv_filename = f"harvest_report_{safe_start}_{safe_end}.csv"
        self.csv_file = os.path.join(self.config.OUTPUT_DIR, csv_filename)
        
        self.ris_file = os.path.join(self.config.OUTPUT_DIR, "citations.ris") 
        self.author_file = os.path.join(self.config.OUTPUT_DIR, "windsor_authors.txt")
        self.publisher_report_file = os.path.join(self.config.OUTPUT_DIR, "department_publisher_report.csv")

    def increment_stat(self, stat_name, amount=1):
        with self.locks['stats']:
            if isinstance(self.STATS[stat_name], Counter):
                # For counters (this shouldn't normally be called directly with increment_stat, but just in case)
                pass
            else:
                self.STATS[stat_name] += amount

    def discover(self):
        print(f"Starting concurrent API discovery (OpenAlex + Crossref) from {self.config.START_DATE} to {self.config.END_DATE}...")
        
        def run_oa():
            return self.api_client.harvest_openalex(
                self.config.START_DATE, 
                self.config.END_DATE, 
                self.config.TARGET_AFFIL,
                stats_callback=self.increment_stat
            )
            
        def run_cr():
            return self.api_client.harvest_crossref(
                self.config.START_DATE, 
                self.config.END_DATE, 
                self.config.TARGET_AFFIL,
                stats_callback=self.increment_stat
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            f_oa = executor.submit(run_oa)
            f_cr = executor.submit(run_cr)
            oa_list = f_oa.result()
            cr_list = f_cr.result()
        return oa_list, cr_list

    def deduplicate_and_merge(self, openalex_list, crossref_list):
        logging.info(f"--- Merging: {len(openalex_list)} OpenAlex items + {len(crossref_list)} Crossref items ---")
        
        unique_map = {}
        for i in openalex_list:
            if i['doi']: unique_map[i['doi']] = i
            else: unique_map[f"title:{normalize_string(i['title'])}"] = i 
            
        crossref_new = 0
        for i in crossref_list:
            if i['doi']:
                if i['doi'] in unique_map:
                    pass 
                else:
                    unique_map[i['doi']] = i
                    crossref_new += 1
            else:
                pass
                
        logging.info(f"--- Deduplication Complete: {crossref_new} new items added from Crossref ---")
        return list(unique_map.values())

    def fetch_enrichment_batch(self, item):
        """Fetch all enrichment data in parallel using ThreadPoolExecutor."""
        doi = item.get('doi')
        issn = item.get('issn')
        
        def fetch_cr():
            return self.api_client.fetch_crossref_data(doi) if doi else {}
        
        def fetch_upw():
            return self.api_client.fetch_unpaywall_data(doi) if doi else {}
        
        def fetch_shp():
            return self.api_client.fetch_sherpa_policy(issn)
        
        def fetch_dc():
            return self.api_client.fetch_datacite_data(doi) if doi else {}
        
        def fetch_doaj():
            return self.api_client.fetch_doaj_data(issn)
        
        results = {}
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = {
                'cr': executor.submit(fetch_cr),
                'upw': executor.submit(fetch_upw),
                'sherpa': executor.submit(fetch_shp),
                'dc': executor.submit(fetch_dc),
                'doaj': executor.submit(fetch_doaj)
            }
            for key, future in futures.items():
                try:
                    results[key] = future.result(timeout=30)
                except Exception as e:
                    logging.error(f"Error fetching {key}: {e}")
                    results[key] = {}
        
        return results['cr'], results['upw'], results['sherpa'], results['dc'], results['doaj']

    def extract_authors(self, item, cr):
        target_check = "windsor"
        def reg(name, orcid=None, dept=None, email=None):
            if not name: return
            if "," not in name and " " in name: 
                parts = name.split()
                name = f"{parts[-1]}, {' '.join(parts[:-1])}" if len(parts) > 1 else name
            with self.locks['author']:
                entry = self.STATS['windsor_author_db'][name]
                if orcid: entry['orcids'].add(orcid)
                if dept: entry['depts'].add(dept)
                if email: entry['emails'].add(email)
                if orcid: self.STATS['enriched_orcid'] += 1
        
        if item['source'] == 'openalex':
            for ship in ensure_list_of_dicts(deep_get(item, ['raw', 'authorships'])):
                is_windsor = False
                dept_found = None
                for inst in ensure_list_of_dicts(ship.get('institutions')):
                    if target_check in inst.get('display_name', '').lower(): is_windsor = True
                if is_windsor:
                    raw_aff = ship.get('raw_affiliation_string', '')
                    if raw_aff:
                        match = re.search(r'(Department of [^,]+|School of [^,]+|Faculty of [^,]+)', raw_aff, re.IGNORECASE) if 're' in sys.modules else None
                        # Wait, we need to import re!
                        import re
                        match = re.search(r'(Department of [^,]+|School of [^,]+|Faculty of [^,]+)', raw_aff, re.IGNORECASE)
                        if match: dept_found = match.group(1).strip()
                    reg(deep_get(ship, ['author', 'display_name']), deep_get(ship, ['author', 'orcid']).replace('https://orcid.org/', '') if deep_get(ship, ['author', 'orcid']) else None, dept_found, None)

        if cr and cr.get('raw_message'):
            for auth in ensure_list_of_dicts(deep_get(cr, ['raw_message', 'author'])):
                is_windsor = False
                dept_found = None
                for aff in ensure_list_of_dicts(auth.get('affiliation')):
                    if target_check in aff.get('name', '').lower(): 
                        is_windsor = True
                        dept_found = aff.get('name')

                if is_windsor:
                    reg(f"{auth.get('family')}, {auth.get('given')}", auth.get('ORCID').replace('http://orcid.org/','').replace('https://orcid.org/','') if auth.get('ORCID') else None, dept_found, None)

    def get_paper_orcids(self, item, cr):
        orcids = set()
        if item['source'] == 'openalex':
             for ship in ensure_list_of_dicts(deep_get(item, ['raw', 'authorships'])):
                  for inst in ensure_list_of_dicts(ship.get('institutions')):
                        if "windsor" in inst.get('display_name', '').lower():
                             oid = deep_get(ship, ['author', 'orcid'])
                             if oid: orcids.add(oid.replace('https://orcid.org/', ''))
        
        if cr and cr.get('raw_message'):
             for auth in ensure_list_of_dicts(deep_get(cr, ['raw_message', 'author'])):
                  for aff in ensure_list_of_dicts(auth.get('affiliation')):
                        if "windsor" in aff.get('name', '').lower():
                             if auth.get('ORCID'): 
                                 orcids.add(auth.get('ORCID').replace('http://orcid.org/','').replace('https://orcid.org/',''))
        return list(orcids)

    def process_item(self, idx, item):
        try:
            doctype = item.get('doctype', 'Article')
            
            if check_dspace_duplicate(item['doi'], self.config.CHECK_DSPACE, self.config.DSPACE_API):
                with self.locks['stats']: self.STATS['dspace_duplicates'] += 1
                return None

            # --- DOI RECOVERY ---
            if not item.get('doi') and item.get('title'):
                rec_doi = self.api_client.lookup_doi_by_title(item['title'])
                if rec_doi: 
                    item['doi'] = rec_doi
                    logging.info(f"Recovered DOI for '{item['title'][:30]}...': {rec_doi}")

            # --- ENRICHMENT ---
            cr, upw, sherpa, dc_data, doaj_data = self.fetch_enrichment_batch(item)

            with self.locks['stats']:
                if cr: self.STATS['enriched_crossref'] += 1
                if cr.get('funders'): self.STATS['enriched_fundref'] += 1
                if doaj_data.get('is_doaj'): self.STATS['enriched_doaj'] += 1
                if dc_data.get('abstract'): self.STATS['enriched_datacite'] += 1

            # --- STATUS & FILTERING ---
            upw_status = upw.get('oa_status')
            if upw_status:
                item['oa_status'] = normalize_oa_status(upw_status, item['doi'])
            
            if doaj_data.get('is_diamond'): 
                item['oa_status'] = 'Diamond'
            elif item.get('oa_status') in ['Unknown', 'Closed'] and doaj_data.get('is_doaj'): 
                item['oa_status'] = 'Gold'
            
            # --- STRICT FILTERING START ---
            current_status = item.get('oa_status')
            if current_status in ['Closed', 'Bronze', 'Unknown']:
                with self.locks['stats']: self.STATS['processed_bronze_closed'] += 1
                return # Skip item
            # --- STRICT FILTERING END ---

            final_abstract = dc_data.get('abstract') or item.get('openalex_abstract')
            enrich = {'publisher': cr.get('publisher'), 'license': None, 'sherpa_uri': sherpa, 'abstract': final_abstract, 'funders': cr.get('funders', []), 'authors': cr.get('authors', []), 'crossref_pdf': cr.get('crossref_pdf')}
            
            # Enhanced license lookup with CrossRef fallback
            final_license = deep_get(upw, ['best_oa_location', 'license']) or deep_get(item, ['raw', 'primary_location', 'license', 'url'])
            
            if not final_license and item.get('oa_status') in ['Hybrid', 'Gold']:
                crossref_licenses = cr.get('license', [])
                if crossref_licenses:
                    if isinstance(crossref_licenses, list) and crossref_licenses:
                        crossref_license = crossref_licenses[0].get('URL') if isinstance(crossref_licenses[0], dict) else crossref_licenses[0]
                    else:
                        crossref_license = crossref_licenses
                    if crossref_license:
                        final_license = crossref_license
                        logging.info(f"Using CrossRef license for {item.get('doi')}: {final_license}")
            
            if final_license and not final_license.startswith('http'):
                final_license = normalize_license_uri(final_license)
            
            enrich['license'] = final_license

            # --- FOLDER SORTING ---
            target_folder = "Items_With_PDF"
            if upw_status == 'green':
                with self.locks['stats']: self.STATS['processed_green'] += 1
                target_folder = "Green"
            elif item.get('oa_status') in ['Gold', 'Hybrid', 'Diamond']:
                with self.locks['stats']:
                    if item.get('oa_status') == 'Hybrid': self.STATS['processed_hybrid'] += 1
                    elif item.get('oa_status') == 'Diamond': self.STATS['processed_diamond'] += 1
                    else: self.STATS['processed_gold'] += 1
                target_folder = "Items_With_PDF"
            else:
                return

            # --- AUTHORS & DEPTS ---
            self.extract_authors(item, cr)
            all_affs = extract_all_affiliations(item, cr)

            if self.config.STRICT_AFFILIATION_MATCH and not affiliation_matches_target(all_affs, self.config.TARGET_AFFIL):
                with self.locks['stats']:
                    self.STATS['skipped_no_target_affiliation'] += 1
                return

            dept = determine_primary_department(all_affs, self.config.UNIT_MAP, self.config.TARGET_AFFIL) 
            
            pub_name = enrich.get('publisher') or item.get('publisher') or "Unknown"

            with self.locks['stats']: 
                self.STATS['dept_breakdown'][dept][item.get('oa_status')] += 1
                self.STATS['dept_publisher_breakdown'][dept][pub_name] += 1

            # --- METADATA & DOWNLOAD ---
            md = map_to_dublin_core(item, enrich, all_affs, self.get_paper_orcids(item, cr))
            
            item_dir = os.path.join(self.config.OUTPUT_DIR, target_folder, dept, f"item_{str(idx).zfill(3)}")

            pdf_success = False
            source = "None"
            temp_pdf = None
            dest_pdf = None
            
            if target_folder == "Items_With_PDF":
                dest_pdf = os.path.join(item_dir, "article.pdf")
                temp_pdf = os.path.join(self.config.OUTPUT_DIR, f"temp_{idx}.pdf")
                
                if os.path.exists(dest_pdf):
                    pdf_success = True
                    source = "Existing (Cached)"
                    with self.locks['stats']: self.STATS['pdf_skipped_existing'] += 1
                else:
                    landing_url = deep_get(item, ['raw', 'primary_location', 'landing_page_url'])
                    if not landing_url and item['doi']: 
                        landing_url = resolve_url(f"https://doi.org/{item['doi']}", self.api_client.SESSION)
                    if landing_url and ("doi.org" in landing_url or "handle.net" in landing_url): 
                        landing_url = resolve_url(landing_url, self.api_client.SESSION)
                    
                    pdf_success, source = self.pdf_downloader.fetch_pdf_with_waterfall(item, enrich, upw, landing_url, temp_pdf)
                    if not pdf_success and landing_url:
                        task_info = {
                            'idx': idx, 'item': item, 'landing_url': landing_url, 
                            'temp_pdf': temp_pdf, 'dest_pdf': dest_pdf, 
                            'item_dir': item_dir, 'target_folder': target_folder, 
                            'dept': dept, 'md': md, 'enrich': enrich, 'doctype': doctype
                        }
                        with self.locks['stats']:
                            self.playwright_queue.append(task_info)
                        return # Defer finalizing this item

            task_info = {
                'idx': idx, 'item': item, 'landing_url': landing_url if 'landing_url' in locals() else None, 
                'temp_pdf': temp_pdf if 'temp_pdf' in locals() else None, 
                'dest_pdf': dest_pdf if 'dest_pdf' in locals() else None, 
                'item_dir': item_dir, 'target_folder': target_folder, 
                'dept': dept, 'md': md, 'enrich': enrich, 'doctype': doctype
            }
            self.finalize_item(task_info, pdf_success, source)
        except Exception as e: 
            logging.error(f"Error processing item {idx}: {e}")

    def finalize_item(self, task, pdf_success, source):
        idx = task['idx']
        item = task['item']
        target_folder = task['target_folder']
        dept = task['dept']
        temp_pdf = task['temp_pdf']
        dest_pdf = task['dest_pdf']
        item_dir = task['item_dir']
        md = task['md']
        enrich = task['enrich']
        doctype = task['doctype']

        # Create the item directory only when we are actually finalizing output.
        os.makedirs(item_dir, exist_ok=True)
        
        if target_folder == "Items_With_PDF":
            if pdf_success:
                robust_move_file(temp_pdf, dest_pdf)
                with self.locks['stats']: 
                    self.STATS['pdf_success'] += 1
                    self.STATS['pdf_sources'][source] += 1
            else:
                robust_remove_file(temp_pdf)
                try:
                    shutil.rmtree(item_dir)
                except Exception:
                    pass
                target_folder = "Items_Only_Link"
                item_dir = os.path.join(self.config.OUTPUT_DIR, target_folder, dept, f"item_{str(idx).zfill(3)}")
                os.makedirs(item_dir, exist_ok=True)
                with self.locks['stats']: 
                    self.STATS['pdf_fail'] += 1
                    self.STATS['gold_metadata_only'] += 1

        if target_folder != "Items_With_PDF":
            with open(os.path.join(item_dir, "link.txt"), 'w', encoding='utf-8') as f: 
                f.write(f"DOI: https://doi.org/{item['doi']}")

        write_saf(md, item_dir, "article.pdf" if pdf_success else None)
        
        with self.locks['csv']:
            self.metadata_exporter.write_csv_row({
                'DOI': item['doi'], 'Title': item['title'], 'ISSN': item.get('issn'), 
                'Doc_Type': doctype, 'Source': item['source'], 'Folder_Type': target_folder, 
                'Dept': dept, 'PDF_Status': 'OK' if pdf_success else 'Missing', 
                'PDF_Source': source, 'OA_Status': item.get('oa_status')
            })
        
        if not pdf_success:
            with self.locks['ris']:
                self.metadata_exporter.write_ris_entry(item, enrich)

        with self.locks['print']: 
            print(f"[{idx}] {item['title'][:40]}... -> {target_folder}")

    def process_items(self, final_list):
        self.metadata_exporter.open_handles()
        self.playwright_queue = []

        print(f"Processing {len(final_list)} unique items with 15 threads...")
        with ThreadPoolExecutor(max_workers=15) as executor:
            futures = [executor.submit(self.process_item, idx, item) for idx, item in enumerate(final_list)]
            for f in as_completed(futures):
                pass

        if self.playwright_queue:
            print(f"Processing {len(self.playwright_queue)} items via Playwright fallback...")
            self.pdf_downloader.process_playwright_queue(self.playwright_queue, self.finalize_item)

        self.metadata_exporter.close_handles()

        self.cleanup_empty_collection_dirs()
        self.generate_summary()
        self.metadata_exporter.generate_publisher_report(self.STATS['dept_publisher_breakdown'])

    def generate_summary(self):
        print(f"\n--- HARVEST COMPLETE ---\nUnique Items: {len(self.STATS['dept_breakdown'])}\nPDFs: {self.STATS['pdf_success']}\nSources: {dict(self.STATS['pdf_sources'])}")
        try:
            with open(os.path.join(self.config.OUTPUT_DIR, "harvest_summary.txt"), 'w', encoding='utf-8') as f: 
                f.write(str(self.STATS))
        except Exception: 
            pass
        
        self.metadata_exporter.generate_author_registry(self.STATS['windsor_author_db'])

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
        collection_roots = ["Green", "Items_With_PDF", "Items_Only_Link"]
        removed = 0

        for collection in collection_roots:
            root = os.path.join(self.config.OUTPUT_DIR, collection)
            if not os.path.isdir(root):
                continue

            # Remove empties bottom-up but keep the collection root folder itself.
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

    def run(self):
        robust_cleanup(self.config.OUTPUT_DIR)
        os.makedirs(self.config.OUTPUT_DIR, exist_ok=True)
        self.cleanup_empty_item_dirs()
        
        # Initialize empty RIS file
        with open(self.ris_file, 'w', encoding='utf-8') as f: 
            pass

        generate_import_scripts(self.config.OUTPUT_DIR, self.config.DSPACE_BIN, self.config.DSPACE_EMAIL)

        oa_list, cr_list = self.discover()
        final_list = self.deduplicate_and_merge(oa_list, cr_list)
        self.process_items(final_list)
