# SPDX-License-Identifier: AGPL-3.0-only
import datetime
import json
import logging
import threading
import time
from collections import deque
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .config import (
    CALLS,
    CROSSREF_API_URL,
    DATACITE_API_URL,
    DOAJ_SEARCH_URL,
    OPENALEX_INST_URL,
    OPENALEX_WORKS_URL,
    RATE_LIMIT_PERIOD,
    SHERPA_API_URL,
    UNPAYWALL_API,
    __version__,
)
from .utils import (
    affiliation_matches_target,
    cached_api_call,
    deep_get,
    encode_doi_for_url,
    ensure_list_of_dicts,
    normalize_doctype,
    normalize_doi,
    normalize_oa_status,
    reconstruct_openalex_abstract,
    safe_call,
    safe_json_dict,
)


class _PerHostRateLimiter:
    """Thread-safe sliding-window rate limiter isolated per upstream hostname."""

    def __init__(self, max_calls=CALLS, period=RATE_LIMIT_PERIOD):
        self.max_calls = max_calls
        self.period = period
        self._guard = threading.Lock()
        self._buckets = {}

    def _get_bucket(self, host):
        with self._guard:
            bucket = self._buckets.get(host)
            if bucket is None:
                bucket = (threading.Lock(), deque())
                self._buckets[host] = bucket
            return bucket

    def acquire(self, url):
        host = urlparse(url).netloc.lower() or "default"
        lock, timestamps = self._get_bucket(host)
        with lock:
            now = time.monotonic()
            while timestamps and now - timestamps[0] >= self.period:
                timestamps.popleft()
            if len(timestamps) >= self.max_calls:
                wait_for = self.period - (now - timestamps[0])
                if wait_for > 0:
                    time.sleep(wait_for)
                now = time.monotonic()
                while timestamps and now - timestamps[0] >= self.period:
                    timestamps.popleft()
            timestamps.append(now)


_HOST_LIMITER = _PerHostRateLimiter(max_calls=CALLS, period=RATE_LIMIT_PERIOD)


def _parse_retry_after(header_val, fallback_seconds):
    if not header_val:
        return fallback_seconds
    text = str(header_val).strip()
    try:
        parsed = float(text)
        return max(0.5, min(parsed, 60.0))
    except (TypeError, ValueError):
        pass
    try:
        retry_dt = parsedate_to_datetime(text)
        if retry_dt.tzinfo is None:
            retry_dt = retry_dt.replace(tzinfo=datetime.timezone.utc)
        delta = (retry_dt - datetime.datetime.now(datetime.timezone.utc)).total_seconds()
        return max(0.5, min(delta, 60.0))
    except (TypeError, ValueError, OverflowError):
        return fallback_seconds


def rate_limited_get(url, session=None, params=None, headers=None, timeout=30, max_tries=5):
    """
    Per-host rate-limited HTTP GET with bounded retries and Retry-After support.
    """
    requester = session if session is not None else requests
    last_exc = None

    for attempt in range(max_tries):
        _HOST_LIMITER.acquire(url)
        try:
            response = requester.get(url, params=params, headers=headers, timeout=timeout)
        except requests.exceptions.SSLError:
            raise
        except requests.exceptions.RequestException as exc:
            last_exc = exc
            if attempt + 1 >= max_tries:
                raise
            time.sleep(min(2 ** attempt, 30))
            continue

        if response.status_code == 429:
            if attempt + 1 >= max_tries:
                return response
            delay = _parse_retry_after(
                response.headers.get("Retry-After"),
                min(2 ** attempt, 30),
            )
            logging.warning(f"Rate limit (429) from {url}. Sleeping {delay}s (attempt {attempt + 1}/{max_tries})...")
            time.sleep(delay)
            continue

        return response

    if last_exc is not None:
        raise last_exc
    raise requests.exceptions.RequestException(f"Request failed after {max_tries} attempts: {url}")


@safe_call(default=False, log_errors=True)
def check_dspace_duplicate(doi, check_enabled, api_url, session=None):
    if not check_enabled or not api_url or not doi:
        return False
    clean_doi = normalize_doi(doi) or doi
    url = f"{api_url.rstrip('/')}/discover/search/objects"
    r = rate_limited_get(
        url,
        session=session,
        params={"query": f"dc.identifier.doi:{clean_doi}", "dsoType": "ITEM"},
        timeout=5,
    )
    if r.status_code == 200:
        total = deep_get(safe_json_dict(r.json()), ["_embedded", "searchResult", "page", "totalElements"])
        if total and int(total) > 0:
            return True
    return False


