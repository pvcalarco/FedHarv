from fedharv.export import generate_ris_block, map_to_dublin_core


def _field_values(md, schema, element, qualifier=None):
    return [
        x["value"]
        for x in md
        if x["schema"] == schema
        and x["element"] == element
        and x["qualifier"] == qualifier
    ]


def test_map_to_dublin_core_uses_fallback_doi_from_crossref_raw():
    item = {
        "title": "Sample Work",
        "source": "crossref",
        "raw": {"DOI": "10.1234/example"},
        "oa_status": "Gold",
        "norm_authors": ["Doe, Jane"],
        "doctype": "Article",
    }
    enrich = {"funders": [], "sherpa_uri": None}

    md = map_to_dublin_core(item, enrich, ["Example University"], ["0000-0001-2345-6789"])

    assert "10.1234/example" in _field_values(md, "dc", "identifier", "doi")
    assert "https://doi.org/10.1234/example" in _field_values(md, "dc", "identifier", "uri")


def test_map_to_dublin_core_adds_cc_license_label_from_uri():
    item = {
        "title": "Licensed Work",
        "source": "openalex",
        "doi": "10.9999/abc",
        "oa_status": "Gold",
        "norm_authors": [],
        "doctype": "Article",
    }
    enrich = {
        "license": "https://creativecommons.org/licenses/by/4.0/",
        "funders": [],
        "sherpa_uri": None,
    }

    md = map_to_dublin_core(item, enrich, [], [])

    assert "Creative Commons CC-BY 4.0 International" in _field_values(md, "dc", "rights", None)


def test_generate_ris_block_uses_conf_type_for_conference_and_has_ur():
    item = {
        "doctype": "Conference Paper",
        "title": "Conference Title",
        "norm_authors": ["Smith, Alex"],
        "journal": "Proceedings Journal",
        "date": "2026-05-20",
        "doi": "10.7777/conf",
    }
    enrich = {}

    ris = generate_ris_block(item, enrich)

    assert "TY  - CONF" in ris
    assert "UR  - https://doi.org/10.7777/conf" in ris
    assert "PY  - 2026/05/20" in ris
