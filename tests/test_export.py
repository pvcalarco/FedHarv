"""Unit tests for the pure builders in fedharv/export.py."""
import pytest

from fedharv.export import generate_ris_block, map_to_dublin_core


def _index(md):
    """Index a Dublin Core field list by (schema, element, qualifier) -> [values]."""
    out = {}
    for f in md:
        out.setdefault((f["schema"], f["element"], f["qualifier"]), []).append(f["value"])
    return out


# --------------------------------------------------------------------------
# map_to_dublin_core
# --------------------------------------------------------------------------
def test_map_to_dublin_core_basic_fields():
    item = {
        "title": "A Study of Things",
        "date": "2023-05-01",
        "doi": "10.1234/abcd",
        "doctype": "Article",
        "journal": "Journal of Things",
        "norm_authors": [{"name": "Doe, Jane"}, "Smith, John"],
    }
    md = _index(map_to_dublin_core(item, {}, all_affils=[], windsor_orcids=[]))

    assert md[("dc", "title", None)] == ["A Study of Things"]
    assert md[("dc", "date", "issued")] == ["2023-05-01"]
    assert md[("dc", "identifier", "doi")] == ["10.1234/abcd"]
    assert md[("dc", "identifier", "uri")] == ["https://doi.org/10.1234/abcd"]
    assert md[("dc", "language", "iso")] == ["en_CA"]
    assert md[("dc", "type", None)] == ["Article"]
    assert md[("dc", "contributor", "author")] == ["Doe, Jane", "Smith, John"]
    assert md[("oaire", "citation", "title")] == ["Journal of Things"]


def test_map_to_dublin_core_cc_license_name_and_uri():
    item = {"title": "X", "doctype": "Article"}
    enrich = {"license": "https://creativecommons.org/licenses/by/4.0/"}
    md = _index(map_to_dublin_core(item, enrich, [], []))

    assert md[("dc", "rights", None)] == ["Creative Commons CC-BY 4.0 International"]
    assert md[("dc", "rights", "uri")] == ["https://creativecommons.org/licenses/by/4.0/"]


def test_map_to_dublin_core_diamond_defaults_to_cc_by():
    item = {"title": "X", "doctype": "Article", "oa_status": "Diamond"}
    md = _index(map_to_dublin_core(item, {}, [], []))

    assert md[("dc", "rights", None)] == ["Creative Commons CC-BY 4.0 International"]
    assert md[("dc", "rights", "uri")] == ["https://creativecommons.org/licenses/by/4.0/"]


def test_map_to_dublin_core_doi_fallback_from_raw_crossref():
    item = {
        "title": "X",
        "doctype": "Article",
        "source": "crossref",
        "raw": {"DOI": "10.9999/fallback"},
    }
    md = _index(map_to_dublin_core(item, {}, [], []))

    assert md[("dc", "identifier", "doi")] == ["10.9999/fallback"]
    assert md[("dc", "identifier", "uri")] == ["https://doi.org/10.9999/fallback"]


def test_map_to_dublin_core_orcids():
    item = {"title": "X", "doctype": "Article"}
    md = _index(map_to_dublin_core(item, {}, [], ["0000-0001", "0000-0002"]))

    assert md[("person", "identifier", "orcid")] == ["0000-0001", "0000-0002"]


# --------------------------------------------------------------------------
# generate_ris_block
# --------------------------------------------------------------------------
def test_generate_ris_block_article_full():
    item = {
        "title": "A Study",
        "doctype": "Article",
        "journal": "J. Things",
        "date": "2023-05-01",
        "volume": "12",
        "issue": "3",
        "pages": "100-110",
        "issn": "1234-5678",
        "doi": "10.1234/abcd",
        "norm_authors": [{"name": "Doe, Jane"}, "Smith, John"],
    }
    enrich = {"abstract": "An abstract.", "publisher": "ACME"}
    ris = generate_ris_block(item, enrich)
    lines = ris.split("\n")

    assert lines[0] == "TY  - JOUR"
    for expected in [
        "TI  - A Study",
        "AU  - Doe, Jane",
        "AU  - Smith, John",
        "JO  - J. Things",
        "PY  - 2023/05/01",   # dashes become slashes
        "VL  - 12",
        "IS  - 3",
        "SP  - 100",          # pages split on '-'
        "EP  - 110",
        "SN  - 1234-5678",
        "DO  - 10.1234/abcd",
        "AB  - An abstract.",
        "UR  - https://doi.org/10.1234/abcd",
        "PB  - ACME",
    ]:
        assert expected in lines
    assert ris.rstrip().endswith("ER  -")


@pytest.mark.parametrize(
    "doctype,ty",
    [
        ("Book Chapter", "CHAP"),
        ("Book", "BOOK"),
        ("Conference Paper", "CONF"),
        ("Dataset", "DATA"),
        ("Technical Report", "RPRT"),
        ("Article", "JOUR"),
    ],
)
def test_generate_ris_block_ty_by_doctype(doctype, ty):
    item = {"title": "X", "doctype": doctype, "journal": "J", "norm_authors": []}
    ris = generate_ris_block(item, {})
    assert ris.split("\n")[0] == f"TY  - {ty}"
