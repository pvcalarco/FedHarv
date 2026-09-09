# SPDX-License-Identifier: AGPL-3.0-only
import time
import logging
import requests
import json
from urllib3.util.retry import Retry
from requests.adapters import HTTPAdapter
from ratelimit import limits, sleep_and_retry
import backoff

from .config import (
    CALLS, RATE_LIMIT_PERIOD,
    OPENALEX_WORKS_URL, OPENALEX_INST_URL,
    UNPAYWALL_API, SHERPA_API_URL,
    CROSSREF_API_URL, DATACITE_API_URL,
    DOAJ_SEARCH_URL
)
from .utils import (
    safe_call, cached_api_call,
    safe_json_dict, safe_get_dict,
    ensure_list_of_dicts, deep_get,
    normalize_oa_status, normalize_doctype,
    reconstruct_openalex_abstract,
    affiliation_matches_target
)

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

@safe_call(default=False, log_errors=True)
def check_dspace_duplicate(doi, check_enabled, api_url):
    if not check_enabled or not api_url or not doi: return False
    try:
        r = rate_limited_get(f"{api_url}/discover/search/objects", params={"query": f"dc.identifier.doi:{doi}", "dsoType": "ITEM"}, timeout=5)
        total = deep_get(safe_json_dict(r.json()), ['_embedded', 'searchResult', 'page', 'totalElements'])
        if r.status_code == 200 and total and int(total) > 0: return True
    except Exception as e:
        logging.warning(f"Error checking DSpace duplicate for {doi}: {e}")
    return False

