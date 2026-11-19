# SPDX-License-Identifier: AGPL-3.0-only
import functools
import hashlib
import html
import json
import logging
import os
import re
import tempfile
import threading
from collections import OrderedDict
from urllib.parse import quote, urlparse

from .config import DOCTYPE_MAPPINGS, LICENSE_URI_MAPPINGS, OA_STATUS_MAPPINGS

cache_lock = threading.Lock()
MEMORY_CACHE_MAX_SIZE = 10000
MEMORY_CACHE = OrderedDict()

_SORTED_DOCTYPE_KEYS = tuple(sorted(DOCTYPE_MAPPINGS.keys(), key=len, reverse=True))
_SORTED_OA_STATUS_KEYS = tuple(sorted(OA_STATUS_MAPPINGS.keys(), key=len, reverse=True))
_SORTED_LICENSE_KEYS = tuple(sorted(LICENSE_URI_MAPPINGS.keys(), key=len, reverse=True))


def safe_call(default=None, log_errors=True, log_level=logging.WARNING):
    """Decorator for safe function calls with consistent error handling."""
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                if log_errors:
                    logging.log(log_level, f"Error in {func.__name__}: {e}")
                return default() if callable(default) else (
                    default.copy() if isinstance(default, (dict, list, set)) else default
                )
        return wrapper
    return decorator


