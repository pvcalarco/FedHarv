# SPDX-License-Identifier: AGPL-3.0-only
import json
import logging
import os
import re
import tempfile
import threading
from urllib.parse import urljoin, urlparse

from .config import DOI_PDF_PATTERNS, DOMAIN_URL_TRANSFORMS
from .utils import encode_doi_for_url, is_safe_http_url

MAX_PDF_BYTES = 100 * 1024 * 1024  # 100 MB cap per downloaded PDF
DOWNLOAD_CHUNK_SIZE = 65536

BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Reject learned patterns whose URL path encodes a specific journal issue/volume.
_ISSUE_SPECIFIC_URL_RE = re.compile(
    r"(?:^|[/_-])(?:19|20)\d{2}[-_]v\d+|(?:^|[/_-])v\d+[-_]n\d+",
    re.IGNORECASE,
)


def resolve_url(url, session):
    if not is_safe_http_url(url):
        return url
    if "doi.org" not in url and "handle.net" not in url:
        return url
    headers = {"User-Agent": BROWSER_USER_AGENT}
    try:
        r = session.head(url, allow_redirects=True, timeout=15, headers=headers)
        if r.status_code in (405, 403):
            with session.get(url, stream=True, allow_redirects=True, timeout=15, headers=headers) as get_resp:
                return get_resp.url
        return r.url
    except Exception as e:
        logging.debug(f"resolve_url failed for {url}: {e}")
        return url


