# SPDX-License-Identifier: AGPL-3.0-only
import os
import re
import json
import logging
import hashlib
import threading
import time
from .config import DOCTYPE_MAPPINGS, OA_STATUS_MAPPINGS, LICENSE_URI_MAPPINGS

cache_lock = threading.Lock()
MEMORY_CACHE = {}

def safe_call(default=None, log_errors=True, log_level=logging.WARNING):
    """Decorator for safe function calls with consistent error handling."""
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

def cached_api_call(cache_prefix, cache_dir_attr='cache_dir'):
    """
    Decorator for API functions that automatically handle caching.
    Expects 'self' to have the attribute specified by cache_dir_attr (e.g. self.cache_dir).
    """
    def decorator(func):
        def wrapper(self, identifier, *args, **kwargs):
            cache_dir = getattr(self, cache_dir_attr, None)
            if not cache_dir:
                # If no cache dir is specified, run without caching
                return func(self, identifier, *args, **kwargs)
                
            # Check cache first
            max_age = getattr(self, 'cache_max_age', None)
            cached = load_from_cache(cache_prefix, identifier, cache_dir, max_age)
            if cached is not None:
                return cached
            
            # Call the actual function
            result = func(self, identifier, *args, **kwargs)
            
            # Cache the result
            if result is not None:
                save_to_cache(cache_prefix, identifier, result, cache_dir)
            
            return result
        return wrapper
    return decorator

def normalize_value(raw_value, mappings, default=None, preprocess_func=None):
    """Generic normalization function that maps raw values to standardized values."""
    if not raw_value:
        return default
    
    processed = preprocess_func(raw_value) if preprocess_func else raw_value
    
    if processed in mappings:
        return mappings[processed]
    
    sorted_patterns = sorted(mappings.keys(), key=len, reverse=True)
    for pattern in sorted_patterns:
        if pattern in processed:
            return mappings[pattern]
    
    return default

def get_cache_path(prefix, identifier, cache_dir):
    safe_id = hashlib.md5(str(identifier).encode('utf-8')).hexdigest()
    return os.path.join(cache_dir, f"{prefix}_{safe_id}.json")

@safe_call(default=None, log_errors=False)
def load_from_cache(prefix, identifier, cache_dir, max_age_seconds=None):
    cache_key = f"{prefix}_{identifier}"
    with cache_lock:
        if cache_key in MEMORY_CACHE:
            return MEMORY_CACHE[cache_key]
    path = get_cache_path(prefix, identifier, cache_dir)
    if os.path.exists(path):
        if max_age_seconds is not None and (time.time() - os.path.getmtime(path)) > max_age_seconds:
            return None  # stale on-disk entry -> treat as a miss
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

def normalize_license_uri(license_raw):
    if not license_raw:
        return None
    if "creativecommons.org" in license_raw:
        return license_raw
    return normalize_value(license_raw, LICENSE_URI_MAPPINGS, default=license_raw, preprocess_func=lambda x: x.lower().strip()) 

def affiliation_matches_target(all_affiliations, target_affil):
    """Return True when any affiliation clearly matches the configured target institution."""
    target_clean = normalize_string(target_affil)
    if not target_clean:
        return False

    for aff in all_affiliations:
        aff_clean = normalize_string(aff)
        if not aff_clean:
            continue
        if target_clean in aff_clean or aff_clean in target_clean:
            return True
    return False

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
