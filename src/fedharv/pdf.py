# SPDX-License-Identifier: AGPL-3.0-only
import os
import re
import time
import logging
from urllib.parse import urljoin

from .config import DOI_PDF_PATTERNS, DOMAIN_URL_TRANSFORMS
from .utils import safe_call

def resolve_url(url, session):
    if not url or ("doi.org" not in url and "handle.net" not in url): return url
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    try:
        r = session.head(url, allow_redirects=True, timeout=15, headers=headers)
        if r.status_code in [405, 403]:
            r = session.get(url, stream=True, allow_redirects=True, timeout=15, headers=headers)
            r.close()
        return r.url
    except Exception as e:
        logging.debug(f"resolve_url failed for {url}: {e}")
        return url

def download_file_stream(url, path, session, extra_headers=None):
    if not url: return False
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/pdf,text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    }
    if extra_headers:
        headers.update(extra_headers)
    try:
        with session.get(url, stream=True, timeout=60, allow_redirects=True, headers=headers) as r:
            if r.status_code != 200: 
                logging.debug(f"download_file_stream: HTTP {r.status_code} for {url}")
                return False
            chunk_iter = r.iter_content(chunk_size=4096)
            try: first_chunk = next(chunk_iter)
            except StopIteration: 
                logging.debug(f"download_file_stream: Empty body for {url}")
                return False
            if b'%PDF-' not in first_chunk: 
                logging.debug(f"download_file_stream: Content not PDF for {url}")
                return False
            with open(path, 'wb') as f:
                f.write(first_chunk)
                for chunk in chunk_iter: f.write(chunk)
        return True
    except Exception as e: 
        logging.debug(f"download_file_stream: Exception downloading {url}: {e}")
        return False

def fetch_html_meta_pdf_link(landing_url, session):
    if not landing_url: return None
    targets = ["academic.oup.com", "jstor.org", "muse.jhu.edu", "diabetesjournals.org", 
               "pubs.aip.org", "hdl.handle.net", "f1000research.com", "ncbi.nlm.nih.gov", 
               "techscience.com", "aimspress.com", "thesai.org", "psycnet.apa.org", 
               "jgaa.info", "casn.ca", "cad-journal.net", "semarakilmu.com.my", 
               "canadianfieldnaturalist.ca", "reabic.net", "journalhosting.ucalgary.ca", 
               "mdpi.com", "iwaponline.com", "sciendo.com", "panafrican-med-journal.com",
               "journals.sagepub.com", "advancesinrehab.com", "lww.com", "medknow.com",
               "pubs.acs.org", "uiowa.edu", "openlibhums.org", "cdc.gov",
               "ietresearch.onlinelibrary.wiley.com", "scielo.br", "scielo.org"]
    
    should_scrape = any(t in landing_url for t in targets)
    if not should_scrape and ("/view/abstract/" in landing_url or "/article/view/" in landing_url or "cgi/viewcontent" in landing_url):
        should_scrape = True
    if not should_scrape: return None

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    }
    try:
        r = session.get(landing_url, headers=headers, timeout=15, allow_redirects=True)
        if r.status_code == 200:
            match = re.search(r'<meta\s+name=["\']citation_pdf_url["\']\s+content=["\'](.*?)["\']', r.text, re.IGNORECASE)
            if match: return match.group(1)
        else:
            logging.debug(f"fetch_html_meta_pdf_link: HTTP {r.status_code} for {landing_url}")
    except Exception as e:
        logging.debug(f"fetch_html_meta_pdf_link: Exception fetching {landing_url}: {e}")
        pass
    return None

