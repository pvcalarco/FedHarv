from fedharv.pdf import (
    PDFDownloader,
    apply_publisher_heuristics,
    fetch_html_meta_pdf_link,
)
import fedharv.pdf as pdf_module


class DummyConfig:
    SCOPUS_KEY = ""


class DummyAPIClient:
    def __init__(self):
        self.SESSION = object()
        self.config = DummyConfig()
        self.BROWSER_HEADERS = {"User-Agent": "test-agent"}


class FakeHtmlResponse:
    def __init__(self, status_code=200, text=""):
        self.status_code = status_code
        self.text = text


class FakeSession:
    def __init__(self, response):
        self._response = response

    def get(self, *_args, **_kwargs):
        return self._response


def _new_downloader():
    return PDFDownloader(DummyAPIClient(), patterns_file="missing_patterns_file.json")


def test_apply_publisher_heuristics_prefers_learned_pattern_over_static_doi_pattern():
    doi = "10.1007/example.1"
    learned = {"10.1007": "https://learned.example/{doi}"}

    url = apply_publisher_heuristics("https://link.springer.com/article/x", doi=doi, learned_patterns=learned)

    assert url == "https://learned.example/10.1007/example.1"


def test_apply_publisher_heuristics_uses_domain_transform_when_no_doi_pattern_match():
    landing = "https://journals.sagepub.com/doi/full/10.1177/123"

    url = apply_publisher_heuristics(landing, doi="10.5555/no-match")

    assert "/doi/pdf/" in url


def test_fetch_html_meta_pdf_link_extracts_citation_pdf_url_for_target_domain():
    html = '<html><head><meta name="citation_pdf_url" content="https://example.org/file.pdf"></head></html>'
    session = FakeSession(FakeHtmlResponse(status_code=200, text=html))

    url = fetch_html_meta_pdf_link("https://mdpi.com/abc", session)

    assert url == "https://example.org/file.pdf"


def test_fetch_pdf_with_waterfall_returns_openalex_before_other_sources(monkeypatch):
    downloader = _new_downloader()

    calls = []

    def _fake_download(url, _path, _session, extra_headers=None):
        calls.append((url, extra_headers))
        return url == "https://openalex.example/file.pdf"

    monkeypatch.setattr(pdf_module, "download_file_stream", _fake_download)

    item = {"pdf_url": "https://openalex.example/file.pdf", "doi": "10.1000/abc", "publisher": "X"}
    enrich = {"crossref_pdf": "https://crossref.example/file.pdf"}
    upw = {"best_oa_location": {"url_for_pdf": "https://upw.example/file.pdf"}}

    ok, source = downloader.fetch_pdf_with_waterfall(item, enrich, upw, "https://landing.example", "temp.pdf")

    assert ok is True
    assert source == "OpenAlex"
    assert calls == [("https://openalex.example/file.pdf", None)]


def test_fetch_pdf_with_waterfall_falls_through_to_doi_heuristics(monkeypatch):
    downloader = _new_downloader()

    monkeypatch.setattr(pdf_module, "download_file_stream", lambda *_args, **_kwargs: _args[0] == "https://heur.example/doi.pdf")
    monkeypatch.setattr(pdf_module, "fetch_html_meta_pdf_link", lambda *_args, **_kwargs: None)

    def _fake_heuristics(landing_url, doi=None, learned_patterns=None):
        if landing_url is None:
            return "https://heur.example/doi.pdf"
        return None

    monkeypatch.setattr(pdf_module, "apply_publisher_heuristics", _fake_heuristics)

    item = {"pdf_url": None, "doi": "10.9999/x", "publisher": "X"}
    enrich = {"crossref_pdf": None}
    upw = {"best_oa_location": {}}

    ok, source = downloader.fetch_pdf_with_waterfall(item, enrich, upw, "https://landing.example", "temp.pdf")

    assert ok is True
    assert source == "DOI Heuristics"