def download_file_stream(url, path, session, extra_headers=None, max_bytes=MAX_PDF_BYTES, verify=True):
    if not is_safe_http_url(url):
        logging.debug(f"download_file_stream: Rejected unsafe URL {url}")
        return False

    headers = {
        "User-Agent": BROWSER_USER_AGENT,
        "Accept": "application/pdf,text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    if extra_headers:
        headers.update(extra_headers)

    try:
        with session.get(url, stream=True, timeout=60, allow_redirects=True, headers=headers, verify=verify) as r:
            if r.status_code != 200:
                logging.debug(f"download_file_stream: HTTP {r.status_code} for {url}")
                return False

            content_len = r.headers.get("Content-Length")
            if content_len and content_len.isdigit() and int(content_len) > max_bytes:
                logging.warning(f"download_file_stream: Skipping oversized PDF ({content_len} bytes) from {url}")
                return False

            chunk_iter = r.iter_content(chunk_size=DOWNLOAD_CHUNK_SIZE)
            try:
                first_chunk = next(chunk_iter)
            except StopIteration:
                logging.debug(f"download_file_stream: Empty body for {url}")
                return False

            if b"%PDF-" not in first_chunk[:4096]:
                logging.debug(f"download_file_stream: Content not PDF for {url}")
                return False

            written = len(first_chunk)
            if written > max_bytes:
                return False

            part_path = f"{path}.part"
            try:
                with open(part_path, "wb") as f:
                    f.write(first_chunk)
                    for chunk in chunk_iter:
                        if not chunk:
                            continue
                        written += len(chunk)
                        if written > max_bytes:
                            logging.warning(
                                f"download_file_stream: Aborted oversized stream (> {max_bytes} bytes) from {url}"
                            )
                            break
                        f.write(chunk)

                if written > max_bytes:
                    return False

                os.replace(part_path, path)
                return True
            finally:
                if os.path.exists(part_path):
                    try:
                        os.remove(part_path)
                    except OSError:
                        pass
    except Exception as e:
        logging.debug(f"download_file_stream: Exception downloading {url}: {e}")
        return False


def extract_pdf_url_from_html(html, page_url, include_href_fallback=False):
    """Extract a PDF link from HTML citation meta tags (and optionally direct .pdf hrefs)."""
    if not html:
        return None
    patterns = [
        r'<meta\s+[^>]*name=["\']citation_pdf_url["\'][^>]*content=["\'](.*?)["\']',
        r'<meta\s+[^>]*content=["\'](.*?)["\'][^>]*name=["\']citation_pdf_url["\']',
        r'<meta\s+[^>]*property=["\']citation_pdf_url["\'][^>]*content=["\'](.*?)["\']',
    ]
    if include_href_fallback:
        patterns.append(r'href=["\']([^"\']+\.pdf(?:\?[^"\']*)?)["\']')

    for pattern in patterns:
        match = re.search(pattern, html, re.IGNORECASE)
        if match:
            candidate = match.group(1).strip()
            if candidate:
                return urljoin(page_url, candidate)
    return None


def fetch_html_meta_pdf_link(landing_url, session):
    if not is_safe_http_url(landing_url):
        return None

    targets = (
        "academic.oup.com", "jstor.org", "muse.jhu.edu", "diabetesjournals.org",
        "pubs.aip.org", "hdl.handle.net", "f1000research.com", "ncbi.nlm.nih.gov",
        "techscience.com", "aimspress.com", "thesai.org", "psycnet.apa.org",
        "jgaa.info", "casn.ca", "cad-journal.net", "semarakilmu.com.my",
        "canadianfieldnaturalist.ca", "reabic.net", "journalhosting.ucalgary.ca",
        "mdpi.com", "iwaponline.com", "sciendo.com", "panafrican-med-journal.com",
        "journals.sagepub.com", "advancesinrehab.com", "lww.com", "medknow.com",
        "pubs.acs.org", "uiowa.edu", "openlibhums.org", "cdc.gov",
        "ietresearch.onlinelibrary.wiley.com", "scielo.br", "scielo.org",
    )

    should_scrape = any(t in landing_url for t in targets)
    if not should_scrape and any(
        marker in landing_url for marker in ("/view/abstract/", "/article/view/", "cgi/viewcontent")
    ):
        should_scrape = True
    if not should_scrape:
        return None

    headers = {
        "User-Agent": BROWSER_USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    try:
        r = session.get(landing_url, headers=headers, timeout=15, allow_redirects=True)
        if r.status_code == 200:
            page_url = getattr(r, "url", None) or landing_url
            return extract_pdf_url_from_html(r.text, page_url)
        logging.debug(f"fetch_html_meta_pdf_link: HTTP {r.status_code} for {landing_url}")
    except Exception as e:
        logging.debug(f"fetch_html_meta_pdf_link: Exception fetching {landing_url}: {e}")
    return None


def apply_publisher_heuristics(landing_url, doi=None, learned_patterns=None):
    """Resolve PDF URL using learned patterns, DOI prefix rules, then domain-specific transforms."""
    # 1. Learned patterns (dynamic)
    if learned_patterns and doi and "/" in doi:
        prefix = doi.split("/", 1)[0]
        suffix = doi.split("/", 1)[1]
        if prefix in learned_patterns:
            pattern = learned_patterns[prefix]
            try:
                return pattern.replace("{doi}", doi).replace("{doi_suffix}", suffix)
            except Exception:
                pass

    # 2. Hardcoded DOI prefix patterns
    if doi and "/" in doi:
        suffix = doi.split("/", 1)[1]
        for prefix, pattern in DOI_PDF_PATTERNS.items():
            if doi.startswith(prefix):
                try:
                    return pattern.replace("{doi}", doi).replace("{doi_suffix}", suffix)
                except Exception:
                    pass

    # 3. Domain-specific landing URL transforms
    if landing_url:
        if "chrome-extension://" in landing_url:
            match = re.search(r"(https?://.*)", landing_url)
            landing_url = match.group(1) if match else landing_url

        if landing_url.lower().endswith(".pdf"):
            return landing_url

        host = urlparse(landing_url).netloc.lower()
        for domain, transform_func in DOMAIN_URL_TRANSFORMS.items():
            if domain in host or domain in landing_url:
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
        self._patterns_lock = threading.Lock()
        self.load_learned_patterns()

    def load_learned_patterns(self):
        if not self.patterns_file or not os.path.exists(self.patterns_file):
            return
        try:
            with open(self.patterns_file, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                with self._patterns_lock:
                    self.patterns = loaded
                logging.info(f"Loaded {len(loaded)} learned PDF patterns.")
        except Exception as exc:
            logging.warning(f"Could not load learned patterns from {self.patterns_file}: {exc}")

    def learn_pattern_from_url(self, doi, resolved_pdf_url):
        if not doi or "/" not in doi or not is_safe_http_url(resolved_pdf_url):
            return
        if _ISSUE_SPECIFIC_URL_RE.search(urlparse(resolved_pdf_url).path):
            logging.debug(f"Skipping issue-specific URL for pattern learning: {resolved_pdf_url}")
            return

        prefix, suffix = doi.split("/", 1)
        if f"{prefix}/" in DOI_PDF_PATTERNS:
            return

        pattern = None
        if doi in resolved_pdf_url:
            pattern = resolved_pdf_url.replace(doi, "{doi}")
        elif suffix and suffix in resolved_pdf_url:
            pattern = resolved_pdf_url.replace(suffix, "{doi_suffix}")

        if not pattern:
            return

        with self._patterns_lock:
            if prefix in self.patterns:
                return
            self.patterns[prefix] = pattern
            snapshot = dict(self.patterns)

        self.save_learned_patterns(snapshot)
        logging.info(f"Learned new PDF pattern for DOI prefix '{prefix}': {pattern}")

    def save_learned_patterns(self, snapshot=None):
        if not self.patterns_file:
            return
        if snapshot is None:
            with self._patterns_lock:
                snapshot = dict(self.patterns)
        tmp_path = None
        try:
            parent = os.path.dirname(os.path.abspath(self.patterns_file))
            os.makedirs(parent, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=parent, prefix=".patterns_", suffix=".tmp", delete=False
            ) as tmp_file:
                tmp_path = tmp_file.name
                json.dump(snapshot, tmp_file, indent=4)
            os.replace(tmp_path, self.patterns_file)
        except Exception as e:
            logging.warning(f"Could not save learned patterns to {self.patterns_file}: {e}")
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass

    def is_elsevier_item(self, item, landing_url):
        pub = (item.get("publisher") or "").lower()
        if "elsevier" in pub:
            return True
        doi = item.get("doi") or ""
        if doi.startswith("10.1016/"):
            return True
        if landing_url and "sciencedirect.com" in landing_url:
            return True
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
        7. DOI-only Heuristics (learned + static DOI prefix rules)

        Returns: (pdf_success: bool, source: str)
        """
        session = self.api_client.SESSION
        tried_urls = set()

        def _try_download(url, extra_headers=None):
            if not url:
                return False
            key = (url, tuple(sorted((extra_headers or {}).items())))
            if key in tried_urls:
                return False
            tried_urls.add(key)
            return download_file_stream(url, temp_pdf, session, extra_headers=extra_headers)

        # 1. OpenAlex
        if _try_download(item.get("pdf_url")):
            return True, "OpenAlex"

        # 2. Unpaywall
        best_oa = (upw or {}).get("best_oa_location") or {}
        if _try_download(best_oa.get("url_for_pdf")):
            return True, "Unpaywall"

        # 3. CrossRef Direct Link
        if _try_download((enrich or {}).get("crossref_pdf")):
            return True, "CrossRef Direct"

        # 4. Scopus API (Elsevier/ScienceDirect)
        doi = item.get("doi")
        if doi and self.api_client.config.SCOPUS_KEY and self.is_elsevier_item(item, landing_url):
            scopus_headers = {"X-ELS-APIKey": self.api_client.config.SCOPUS_KEY, "Accept": "application/pdf"}
            scopus_url = f"https://api.elsevier.com/content/article/doi/{encode_doi_for_url(doi)}"
            if _try_download(scopus_url, extra_headers=scopus_headers):
                return True, "Scopus API"

        # 5. Publisher Heuristics (landing-page domain transform only)
        if landing_url:
            h_url = apply_publisher_heuristics(landing_url, None, None)
            if _try_download(h_url):
                return True, "Heuristics"

        # 6. Meta Tag Scraper (from landing URL)
        if landing_url:
            s_url = fetch_html_meta_pdf_link(landing_url, session)
            if _try_download(s_url):
                self.learn_pattern_from_url(doi, s_url)
                return True, "Meta-Scraper"

        # 7. DOI-only Heuristics (learned + static DOI prefix rules)
        if doi:
            with self._patterns_lock:
                patterns_snapshot = dict(self.patterns)
            h_url = apply_publisher_heuristics(None, doi, patterns_snapshot)
            if _try_download(h_url):
                return True, "DOI Heuristics"

        return False, "None"

    def process_playwright_queue(self, playwright_queue, finalize_item_callback):
        """Processes the playwright download queue sequentially without double-finalizing on failure."""
        finalized_indices = set()
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                try:
                    context = browser.new_context(user_agent=self.api_client.BROWSER_HEADERS["User-Agent"])

                    for i, task in enumerate(playwright_queue):
                        pdf_success = False
                        source = "None"
                        landing_url = task.get("landing_url")

                        if is_safe_http_url(landing_url):
                            try:
                                page = context.new_page()
                                try:
                                    page.goto(landing_url, timeout=30000, wait_until="domcontentloaded")
                                    try:
                                        page.wait_for_load_state("networkidle", timeout=3000)
                                    except Exception:
                                        page.wait_for_timeout(1000)

                                    pdf_meta = None
                                    try:
                                        pdf_meta = page.locator('meta[name="citation_pdf_url"]').get_attribute(
                                            "content", timeout=3000
                                        )
                                    except Exception:
                                        pass

                                    if pdf_meta:
                                        if not pdf_meta.startswith(("http://", "https://")):
                                            pdf_meta = urljoin(page.url, pdf_meta)
                                        if is_safe_http_url(pdf_meta):
                                            r = context.request.get(pdf_meta, timeout=30000)
                                            if r.ok:
                                                body = r.body()
                                                if len(body) <= MAX_PDF_BYTES and b"%PDF-" in body[:4000]:
                                                    with open(task["temp_pdf"], "wb") as f:
                                                        f.write(body)
                                                    pdf_success = True
                                                    source = "Browser Meta-Scraper"
                                                    self.learn_pattern_from_url(task["item"].get("doi"), pdf_meta)

                                    if not pdf_success:
                                        selectors = (
                                            "a[href$='.pdf']",
                                            "a:has-text('PDF')",
                                            "button:has-text('PDF')",
                                            ".pdf-download",
                                        )
                                        for sel in selectors:
                                            try:
                                                if page.locator(sel).count() == 0:
                                                    continue
                                                with page.expect_download(timeout=8000) as download_info:
                                                    page.locator(sel).first.click(force=True)
                                                download = download_info.value
                                                download.save_as(task["temp_pdf"])
                                                valid_pdf = False
                                                if os.path.getsize(task["temp_pdf"]) <= MAX_PDF_BYTES:
                                                    with open(task["temp_pdf"], "rb") as f:
                                                        valid_pdf = b"%PDF-" in f.read(1000)
                                                if valid_pdf:
                                                    pdf_success = True
                                                    source = "Browser Click-Bot"
                                                    break
                                                try:
                                                    os.remove(task["temp_pdf"])
                                                except OSError:
                                                    pass
                                            except Exception as sel_exc:
                                                logging.debug(f"Playwright selector '{sel}' failed for {landing_url}: {sel_exc}")
                                except Exception as page_exc:
                                    logging.debug(f"Playwright page processing failed for {landing_url}: {page_exc}")
                                finally:
                                    page.close()
                            except Exception as outer_exc:
                                logging.debug(f"Playwright task failed for {landing_url}: {outer_exc}")

                        finalize_item_callback(task, pdf_success, source)
                        finalized_indices.add(i)
                finally:
                    browser.close()
        except Exception as e:
            logging.error(f"Playwright batch failed: {e}")
            for i, task in enumerate(playwright_queue):
                if i not in finalized_indices:
                    finalize_item_callback(task, False, "Playwright Error")