def apply_publisher_heuristics(landing_url, doi=None, learned_patterns=None):
    """Resolve PDF URL using learned patterns, DOI prefix rules, then domain-specific transforms."""
    # 1. Try learned patterns first (experimental/dynamic patterns)
    if learned_patterns and doi:
        prefix = doi.split('/')[0]
        if prefix in learned_patterns:
            pattern = learned_patterns[prefix]
            try:
                candidate = pattern.replace('{doi}', doi).replace('{doi_suffix}', doi.split('/')[-1])
                return candidate
            except Exception:
                pass

    # 2. Try DOI prefix-based patterns (hardcoded)
    if doi:
        for prefix, pattern in DOI_PDF_PATTERNS.items():
            if doi.startswith(prefix):
                try:
                    return pattern.replace('{doi}', doi).replace('{doi_suffix}', doi.split('/')[-1])
                except Exception:
                    pass

    # 3. Transform landing URL using domain-specific rules
    if landing_url:
        if "chrome-extension://" in landing_url:
            match = re.search(r'(https?://.*)', landing_url)
            landing_url = match.group(1) if match else landing_url
        
        if landing_url.lower().endswith(".pdf"):
            return landing_url
        
        for domain, transform_func in DOMAIN_URL_TRANSFORMS.items():
            if domain in landing_url:
                try:
                    return transform_func(landing_url)
                except Exception:
                    pass
    
    return None

