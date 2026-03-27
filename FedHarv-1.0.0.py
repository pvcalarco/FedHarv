"""
Federated OA Harvester (v1.0.0: First public release with comprehensive features and optimizations)
----------------------------------------------------
Features:
  1. SPLIT OUTPUT: 
     - If PDF found -> Save to 'Items_With_PDF' folder (No RIS).
     - If PDF missing -> Save to 'Items_Only_Link' AND write to 'citations.ris'.
  2. METADATA GUARANTEE: Always harvests CrossRef, FundRef, Datacite, DOAJ.
  3. PDF WATERFALL: OpenAlex -> Unpaywall -> CrossRef TDM -> Heuristics.
  4. STRICT FILTERING: Only processes Gold, Hybrid, Diamond, and Green.
  5. DATE RANGE: Queries APIs using YYYY-MM-DD date ranges instead of a single year.

License: MIT License
"""
import os
from dotenv import load_dotenv

# Load the variables from .env into the environment
load_dotenv()

# Retrieve the keys
scopus_key = os.getenv("SCOPUS_API_KEY")
contact_email = os.getenv("OPENALEX_EMAIL")

import sys
import shutil
import logging
import time
import requests
import re
import json
import csv
import hashlib
import argparse
import configparser
import xml.etree.ElementTree as ET
import threading
import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from xml.dom import minidom
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from collections import Counter, defaultdict
from ratelimit import limits, sleep_and_retry
import backoff
import concurrent.futures
from tqdm import tqdm # For a visual progress bar


def validate_date(date_str):
    """Ensure date is YYYY-MM-DD format."""
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date_str):
        print(f"Error: Date '{date_str}' must be in YYYY-MM-DD format.")
        sys.exit(1)
    try:
        datetime.datetime.strptime(date_str, '%Y-%m-%d')
    except ValueError:
        print(f"Error: '{date_str}' is not a valid calendar date.")
        sys.exit(1)


# --- UTF-8 CONSOLE FIX FOR WINDOWS ---
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except:
        pass

# ==========================================
# GLOBAL CONSTANTS
# ==========================================
OPENALEX_WORKS_URL = "https://api.openalex.org/works"
OPENALEX_INST_URL = "https://api.openalex.org/institutions"
UNPAYWALL_API = "https://api.unpaywall.org/v2/"
SHERPA_API_URL = "https://v2.sherpa.ac.uk/cgi/retrieve"
CROSSREF_API_URL = "https://api.crossref.org/works"
DATACITE_API_URL = "https://api.datacite.org/dois/"
DOAJ_SEARCH_URL = "https://doaj.org/api/v2/search/journals/"

# ==========================================
# RATE LIMITING CONFIGURATION
# ==========================================
CALLS = 10  # Max API calls per period
RATE_LIMIT_PERIOD = 1  # Period in seconds (10 calls/sec)

# ==========================================
# PUBLISHER & PDF CONFIGURATION
# ==========================================
# DOI Prefix to PDF URL patterns - used by apply_publisher_heuristics()
DOI_PDF_PATTERNS = {
    "10.3233/": "https://ebooks.iospress.nl/pdf/doi/{doi}",
    "10.2478/": "https://sciendo.com/pdf/{doi}",
    "10.1049/": "https://ietresearch.onlinelibrary.wiley.com/doi/pdf/{doi}",
    "10.1007/": "https://link.springer.com/content/pdf/{doi}.pdf",
    "10.1021/": "https://pubs.acs.org/doi/pdf/{doi}",
    "10.1002/": "https://onlinelibrary.wiley.com/doi/pdf/{doi}",
    "10.1111/": "https://onlinelibrary.wiley.com/doi/pdf/{doi}",
    "10.1155/": "https://onlinelibrary.wiley.com/doi/pdf/{doi}",
    "10.1080/": "https://www.tandfonline.com/doi/pdf/{doi}",
    "10.1177/": "https://journals.sagepub.com/doi/pdf/{doi}",
    "10.3390/": "https://www.mdpi.com/{doi}/pdf",
    "10.3389/": "https://www.frontiersin.org/articles/{doi}/pdf",
    "10.1371/": "https://journals.plos.org/plosone/article/file?id={doi}&type=printable",
    "10.3934/": "https://www.aimspress.com/article/{doi}/pdf",
    "10.16995/": "https://pr.openlibhums.org/article/id/{doi_suffix}/download/pdf/",
    "10.1590/": "https://www.scielo.br/j/hcsm/a/{doi_suffix}/?format=pdf&lang=en",
}

# Domain-specific URL transformations for landing pages
DOMAIN_URL_TRANSFORMS = {
    "scielo.br": lambda url: url if "format=pdf" in url else url + "&format=pdf",
    "scielo.org": lambda url: url if "format=pdf" in url else url + "&format=pdf",
    "sciendo.com": lambda url: url.replace("/article/", "/pdf/") if "/article/" in url else url,
    "pnas.org": lambda url: url.replace("/doi/", "/doi/pdf/") if "/doi/pdf/" not in url else url,
    "aimspress.com": lambda url: url.replace("/article/doi/", "/article/") + "/pdf",
    "f1000research.com": lambda url: url + "/pdf",
    "pubs.acs.org": lambda url: url.replace("/doi/", "/doi/pdf/"),
    "nature.com": lambda url: url + ".pdf" if "/articles/" in url else url,
    "mdpi.com": lambda url: url + "/pdf" if "/pdf" not in url else url,
    "pubs.rsc.org": lambda url: url.replace("/articlelanding/", "/articlepdf/"),
    "onlinelibrary.wiley.com": lambda url: _wiley_url_transform(url),
    "link.springer.com": lambda url: url.replace("/article/", "/content/pdf/") + ".pdf",
    "journals.sagepub.com": lambda url: url.replace("/full/", "/pdf/").replace("/doi/", "/doi/pdf/"),
    "tandfonline.com": lambda url: url.split("?")[0].replace("/full/", "/pdf/") if "?" in url else url.replace("/full/", "/pdf/"),
    "ieeexplore.ieee.org": lambda url: _ieee_url_transform(url),
    "iopscience.iop.org": lambda url: url + "/pdf",
    "cambridge.org": lambda url: _cambridge_url_transform(url),
    "ncbi.nlm.nih.gov": lambda url: url.rstrip('/') + "/pdf" if "/pmc/" in url else url,
    "frontiersin.org": lambda url: url.replace("/full", "/pdf"),
    "projecteuclid.org": lambda url: url.replace(".full", ".pdf"),
    "journals.asm.org": lambda url: url.replace("/doi/", "/doi/pdf/"),
    "journals.plos.org": lambda url: url.replace("/article?", "/article/file?") + "&type=printable",
    "pensoft.net": lambda url: url.rstrip('/') + "/download/pdf/" if "/download/pdf/" not in url else url,
    "science.org": lambda url: url.replace("/doi/", "/doi/pdf/"),
    "cdnsciencepub.com": lambda url: url.replace("/abs/", "/pdf/").replace("/full/", "/pdf/").replace("/doi/", "/doi/pdf/"),
}

def _wiley_url_transform(url):
    """Handle Wiley-specific URL transformations."""
    if "/pdfdirect/" in url: return url
    if "/epdf/" in url: return url.replace("/epdf/", "/pdf/")
    if "/pdf/" in url: return url
    return url.replace("/full/", "/pdf/").replace("/abs/", "/pdf/").replace("/doi/", "/doi/pdf/")

def _ieee_url_transform(url):
    """Handle IEEE-specific URL transformations."""
    if "/document/" not in url: return url
    doc_id = url.split("/document/")[-1].split("/")[0]
    return f"https://ieeexplore.ieee.org/stamp/stamp.jsp?tp=&arnumber={doc_id}"

def _cambridge_url_transform(url):
    """Handle Cambridge-specific URL transformations."""
    parts = url.split('/')
    return f"https://www.cambridge.org/core/services/aop-cambridge-core/content/view/{parts[-1]}/{parts[-1]}.pdf" if parts else url

