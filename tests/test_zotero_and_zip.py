import zipfile

from process_zotero_pdfs import _resolve_contained_path
from zip_saf_packages import zip_saf_packages


def test_resolve_contained_path_blocks_traversal_and_absolute_paths(tmp_path):
    zotero_base = tmp_path / "Exported Items"
    pdf_file = zotero_base / "files" / "1" / "paper.pdf"
    pdf_file.parent.mkdir(parents=True)
    pdf_file.write_bytes(b"%PDF-1.4")

    resolved = _resolve_contained_path(str(zotero_base), "files/1/paper.pdf")
    assert resolved is not None
    assert resolved.endswith("paper.pdf")

    assert _resolve_contained_path(str(zotero_base), "../../secret.txt") is None
    assert _resolve_contained_path(str(zotero_base), str(pdf_file)) is None


def test_zip_saf_packages_creates_zip_and_ignores_saf_packages_dir(tmp_path):
    item_dir = tmp_path / "Items_With_PDF" / "Faculty_of_Law" / "item_001"
    item_dir.mkdir(parents=True)
    (item_dir / "dublin_core.xml").write_text("<dublin_core/>", encoding="utf-8")

    count = zip_saf_packages(str(tmp_path))
    assert count == 1

    zip_path = tmp_path / "SAF_Packages" / "Items_With_PDF_Faculty_of_Law.zip"
    assert zip_path.exists()
    with zipfile.ZipFile(zip_path) as zf:
        assert "item_001/dublin_core.xml" in zf.namelist()