def parse_crossref_message(message):
    """Extract normalized metadata fields from a CrossRef work message dict."""
    if not isinstance(message, dict) or not message:
        return {}

    affs = [
        a["name"]
        for auth in ensure_list_of_dicts(message.get("author"))
        for a in ensure_list_of_dicts(auth.get("affiliation"))
        if a.get("name")
    ]
    funders = []
    for f in ensure_list_of_dicts(message.get("funder")):
        fname = f.get("name")
        awards = [str(x) for x in (f.get("award") or []) if x]
        if fname:
            funders.append(f"{fname} (Award: {', '.join(awards)})" if awards else fname)

    authors = []
    for auth in ensure_list_of_dicts(message.get("author")):
        family = auth.get("family")
        given = auth.get("given")
        if family and given:
            authors.append(f"{family}, {given}")
        elif family:
            authors.append(family)
        elif given:
            authors.append(given)

    crossref_pdf = None
    for link in ensure_list_of_dicts(message.get("link")):
        if link.get("content-type") == "application/pdf":
            crossref_pdf = link.get("URL")
            if link.get("intended-application") == "text-mining":
                break

    license_url = next(
        (lic["URL"] for lic in ensure_list_of_dicts(message.get("license")) if lic.get("URL")),
        None,
    )

    return {
        "publisher": message.get("publisher"),
        "license": license_url,
        "affiliations": affs,
        "funders": funders,
        "authors": authors,
        "crossref_pdf": crossref_pdf,
        "raw_message": message,
    }


def _parse_crossref_date(item, fallback_year):
    published_date = item.get("published-print") or item.get("published-online") or {}
    raw_parts = published_date.get("date-parts")
    date_parts = raw_parts[0] if isinstance(raw_parts, list) and raw_parts and isinstance(raw_parts[0], list) else []

    def _int_part(idx, default_val):
        if len(date_parts) > idx and date_parts[idx] is not None:
            text = str(date_parts[idx]).strip()
            if text.isdigit():
                return int(text)
        return default_val

    year = _int_part(0, int(fallback_year))
    month = max(1, min(12, _int_part(1, 1)))
    day = max(1, min(31, _int_part(2, 1)))
    return f"{year:04d}-{month:02d}-{day:02d}"


