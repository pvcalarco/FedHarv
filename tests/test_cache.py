"""Tests for the on-disk API cache (fedharv/utils.py).

These prove the cache survives a *second run*: the whole point of P1 #1. A run is
a fresh process with an empty MEMORY_CACHE, so we clear MEMORY_CACHE between the
save and the load to force the on-disk path that previously got wiped.
"""
import os

import pytest

import fedharv.utils as u
from fedharv.utils import get_cache_path, load_from_cache, save_to_cache


@pytest.fixture(autouse=True)
def _clear_memory_cache():
    """Isolate the disk path: start each test with an empty in-memory cache."""
    u.MEMORY_CACHE.clear()
    yield
    u.MEMORY_CACHE.clear()


def test_save_then_load_round_trips(tmp_path):
    cache_dir = str(tmp_path)
    save_to_cache("crossref", "10.1/doi", {"title": "X"}, cache_dir)
    assert load_from_cache("crossref", "10.1/doi", cache_dir) == {"title": "X"}


def test_load_hits_disk_across_a_simulated_second_run(tmp_path):
    cache_dir = str(tmp_path)
    save_to_cache("unpaywall", "10.2/doi", {"oa": True}, cache_dir)

    # Simulate a brand-new process (the next run): in-memory cache is empty,
    # but the JSON file on disk must still be found and reused.
    u.MEMORY_CACHE.clear()
    assert os.path.exists(get_cache_path("unpaywall", "10.2/doi", cache_dir))
    assert load_from_cache("unpaywall", "10.2/doi", cache_dir) == {"oa": True}


def test_fresh_entry_within_ttl_is_returned(tmp_path):
    cache_dir = str(tmp_path)
    save_to_cache("sherpa", "1234-5678", {"policy": "ok"}, cache_dir)
    u.MEMORY_CACHE.clear()
    # Generous TTL -> a just-written entry is still fresh.
    assert load_from_cache("sherpa", "1234-5678", cache_dir, max_age_seconds=3600) == {"policy": "ok"}


def test_stale_entry_beyond_ttl_is_a_miss(tmp_path):
    cache_dir = str(tmp_path)
    save_to_cache("datacite", "10.3/doi", {"k": "v"}, cache_dir)
    u.MEMORY_CACHE.clear()

    # Back-date the file well beyond the TTL.
    path = get_cache_path("datacite", "10.3/doi", cache_dir)
    old = os.path.getmtime(path) - 10_000
    os.utime(path, (old, old))

    assert load_from_cache("datacite", "10.3/doi", cache_dir, max_age_seconds=3600) is None


def test_no_ttl_never_expires(tmp_path):
    cache_dir = str(tmp_path)
    save_to_cache("doaj", "1111-2222", {"in": "doaj"}, cache_dir)
    u.MEMORY_CACHE.clear()

    path = get_cache_path("doaj", "1111-2222", cache_dir)
    old = os.path.getmtime(path) - 10_000_000
    os.utime(path, (old, old))

    # max_age_seconds=None (the default) means entries never expire.
    assert load_from_cache("doaj", "1111-2222", cache_dir) == {"in": "doaj"}
