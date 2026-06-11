from pathlib import Path

import fedharv.core as core_module
from fedharv.core import HarvesterEngine


def _write_test_config(tmp_path: Path) -> Path:
    output_dir = (tmp_path / "output").as_posix()
    config_text = f"""
[Authentication]
SherpaKey =
ScopusKey =
CrossrefPlusToken =

[General]
Email = test@example.org
OutputDir = {output_dir}

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


def test_run_dry_run_skips_process_items(tmp_path, monkeypatch):
    cfg = _write_test_config(tmp_path)
    harvester = HarvesterEngine(config_path=str(cfg))

    monkeypatch.setattr(harvester, "discover", lambda: ([{"doi": "10.1/a"}], []))
    monkeypatch.setattr(harvester, "deduplicate_and_merge", lambda oa, cr: oa)

    called = {"process_items": False}

    def _process_items(_items):
        called["process_items"] = True

    monkeypatch.setattr(harvester, "process_items", _process_items)

    harvester.run(dry_run=True, resume=True)

    assert called["process_items"] is False


def test_run_resume_does_not_call_cleanup(tmp_path, monkeypatch):
    cfg = _write_test_config(tmp_path)
    harvester = HarvesterEngine(config_path=str(cfg))

    marker = Path(harvester.config.OUTPUT_DIR) / "keep.txt"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("keep", encoding="utf-8")

    called = {"cleanup": False}

    def _cleanup(*_args, **_kwargs):
        called["cleanup"] = True

    monkeypatch.setattr(core_module, "robust_cleanup_output_dir", _cleanup)
    monkeypatch.setattr(harvester, "discover", lambda: ([], []))
    monkeypatch.setattr(harvester, "deduplicate_and_merge", lambda oa, cr: [])
    monkeypatch.setattr(harvester, "process_items", lambda _items: None)

    harvester.run(dry_run=True, resume=True)

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

    harvester.run(dry_run=True, resume=False)

    assert called["cleanup"] is True
    assert called["output"] == harvester.config.OUTPUT_DIR
    assert called["preserve"] == harvester.config.CACHE_DIR
