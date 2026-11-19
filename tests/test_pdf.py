import fedharv.pdf as pdf_module
from fedharv.pdf import (
    PDFDownloader,
    apply_publisher_heuristics,
    download_file_stream,
    fetch_html_meta_pdf_link,
)


class DummyConfig:
    SCOPUS_KEY = ""


class DummyAPIClient:
    def __init__(self):
        self.SESSION = object()
        self.config = DummyConfig()
        self.BROWSER_HEADERS = {"User-Agent": "test-agent"}


class FakeHtmlResponse:
    def __init__(self, status_code=200, text="", url="https://mdpi.com/abc"):
        self.status_code = status_code
        self.text = text
        self.url = url


class FakeSession:
    def __init__(self, response):
        self._response = response

    def get(self, *_args, **_kwargs):
        return self._response


def _new_downloader(tmp_path=None):
    patterns_path = str(tmp_path / "patterns.json") if tmp_path else ""
    return PDFDownloader(DummyAPIClient(), patterns_file=patterns_path)


def test_apply_publisher_heuristics_prefers_learned_pattern_over_static_doi_pattern():
    doi = "10.1007/example.1"
    learned = {"10.1007": "https://learned.example/{doi}"}

    url = apply_publisher_heuristics("https://link.springer.com/article/x", doi=doi, learned_patterns=learned)

    assert url == "https://learned.example/10.1007/example.1"


def test_apply_publisher_heuristics_domain_transforms_avoid_double_pdf():
    sage = apply_publisher_heuristics("https://journals.sagepub.com/doi/full/10.1177/123", doi="10.5555/no-match")
    assert sage == "https://journals.sagepub.com/doi/pdf/10.1177/123"

    wiley = apply_publisher_heuristics("https://onlinelibrary.wiley.com/doi/full/10.1002/abc", doi=None)
    assert wiley == "https://onlinelibrary.wiley.com/doi/pdf/10.1002/abc"

    cdn = apply_publisher_heuristics("https://cdnsciencepub.com/doi/abs/10.1139/xyz", doi=None)
    assert cdn == "https://cdnsciencepub.com/doi/pdf/10.1139/xyz"

    scielo_no_q = apply_publisher_heuristics("https://www.scielo.br/j/abc/a/123/", doi=None)
    assert scielo_no_q.endswith("?format=pdf")

    scielo_with_q = apply_publisher_heuristics("https://www.scielo.br/j/abc/a/123/?lang=en", doi=None)
    assert scielo_with_q.endswith("&format=pdf")

    cambridge = apply_publisher_heuristics("https://www.cambridge.org/core/journals/x/article/ABC123/?foo=1", doi=None)
    assert cambridge.endswith("/ABC123/ABC123.pdf")


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


def test_fetch_pdf_with_waterfall_handles_null_best_oa_location(monkeypatch):
    downloader = _new_downloader()
    monkeypatch.setattr(pdf_module, "download_file_stream", lambda *_a, **_kw: False)
    monkeypatch.setattr(pdf_module, "fetch_html_meta_pdf_link", lambda *_a, **_kw: None)

    item = {"pdf_url": None, "doi": "10.9999/null-test", "publisher": "X"}
    ok, source = downloader.fetch_pdf_with_waterfall(item, {}, {"best_oa_location": None}, None, "temp.pdf")

    assert ok is False
    assert source == "None"


def test_fetch_pdf_with_waterfall_falls_through_to_doi_heuristics(monkeypatch):
    downloader = _new_downloader()

    monkeypatch.setattr(
        pdf_module, "download_file_stream", lambda *_args, **_kwargs: _args[0] == "https://heur.example/doi.pdf"
    )
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


