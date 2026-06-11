"""Unit tests for the pure helpers in fedharv/utils.py.

Assertions are derived from the real mapping tables in fedharv/config.py
(DOCTYPE_MAPPINGS, OA_STATUS_MAPPINGS) and the normalize_value() semantics
(lowercase -> exact-key match -> longest-substring match -> default).
"""
import pytest

from fedharv.utils import (
    affiliation_matches_target,
    deep_get,
    determine_primary_department,
    normalize_doctype,
    normalize_oa_status,
    reconstruct_openalex_abstract,
)


# --------------------------------------------------------------------------
# normalize_oa_status
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("gold", "Gold"),
        ("GOLD", "Gold"),        # preprocess_func lowercases
        ("fullgold", "Gold"),
        ("hybrid", "Hybrid"),
        ("green", "Green"),
        ("repository", "Green"),
        ("diamond", "Diamond"),
        ("bronze", "Bronze"),
    ],
)
def test_normalize_oa_status_known(raw, expected):
    assert normalize_oa_status(raw) == expected


@pytest.mark.parametrize("raw", ["closed", None, ""])
def test_normalize_oa_status_default(raw):
    assert normalize_oa_status(raw) == "Open Access"


@pytest.mark.parametrize("raw", ["closed", None])
def test_normalize_oa_status_scielo_doi_forces_diamond(raw):
    # SciELO DOIs (10.1590/) are Diamond regardless of the raw status.
    assert normalize_oa_status(raw, doi="10.1590/abc123") == "Diamond"


# --------------------------------------------------------------------------
# normalize_doctype
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("journal-article", "Article"),
        ("book-chapter", "Book Chapter"),
        ("book-section", "Book Chapter"),
        ("book", "Book"),
        ("BOOK", "Book"),
        ("dataset", "Dataset"),
        ("report", "Technical Report"),
        ("proceedings-article", "Conference Paper"),  # substring "proceeding"
        ("review", "Article"),
    ],
)
def test_normalize_doctype_known(raw, expected):
    assert normalize_doctype(raw) == expected


@pytest.mark.parametrize("raw", ["xyz", None])
def test_normalize_doctype_default(raw):
    assert normalize_doctype(raw) == "Article"


# --------------------------------------------------------------------------
# deep_get
# --------------------------------------------------------------------------
def test_deep_get_nested_hit():
    assert deep_get({"a": {"b": {"c": 3}}}, ["a", "b", "c"]) == 3


def test_deep_get_missing_returns_default():
    assert deep_get({"a": {"b": 1}}, ["a", "z"]) is None
    assert deep_get({"a": {"b": 1}}, ["a", "z"], default=0) == 0


def test_deep_get_descends_into_first_list_element():
    assert deep_get({"a": [{"b": 2}, {"b": 9}]}, ["a", "b"]) == 2


@pytest.mark.parametrize(
    "data,keys",
    [
        ({"a": []}, ["a", "b"]),
        (None, ["a"]),
        ({}, ["a"]),
    ],
)
def test_deep_get_handles_empty_and_none(data, keys):
    assert deep_get(data, keys) is None


# --------------------------------------------------------------------------
# reconstruct_openalex_abstract
# --------------------------------------------------------------------------
def test_reconstruct_abstract_orders_by_position():
    assert reconstruct_openalex_abstract({"world": [1], "Hello": [0]}) == "Hello world"


def test_reconstruct_abstract_repeated_positions():
    assert reconstruct_openalex_abstract({"a": [0, 2], "b": [1]}) == "a b a"


@pytest.mark.parametrize("bad", [None, {}, "not-a-dict", []])
def test_reconstruct_abstract_bad_input_returns_none(bad):
    assert reconstruct_openalex_abstract(bad) is None


# --------------------------------------------------------------------------
# determine_primary_department
# --------------------------------------------------------------------------
UNIT_MAP = {
    "computer science": "Computer Science",
    "biology": "Biology",
    "mechanical engineering": "Mechanical Engineering",
}
TARGET = "University of Windsor"


def test_determine_department_single():
    affils = ["University of Windsor, Department of Computer Science"]
    assert determine_primary_department(affils, UNIT_MAP, TARGET) == "Computer_Science"


def test_determine_department_multiple():
    affils = ["Dept of Computer Science, Windsor", "Dept of Biology, Windsor"]
    assert determine_primary_department(affils, UNIT_MAP, TARGET) == "Multiple"


def test_determine_department_no_match_returns_sanitized_target():
    affils = ["Some Unlisted Institute"]
    assert determine_primary_department(affils, UNIT_MAP, TARGET) == "University_of_Windsor"


def test_determine_department_skips_affiliation_equal_to_target():
    # An affiliation that normalizes to the target itself contributes no folder.
    assert determine_primary_department([TARGET], UNIT_MAP, TARGET) == "University_of_Windsor"


# --------------------------------------------------------------------------
# affiliation_matches_target
# --------------------------------------------------------------------------
def test_affiliation_matches_target_substring_both_ways():
    assert affiliation_matches_target(["University of Windsor"], "Windsor") is True
    assert affiliation_matches_target(["Windsor"], "University of Windsor") is True


def test_affiliation_matches_target_no_match():
    assert affiliation_matches_target(["Harvard University"], "Windsor") is False


@pytest.mark.parametrize(
    "affils,target",
    [
        ([], "Windsor"),
        (["x"], ""),
    ],
)
def test_affiliation_matches_target_empty_inputs(affils, target):
    assert affiliation_matches_target(affils, target) is False
