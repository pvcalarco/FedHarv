import csv

from fedharv.export import (
    MetadataExporter,
    generate_import_scripts,
    generate_ris_block,
    map_to_dublin_core,
    write_saf,
)


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


def test_map_to_dublin_core_does_not_fabricate_cc_by_for_diamond_without_license():
    item = {
        "title": "Diamond Work",
        "source": "openalex",
        "doi": "10.1590/abc",
        "oa_status": "Diamond",
        "norm_authors": [],
        "doctype": "Article",
    }
    enrich = {"license": None, "funders": [], "sherpa_uri": None}

    md = map_to_dublin_core(item, enrich, [], [])

    assert _field_values(md, "dc", "rights", None) == ["Diamond"]
    assert _field_values(md, "dc", "rights", "uri") == []


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


def test_generate_import_scripts_quotes_shell_arguments_and_writes_lf(tmp_path):
    dept_dir = tmp_path / "Items_With_PDF" / "Dept $(whoami) 'quoted'" / "item_001"
    dept_dir.mkdir(parents=True)

    generate_import_scripts(
        str(tmp_path),
        "/dspace/bin/dspace",
        "admin@example.org",
        default_collection_handle="123456789/0",
    )

    script_bytes = (tmp_path / "import_batch.sh").read_bytes()
    assert b"\r\n" not in script_bytes
    script_text = script_bytes.decode("utf-8")
    assert "set -euo pipefail" in script_text
    assert "'--source=" not in script_text  # --source= precedes quoted path
    assert "'\"'\"'quoted'\"'\"'" in script_text


def test_write_saf_writes_utf8_xml_declaration(tmp_path):
    md = [{"schema": "dc", "element": "title", "qualifier": None, "value": "Café Study"}]
    write_saf(md, str(tmp_path), "article.pdf")

    xml_text = (tmp_path / "dublin_core.xml").read_text(encoding="utf-8")
    assert "encoding='utf-8'" in xml_text or 'encoding="utf-8"' in xml_text
    assert "Café Study" in xml_text
    assert (tmp_path / "contents").read_text(encoding="utf-8") == "article.pdf"


def test_metadata_exporter_neutralizes_csv_formula_injection(tmp_path):
    csv_file = tmp_path / "report.csv"
    exporter = MetadataExporter(
        output_dir=str(tmp_path),
        csv_file=str(csv_file),
        ris_file=str(tmp_path / "citations.ris"),
        author_file=str(tmp_path / "authors.txt"),
        publisher_report_file=str(tmp_path / "pubs.csv"),
        institution_name="Example University",
    )
    exporter.open_handles()
    exporter.write_csv_row({"DOI": "10.1/a", "Title": "=CMD|'/C calc'!A0"})
    exporter.close_handles()

    with open(csv_file, "r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    assert rows[0]["Title"] == "'=CMD|'/C calc'!A0"
