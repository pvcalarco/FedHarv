from fedharv.utils import (
    affiliation_matches_target,
    deep_get,
    ensure_list_of_dicts,
    normalize_doctype,
    normalize_oa_status,
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


def test_affiliation_matches_target_is_fuzzy_and_case_insensitive():
    affs = ["University of Windsor, Faculty of Law"]
    assert affiliation_matches_target(affs, "university of windsor") is True