class APIClient:
    """Consolidated API Client managing requests session, headers, and upstream API queries."""

    def __init__(self, config):
        self.config = config
        self.cache_dir = config.CACHE_DIR
        self.setup_session()

    def setup_session(self):
        retry_strategy = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[500, 502, 503, 504],
            allowed_methods=["HEAD", "GET", "OPTIONS"],
        )
        adapter = HTTPAdapter(max_retries=retry_strategy, pool_connections=30, pool_maxsize=30)
        self.SESSION = requests.Session()
        self.SESSION.mount("https://", adapter)
        self.SESSION.mount("http://", adapter)

        self.HEADERS = {
            "Accept": "application/json",
            "User-Agent": f"FedHarv/{__version__} (mailto:{self.config.EMAIL_CONTACT})",
        }
        if getattr(self.config, "CROSSREF_TOKEN", ""):
            self.HEADERS["Crossref-Plus-API-Token"] = f"Bearer {self.config.CROSSREF_TOKEN}"

        self.BROWSER_HEADERS = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Connection": "keep-alive",
        }

    def close(self):
        if getattr(self, "SESSION", None):
            self.SESSION.close()

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()
        return False

    @safe_call(default={"is_doaj": False}, log_errors=True)
    @cached_api_call("doaj")
    def fetch_doaj_data(self, issn):
        if not issn:
            return {"is_doaj": False}
        url = f"{DOAJ_SEARCH_URL}issn:{issn}"
        r = rate_limited_get(url, session=self.SESSION, headers=self.HEADERS, timeout=10)
        if r.status_code == 200:
            data = safe_json_dict(r.json())
            results = ensure_list_of_dicts(data.get("results"))
            if results:
                bibjson = results[0].get("bibjson") or {}
                apc = bibjson.get("apc") or {}
                has_apc = apc.get("has_apc")
                licenses = ensure_list_of_dicts(bibjson.get("license"))
                license_type = licenses[0].get("type") if licenses else None
                return {
                    "is_doaj": True,
                    "is_diamond": (has_apc is False),
                    "license": license_type,
                    "title": bibjson.get("title"),
                }
        return {"is_doaj": False}

    @safe_call(default={}, log_errors=True)
    @cached_api_call("unpaywall")
    def fetch_unpaywall_data(self, doi):
        if not doi:
            return {}
        url = f"{UNPAYWALL_API}{encode_doi_for_url(doi)}"
        params = {"email": self.config.EMAIL_CONTACT}
        r = rate_limited_get(url, session=self.SESSION, params=params, headers=self.HEADERS, timeout=30)
        if r.status_code == 200:
            return safe_json_dict(r.json())
        return {}

    @safe_call(default={}, log_errors=True)
    @cached_api_call("crossref")
    def fetch_crossref_data(self, doi):
        if not doi:
            return {}
        url = f"{CROSSREF_API_URL}/{encode_doi_for_url(doi)}"
        r = rate_limited_get(url, session=self.SESSION, headers=self.HEADERS, timeout=30)
        if r.status_code == 200:
            message = safe_json_dict(r.json()).get("message", {})
            return parse_crossref_message(message)
        return {}

    @safe_call(default=None, log_errors=True)
    @cached_api_call("sherpa")
    def fetch_sherpa_policy(self, issn):
        if not self.config.SHERPA_KEY or not issn:
            return None
        r = rate_limited_get(
            SHERPA_API_URL,
            session=self.SESSION,
            params={
                "item-type": "publication",
                "format": "json",
                "limit": "1",
                "api-key": self.config.SHERPA_KEY,
                "filter": json.dumps([["issn", "equals", issn]]),
            },
            timeout=10,
        )
        if r.status_code == 200:
            data = safe_json_dict(r.json())
            items = ensure_list_of_dicts(data.get("items"))
            if items:
                return items[0].get("uri")
        return ""

    @safe_call(default={}, log_errors=True)
    @cached_api_call("datacite")
    def fetch_datacite_data(self, doi):
        if not doi:
            return {}
        r = rate_limited_get(
            f"{DATACITE_API_URL}{encode_doi_for_url(doi)}",
            session=self.SESSION,
            headers=self.HEADERS,
            timeout=10,
        )
        if r.status_code == 200:
            data = safe_json_dict(r.json())
            attrs = deep_get(data, ["data", "attributes"])
            if attrs:
                abstract = None
                for desc in ensure_list_of_dicts(attrs.get("descriptions")):
                    if desc.get("descriptionType") == "Abstract":
                        abstract = desc.get("description")
                        break
                datasets = []
                for re_id in ensure_list_of_dicts(attrs.get("relatedIdentifiers")):
                    if re_id.get("resourceTypeGeneral") in ("Dataset", "Software", "Model"):
                        datasets.append(f"{re_id.get('relatedIdentifier')} ({re_id.get('resourceTypeGeneral')})")
                return {"abstract": abstract, "datasets": datasets}
        return {}

    def _openalex_auth_params(self):
        params = {}
        if self.config.EMAIL_CONTACT:
            params["mailto"] = self.config.EMAIL_CONTACT
        if getattr(self.config, "OPENALEX_KEY", ""):
            params["api_key"] = self.config.OPENALEX_KEY
        return params

    def harvest_openalex(self, start_date, end_date, target_affil, stats_callback=None):
        oa_filter = (
            f"from_publication_date:{start_date},"
            f"to_publication_date:{end_date},"
            "type:!dissertation"
        )
        logging.info(f"--- OpenAlex Discovery: {target_affil} ({oa_filter}) ---")

        inst_id = None
        try:
            logging.info(f"OpenAlex: Searching for institution '{target_affil}'...")
            inst_params = {"search": target_affil, **self._openalex_auth_params()}
            r = rate_limited_get(OPENALEX_INST_URL, session=self.SESSION, params=inst_params, headers=self.HEADERS)
            if r.status_code == 200:
                data = safe_json_dict(r.json())
                results_list = ensure_list_of_dicts(data.get("results"))
                if results_list and results_list[0].get("id"):
                    inst_id = results_list[0]["id"].split("/")[-1]
                    logging.info(f"OpenAlex: Found Institution ID: {inst_id} ({results_list[0].get('display_name')})")
                else:
                    logging.warning(f"OpenAlex: No institution found matching '{target_affil}'. Falling back to string match.")
        except Exception as e:
            logging.error(f"OpenAlex Institution Search Failed: {e}")

        if inst_id:
            filter_val = f"institutions.id:{inst_id},{oa_filter}"
        else:
            filter_val = f"institutions.display_name:{target_affil},{oa_filter}"

        filter_val += ",is_oa:true,type:article|review|book|book-chapter|proceedings-article|report|dataset|preprint|letter|standard"
        params = {"filter": filter_val, "per-page": 100, "cursor": "*", **self._openalex_auth_params()}
        logging.info(f"OpenAlex Filter: {filter_val}")

        results = []
        while True:
            try:
                r = rate_limited_get(OPENALEX_WORKS_URL, session=self.SESSION, params=params, headers=self.HEADERS)
                if r.status_code != 200:
                    logging.error(f"OpenAlex API Error {r.status_code}: {r.text[:300]}")
                    break

                data = safe_json_dict(r.json())
                items = ensure_list_of_dicts(data.get("results"))
                if not items:
                    logging.info("OpenAlex: No more results found (End of Cursor).")
                    break

                for item in items:
                    if stats_callback:
                        stats_callback("openalex_raw_total", 1)
                    doi = normalize_doi(item.get("doi"))
                    raw_status = deep_get(item, ["open_access", "oa_status"])
                    oa_status = normalize_oa_status(raw_status, doi)
                    if oa_status in ("Gold", "Hybrid", "Diamond") and stats_callback:
                        stats_callback("openalex_oa_kept", 1)

                    bib = item.get("biblio") or {}
                    first_page = bib.get("first_page")
                    last_page = bib.get("last_page")
                    pgs = f"{first_page}-{last_page}" if (first_page and last_page) else first_page

                    norm_authors = [
                        {
                            "name": deep_get(s, ["author", "display_name"]),
                            "orcid": deep_get(s, ["author", "orcid"]),
                        }
                        for s in ensure_list_of_dicts(item.get("authorships"))
                    ]

                    results.append({
                        "source": "openalex",
                        "doi": doi,
                        "title": item.get("display_name") or "Untitled",
                        "date": item.get("publication_date"),
                        "journal": deep_get(item, ["primary_location", "source", "display_name"]),
                        "publisher": deep_get(item, ["primary_location", "source", "host_organization_name"]),
                        "oa_status": oa_status,
                        "pdf_url": deep_get(item, ["best_oa_location", "pdf_url"]),
                        "norm_authors": norm_authors,
                        "raw": item,
                        "volume": bib.get("volume"),
                        "issue": bib.get("issue"),
                        "pages": pgs,
                        "issn": deep_get(item, ["primary_location", "source", "issn_l"]),
                        "doctype": normalize_doctype(item.get("type")),
                        "openalex_abstract": reconstruct_openalex_abstract(item.get("abstract_inverted_index")),
                    })

                cursor = deep_get(data, ["meta", "next_cursor"])
                logging.info(f"OpenAlex: Collected {len(results)} records so far...")
                if not cursor or cursor == params["cursor"]:
                    break
                params["cursor"] = cursor
            except Exception as e:
                logging.error(f"OpenAlex Loop Error: {e}")
                break
        return results

    def harvest_crossref(self, start_date, end_date, target_affil, stats_callback=None):
        logging.info(f"--- Crossref Discovery: {target_affil} ({start_date} to {end_date}) ---")

        # Enclose target_affil in quotes for exact phrase matching on Crossref's query.affiliation.
        params = {
            "query.affiliation": f'"{target_affil}"',
            "filter": f"from-pub-date:{start_date},until-pub-date:{end_date}",
            "rows": 100,
            "cursor": "*",
            "mailto": self.config.EMAIL_CONTACT,
        }

        start_year = int(start_date[:4])
        end_year = int(end_date[:4])
        results = []
        skipped_date = 0
        skipped_affil = 0

        def _get_year(date_obj):
            if not isinstance(date_obj, dict):
                return None
            dp = date_obj.get("date-parts")
            if isinstance(dp, list) and dp and isinstance(dp[0], list) and dp[0]:
                val = str(dp[0][0])
                return int(val) if val.isdigit() else None
            return None

        while True:
            try:
                r = rate_limited_get(
                    CROSSREF_API_URL,
                    session=self.SESSION,
                    params=params,
                    headers=self.HEADERS,
                    timeout=60,
                )
                if r.status_code != 200:
                    logging.error(f"Crossref API Error {r.status_code}")
                    break

                data = safe_json_dict(r.json()).get("message") or {}
                items = ensure_list_of_dicts(data.get("items"))
                if not items:
                    logging.info("Crossref: No more results found.")
                    break

                for item in items:
                    if stats_callback:
                        stats_callback("crossref_raw_total", 1)

                    try:
                        doi = normalize_doi(item.get("DOI"))
                        titles = item.get("title") or []
                        title = titles[0] if isinstance(titles, list) and titles else "No Title"
                        journals = item.get("container-title") or []
                        journal = journals[0] if isinstance(journals, list) and journals else ""
                        issns = item.get("ISSN") or []
                        issn = issns[0] if isinstance(issns, list) and issns else None

                        raw_authors = ensure_list_of_dicts(item.get("author"))
                        norm_authors = []
                        for auth in raw_authors:
                            name = f"{auth.get('given', '')} {auth.get('family', '')}".strip()
                            if name:
                                norm_authors.append({"name": name, "orcid": auth.get("ORCID")})

                        date_candidates = [
                            item.get("published-online"),
                            item.get("published-print"),
                            item.get("issued"),
                            item.get("created"),
                        ]
                        years = [y for y in (_get_year(c) for c in date_candidates) if y is not None]
                        if years and not any(start_year <= y <= end_year for y in years):
                            logging.debug(
                                f"Crossref: Skipping '{title}' — years {years} outside {start_year}-{end_year}."
                            )
                            skipped_date += 1
                            continue

                        best_parts = None
                        for cand in date_candidates:
                            if isinstance(cand, dict):
                                dp = cand.get("date-parts")
                                if isinstance(dp, list) and dp and isinstance(dp[0], list) and dp[0]:
                                    parts = dp[0]
                                    if str(parts[0]).isdigit() and start_year <= int(parts[0]) <= end_year:
                                        best_parts = parts
                                        break
                                    if best_parts is None:
                                        best_parts = parts

                        if best_parts:
                            year = int(str(best_parts[0])) if str(best_parts[0]).isdigit() else start_year
                            month = (
                                max(1, min(12, int(str(best_parts[1]))))
                                if len(best_parts) > 1 and str(best_parts[1]).isdigit()
                                else 1
                            )
                            day = (
                                max(1, min(31, int(str(best_parts[2]))))
                                if len(best_parts) > 2 and str(best_parts[2]).isdigit()
                                else 1
                            )
                            date_str = f"{year:04d}-{month:02d}-{day:02d}"
                        else:
                            date_str = _parse_crossref_date(item, start_date[:4])

                        aff_names = [
                            aff["name"]
                            for auth in raw_authors
                            for aff in ensure_list_of_dicts(auth.get("affiliation"))
                            if aff.get("name")
                        ]
                        all_authors_have_affils = bool(raw_authors) and all(
                            len(ensure_list_of_dicts(a.get("affiliation"))) > 0 for a in raw_authors
                        )
                        if (
                            aff_names
                            and all_authors_have_affils
                            and not affiliation_matches_target(aff_names, target_affil)
                        ):
                            logging.debug(f"Crossref: Skipping '{title}' — authors affiliated elsewhere.")
                            skipped_affil += 1
                            continue

                        results.append({
                            "source": "crossref",
                            "doi": doi,
                            "title": title,
                            "date": date_str,
                            "journal": journal,
                            "publisher": item.get("publisher"),
                            "oa_status": "Unknown",
                            "pdf_url": None,
                            "norm_authors": norm_authors,
                            "raw": item,
                            "volume": item.get("volume"),
                            "issue": item.get("issue"),
                            "pages": item.get("page"),
                            "issn": issn,
                            "doctype": normalize_doctype(item.get("type")),
                        })
                    except Exception as item_err:
                        logging.debug(f"Crossref: Error parsing item {item.get('DOI', 'unknown')}: {item_err}")

                cursor = data.get("next-cursor")
                if not cursor or cursor == params["cursor"]:
                    break
                params["cursor"] = cursor
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
        if not title:
            return None
        try:
            params = {"query.title": title, "rows": 1, "mailto": self.config.EMAIL_CONTACT}
            r = rate_limited_get(
                CROSSREF_API_URL,
                session=self.SESSION,
                params=params,
                headers=self.HEADERS,
                timeout=10,
            )
            if r.status_code == 200:
                items = ensure_list_of_dicts(safe_json_dict(r.json()).get("message", {}).get("items"))
                if items:
                    return normalize_doi(items[0].get("DOI"))
        except Exception as e:
            logging.debug(f"Error looking up DOI by title '{title}': {e}")
        return None