def cached_api_call(cache_prefix, cache_dir_attr="cache_dir"):
    """
    Decorator for API functions that automatically handles memory + disk caching.
    Place inside @safe_call so transient exceptions are never cached, while clean
    API responses (including empty 404 dicts) are cached.
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(self, identifier, *args, **kwargs):
            if not identifier:
                return func(self, identifier, *args, **kwargs)

            cache_dir = getattr(self, cache_dir_attr, None)
            if not cache_dir:
                return func(self, identifier, *args, **kwargs)

            cached = load_from_cache(cache_prefix, identifier, cache_dir)
            if cached is not None:
                return cached

            result = func(self, identifier, *args, **kwargs)
            if result is not None:
                save_to_cache(cache_prefix, identifier, result, cache_dir)
            return result
        return wrapper
    return decorator


def normalize_value(raw_value, mappings, default=None, preprocess_func=None, sorted_keys=None):
    """Generic normalization function that maps raw values to standardized values."""
    if not raw_value:
        return default

    processed = preprocess_func(raw_value) if preprocess_func else raw_value

    if processed in mappings:
        return mappings[processed]

    keys = sorted_keys if sorted_keys is not None else sorted(mappings.keys(), key=len, reverse=True)
    for pattern in keys:
        if pattern in processed:
            return mappings[pattern]

    return default


def get_cache_path(prefix, identifier, cache_dir):
    safe_id = hashlib.md5(str(identifier).encode("utf-8"), usedforsecurity=False).hexdigest()
    return os.path.join(cache_dir, f"{prefix}_{safe_id}.json")


def _memory_cache_key(prefix, identifier, cache_dir):
    return (os.path.abspath(str(cache_dir)), prefix, str(identifier))


def _memory_cache_put(key, value):
    with cache_lock:
        MEMORY_CACHE[key] = value
        MEMORY_CACHE.move_to_end(key)
        while len(MEMORY_CACHE) > MEMORY_CACHE_MAX_SIZE:
            MEMORY_CACHE.popitem(last=False)


def load_from_cache(prefix, identifier, cache_dir):
    cache_key = _memory_cache_key(prefix, identifier, cache_dir)
    with cache_lock:
        if cache_key in MEMORY_CACHE:
            MEMORY_CACHE.move_to_end(cache_key)
            return MEMORY_CACHE[cache_key]

    path = get_cache_path(prefix, identifier, cache_dir)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        _memory_cache_put(cache_key, data)
        return data
    except (OSError, json.JSONDecodeError) as exc:
        logging.debug(f"Cache read failed for {path}: {exc}")
        return None


def save_to_cache(prefix, identifier, data, cache_dir):
    if data is None:
        return
    cache_key = _memory_cache_key(prefix, identifier, cache_dir)
    _memory_cache_put(cache_key, data)

    path = get_cache_path(prefix, identifier, cache_dir)
    tmp_path = None
    try:
        os.makedirs(cache_dir, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=cache_dir, prefix=f".{prefix}_", suffix=".tmp", delete=False
        ) as tmp_file:
            tmp_path = tmp_file.name
            json.dump(data, tmp_file)
        os.replace(tmp_path, path)
    except OSError as exc:
        logging.debug(f"Cache write failed for {path}: {exc}")
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def is_safe_http_url(url):
    """Return True if url is a valid http(s) URL with a non-empty network location."""
    if not url or not isinstance(url, str):
        return False
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return False
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def normalize_doi(doi):
    """Normalize a DOI string to lowercase canonical form without URL resolver prefixes."""
    if not doi:
        return None
    cleaned = str(doi).strip()
    cleaned = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^doi:\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.strip().lower()
    return cleaned or None


def encode_doi_for_url(doi):
    """Percent-encode special URL characters in a DOI while keeping '/' path separators."""
    return quote(str(doi), safe="/")


_HTML_TAG_RE = re.compile(r"</?[a-zA-Z][^>]*>")


def clean_text(text):
    if text:
        cleaned = _HTML_TAG_RE.sub("", str(text))
        cleaned = html.unescape(cleaned)
        cleaned = _HTML_TAG_RE.sub("", cleaned)
        return cleaned.encode("utf-8", "ignore").decode("utf-8").strip()
    return None


def normalize_string(text):
    if not text:
        return ""
    return re.sub(r"[^a-z0-9]", "", str(text).lower())


def sanitize_filename(name):
    if not name:
        return "General_Output"
    name = " ".join(str(name).split())
    clean = re.sub(r"[^\w\s-]", "", name)
    clean = clean.strip().replace(" ", "_")
    return clean[:64] or "General_Output"


def clean_abstract(text):
    if not text:
        return None
    text = clean_text(text)
    if not text:
        return None
    text = re.sub(r"^Copyright © \d{4}.*?\. ", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^© \d{4}.*?\. ", "", text, flags=re.IGNORECASE)
    text = re.sub(r"All rights reserved\.$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^Abstract\s*", "", text, flags=re.IGNORECASE)
    return text.strip()


@safe_call(default=None, log_errors=False)
def reconstruct_openalex_abstract(inverted_index):
    if not inverted_index or not isinstance(inverted_index, dict):
        return None
    word_list = []
    for word, positions in inverted_index.items():
        for pos in positions:
            word_list.append((pos, word))
    word_list.sort()
    return " ".join(w[1] for w in word_list)


def deep_get(d, keys, default=None):
    current = d
    for key in keys:
        if isinstance(current, list) and len(current) > 0:
            if isinstance(current[0], dict):
                current = current[0]
            else:
                return default
        if isinstance(current, dict):
            current = current.get(key)
        else:
            return default
        if current is None:
            return default
    return current


def safe_json_dict(data):
    if isinstance(data, dict):
        return data
    if isinstance(data, list) and len(data) > 0 and isinstance(data[0], dict):
        return data[0]
    return {}


def ensure_list_of_dicts(data):
    if data is None:
        return []
    if isinstance(data, dict):
        return [data]
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    return []


def normalize_doctype(raw_type):
    return normalize_value(
        raw_type,
        DOCTYPE_MAPPINGS,
        default="Article",
        preprocess_func=str.lower,
        sorted_keys=_SORTED_DOCTYPE_KEYS,
    )


def normalize_oa_status(raw_status, doi=None):
    if doi and "10.1590/" in doi:
        return "Diamond"
    return normalize_value(
        raw_status,
        OA_STATUS_MAPPINGS,
        default="Open Access",
        preprocess_func=str.lower,
        sorted_keys=_SORTED_OA_STATUS_KEYS,
    )


def normalize_license_uri(license_raw):
    if not license_raw:
        return None
    if "creativecommons.org" in license_raw:
        return license_raw
    return normalize_value(
        license_raw,
        LICENSE_URI_MAPPINGS,
        default=license_raw,
        preprocess_func=lambda x: x.lower().strip(),
        sorted_keys=_SORTED_LICENSE_KEYS,
    )


def affiliation_matches_target(all_affiliations, target_affil):
    """Return True when any affiliation contains the configured target institution name."""
    target_clean = normalize_string(target_affil)
    if not target_clean:
        return False

    for aff in all_affiliations:
        aff_clean = normalize_string(aff)
        if aff_clean and target_clean in aff_clean:
            return True
    return False


def determine_primary_department(all_affiliations, unit_map, target_affil, sorted_keys=None):
    target_clean = normalize_string(target_affil)
    found_folders = set()
    keys = sorted_keys if sorted_keys is not None else sorted(unit_map.keys(), key=len, reverse=True)
    for aff in all_affiliations:
        if normalize_string(aff) == target_clean:
            continue
        aff_clean = aff.lower()
        for key in keys:
            if key in aff_clean:
                found_folders.add(sanitize_filename(unit_map[key]))
                break
    if len(found_folders) > 1:
        return "Multiple"
    if len(found_folders) == 1:
        return next(iter(found_folders))
    return sanitize_filename(target_affil)


def extract_all_affiliations(item, crossref_data):
    affils = set()
    if item.get("source") == "openalex":
        for ship in ensure_list_of_dicts(deep_get(item, ["raw", "authorships"])):
            for inst in ensure_list_of_dicts(ship.get("institutions")):
                if inst.get("display_name"):
                    affils.add(inst["display_name"])
            for raw_aff in (ship.get("raw_affiliation_strings") or []):
                if isinstance(raw_aff, str) and raw_aff.strip():
                    affils.add(raw_aff.strip())
            raw_single = ship.get("raw_affiliation_string")
            if isinstance(raw_single, str) and raw_single.strip():
                affils.add(raw_single.strip())
    for a in (crossref_data.get("affiliations") or []):
        if a:
            affils.add(a)
    return list(affils)
