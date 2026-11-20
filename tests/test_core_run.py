from pathlib import Path

import fedharv.core as core_module
from fedharv.core import HarvesterEngine


def _write_test_config(tmp_path: Path) -> Path:
    output_dir = (tmp_path / "output").as_posix()
    cache_dir = (tmp_path / "cache").as_posix()
    config_text = f"""
[Authentication]
ScopusKey =
CrossrefPlusToken =
OpenAlexKey =

[General]
Email = test@example.org
OutputDir = {output_dir}
CacheDir = {cache_dir}

[Search]
Affiliation = Example University
StrictAffiliationMatch = yes
StartDate = 2026-01-01
EndDate = 2026-01-31

[DSpace]
CheckDuplicates = no
ApiUrl =
AdminEmail = admin@example.org
BinPath = /dspace/bin/dspace
DefaultCollectionHandle = 123456789/0
""".strip()
    cfg = tmp_path / "config.ini"
    cfg.write_text(config_text, encoding="utf-8")
    return cfg


def test_run_dry_run_skips_process_items_and_cleanup(tmp_path, monkeypatch):
    cfg = _write_test_config(tmp_path)
    harvester = HarvesterEngine(config_path=str(cfg))

    marker = Path(harvester.config.OUTPUT_DIR) / "keep.txt"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("keep", encoding="utf-8")

    called = {"process_items": False, "cleanup": False}
    monkeypatch.setattr(core_module, "robust_cleanup_output_dir", lambda *_a, **_kw: called.__setitem__("cleanup", True))
    monkeypatch.setattr(harvester, "discover", lambda: ([{"doi": "10.1/a"}], []))
    monkeypatch.setattr(harvester, "deduplicate_and_merge", lambda oa, cr: oa)
    monkeypatch.setattr(harvester, "process_items", lambda _items: called.__setitem__("process_items", True))

    harvester.run(dry_run=True, resume=False)

    assert called["process_items"] is False
    assert called["cleanup"] is False
    assert marker.exists()
    assert not (Path(harvester.config.OUTPUT_DIR) / "import_batch.sh").exists()


def test_run_resume_does_not_call_cleanup(tmp_path, monkeypatch):
    cfg = _write_test_config(tmp_path)
    harvester = HarvesterEngine(config_path=str(cfg))

    marker = Path(harvester.config.OUTPUT_DIR) / "keep.txt"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("keep", encoding="utf-8")

    called = {"cleanup": False}
    monkeypatch.setattr(core_module, "robust_cleanup_output_dir", lambda *_a, **_kw: called.__setitem__("cleanup", True))
    monkeypatch.setattr(harvester, "discover", lambda: ([], []))
    monkeypatch.setattr(harvester, "deduplicate_and_merge", lambda oa, cr: [])
    monkeypatch.setattr(harvester, "process_items", lambda _items: None)

    harvester.run(dry_run=False, resume=True)

    assert called["cleanup"] is False
    assert marker.exists()


def test_run_fresh_calls_cleanup_with_cache_preservation(tmp_path, monkeypatch):
    cfg = _write_test_config(tmp_path)
    harvester = HarvesterEngine(config_path=str(cfg))

    called = {"cleanup": False, "output": None, "preserve": None}

    def _cleanup(output_dir, preserve_path=None):
        called["cleanup"] = True
        called["output"] = output_dir
        called["preserve"] = preserve_path

    monkeypatch.setattr(core_module, "robust_cleanup_output_dir", _cleanup)
    monkeypatch.setattr(harvester, "discover", lambda: ([], []))
    monkeypatch.setattr(harvester, "deduplicate_and_merge", lambda oa, cr: [])
    monkeypatch.setattr(harvester, "process_items", lambda _items: None)

    harvester.run(dry_run=False, resume=False)

    assert called["cleanup"] is True
    assert called["output"] == harvester.config.OUTPUT_DIR
    assert called["preserve"] == harvester.config.CACHE_DIR


def test_deduplicate_and_merge_is_case_insensitive_on_doi(tmp_path):
    cfg = _write_test_config(tmp_path)
    harvester = HarvesterEngine(config_path=str(cfg))

    oa = [{"doi": "10.1000/AbC", "title": "Paper A", "source": "openalex"}]
    cr = [
        {"doi": "https://doi.org/10.1000/abc", "title": "Paper A", "source": "crossref"},
        {"doi": "10.1000/DEF", "title": "Paper B", "source": "crossref"},
    ]

    merged = harvester.deduplicate_and_merge(oa, cr)
    assert [item["doi"] for item in merged] == ["10.1000/abc", "10.1000/def"]


def test_classify_route_green_oa_checks_url_for_pdf(tmp_path):
    cfg = _write_test_config(tmp_path)
    harvester = HarvesterEngine(config_path=str(cfg))

    folder, has_pdf = harvester._classify_route(
        {"oa_status": "Green"},
        {"oa_status": "green", "best_oa_location": {"url_for_pdf": "https://repo.example/paper.pdf"}},
    )
    assert folder == "Green"
    assert has_pdf is True

    folder2, has_pdf2 = harvester._classify_route(
        {"oa_status": "Green"},
        {"oa_status": "green", "best_oa_location": {"url_for_pdf": "https://doi.org/10.1000/x"}},
    )
    assert folder2 == "Green"
    assert has_pdf2 is False


def test_process_items_generates_import_batch_after_creating_items(tmp_path):
    cfg = _write_test_config(tmp_path)
    harvester = HarvesterEngine(config_path=str(cfg))
    out_dir = Path(harvester.config.OUTPUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Simulate an existing item folder created during process_item
    def _fake_process_item(idx, _item):
        item_dir = out_dir / "Items_With_PDF" / "Example_University" / f"item_{idx:03d}"
        item_dir.mkdir(parents=True, exist_ok=True)
        (item_dir / "dublin_core.xml").write_text("<dublin_core/>", encoding="utf-8")

    harvester.process_item = _fake_process_item
    harvester.process_items([{"title": "Item 0"}])

    import_script = (out_dir / "import_batch.sh").read_text(encoding="utf-8")
    assert "Example_University" in import_script
    assert "set -euo pipefail" in import_script


def test_generate_summary_serializes_nested_counters_to_json(tmp_path):
    import json

    cfg = _write_test_config(tmp_path)
    harvester = HarvesterEngine(config_path=str(cfg))
    out_dir = Path(harvester.config.OUTPUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)

    harvester.STATS["pdf_success"] = 3
    harvester.STATS["pdf_sources"]["OpenAlex"] += 2
    harvester.STATS["pdf_sources"]["Browser Click-Bot"] += 1
    harvester.STATS["dept_breakdown"]["Faculty_of_Law"]["Gold"] += 2
    harvester.STATS["dept_publisher_breakdown"]["Faculty_of_Law"]["Springer"] += 2
    harvester.STATS["author_db"]["Doe, Jane"]["depts"].add("Faculty of Law")

    harvester.generate_summary()

    summary = json.loads((out_dir / "harvest_summary.txt").read_text(encoding="utf-8"))
    assert summary["pdf_success"] == 3
    assert summary["dept_breakdown"] == {"Faculty_of_Law": {"Gold": 2}}
    assert summary["dept_publisher_breakdown"] == {"Faculty_of_Law": {"Springer": 2}}
    assert summary["unique_authors"] == 1
    assert (out_dir / harvester.config.AUTHOR_REGISTRY_FILE).exists()
