from pathlib import Path

import pytest

from fedharv.config import ConfigError, ConfigManager
from fedharv.utils import (
    affiliation_matches_target,
    clean_text,
    deep_get,
    encode_doi_for_url,
    ensure_list_of_dicts,
    load_from_cache,
    normalize_doctype,
    normalize_oa_status,
    save_to_cache,
)


def test_deep_get_handles_list_of_dicts():
    payload = {"outer": [{"inner": {"value": 42}}]}
    assert deep_get(payload, ["outer", "inner", "value"]) == 42


def test_ensure_list_of_dicts_filters_non_dict_items():
    data = [{"a": 1}, "x", 4, {"b": 2}]
    assert ensure_list_of_dicts(data) == [{"a": 1}, {"b": 2}]


def test_normalize_oa_status_for_scielo_prefix_forces_diamond():
    assert normalize_oa_status("gold", doi="10.1590/abcd.123") == "Diamond"


def test_normalize_doctype_maps_book_section_to_book_chapter():
    assert normalize_doctype("book-section") == "Book Chapter"


def test_affiliation_matches_target_is_case_insensitive_and_rejects_generic_substrings():
    assert affiliation_matches_target(["University of Windsor, Faculty of Law"], "university of windsor") is True
    assert affiliation_matches_target(["University"], "University of Windsor") is False
    assert affiliation_matches_target(["Windsor"], "University of Windsor") is False


def test_cache_isolates_cache_dirs_and_persists_empty_dict(tmp_path: Path):
    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b"

    save_to_cache("datacite", "10.1/missing", {}, str(dir_a))
    assert load_from_cache("datacite", "10.1/missing", str(dir_a)) == {}
    assert load_from_cache("datacite", "10.1/missing", str(dir_b)) is None


def test_config_manager_uses_env_fallback_for_blank_ini_and_strips_email_brackets(tmp_path, monkeypatch):
    monkeypatch.setenv("SCOPUS_API_KEY", "env-scopus")
    monkeypatch.setenv("CROSSREF_TOKEN", "env-crossref")
    monkeypatch.setenv("OPENALEX_API_KEY", "env-oa")

    cfg_path = tmp_path / "config.ini"
    cfg_path.write_text(
        f"""
[Authentication]
ScopusKey =
CrossrefPlusToken =
OpenAlexKey =

[General]
Email = [user@example.org]
OutputDir = {(tmp_path / 'out').as_posix()}
CacheDir = {(tmp_path / 'cache').as_posix()}

[Search]
Affiliation = Example University
StartDate = 2026-01-01
EndDate = 2026-01-31
""".strip(),
        encoding="utf-8",
    )

    cfg = ConfigManager(str(cfg_path))
    assert cfg.SCOPUS_KEY == "env-scopus"
    assert cfg.CROSSREF_TOKEN == "env-crossref"
    assert cfg.OPENALEX_KEY == "env-oa"
    assert cfg.EMAIL_CONTACT == "user@example.org"


def test_config_manager_raises_config_error_for_inverted_dates(tmp_path):
    cfg_path = tmp_path / "bad.ini"
    cfg_path.write_text(
        """
[General]
Email = user@example.org

[Search]
Affiliation = Example University
StartDate = 2026-05-01
EndDate = 2026-01-01
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ConfigError):
        ConfigManager(str(cfg_path))


def test_clean_text_preserves_math_and_unescapes_html_entities():
    raw = "<jats:p>Results showed p < 0.05 and q > 0.01 for AT&amp;T&#x2019;s <i>model</i>.</jats:p>"
    assert clean_text(raw) == "Results showed p < 0.05 and q > 0.01 for AT&T\u2019s model."


def test_encode_doi_for_url_encodes_reserved_chars_but_preserves_slash():
    assert encode_doi_for_url("10.1002/a#b?c") == "10.1002/a%23b%3Fc"


def test_config_manager_resolves_relative_output_dir_from_src_parent(tmp_path):
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    cfg_path = src_dir / "config.ini"
    cfg_path.write_text(
        f"""
[General]
Email = user@example.org
OutputDir = Output-{{StartYear}}
CacheDir = {(tmp_path / 'cache').as_posix()}

[Search]
Affiliation = Example University
StartDate = 2026-01-01
EndDate = 2026-01-31
""".strip(),
        encoding="utf-8",
    )
    cfg = ConfigManager(str(cfg_path))
    assert Path(cfg.OUTPUT_DIR) == tmp_path / "Output-2026"