class APIClient:
    """Consolidated API Client managing requests session, headers, and individual resource API requests."""
    def __init__(self, config):
        self.config = config
        self.cache_dir = config.CACHE_DIR
        self.setup_session()

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
            "User-Agent": f"SAF-Harvester/16.1 (mailto:{self.config.EMAIL_CONTACT})" 
        }
        if self.config.CROSSREF_TOKEN:
            self.HEADERS["Crossref-Plus-API-Token"] = f"Bearer {self.config.CROSSREF_TOKEN}"

        self.BROWSER_HEADERS = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Connection": "keep-alive"
        }

    @cached_api_call("doaj")
    @safe_call(default={'is_doaj': False}, log_errors=True)
    def fetch_doaj_data(self, issn):
        if not issn: return {'is_doaj': False}
        url = f"{DOAJ_SEARCH_URL}issn:{issn}"
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
        url = f"{UNPAYWALL_API}{doi}"
        params = {"email": self.config.EMAIL_CONTACT}
        r = rate_limited_get(url, session=self.SESSION, params=params, timeout=30)
        if r.status_code == 200:
            return safe_json_dict(r.json())
        return {}

    @cached_api_call("crossref")
    def fetch_crossref_data(self, doi):
        try:
            url = f"{CROSSREF_API_URL}/{doi}"
            r = rate_limited_get(url, session=self.SESSION, headers=self.HEADERS, timeout=30)
            
            if r.status_code == 200:
                m = safe_json_dict(r.json()).get('message', {})
                affs = [a['name'] for auth in ensure_list_of_dicts(m.get('author')) for a in ensure_list_of_dicts(auth.get('affiliation')) if 'name' in a]
                funders = []
                for f in ensure_list_of_dicts(m.get('funder')):
                    fname = f.get('name')
                    awards = f.get('award', [])
                    if fname: funders.append(f"{fname} (Award: {', '.join([str(x) for x in awards if x])})" if awards else fname)
                authors = []
                for auth in ensure_list_of_dicts(m.get('author')):
                    family = auth.get('family')
                    given = auth.get('given')
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
            else:
                return {}

        except Exception as e:
            logging.error(f"CrossRef Fetch Error for {doi}: {e}")
            return {}

    @cached_api_call("sherpa")
    @safe_call(default=None, log_errors=True)
    def fetch_sherpa_policy(self, issn):
        if self.config.SHERPA_KEY and issn:
            r = rate_limited_get(SHERPA_API_URL, params={"item-type": "publication", "format": "json", "limit": "1", "api-key": self.config.SHERPA_KEY, "filter": json.dumps([["issn", "equals", issn]])}, timeout=10)
            if r.status_code == 200:
                data = safe_json_dict(r.json())
                uri = None
                if data.get("items"): uri = data["items"][0].get("uri")
                return uri
        return None

    @cached_api_call("datacite")
    @safe_call(default={}, log_errors=True)
    def fetch_datacite_data(self, doi):
        r = rate_limited_get(f"{DATACITE_API_URL}{doi}", headers=self.HEADERS, timeout=10)
        if r.status_code == 200:
            data = safe_json_dict(r.json())
            attrs = deep_get(data, ['data', 'attributes'])
            if attrs:
                abstract = None
                for d in ensure_list_of_dicts(attrs.get('descriptions')):
                    if d.get('descriptionType') == 'Abstract': abstract = d.get('description'); break
                datasets = []
                for re_id in ensure_list_of_dicts(attrs.get('relatedIdentifiers')):
                    if re_id.get('resourceTypeGeneral') in ['Dataset', 'Software', 'Model']:
                        datasets.append(f"{re_id.get('relatedIdentifier')} ({re_id.get('resourceTypeGeneral')})")
                ret = {'abstract': abstract, 'datasets': datasets}
                return ret
        return {}

    def harvest_openalex(self, start_date, end_date, target_affil, stats_callback=None):
        oa_filter = (
            f"from_publication_date:{start_date},"
            f"to_publication_date:{end_date},"
            "type:!dissertation" 
        )
        
        logging.info(f"--- OpenAlex Discovery: {target_affil} ({oa_filter}) ---")
        
        # 1. Lookup Institution ID
        inst_id = None
        try:
            logging.info(f"OpenAlex: Searching for institution '{target_affil}'...")
            r = rate_limited_get(OPENALEX_INST_URL, params={'search': target_affil}, headers=self.HEADERS)
            if r.status_code == 200:
                data = safe_json_dict(r.json())
                results_list = data.get('results', [])
                if results_list: 
                    inst_id = results_list[0]['id'].split('/')[-1]
                    logging.info(f"OpenAlex: Found Institution ID: {inst_id} ({results_list[0]['display_name']})")
                else:
                    logging.warning(f"OpenAlex: No institution found matching '{target_affil}'. Falling back to string match.")
        except Exception as e: 
            logging.error(f"OpenAlex Institution Search Failed: {e}")

        # 2. Construct Filter
        if inst_id:
            filter_val = f"institutions.id:{inst_id},{oa_filter}"
        else:
            filter_val = f"institutions.display_name:{target_affil},{oa_filter}"

        filter_val += ",is_oa:true,type:article|review|book|book-chapter|proceedings-article|report|dataset|preprint|letter|standard"
        params = {'filter': filter_val, 'per-page': 100, 'mailto': self.config.EMAIL_CONTACT, 'cursor': '*'}
        
        logging.info(f"OpenAlex Filter: {filter_val}")

        results = []
        while True:
            try:
                r = rate_limited_get(OPENALEX_WORKS_URL, params=params)
                if r.status_code != 200: 
                    logging.error(f"OpenAlex API Error {r.status_code}: {r.text}")
                    break
                
                data = safe_json_dict(r.json())
                items = ensure_list_of_dicts(data.get('results'))
                
                if not items: 
                    logging.info("OpenAlex: No more results found (End of Cursor).")
                    break
                    
                for i in items:
                    if stats_callback:
                        stats_callback('openalex_raw_total', 1)
                    raw_status = deep_get(i, ['open_access', 'oa_status'])
                    if raw_status in ['gold', 'hybrid', 'diamond'] and stats_callback: 
                        stats_callback('openalex_oa_kept', 1)
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

    def harvest_crossref(self, start_date, end_date, target_affil, stats_callback=None):
        logging.info(f"--- Crossref Discovery: {target_affil} ({start_date} to {end_date}) ---")
        
        # Enclose target_affil in quotes for Crossref's query.affiliation to perform exact phrase matching
        # rather than matching any paper where an author is affiliated with any "University".
        params = {
            'query.affiliation': f'"{target_affil}"',
            'filter': f'from-pub-date:{start_date},until-pub-date:{end_date}',
            'rows': 100,
            'cursor': '*',
            'mailto': self.config.EMAIL_CONTACT
        }
        
        start_year = int(start_date[:4])
        end_year = int(end_date[:4])
        results = []
        skipped_date = 0
        skipped_affil = 0

        def _get_year(d):
            if not isinstance(d, dict):
                return None
            dp = d.get('date-parts')
            if dp and isinstance(dp, list) and len(dp) > 0 and isinstance(dp[0], list) and len(dp[0]) > 0:
                val = str(dp[0][0])
                return int(val) if val.isdigit() else None
            return None

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
                    if stats_callback:
                        stats_callback('crossref_raw_total', 1)
                    
                    try:
                        doi = i.get('DOI')
                        title = i.get('title', ['No Title'])[0]
                        journal = i.get('container-title', [''])[0]
                        publisher = i.get('publisher')
                        
                        norm_authors = []
                        raw_authors = ensure_list_of_dicts(i.get('author'))
                        for auth in raw_authors:
                            name = f"{auth.get('given','')} {auth.get('family','')}".strip()
                            if name:
                                norm_authors.append({'name': name, 'orcid': auth.get('ORCID')})

                        # Collect all available publication date years
                        years = [y for y in [
                            _get_year(i.get('published-online')),
                            _get_year(i.get('published-print')),
                            _get_year(i.get('issued')),
                            _get_year(i.get('created'))
                        ] if y is not None]

                        # If years are found and none fall within the target year range, skip
                        if years and not any(start_year <= y <= end_year for y in years):
                            logging.debug(f"Crossref: Skipping '{title}' — years {years} outside {start_year}–{end_year}.")
                            skipped_date += 1
                            continue

                        # Determine the best date string representation
                        candidates = [
                            i.get('published-online'),
                            i.get('published-print'),
                            i.get('issued'),
                            i.get('created')
                        ]
                        best_parts = None
                        for cand in candidates:
                            if isinstance(cand, dict):
                                dp = cand.get('date-parts')
                                if dp and isinstance(dp, list) and len(dp) > 0 and isinstance(dp[0], list) and len(dp[0]) > 0:
                                    parts = dp[0]
                                    if str(parts[0]).isdigit() and start_year <= int(parts[0]) <= end_year:
                                        best_parts = parts
                                        break
                                    elif best_parts is None:
                                        best_parts = parts

                        parts = best_parts or [start_date[:4]]
                        year = int(str(parts[0])) if len(parts) > 0 and str(parts[0]).isdigit() else start_year
                        month = int(str(parts[1])) if len(parts) > 1 and str(parts[1]).isdigit() else 1
                        day = int(str(parts[2])) if len(parts) > 2 and str(parts[2]).isdigit() else 1
                        date_str = f"{year:04d}-{month:02d}-{day:02d}"

                        # Affiliation validation:
                        # Extract all affiliation names present in the Crossref work item
                        aff_names = [
                            aff.get('name')
                            for auth in raw_authors
                            for aff in ensure_list_of_dicts(auth.get('affiliation'))
                            if isinstance(aff, dict) and aff.get('name')
                        ]

                        # If all authors have affiliations explicitly present and none match the target, skip.
                        all_authors_have_affils = (
                            len(raw_authors) > 0 and
                            all(len(ensure_list_of_dicts(a.get('affiliation'))) > 0 for a in raw_authors)
                        )
                        if aff_names and all_authors_have_affils and not affiliation_matches_target(aff_names, target_affil):
                            logging.debug(f"Crossref: Skipping '{title}' — authors affiliated elsewhere.")
                            skipped_affil += 1
                            continue

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
                    except Exception as item_err:
                        logging.debug(f"Crossref: Error parsing item {i.get('DOI', 'unknown')}: {item_err}")
                
                cursor = data.get('next-cursor')
                if not cursor or cursor == params['cursor']:
                    break
                params['cursor'] = cursor
                logging.info(f"Crossref: Collected {len(results)} records so far...")
            except Exception as e:
                logging.error(f"Crossref Loop Error: {e}")
                break
        logging.info(
            f"Crossref Discovery complete: {len(results)} records collected "
            f"(skipped {skipped_date} out-of-range, {skipped_affil} non-matching affiliation)."
        )
        return results

    def lookup_doi_by_title(self, title):
        if not title: return None
        try:
            params = {'query.title': title, 'rows': 1, 'mailto': self.config.EMAIL_CONTACT}
            r = rate_limited_get(CROSSREF_API_URL, session=self.SESSION, params=params, headers=self.HEADERS, timeout=10)
            if r.status_code == 200:
                items = safe_json_dict(r.json()).get('message', {}).get('items', [])
                if items:
                     return items[0].get('DOI')
        except Exception as e:
            logging.debug(f"Error looking up DOI by title '{title}': {e}")
        return None