# ==========================================
# NORMALIZATION MAPPINGS
# ==========================================
DOCTYPE_MAPPINGS = {
    "review": "Article",
    "chapter": "Book Chapter", "ch": "Book Chapter", "book-chapter": "Book Chapter",
    "book": "Book", "bk": "Book",
    "conference": "Conference Paper", "proceeding": "Conference Paper", "cp": "Conference Paper",
    "data": "Dataset", "dataset": "Dataset", "dp": "Dataset",
    "report": "Technical Report", "rp": "Technical Report",
    "letter": "Article", "le": "Article", "note": "Article", "no": "Article",
    "short survey": "Article", "sh": "Article",
    "journal-article": "Article",
    "book-section": "Book Chapter"
}

OA_STATUS_MAPPINGS = {
    "diamond": "Diamond",
    "fullgold": "Gold", "gold": "Gold",
    "hybrid": "Hybrid",
    "green": "Green", "repository": "Green",
    "bronze": "Bronze"
}

LICENSE_URI_MAPPINGS = {
    "cc by": "https://creativecommons.org/licenses/by/4.0/",
    "cc-by": "https://creativecommons.org/licenses/by/4.0/",
    "cc by-sa": "https://creativecommons.org/licenses/by-sa/4.0/",
    "cc-by-sa": "https://creativecommons.org/licenses/by-sa/4.0/",
    "cc by-nc": "https://creativecommons.org/licenses/by-nc/4.0/",
    "cc-by-nc": "https://creativecommons.org/licenses/by-nc/4.0/",
    "cc by-nd": "https://creativecommons.org/licenses/by-nd/4.0/",
    "cc-by-nd": "https://creativecommons.org/licenses/by-nd/4.0/",
    "cc by-nc-nd": "https://creativecommons.org/licenses/by-nc-nd/4.0/",
    "cc-by-nc-nd": "https://creativecommons.org/licenses/by-nc-nd/4.0/",
    "cc by-nc-sa": "https://creativecommons.org/licenses/by-nc-sa/4.0/",
    "cc-by-nc-sa": "https://creativecommons.org/licenses/by-nc-sa/4.0/"
}

# Creative Commons license display names for Dublin Core
CC_LICENSE_NAMES = {
    "https://creativecommons.org/licenses/by/4.0/": "Creative Commons CC-BY 4.0 International",
    "https://creativecommons.org/licenses/by-sa/4.0/": "Creative Commons CC-BY-SA 4.0 International",
    "https://creativecommons.org/licenses/by-nc/4.0/": "Creative Commons CC-BY-NC 4.0 International",
    "https://creativecommons.org/licenses/by-nd/4.0/": "Creative Commons CC-BY-ND 4.0 International",
    "https://creativecommons.org/licenses/by-nc-nd/4.0/": "Creative Commons CC-BY-NC-ND 4.0 International",
    "https://creativecommons.org/licenses/by-nc-sa/4.0/": "Creative Commons CC-BY-NC-SA 4.0 International"
}

# ==========================================
# HELPERS
# ==========================================

def safe_call(default=None, log_errors=True, log_level=logging.WARNING):
    """
    Decorator for safe function calls with consistent error handling.
    
    Args:
        default: Value to return on exception
        log_errors: Whether to log exceptions
        log_level: Logging level for exceptions
    
    Usage:
        @safe_call(default={}, log_errors=True)
        def my_function():
            # Function that might raise exceptions
            return result
    """
    def decorator(func):
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                if log_errors:
                    logging.log(log_level, f"Error in {func.__name__}: {e}")
                return default
        return wrapper
    return decorator

def cached_api_call(cache_prefix, cache_dir_attr='CACHE_DIR'):
    """
    Decorator for API functions that automatically handle caching.
    
    Args:
        cache_prefix: String prefix for cache keys (e.g., "crossref", "doaj")
        cache_dir_attr: Attribute name on 'self' that contains cache directory
    
    Usage:
        @cached_api_call("crossref")
        def fetch_crossref_data(self, doi):
            # Do API work here - caching is handled automatically
            return data
    """
    def decorator(func):
        def wrapper(self, identifier, *args, **kwargs):
            # Check cache first
            cached = load_from_cache(cache_prefix, identifier, getattr(self, cache_dir_attr))
            if cached is not None:  # Note: allows empty dict/list as valid cached data
                return cached
            
            # Call the actual function
            result = func(self, identifier, *args, **kwargs)
            
            # Cache the result (only if not None/empty to avoid cache pollution)
            if result is not None:
                save_to_cache(cache_prefix, identifier, result, getattr(self, cache_dir_attr))
            
            return result
        return wrapper
    return decorator

def normalize_value(raw_value, mappings, default=None, preprocess_func=None):
    """
    Decorator for safe function calls with consistent error handling.
    
    Args:
        default: Value to return on exception
        log_errors: Whether to log exceptions
        log_level: Logging level for exceptions
    
    Usage:
        @safe_call(default={}, log_errors=True)
        def my_function():
            # Function that might raise exceptions
            return result
    """
    def decorator(func):
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                if log_errors:
                    logging.log(log_level, f"Error in {func.__name__}: {e}")
                return default
        return wrapper
    return decorator

def normalize_value(raw_value, mappings, default=None, preprocess_func=None):
    """
    Generic normalization function that maps raw values to standardized values.
    
    Args:
        raw_value: The raw input value to normalize
        mappings: Dict mapping raw patterns to standardized values
        default: Default value if no mapping matches
        preprocess_func: Optional function to preprocess raw_value (e.g., str.lower)
    
    Returns: Standardized value or default
    """
    if not raw_value:
        return default
    
    # Apply preprocessing (e.g., convert to lowercase)
    processed = preprocess_func(raw_value) if preprocess_func else raw_value
    
    # Check for exact matches first
    if processed in mappings:
        return mappings[processed]
    
    # Check for substring matches, preferring longer patterns
    # Sort patterns by length (longest first) to match more specific licenses first
    sorted_patterns = sorted(mappings.keys(), key=len, reverse=True)
    for pattern in sorted_patterns:
        if pattern in processed:
            return mappings[pattern]
    
    return default

@sleep_and_retry
@limits(calls=CALLS, period=RATE_LIMIT_PERIOD)
@backoff.on_exception(backoff.expo, requests.exceptions.RequestException, max_tries=5)
def rate_limited_get(url, session=None, params=None, headers=None, timeout=30):
    """
    Universal rate-limited API GET request with exponential backoff retry.
    Enforces: 1. Rate limiting (10 calls/sec)
              2. Exponential backoff on request errors (max 5 retries)
    """
    if session:
        r = session.get(url, params=params, headers=headers, timeout=timeout)
    else:
        r = requests.get(url, params=params, headers=headers, timeout=timeout)
    
    if r.status_code == 429:
        raise requests.exceptions.RequestException(f"Rate limit (429) from {url}")
    return r

def get_cache_path(prefix, identifier, cache_dir):
    safe_id = hashlib.md5(str(identifier).encode('utf-8')).hexdigest()
    return os.path.join(cache_dir, f"{prefix}_{safe_id}.json")

cache_lock = threading.Lock()
MEMORY_CACHE = {}

@safe_call(default=None, log_errors=False)
def load_from_cache(prefix, identifier, cache_dir):
    cache_key = f"{prefix}_{identifier}"
    with cache_lock:
        if cache_key in MEMORY_CACHE:
            return MEMORY_CACHE[cache_key]
    path = get_cache_path(prefix, identifier, cache_dir)
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f: 
            data = json.load(f)
            with cache_lock:
                MEMORY_CACHE[cache_key] = data
            return data
    return None

@safe_call(log_errors=False)
def save_to_cache(prefix, identifier, data, cache_dir):
    if not data: return
    cache_key = f"{prefix}_{identifier}"
    with cache_lock:
        MEMORY_CACHE[cache_key] = data
    path = get_cache_path(prefix, identifier, cache_dir)
    with open(path, 'w', encoding='utf-8') as f: 
        json.dump(data, f)

def clean_text(text):
    if text:
        text = re.sub(r'<[^>]+>', '', str(text))
        return text.encode('utf-8', 'ignore').decode('utf-8').strip()
    return None

def normalize_string(text):
    if not text: return ""
    return re.sub(r'[^a-z0-9]', '', str(text).lower())

def sanitize_filename(name):
    if not name: return "General_Output"
    name = " ".join(str(name).split())
    clean = re.sub(r'[^\w\s-]', '', name)
    clean = clean.strip().replace(' ', '_')
    return clean[:64] 