def test_learn_pattern_from_url_deduces_doi_placeholder_and_skips_bad_urls(tmp_path):
    downloader = _new_downloader(tmp_path)
    downloader.patterns = {}

    downloader.learn_pattern_from_url("10.1234/5678", "https://publisher.com/pdf/10.1234/5678.pdf")
    assert downloader.patterns.get("10.1234") == "https://publisher.com/pdf/{doi}.pdf"

    downloader.patterns = {}
    downloader.learn_pattern_from_url("10.1234/5678", "https://publisher.com/download/5678/document.pdf")
    assert downloader.patterns.get("10.1234") == "https://publisher.com/download/{doi_suffix}/document.pdf"

    # Must not overwrite curated DOI_PDF_PATTERNS (e.g. 10.1007/)
    downloader.patterns = {}
    downloader.learn_pattern_from_url("10.1007/abc", "https://other.example/10.1007/abc.pdf")
    assert "10.1007" not in downloader.patterns

    # Must not learn issue-specific URLs like Erudit 2025-v117-n1
    downloader.learn_pattern_from_url(
        "10.7202/123456ar",
        "https://www.erudit.org/fr/revues/onhistory/2025-v117-n1-onhistory09996/123456ar.pdf",
    )
    assert "10.7202" not in downloader.patterns


def test_download_file_stream_rejects_unsafe_scheme_and_oversized_pdf(tmp_path):
    class _OversizedResp:
        status_code = 200
        headers = {"Content-Length": str(200 * 1024 * 1024)}

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

        def iter_content(self, chunk_size=65536):
            yield b"%PDF-1.7"

    assert download_file_stream("file:///etc/passwd", str(tmp_path / "a.pdf"), FakeSession(_OversizedResp())) is False
    assert download_file_stream("https://example.org/huge.pdf", str(tmp_path / "b.pdf"), FakeSession(_OversizedResp())) is False


def test_download_file_stream_leaves_no_partial_file_on_mid_stream_error(tmp_path):
    class _BrokenStreamResp:
        status_code = 200
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

        def iter_content(self, chunk_size=65536):
            yield b"%PDF-1.7 partial"
            raise ConnectionError("stream reset")

    dest = tmp_path / "article.pdf"
    assert download_file_stream("https://example.org/broken.pdf", str(dest), FakeSession(_BrokenStreamResp())) is False
    assert not dest.exists()
    assert not (tmp_path / "article.pdf.part").exists()


def test_process_playwright_queue_does_not_double_finalize_on_mid_batch_error():
    import sys
    import types

    downloader = _new_downloader()
    calls = []

    class _FakePage:
        url = "https://example.org/p"

        def goto(self, _url, **_kw):
            pass

        def wait_for_load_state(self, *_a, **_kw):
            pass

        def locator(self, *_a, **_kw):
            return types.SimpleNamespace(get_attribute=lambda *_a, **_kw: None, count=lambda: 0)

        def close(self):
            pass

    class _CloseCrashBrowser:
        def new_context(self, **_kw):
            return types.SimpleNamespace(new_page=lambda: _FakePage())

        def close(self):
            raise RuntimeError("close failure")

    class _FakePW:
        def __enter__(self):
            return types.SimpleNamespace(chromium=types.SimpleNamespace(launch=lambda **_kw: _CloseCrashBrowser()))

        def __exit__(self, *_a):
            return False

    fake_sync_api = types.SimpleNamespace(sync_playwright=lambda: _FakePW())
    fake_pw = types.SimpleNamespace(sync_api=fake_sync_api)
    old_pw = sys.modules.get("playwright")
    old_sa = sys.modules.get("playwright.sync_api")
    sys.modules["playwright"] = fake_pw
    sys.modules["playwright.sync_api"] = fake_sync_api
    try:
        queue = [
            {"idx": 0, "landing_url": "https://example.org/a", "temp_pdf": "t0.pdf", "item": {"doi": "10.1/a"}},
            {"idx": 1, "landing_url": "https://example.org/b", "temp_pdf": "t1.pdf", "item": {"doi": "10.1/b"}},
        ]
        downloader.process_playwright_queue(queue, lambda task, ok, src: calls.append((task["idx"], ok, src)))
    finally:
        if old_pw is None:
            sys.modules.pop("playwright", None)
        else:
            sys.modules["playwright"] = old_pw
        if old_sa is None:
            sys.modules.pop("playwright.sync_api", None)
        else:
            sys.modules["playwright.sync_api"] = old_sa

    assert [c[0] for c in calls] == [0, 1]