class PDFDownloader:
    """Handles PDF discovery, downloading, heuristics, and Playwright fallback."""
    def __init__(self, api_client, patterns_file="learned_patterns.json"):
        self.api_client = api_client
        self.patterns_file = patterns_file
        self.patterns = {}
        self.load_learned_patterns()

    def load_learned_patterns(self):
        if os.path.exists(self.patterns_file):
            try:
                import json
                with open(self.patterns_file, 'r') as f: 
                    self.patterns = json.load(f)
                logging.info(f"Loaded {len(self.patterns)} learned PDF patterns.")
            except Exception:
                pass

    def learn_pattern_from_url(self, doi, resolved_pdf_url):
        if not doi or not resolved_pdf_url: return
        prefix = doi.split('/')[0]
        if prefix in DOI_PDF_PATTERNS or prefix in self.patterns:
            return
            
        suffix = doi.split('/')[-1]
        pattern = None
        if doi in resolved_pdf_url:
            pattern = resolved_pdf_url.replace(doi, '{doi}')
        elif suffix in resolved_pdf_url:
            pattern = resolved_pdf_url.replace(suffix, '{doi_suffix}')
            
        if pattern:
            self.patterns[prefix] = pattern
            self.save_learned_patterns()
            logging.info(f"Learned new PDF pattern for DOI prefix '{prefix}': {pattern}")

    def save_learned_patterns(self):
        try:
            import json
            with open(self.patterns_file, 'w') as f:
                json.dump(self.patterns, f, indent=4)
        except Exception as e:
            logging.warning(f"Could not save learned patterns to {self.patterns_file}: {e}")

    def is_elsevier_item(self, item, landing_url):
        pub = (item.get('publisher') or '').lower()
        if 'elsevier' in pub: return True
        if item.get('doi') and item['doi'].startswith('10.1016/'): return True
        if landing_url and 'sciencedirect.com' in landing_url: return True
        return False

    def fetch_pdf_with_waterfall(self, item, enrich, upw, landing_url, temp_pdf):
        """
        Attempt PDF download using waterfall priority:
        1. OpenAlex PDF URL
        2. Unpaywall Best OA Location
        3. CrossRef Direct Link
        4. Scopus API (Elsevier/ScienceDirect only)
        5. Publisher Heuristics (from landing page)
        6. Meta Tag Scraper (from landing page)
        7. DOI-only Heuristics (no landing page needed)
        
        Returns: (pdf_success: bool, source: str)
        """
        session = self.api_client.SESSION
        
        # 1. OpenAlex
        if item.get('pdf_url') and download_file_stream(item['pdf_url'], temp_pdf, session):
            return True, "OpenAlex"
        
        # 2. Unpaywall (Fallback 1)
        if upw.get('best_oa_location', {}).get('url_for_pdf'):
            if download_file_stream(upw['best_oa_location']['url_for_pdf'], temp_pdf, session):
                return True, "Unpaywall"
        
        # 3. CrossRef Direct Link (Fallback 2)
        if enrich.get('crossref_pdf'):
            if download_file_stream(enrich['crossref_pdf'], temp_pdf, session):
                return True, "CrossRef Direct"
        
        # 4. Scopus API (Elsevier/ScienceDirect)
        if self.api_client.config.SCOPUS_KEY and self.is_elsevier_item(item, landing_url):
            try:
                scopus_headers = {'X-ELS-APIKey': self.api_client.config.SCOPUS_KEY, 'Accept': 'application/pdf'}
                scopus_url = f"https://api.elsevier.com/content/article/doi/{item['doi']}"
                if download_file_stream(scopus_url, temp_pdf, session, extra_headers=scopus_headers):
                    return True, "Scopus API"
            except Exception:
                pass
        
        # 5. Publisher Heuristics (from landing URL)
        if landing_url:
            h_url = apply_publisher_heuristics(landing_url, item['doi'], self.patterns)
            if h_url and download_file_stream(h_url, temp_pdf, session):
                return True, "Heuristics"
        
        # 6. Meta Tag Scraper (from landing URL)
        if landing_url:
            s_url = fetch_html_meta_pdf_link(landing_url, session)
            if s_url and download_file_stream(s_url, temp_pdf, session):
                self.learn_pattern_from_url(item['doi'], s_url)
                return True, "Meta-Scraper"
        
        # 7. DOI-only Heuristics (no landing page needed)
        if item['doi']:
            h_url = apply_publisher_heuristics(None, item['doi'], self.patterns)
            if h_url and download_file_stream(h_url, temp_pdf, session):
                return True, "DOI Heuristics"
        
        return False, "None"

    def process_playwright_queue(self, playwright_queue, finalize_item_callback):
        """Processes the playwright download queue sequentially."""
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(user_agent=self.api_client.BROWSER_HEADERS['User-Agent'])
                
                for task in playwright_queue:
                    pdf_success = False
                    source = "None"
                    
                    try:
                        page = context.new_page()
                        try:
                            page.goto(task['landing_url'], timeout=30000, wait_until="domcontentloaded")
                            try:
                                page.wait_for_load_state("networkidle", timeout=3000)
                            except Exception:
                                page.wait_for_timeout(1000)
                            
                            pdf_meta = None
                            try: 
                                pdf_meta = page.locator('meta[name="citation_pdf_url"]').get_attribute("content", timeout=3000)
                            except Exception: 
                                pass
                            
                            if pdf_meta:
                                if not pdf_meta.startswith("http"):
                                    pdf_meta = urljoin(page.url, pdf_meta)
                                r = context.request.get(pdf_meta)
                                if r.ok and b'%PDF-' in r.body()[:4000]:
                                     with open(task['temp_pdf'], 'wb') as f: 
                                         f.write(r.body())
                                     pdf_success = True
                                     source = "Browser Meta-Scraper"
                                     self.learn_pattern_from_url(task['item']['doi'], pdf_meta)
                            
                            if not pdf_success:
                                selectors = ["a[href$='.pdf']", "a:has-text('PDF')", "button:has-text('PDF')", ".pdf-download"]
                                for sel in selectors:
                                    if page.locator(sel).count() > 0:
                                        with page.expect_download(timeout=8000) as download_info:
                                             page.locator(sel).first.click(force=True)
                                        download = download_info.value
                                        download.save_as(task['temp_pdf'])
                                        with open(task['temp_pdf'], 'rb') as f: 
                                             if b'%PDF-' in f.read(1000): 
                                                 pdf_success = True
                                                 source = "Browser Click-Bot"
                                        break
                        except Exception: 
                            pass
                        finally:
                            page.close()
                    except Exception: 
                        pass
                    
                    finalize_item_callback(task, pdf_success, source)
                    
                browser.close()
        except BaseException as e:
            logging.error(f"Playwright batch failed: {e}")
            for task in playwright_queue:
                finalize_item_callback(task, False, "Playwright Error")