def clean_abstract(text):
    if not text: return None
    text = clean_text(text)
    text = re.sub(r'^Copyright © \d{4}.*?\. ', '', text, flags=re.IGNORECASE)
    text = re.sub(r'^© \d{4}.*?\. ', '', text, flags=re.IGNORECASE)
    text = re.sub(r'All rights reserved\.$', '', text, flags=re.IGNORECASE)
    text = re.sub(r'^Abstract\s*', '', text, flags=re.IGNORECASE)
    return text.strip()

@safe_call(default=None, log_errors=False)
def reconstruct_openalex_abstract(inverted_index):
    if not inverted_index or not isinstance(inverted_index, dict): return None
    word_list = []
    for word, positions in inverted_index.items():
        for pos in positions: word_list.append((pos, word))
    word_list.sort()
    return " ".join([w[1] for w in word_list])

def deep_get(d, keys, default=None):
    current = d
    for key in keys:
        if isinstance(current, list) and len(current) > 0:
             if isinstance(current[0], dict): current = current[0]
             else: return default
        if isinstance(current, dict): current = current.get(key)
        else: return default
        if current is None: return default
    return current

def safe_get_dict(d, key):
    if not isinstance(d, dict): return {}
    val = d.get(key)
    if isinstance(val, dict): return val
    return {}

def safe_json_dict(data):
    if isinstance(data, dict): return data
    if isinstance(data, list) and len(data) > 0 and isinstance(data[0], dict): return data[0]
    return {}

def ensure_list_of_dicts(data):
    if data is None: return []
    if isinstance(data, dict): return [data]
    if isinstance(data, list): return [x for x in data if isinstance(x, dict)]
    return []

def normalize_doctype(raw_type):
    return normalize_value(raw_type, DOCTYPE_MAPPINGS, default="Article", preprocess_func=str.lower)

def normalize_oa_status(raw_status, doi=None):
    if doi and "10.1590/" in doi:
        return "Diamond"
    return normalize_value(raw_status, OA_STATUS_MAPPINGS, default="Open Access", preprocess_func=str.lower)

def resolve_url(url, session):
    if not url or ("doi.org" not in url and "handle.net" not in url): return url
    try:
        r = session.head(url, allow_redirects=True, timeout=15)
        if r.status_code in [405, 403]:
            r = session.get(url, stream=True, allow_redirects=True, timeout=15)
            r.close()
        return r.url
    except: return url

def download_file_stream(url, path, session, extra_headers=None):
    if not url: return False
    try:
        with session.get(url, stream=True, timeout=60, allow_redirects=True, headers=extra_headers) as r:
            if r.status_code != 200: return False
            chunk_iter = r.iter_content(chunk_size=4096)
            try: first_chunk = next(chunk_iter)
            except StopIteration: return False
            if b'%PDF-' not in first_chunk: return False
            with open(path, 'wb') as f:
                f.write(first_chunk)
                for chunk in chunk_iter: f.write(chunk)
        return True
    except: 
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

    try:
        r = session.get(landing_url, timeout=15, allow_redirects=True)
        if r.status_code == 200:
            match = re.search(r'<meta\s+name=["\']citation_pdf_url["\']\s+content=["\'](.*?)["\']', r.text, re.IGNORECASE)
            if match: return match.group(1)
    except: pass
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
            except: pass

    # 2. Try DOI prefix-based patterns (hardcoded)
    if doi:
        for prefix, pattern in DOI_PDF_PATTERNS.items():
            if doi.startswith(prefix):
                try:
                    return pattern.replace('{doi}', doi).replace('{doi_suffix}', doi.split('/')[-1])
                except: pass

    # 3. Transform landing URL using domain-specific rules
    if landing_url:
        # Clean up chrome-extension URLs
        if "chrome-extension://" in landing_url:
            match = re.search(r'(https?://.*)', landing_url)
            landing_url = match.group(1) if match else landing_url
        
        # Already a PDF, return as-is
        if landing_url.lower().endswith(".pdf"):
            return landing_url
        
        # Try domain-specific transformations
        for domain, transform_func in DOMAIN_URL_TRANSFORMS.items():
            if domain in landing_url:
                try:
                    return transform_func(landing_url)
                except: pass
    
    return None

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
    # Final attempt (might fail)
    try: shutil.move(src, dst)
    except: pass

def normalize_license_uri(license_raw):
    if not license_raw:
        return None
    if "creativecommons.org" in license_raw:
        return license_raw
    return normalize_value(license_raw, LICENSE_URI_MAPPINGS, default=license_raw, preprocess_func=lambda x: x.lower().strip()) 

def determine_primary_department(all_affiliations, unit_map, target_affil):
    target_clean = normalize_string(target_affil)
    found_folders = set()
    sorted_keys = sorted(unit_map.keys(), key=len, reverse=True)
    for aff in all_affiliations:
        if normalize_string(aff) == target_clean: continue
        aff_clean = aff.lower()
        for key in sorted_keys:
            if key in aff_clean: 
                found_folders.add(sanitize_filename(unit_map[key]))
                break
    if len(found_folders) > 1: return "Multiple"
    if len(found_folders) == 1: return list(found_folders)[0]
    return sanitize_filename(target_affil)

def extract_all_affiliations(item, crossref_data):
    affils = set()
    if item['source'] == 'openalex':
        for ship in ensure_list_of_dicts(deep_get(item, ['raw', 'authorships'])):
            for inst in ensure_list_of_dicts(ship.get('institutions')):
                if inst.get('display_name'): affils.add(inst.get('display_name'))
    for a in (crossref_data.get('affiliations') or []): affils.add(a)
    return list(affils)

def map_to_dublin_core(item, enrich, all_affils, windsor_orcids):
    md = [] 
    def add(s, e, q, v):
        if v: md.append({'schema': s, 'element': e, 'qualifier': q, 'value': clean_text(v)})
    
    add('dc', 'title', None, item['title'])
    
    # Ensure full date string is captured
    pub_date = item.get('date')
    if pub_date:
        add('dc', 'date', 'issued', pub_date) 

    # Ensure DOI is always included when available from OpenAlex or CrossRef
    doi_value = item.get('doi')
    if not doi_value:
        # Fallback to raw data if merged DOI is missing
        raw_data = item.get('raw', {})
        if item.get('source') == 'openalex':
            doi_value = raw_data.get('doi')
        elif item.get('source') == 'crossref':
            doi_value = raw_data.get('DOI')
    
    add('dc', 'identifier', 'doi', doi_value)
    if doi_value: add('dc', 'identifier', 'uri', f"https://doi.org/{doi_value}")
    
    # Enhanced Creative Commons license reporting
    lic_uri = enrich.get('license')
    if lic_uri and lic_uri in CC_LICENSE_NAMES:
        # Use human-readable Creative Commons license name
        add('dc', 'rights', None, CC_LICENSE_NAMES[lic_uri])
        add('dc', 'rights', 'uri', lic_uri)
    elif item.get('oa_status') == 'Diamond':
        # Default CC-BY for Diamond OA when no specific license found
        add('dc', 'rights', None, 'Creative Commons CC-BY 4.0 International')
        add('dc', 'rights', 'uri', 'https://creativecommons.org/licenses/by/4.0/')
    else:
        # Fallback to OA status for non-CC licensed content
        add('dc', 'rights', None, item.get('oa_status'))
        if lic_uri:
            add('dc', 'rights', 'uri', lic_uri)
    
    add('dc', 'rights', 'policy', enrich.get('sherpa_uri'))
    for f in enrich.get('funders', []): add('dc', 'description', 'sponsorship', f)
    for aff in all_affils: add('organization', 'legalName', None, aff)
    for auth in item.get('norm_authors', []): 
        if isinstance(auth, dict): add('dc', 'contributor', 'author', auth.get('name'))
        else: add('dc', 'contributor', 'author', auth)
    for oid in windsor_orcids: add('person', 'identifier', 'orcid', oid)
    
    add('oaire', 'citation', 'title', item.get('journal'))
    if item.get('volume'): add('oaire', 'citation', 'volume', item['volume'])
    if item.get('issue'): add('oaire', 'citation', 'issue', item['issue'])
    if item.get('pages'): 
        add('dc', 'format', 'extent', item['pages'])
        parts = str(item['pages']).split('-')
        add('oaire', 'citation', 'startPage', parts[0])
        if len(parts) > 1: add('oaire', 'citation', 'endPage', parts[1])
    if item.get('issn'): add('dc', 'identifier', 'issn', item['issn'])
    
    abstract = enrich.get('abstract')
    if abstract: add('dc', 'description', 'abstract', clean_abstract(abstract))
    pub = item.get('publisher') or enrich.get('publisher')
    if pub: add('dc', 'publisher', None, pub)
    add('dc', 'type', None, item.get('doctype', 'Article'))
    add('dc', 'language', 'iso', 'en_CA')
    return md

def write_saf(metadata, item_dir, bitstream_file):
    schema_map = defaultdict(ET.Element)
    for field in metadata:
        prefix = field['schema']
        if prefix not in schema_map:
            root = ET.Element('dublin_core')
            root.set('schema', prefix)
            schema_map[prefix] = root
        el = ET.SubElement(schema_map[prefix], 'dcvalue')
        el.set('element', field['element'])
        if field['qualifier']: el.set('qualifier', field['qualifier'])
        else: el.set('qualifier', 'none')
        el.text = field['value']
    for prefix, root in schema_map.items():
        fname = "dublin_core.xml" if prefix == 'dc' else f"metadata_{prefix}.xml"
        with open(os.path.join(item_dir, fname), 'w', encoding='utf-8') as f:
            f.write(minidom.parseString(ET.tostring(root)).toprettyxml(indent="  "))
    if bitstream_file:
        with open(os.path.join(item_dir, 'contents'), 'w', encoding='utf-8') as f: f.write(bitstream_file)

def generate_import_scripts(base_dir, dspace_bin, dspace_email):
    script_file = os.path.join(base_dir, "import_batch.sh")
    with open(script_file, 'w') as f:
        f.write("#!/bin/bash\n\n")
        for root, dirs, files in os.walk(base_dir):
            if any(f == "dublin_core.xml" for f in files): continue
            if any(d.startswith("item_") for d in dirs):
                 f.write(f"echo 'Importing: {root}'\n")
                 f.write(f"{dspace_bin} import --add --eperson={dspace_email} --collection=123456789/0 --source={root} --mapfile=mapfile_{sanitize_filename(root)}\n\n")

def write_report(filepath, row):
    exists = os.path.isfile(filepath)
    with open(filepath, 'a', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=['DOI', 'Title', 'ISSN', 'Doc_Type', 'Source', 'Folder_Type', 'Dept', 'PDF_Status', 'PDF_Source', 'Sherpa_Policy', 'OA_Status', 'Notes'])
        if not exists: w.writeheader()
        w.writerow(row)

# RIS Generation Helper
def generate_ris_block(item, enrich):
    dt = item.get('doctype', 'Article').lower()
    ty = "JOUR" # Default
    if "book chapter" in dt: ty = "CHAP"
    elif "book" in dt: ty = "BOOK"
    elif "conference" in dt or "proceeding" in dt: ty = "CONF"
    elif "dataset" in dt: ty = "DATA"
    elif "report" in dt: ty = "RPRT"
    
    lines = [f"TY  - {ty}"]
    lines.append(f"TI  - {clean_text(item.get('title'))}")
    
    for auth in item.get('norm_authors', []):
        if isinstance(auth, dict): name = auth.get('name')
        else: name = auth
        if name: lines.append(f"AU  - {name}")
        
    lines.append(f"JO  - {clean_text(item.get('journal'))}")
    
    date_val = item.get('date')
    if date_val:
        date_val = str(date_val).replace('-', '/')
        lines.append(f"PY  - {date_val}")
        
    if item.get('volume'): lines.append(f"VL  - {item['volume']}")
    if item.get('issue'): lines.append(f"IS  - {item['issue']}")
    
    if item.get('pages'):
        parts = str(item['pages']).split('-')
        lines.append(f"SP  - {parts[0]}")
        if len(parts) > 1: lines.append(f"EP  - {parts[1]}")
        
    if item.get('issn'): lines.append(f"SN  - {item['issn']}")
    if item.get('doi'): lines.append(f"DO  - {item['doi']}")
    
    abstract = enrich.get('abstract')
    if abstract: lines.append(f"AB  - {clean_text(abstract)}")
    
    if item.get('doi'): lines.append(f"UR  - https://doi.org/{item['doi']}")
    
    pub = item.get('publisher') or enrich.get('publisher')
    if pub: lines.append(f"PB  - {pub}")
    
    lines.append("ER  - \n")
    return "\n".join(lines)

# ==========================================
# CLASS DEFINITION
# ==========================================

def check_dspace_duplicate(doi, check_enabled, api_url):
    if not check_enabled or not api_url or not doi: return False
    try:
        r = rate_limited_get(f"{api_url}/discover/search/objects", params={"query": f"dc.identifier.doi:{doi}", "dsoType": "ITEM"}, timeout=5)
        total = deep_get(safe_json_dict(r.json()), ['_embedded', 'searchResult', 'page', 'totalElements'])
        if r.status_code == 200 and total and int(total) > 0: return True
    except: pass
    return False

class FedHarv:
    def __init__(self):
        self.load_config()
        self.setup_logging()
        self.setup_session()
        self.init_stats()
        self.load_learned_patterns() 
        self.locks = {
            'stats': threading.Lock(),
            'csv': threading.Lock(),
            'author': threading.Lock(),
            'print': threading.Lock(),
            'ris': threading.Lock() 
        }

    def load_config(self):
        # Simplify argparse to only handle the config file path
        parser = argparse.ArgumentParser(description="Harvest OA Content for DSpace")
        parser.add_argument("--config", type=str, default="config.ini", help="Path to config file")
        self.args = parser.parse_args()

        self.config = configparser.ConfigParser()
        self.config.optionxform = str 
        
        if not os.path.exists(self.args.config):
            print(f"CRITICAL: Configuration file '{self.args.config}' not found.")
            sys.exit(1)
            
        self.config.read(self.args.config)

        try:
            # Read variables directly from config.ini
            self.START_DATE = self.config.get('Search', 'StartDate')
            self.END_DATE = self.config.get('Search', 'EndDate')
            self.TARGET_AFFIL = self.config.get('Search', 'Affiliation')

            # Validate the dates pulled from the config
            validate_date(self.START_DATE)
            validate_date(self.END_DATE)

            self.SHERPA_KEY = self.config.get('Authentication', 'SherpaKey', fallback='')
            self.SCOPUS_KEY = self.config.get('Authentication', 'ScopusKey', fallback='')
            self.EMAIL_CONTACT = self.config.get('General', 'Email')
            self.OUTPUT_DIR = self.config.get('General', 'OutputDir', fallback='FedHarv_Output')
            self.CACHE_DIR = os.path.join(self.OUTPUT_DIR, "cache")
            
            self.CHECK_DSPACE = self.config.getboolean('DSpace', 'CheckDuplicates', fallback=False)
            self.DSPACE_API = self.config.get('DSpace', 'ApiUrl', fallback='')
            self.DSPACE_EMAIL = self.config.get('DSpace', 'AdminEmail', fallback='admin@uwindsor.ca')
            self.DSPACE_BIN = self.config.get('DSpace', 'BinPath', fallback='/dspace/bin/dspace')
            self.CROSSREF_TOKEN = self.config.get('Authentication', 'CrossrefPlusToken', fallback='')
            
            self.UNIT_MAP = {}
            if 'Mappings' in self.config:
                for key, val in self.config.items('Mappings'):
                    self.UNIT_MAP[key.lower()] = val
        except configparser.NoOptionError as e:
            print(f"Configuration Error: Missing required setting - {e}")
            sys.exit(1)
        except Exception as e:
            print(f"Configuration Error: {e}")
            sys.exit(1)
        
        if not os.path.exists(self.CACHE_DIR): os.makedirs(self.CACHE_DIR)

    def setup_logging(self):
        logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

    def setup_session(self):
        retry_strategy = Retry(
            total=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504], 
            allowed_methods=["HEAD", "GET", "OPTIONS"]
        )
        adapter = HTTPAdapter(max_retries=retry_strategy, pool_connections=30, pool_maxsize=30)
        self.SESSION = requests.Session()
        self.SESSION.mount("https://", adapter)
        self.SESSION.mount("http://", adapter)
        
        self.HEADERS = {
            "Accept": "application/json", 
            "User-Agent": f"SAF-Harvester/16.1 (mailto:{self.EMAIL_CONTACT})" 
        }
        if self.CROSSREF_TOKEN:
            self.HEADERS["Crossref-Plus-API-Token"] = f"Bearer {self.CROSSREF_TOKEN}"

        self.BROWSER_HEADERS = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Connection": "keep-alive"
        }

    def init_stats(self):
        self.STATS = {
            'openalex_total_est': 0, 'openalex_raw_total': 0, 'openalex_oa_kept': 0,
            'crossref_total_est': 0, 'crossref_raw_total': 0, 'crossref_oa_kept': 0,
            'dspace_duplicates': 0, 
            'processed_gold': 0, 'processed_hybrid': 0, 'processed_diamond': 0,
            'processed_green': 0, 'processed_bronze_closed': 0, 'gold_metadata_only': 0,
            'skipped_no_license': 0, 'skipped_closed': 0, 'skipped_bronze': 0, 'skipped_green': 0,
            'pdf_sources': Counter(), 'pdf_success': 0, 'pdf_fail': 0, 'pdf_skipped_existing': 0,
            'enriched_crossref': 0, 'enriched_fundref': 0, 
            'enriched_datacite': 0, 'enriched_orcid': 0, 'enriched_doaj': 0,
            'dept_breakdown': defaultdict(Counter),
            'dept_publisher_breakdown': defaultdict(Counter),
            'windsor_author_db': defaultdict(lambda: {'depts': set(), 'emails': set(), 'orcids': set()}),
        }
        
        # Create safe, hyphen-free date strings for the filenames
        safe_start = self.START_DATE.replace('-', '')
        safe_end = self.END_DATE.replace('-', '')
        
        # Apply the dates to the CSV filename
        csv_filename = f"harvest_report_{safe_start}_{safe_end}.csv"
        self.csv_file = os.path.join(self.OUTPUT_DIR, csv_filename)
        
        self.ris_file = os.path.join(self.OUTPUT_DIR, "citations.ris") 
        self.author_file = os.path.join(self.OUTPUT_DIR, "windsor_authors.txt")
        self.publisher_report_file = os.path.join(self.OUTPUT_DIR, "department_publisher_report.csv")
        self.patterns_file = "learned_patterns.json"

    # ==========================================
    # ML ENGINE
    # ==========================================
    def load_learned_patterns(self):
        self.patterns = {}
        if os.path.exists(self.patterns_file):
            try:
                with open(self.patterns_file, 'r') as f: self.patterns = json.load(f)
                logging.info(f"Loaded {len(self.patterns)} learned PDF patterns.")
            except: pass

    # ==========================================
    # API FETCHERS
    # ==========================================
    @cached_api_call("doaj")
    @safe_call(default={'is_doaj': False}, log_errors=True)
    def fetch_doaj_data(self, issn):
        if not issn: return {'is_doaj': False}
        url = f"https://doaj.org/api/v2/search/journals/issn:{issn}"
        r = rate_limited_get(url, timeout=10)
        if r.status_code == 200:
            data = r.json()
            if data.get('results') and len(data['results']) > 0:
                result = data['results'][0]
                bibjson = result.get('bibjson', {})
                apc = bibjson.get('apc', {})
                has_apc = apc.get('has_apc')
                is_diamond = (has_apc is False)
                licenses = bibjson.get('license', [])
                license_type = licenses[0].get('type') if licenses else None
                ret = {'is_doaj': True, 'is_diamond': is_diamond, 'license': license_type, 'title': bibjson.get('title')}
                return ret
        return {'is_doaj': False}

    @cached_api_call("unpaywall")
    @safe_call(default={}, log_errors=True)
    def fetch_unpaywall_data(self, doi):
        r = self.SESSION.get(f"https://api.unpaywall.org/v2/{doi}?email={self.EMAIL_CONTACT}", timeout=30)
        data = safe_json_dict(r.json())
        return data

    @cached_api_call("crossref")
    def fetch_crossref_data(self, doi):
        while True:
            try:
                r = self.SESSION.get(f"https://api.crossref.org/works/{doi}", headers=self.HEADERS, timeout=30)
                
                if r.status_code == 200:
                    m = safe_json_dict(r.json()).get('message', {})
                    affs = [a['name'] for auth in ensure_list_of_dicts(m.get('author')) for a in ensure_list_of_dicts(auth.get('affiliation')) if 'name' in a]
                    funders = []
                    for f in ensure_list_of_dicts(m.get('funder')):
                        fname = f.get('name'); awards = f.get('award', [])
                        if fname: funders.append(f"{fname} (Award: {', '.join([str(x) for x in awards if x])})" if awards else fname)
                    authors = []
                    for auth in ensure_list_of_dicts(m.get('author')):
                        family = auth.get('family'); given = auth.get('given')
                        if family and given: authors.append(f"{family}, {given}")
                        elif family: authors.append(family)
                    
                    crossref_pdf = None
                    if 'link' in m:
                        for l in ensure_list_of_dicts(m['link']):
                            if l.get('content-type') == 'application/pdf':
                                crossref_pdf = l.get('URL')
                                if l.get('intended-application') == 'text-mining':
                                    break 
                                    
                    data = {'publisher': m.get('publisher'), 'license': next((l['URL'] for l in ensure_list_of_dicts(m.get('license')) if 'URL' in l), None), 'affiliations': affs, 'funders': funders, 'authors': authors, 'crossref_pdf': crossref_pdf, 'raw_message': m}
                    return data
                
                elif r.status_code == 429:
                    retry_after = int(r.headers.get("Retry-After", 2))
                    logging.warning(f"CrossRef Rate Limit (429). Sleeping {retry_after}s for {doi}...")
                    time.sleep(retry_after)
                    continue 
                
                else:
                    return {}

            except Exception as e:
                logging.error(f"CrossRef Fetch Error for {doi}: {e}")
                return {}
        return {}

    @cached_api_call("sherpa")
    @safe_call(default=None, log_errors=True)
    def fetch_sherpa_policy(self, issn):
        if self.SHERPA_KEY and issn:
            r = rate_limited_get("https://v2.sherpa.ac.uk/cgi/retrieve", params={"item-type": "publication", "format": "json", "limit": "1", "api-key": self.SHERPA_KEY, "filter": json.dumps([["issn", "equals", issn]])}, timeout=10)
            if r.status_code == 200:
                data = safe_json_dict(r.json())
                uri = None
                if data.get("items"): uri = data["items"][0].get("uri")
                return uri
        return None

    @cached_api_call("datacite")
    @safe_call(default={}, log_errors=True)
    def fetch_datacite_data(self, doi):
        r = rate_limited_get(f"https://api.datacite.org/dois/{doi}", headers=self.HEADERS, timeout=10)
        if r.status_code == 200:
            data = safe_json_dict(r.json())
            attrs = deep_get(data, ['data', 'attributes'])
            if attrs:
                abstract = None
                for d in ensure_list_of_dicts(attrs.get('descriptions')):
                    if d.get('descriptionType') == 'Abstract': abstract = d.get('description'); break
                datasets = []
                for r in ensure_list_of_dicts(attrs.get('relatedIdentifiers')):
                    if r.get('resourceTypeGeneral') in ['Dataset', 'Software', 'Model']:
                        datasets.append(f"{r.get('relatedIdentifier')} ({r.get('resourceTypeGeneral')})")
                ret = {'abstract': abstract, 'datasets': datasets}
                return ret
        return {}

    # ==========================================
    # HARVESTING ENGINES
    # ==========================================

    def harvest_openalex(self):
        oa_filter = (
            f"from_publication_date:{self.START_DATE},"
            f"to_publication_date:{self.END_DATE},"
            "type:!dissertation" 
        )
        
        logging.info(f"--- OpenAlex Discovery: {self.TARGET_AFFIL} ({oa_filter}) ---")
        
        # 1. Lookup Institution ID
        inst_id = None
        try:
            logging.info(f"OpenAlex: Searching for institution '{self.TARGET_AFFIL}'...")
            r = rate_limited_get("https://api.openalex.org/institutions", params={'search': self.TARGET_AFFIL}, headers=self.HEADERS)
            if r.status_code == 200:
                data = safe_json_dict(r.json())
                results = data.get('results', [])
                if results: 
                    inst_id = results[0]['id'].split('/')[-1]
                    logging.info(f"OpenAlex: Found Institution ID: {inst_id} ({results[0]['display_name']})")
                else:
                    logging.warning(f"OpenAlex: No institution found matching '{self.TARGET_AFFIL}'. Falling back to string match.")
        except Exception as e: 
            logging.error(f"OpenAlex Institution Search Failed: {e}")

        # 2. Construct Filter
        if inst_id:
            filter_val = f"institutions.id:{inst_id},{oa_filter}"
        else:
            filter_val = f"institutions.display_name:{self.TARGET_AFFIL},{oa_filter}"

        filter_val += ",is_oa:true,type:article|review|book|book-chapter|proceedings-article|report|dataset|preprint|letter|standard"
        params = {'filter': filter_val, 'per-page': 100, 'mailto': self.EMAIL_CONTACT, 'cursor': '*'}
        
        logging.info(f"OpenAlex Filter: {filter_val}")

        results = []
        while True:
            try:
                r = rate_limited_get("https://api.openalex.org/works", params=params)
                if r.status_code != 200: 
                    logging.error(f"OpenAlex API Error {r.status_code}: {r.text}")
                    break
                
                data = safe_json_dict(r.json())
                items = ensure_list_of_dicts(data.get('results'))
                
                if not items: 
                    logging.info("OpenAlex: No more results found (End of Cursor).")
                    break
                    
                for i in items:
                    self.STATS['openalex_raw_total'] += 1
                    raw_status = deep_get(i, ['open_access', 'oa_status'])
                    if raw_status in ['gold', 'hybrid', 'diamond']: self.STATS['openalex_oa_kept'] += 1
                    bib = i.get('biblio', {})
                    pgs = f"{bib.get('first_page')}-{bib.get('last_page')}" if bib.get('last_page') else bib.get('first_page')
                    norm_authors = [{'name': deep_get(s, ['author', 'display_name']), 'orcid': deep_get(s, ['author', 'orcid'])} for s in ensure_list_of_dicts(i.get('authorships'))]
                    
                    abstract_text = reconstruct_openalex_abstract(i.get('abstract_inverted_index'))
                    
                    results.append({
                        'source': 'openalex', 'doi': i.get('doi', '').replace('https://doi.org/', '') if i.get('doi') else None,
                        'title': i.get('display_name'), 'date': i.get('publication_date'),
                        'journal': deep_get(i, ['primary_location', 'source', 'display_name']),
                        'publisher': deep_get(i, ['primary_location', 'source', 'host_organization_name']),
                        'oa_status': normalize_oa_status(raw_status, i.get('doi')),
                        'pdf_url': deep_get(i, ['best_oa_location', 'pdf_url']),
                        'norm_authors': norm_authors, 'raw': i,
                        'volume': bib.get('volume'), 'issue': bib.get('issue'), 'pages': pgs,
                        'issn': deep_get(i, ['primary_location', 'source', 'issn_l']),
                        'doctype': normalize_doctype(i.get('type')),
                        'openalex_abstract': abstract_text
                    })
                cursor = deep_get(data, ['meta', 'next_cursor'])
                params['cursor'] = cursor
                logging.info(f"OpenAlex: Collected {len(results)} records so far...")
                if not cursor: break
            except Exception as e:
                logging.error(f"OpenAlex Loop Error: {e}")
                break
        return results

    def harvest_crossref(self):
        logging.info(f"--- Crossref Discovery: {self.TARGET_AFFIL} ({self.START_DATE} to {self.END_DATE}) ---")
        
        params = {
            'query.affiliation': self.TARGET_AFFIL,
            'filter': f'from-pub-date:{self.START_DATE},until-pub-date:{self.END_DATE}',
            'rows': 100,
            'cursor': '*',
            'mailto': self.EMAIL_CONTACT
        }
        
        results = []
        while True:
            try:
                r = self.SESSION.get(CROSSREF_API_URL, params=params, headers=self.HEADERS, timeout=60)
                
                if r.status_code == 429:
                    retry_after = int(r.headers.get("Retry-After", 2))
                    logging.warning(f"CrossRef Discovery Rate Limit (429). Sleeping {retry_after}s...")
                    time.sleep(retry_after)
                    continue

                if r.status_code != 200:
                    logging.error(f"Crossref API Error {r.status_code}")
                    break
                
                data = safe_json_dict(r.json()).get('message', {})
                items = ensure_list_of_dicts(data.get('items'))
                
                if not items:
                    logging.info("Crossref: No more results found.")
                    break
                
                for i in items:
                    self.STATS['crossref_raw_total'] += 1
                    
                    doi = i.get('DOI')
                    title = i.get('title', ['No Title'])[0]
                    journal = i.get('container-title', [''])[0]
                    publisher = i.get('publisher')
                    
                    norm_authors = []
                    for auth in ensure_list_of_dicts(i.get('author')):
                        name = f"{auth.get('given','')} {auth.get('family','')}".strip()
                        if name: norm_authors.append({'name': name, 'orcid': auth.get('ORCID')})

                    # Crossref handles dates differently, try to extract the print or online date
                    published_date = i.get('published-print', i.get('published-online', {}))
                    date_parts = published_date.get('date-parts', [[self.START_DATE[:4]]])[0]
                    # Pad out to YYYY-MM-DD
                    date_str = f"{date_parts[0]:04d}"
                    if len(date_parts) > 1: date_str += f"-{date_parts[1]:02d}"
                    else: date_str += "-01"
                    if len(date_parts) > 2: date_str += f"-{date_parts[2]:02d}"
                    else: date_str += "-01"


                    results.append({
                        'source': 'crossref', 
                        'doi': doi,
                        'title': title, 
                        'date': date_str, 
                        'journal': journal,
                        'publisher': publisher,
                        'oa_status': "Unknown", 
                        'pdf_url': None, 
                        'norm_authors': norm_authors, 
                        'raw': i,
                        'volume': i.get('volume'), 
                        'issue': i.get('issue'), 
                        'pages': i.get('page'),
                        'issn': i.get('ISSN', [None])[0],
                        'doctype': normalize_doctype(i.get('type'))
                    })
                
                cursor = data.get('next-cursor')
                if not cursor or cursor == params['cursor']: break
                params['cursor'] = cursor
                logging.info(f"Crossref: Collected {len(results)} records so far...")
            except Exception as e:
                logging.error(f"Crossref Loop Error: {e}")
                break
        return results

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

    def is_elsevier_item(self, item, landing_url):
        pub = (item.get('publisher') or '').lower()
        if 'elsevier' in pub: return True
        if item.get('doi') and item['doi'].startswith('10.1016/'): return True
        if landing_url and 'sciencedirect.com' in landing_url: return True
        return False

    def lookup_doi_by_title(self, title):
        if not title: return None
        try:
            params = {'query.title': title, 'rows': 1, 'mailto': self.EMAIL_CONTACT}
            r = rate_limited_get("https://api.crossref.org/works", session=self.SESSION, params=params, headers=self.HEADERS, timeout=10)
            if r.status_code == 200:
                items = safe_json_dict(r.json()).get('message', {}).get('items', [])
                if items:
                     return items[0].get('DOI')
        except: pass
        return None

    def fetch_enrichment_batch(self, item):
        """Fetch all enrichment data in parallel using ThreadPoolExecutor."""
        doi = item.get('doi')
        issn = item.get('issn')
        
        def fetch_cr():
            return self.fetch_crossref_data(doi) if doi else {}
        
        def fetch_upw():
            return self.fetch_unpaywall_data(doi) if doi else {}
        
        def fetch_shp():
            return self.fetch_sherpa_policy(issn)
        
        def fetch_dc():
            return self.fetch_datacite_data(doi) if doi else {}
        
        def fetch_doaj():
            return self.fetch_doaj_data(issn)
        
        # Execute all 5 fetches concurrently with max 4 workers
        results = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            futures = {
                'cr': executor.submit(fetch_cr),
                'upw': executor.submit(fetch_upw),
                'sherpa': executor.submit(fetch_shp),
                'dc': executor.submit(fetch_dc),
                'doaj': executor.submit(fetch_doaj)
            }
            # Collect results as they complete
            for key, future in futures.items():
                try:
                    results[key] = future.result(timeout=30)
                except Exception as e:
                    logging.error(f"Error fetching {key}: {e}")
                    results[key] = {} if key != 'sherpa' else {}
        
        return results['cr'], results['upw'], results['sherpa'], results['dc'], results['doaj']

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
        # 1. OpenAlex
        if item.get('pdf_url') and download_file_stream(item['pdf_url'], temp_pdf, self.SESSION):
            return True, "OpenAlex"
        
        # 2. Unpaywall (Fallback 1)
        if upw.get('best_oa_location', {}).get('url_for_pdf'):
            if download_file_stream(upw['best_oa_location']['url_for_pdf'], temp_pdf, self.SESSION):
                return True, "Unpaywall"
        
        # 3. CrossRef Direct Link (Fallback 2)
        if enrich.get('crossref_pdf'):
            if download_file_stream(enrich['crossref_pdf'], temp_pdf, self.SESSION):
                return True, "CrossRef Direct"
        
        # 4. Scopus API (Elsevier/ScienceDirect)
        if self.SCOPUS_KEY and self.is_elsevier_item(item, landing_url):
            try:
                scopus_headers = {'X-ELS-APIKey': self.SCOPUS_KEY, 'Accept': 'application/pdf'}
                scopus_url = f"https://api.elsevier.com/content/article/doi/{item['doi']}"
                if download_file_stream(scopus_url, temp_pdf, self.SESSION, extra_headers=scopus_headers):
                    return True, "Scopus API"
            except:
                pass
        
        # 5. Publisher Heuristics (from landing URL)
        if landing_url:
            h_url = apply_publisher_heuristics(landing_url, item['doi'], self.patterns)
            if h_url and download_file_stream(h_url, temp_pdf, self.SESSION):
                return True, "Heuristics"
        
        # 6. Meta Tag Scraper (from landing URL)
        if landing_url:
            s_url = fetch_html_meta_pdf_link(landing_url, self.SESSION)
            if s_url and download_file_stream(s_url, temp_pdf, self.SESSION):
                return True, "Meta-Scraper"
        
        # 7. DOI-only Heuristics (no landing page needed)
        if item['doi']:
            h_url = apply_publisher_heuristics(None, item['doi'], self.patterns)
            if h_url and download_file_stream(h_url, temp_pdf, self.SESSION):
                return True, "DOI Heuristics"
        
        # All methods failed - return for Playwright fallback
        return False, "None"

    def process_item(self, idx, item):
        try:
            doctype = item.get('doctype', 'Article')
            
            if check_dspace_duplicate(item['doi'], self.CHECK_DSPACE, self.DSPACE_API):
                with self.locks['stats']: self.STATS['dspace_duplicates'] += 1
                return None

            # --- DOI RECOVERY ---
            if not item.get('doi') and item.get('title'):
                rec_doi = self.lookup_doi_by_title(item['title'])
                if rec_doi: 
                    item['doi'] = rec_doi
                    logging.info(f"Recovered DOI for '{item['title'][:30]}...': {rec_doi}")

            # --- ENRICHMENT (No Scopus) ---
            # NOTE: Always called to satisfy "Metadata Guarantee"
            # All 5 enrichment calls are now executed in parallel
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
            
            if doaj_data.get('is_diamond'): item['oa_status'] = 'Diamond'
            elif item.get('oa_status') in ['Unknown', 'Closed'] and doaj_data.get('is_doaj'): item['oa_status'] = 'Gold'
            
            # --- STRICT FILTERING START ---
            # If status resolves to Closed or Bronze (Publisher Read-Only), skip immediately.
            current_status = item.get('oa_status')
            if current_status in ['Closed', 'Bronze', 'Unknown']:
                with self.locks['stats']: self.STATS['processed_bronze_closed'] += 1
                return # Skip item
            # --- STRICT FILTERING END ---

            final_abstract = dc_data.get('abstract') or item.get('openalex_abstract')

            enrich = {'publisher': cr.get('publisher'), 'license': None, 'sherpa_uri': sherpa, 'abstract': final_abstract, 'funders': cr.get('funders', []), 'authors': cr.get('authors', []), 'crossref_pdf': cr.get('crossref_pdf')}
            
            # Enhanced license lookup with CrossRef fallback for Hybrid/Gold OA
            final_license = deep_get(upw, ['best_oa_location', 'license']) or deep_get(item, ['raw', 'primary_location', 'license', 'url'])
            
            # If no license found and OA status is Hybrid or Gold, check CrossRef license
            if not final_license and item.get('oa_status') in ['Hybrid', 'Gold']:
                crossref_licenses = cr.get('license', [])
                if crossref_licenses:
                    # CrossRef license is typically a list of dicts with 'URL' key
                    if isinstance(crossref_licenses, list) and crossref_licenses:
                        crossref_license = crossref_licenses[0].get('URL') if isinstance(crossref_licenses[0], dict) else crossref_licenses[0]
                    else:
                        crossref_license = crossref_licenses
                    if crossref_license:
                        final_license = crossref_license
                        logging.info(f"Using CrossRef license for {item.get('doi')}: {final_license}")
            
            # Normalize license URI if it's a text license
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
            dept = determine_primary_department(all_affs, self.UNIT_MAP, self.TARGET_AFFIL) 
            
            pub_name = enrich.get('publisher') or item.get('publisher') or "Unknown"

            with self.locks['stats']: 
                self.STATS['dept_breakdown'][dept][item.get('oa_status')] += 1
                self.STATS['dept_publisher_breakdown'][dept][pub_name] += 1

            # --- METADATA & DOWNLOAD ---
            md = map_to_dublin_core(item, enrich, all_affs, self.get_paper_orcids(item, cr))
            
            item_dir = os.path.join(self.OUTPUT_DIR, target_folder, dept, f"item_{str(idx).zfill(3)}")
            os.makedirs(item_dir, exist_ok=True)

            pdf_success = False; source = "None"
            temp_pdf = None
            dest_pdf = None
            if target_folder == "Items_With_PDF":
                dest_pdf = os.path.join(item_dir, "article.pdf")
                temp_pdf = os.path.join(self.OUTPUT_DIR, f"temp_{idx}.pdf")
                
                if os.path.exists(dest_pdf):
                    pdf_success = True; source = "Existing (Cached)"
                    with self.locks['stats']: self.STATS['pdf_skipped_existing'] += 1
                else:
                    landing_url = deep_get(item, ['raw', 'primary_location', 'landing_page_url'])
                    if not landing_url and item['doi']: landing_url = resolve_url(f"https://doi.org/{item['doi']}", self.SESSION)
                    if landing_url and ("doi.org" in landing_url or "handle.net" in landing_url): 
                         landing_url = resolve_url(landing_url, self.SESSION)
                    
                    # Try to download PDF via waterfall priority
                    pdf_success, source = self.fetch_pdf_with_waterfall(item, enrich, upw, landing_url, temp_pdf)
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
        except Exception as e: logging.error(f"Error processing item {idx}: {e}")

    def extract_authors(self, item, cr):
        target_check = "windsor"
        def reg(name, orcid=None, dept=None, email=None):
            if not name: return
            if "," not in name and " " in name: parts = name.split(); name = f"{parts[-1]}, {' '.join(parts[:-1])}" if len(parts) > 1 else name
            with self.locks['author']:
                entry = self.STATS['windsor_author_db'][name]
                if orcid: entry['orcids'].add(orcid)
                if dept: entry['depts'].add(dept)
                if email: entry['emails'].add(email)
                if orcid: self.STATS['enriched_orcid'] += 1
        
        if item['source'] == 'openalex':
            for ship in ensure_list_of_dicts(deep_get(item, ['raw', 'authorships'])):
                is_windsor = False; dept_found = None
                for inst in ensure_list_of_dicts(ship.get('institutions')):
                    if target_check in inst.get('display_name', '').lower(): is_windsor = True
                if is_windsor:
                    raw_aff = ship.get('raw_affiliation_string', '')
                    if raw_aff:
                        match = re.search(r'(Department of [^,]+|School of [^,]+|Faculty of [^,]+)', raw_aff, re.IGNORECASE)
                        if match: dept_found = match.group(1).strip()
                    reg(deep_get(ship, ['author', 'display_name']), deep_get(ship, ['author', 'orcid']).replace('https://orcid.org/', '') if deep_get(ship, ['author', 'orcid']) else None, dept_found, None)

        if cr and cr.get('raw_message'):
            for auth in ensure_list_of_dicts(deep_get(cr, ['raw_message', 'author'])):
                is_windsor = False; dept_found = None
                for aff in ensure_list_of_dicts(auth.get('affiliation')):
                    if target_check in aff.get('name', '').lower(): is_windsor = True; dept_found = aff.get('name')

                if is_windsor:
                    reg(f"{auth.get('family')}, {auth.get('given')}", auth.get('ORCID').replace('http://orcid.org/','').replace('https://orcid.org/','') if auth.get('ORCID') else None, dept_found, None)

    def get_paper_orcids(self, item, cr):
        orcids = set()
        if item['source']=='openalex':
             for ship in ensure_list_of_dicts(deep_get(item, ['raw', 'authorships'])):
                  for inst in ensure_list_of_dicts(ship.get('institutions')):
                        if "windsor" in inst.get('display_name', '').lower():
                             oid = deep_get(ship, ['author', 'orcid'])
                             if oid: orcids.add(oid.replace('https://orcid.org/', ''))
        
        if cr and cr.get('raw_message'):
             for auth in ensure_list_of_dicts(deep_get(cr, ['raw_message', 'author'])):
                  for aff in ensure_list_of_dicts(auth.get('affiliation')):
                        if "windsor" in aff.get('name', '').lower():
                             if auth.get('ORCID'): orcids.add(auth.get('ORCID').replace('http://orcid.org/','').replace('https://orcid.org/',''))

        return list(orcids)


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
        
        if target_folder == "Items_With_PDF":
            if pdf_success:
                robust_move_file(temp_pdf, dest_pdf)
                with self.locks['stats']: 
                    self.STATS['pdf_success'] += 1
                    self.STATS['pdf_sources'][source] += 1
            else:
                robust_remove_file(temp_pdf)
                shutil.rmtree(item_dir)
                target_folder = "Items_Only_Link"
                item_dir = os.path.join(self.OUTPUT_DIR, target_folder, dept, f"item_{str(idx).zfill(3)}")
                os.makedirs(item_dir, exist_ok=True)
                with self.locks['stats']: 
                    self.STATS['pdf_fail'] += 1
                    self.STATS['gold_metadata_only'] += 1

        if target_folder != "Items_With_PDF":
            with open(os.path.join(item_dir, "link.txt"), 'w', encoding='utf-8') as f: 
                f.write(f"DOI: https://doi.org/{item['doi']}")

        write_saf(md, item_dir, "article.pdf" if pdf_success else None)
        
        with self.locks['csv']:
            self.csv_writer.writerow({
                'DOI': item['doi'], 'Title': item['title'], 'ISSN': item.get('issn'), 
                'Doc_Type': doctype, 'Source': item['source'], 'Folder_Type': target_folder, 
                'Dept': dept, 'PDF_Status': 'OK' if pdf_success else 'Missing', 
                'PDF_Source': source, 'OA_Status': item.get('oa_status')
            })
            self.csv_handle.flush()
        
        # --- SPLIT OUTPUT LOGIC ---
        if not pdf_success:
            ris_entry = generate_ris_block(item, enrich)
            with self.locks['ris']:
                self.ris_handle.write(ris_entry + "\n")
                self.ris_handle.flush()

        with self.locks['print']: 
            print(f"[{idx}] {item['title'][:40]}... -> {target_folder}")

    def process_playwright_queue(self):
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(user_agent=self.BROWSER_HEADERS['User-Agent'])
                
                for task in self.playwright_queue:
                    pdf_success = False
                    source = "None"
                    
                    try:
                        page = context.new_page()
                        try:
                            page.goto(task['landing_url'], timeout=60000, wait_until="domcontentloaded")
                            page.wait_for_timeout(5000)
                            
                            pdf_meta = None
                            try: pdf_meta = page.locator('meta[name="citation_pdf_url"]').get_attribute("content", timeout=3000)
                            except: pass
                            
                            if pdf_meta:
                                if not pdf_meta.startswith("http"):
                                    from urllib.parse import urljoin
                                    pdf_meta = urljoin(page.url, pdf_meta)
                                r = context.request.get(pdf_meta)
                                if r.ok and b'%PDF-' in r.body()[:4000]:
                                     with open(task['temp_pdf'], 'wb') as f: f.write(r.body())
                                     pdf_success = True; source = "Browser Meta-Scraper"
                            
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
                                                 pdf_success = True; source = "Browser Click-Bot"
                                        break
                        except: pass
                        finally:
                            page.close()
                    except: pass
                    
                    self.finalize_item(task, pdf_success, source)
                    
                browser.close()
        except BaseException as e:
            logging.error(f"Playwright batch failed: {e}")
            for task in self.playwright_queue:
                self.finalize_item(task, False, "Playwright Error")

    def generate_publisher_report(self):
        with open(self.publisher_report_file, 'w', newline='', encoding='utf-8') as f:
            w = csv.writer(f)
            w.writerow(['Department', 'Publisher', 'Count'])
            for dept, pubs in sorted(self.STATS['dept_publisher_breakdown'].items()):
                for pub, count in pubs.most_common():
                    w.writerow([dept, pub, count])

    def run(self):
        robust_cleanup(self.OUTPUT_DIR)
        os.makedirs(self.OUTPUT_DIR)
        with open(self.ris_file, 'w', encoding='utf-8') as f: pass

        generate_import_scripts(self.OUTPUT_DIR, self.DSPACE_BIN, self.DSPACE_EMAIL)
        
        print(f"Starting concurrent API discovery (OpenAlex + Crossref) from {self.START_DATE} to {self.END_DATE}...")
        
        # NOTE: Updated to pass no arguments since start/end date are now instance attributes
        with ThreadPoolExecutor(max_workers=2) as executor:
            f_oa = executor.submit(self.harvest_openalex)
            f_cr = executor.submit(self.harvest_crossref)
            oa_list = f_oa.result()
            cr_list = f_cr.result()
            
        final_list = self.deduplicate_and_merge(oa_list, cr_list)
        
        self.csv_handle = open(self.csv_file, 'a', newline='', encoding='utf-8')
        self.csv_writer = csv.DictWriter(self.csv_handle, fieldnames=['DOI', 'Title', 'ISSN', 'Doc_Type', 'Source', 'Folder_Type', 'Dept', 'PDF_Status', 'PDF_Source', 'Sherpa_Policy', 'OA_Status', 'Notes'])
        if os.path.getsize(self.csv_file) == 0:
            self.csv_writer.writeheader()
            
        self.ris_handle = open(self.ris_file, 'a', encoding='utf-8')
        self.playwright_queue = []
        
        print(f"Processing {len(final_list)} unique items with 15 threads...")
        with ThreadPoolExecutor(max_workers=15) as executor:
            futures = [executor.submit(self.process_item, idx, item) for idx, item in enumerate(final_list)]
            for f in as_completed(futures): pass
            
        if self.playwright_queue:
            print(f"Processing {len(self.playwright_queue)} items via Playwright fallback...")
            self.process_playwright_queue()
            
        self.csv_handle.close()
        self.ris_handle.close()
        
        self.generate_summary()
        self.generate_publisher_report()

    def generate_summary(self):
        print(f"\n--- HARVEST COMPLETE ---\nUnique Items: {len(self.STATS['dept_breakdown'])}\nPDFs: {self.STATS['pdf_success']}\nSources: {dict(self.STATS['pdf_sources'])}")
        try:
            with open(os.path.join(self.OUTPUT_DIR, "harvest_summary.txt"), 'w', encoding='utf-8') as f: f.write(str(self.STATS))
        except: pass
        
        with open(self.author_file, 'w', encoding='utf-8') as af:
            af.write("Windsor Author Registry\n=======================\n")
            for name, data in sorted(self.STATS['windsor_author_db'].items()):
                depts = "; ".join(sorted(data['depts'])) if data['depts'] else "Unknown"
                emails = ", ".join(sorted([e for e in data['emails'] if e])) if data['emails'] else "N/A"
                orcids = ", ".join(sorted([o for o in data['orcids'] if o])) if data['orcids'] else "N/A"
                af.write(f"{name} : {depts} : {emails} : {orcids}\n")

if __name__ == "__main__":
    harvester = FedHarv()
    harvester.run()
