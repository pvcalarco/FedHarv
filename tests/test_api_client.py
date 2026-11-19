from pathlib import Path

import fedharv.api as api_module
from fedharv.api import APIClient


class DummyConfig:
    def __init__(self, cache_dir, openalex_key=""):
        self.CACHE_DIR = str(cache_dir)
        self.EMAIL_CONTACT = "test@example.org"
        self.CROSSREF_TOKEN = ""
        self.SCOPUS_KEY = ""
        self.OPENALEX_KEY = openalex_key


class FakeResponse:
    def __init__(self, status_code, payload=None, headers=None, text=""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.headers = headers or {}
        self.text = text

    def json(self):
        return self._payload


def _new_client(tmp_path: Path, openalex_key=""):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return APIClient(DummyConfig(cache_dir, openalex_key=openalex_key))


def test_fetch_crossref_data_parses_core_fields(tmp_path, monkeypatch):
    client = _new_client(tmp_path)

    payload = {
        "message": {
            "publisher": "Example Publisher",
            "author": [
                {
                    "family": "Doe",
                    "given": "Jane",
                    "affiliation": [{"name": "Example University"}],
                }
            ],
            "funder": [{"name": "Grant Org", "award": ["A-1"]}],
            "license": [{"URL": "https://creativecommons.org/licenses/by/4.0/"}],
            "link": [
                {
                    "content-type": "application/pdf",
                    "URL": "https://example.org/paper.pdf",
                    "intended-application": "text-mining",
                }
            ],
        }
    }

    monkeypatch.setattr(
        client.SESSION,
        "get",
        lambda *_args, **_kwargs: FakeResponse(200, payload=payload),
    )

    data = client.fetch_crossref_data("10.1000/test-parse")

    assert data["publisher"] == "Example Publisher"
    assert data["crossref_pdf"] == "https://example.org/paper.pdf"
    assert data["authors"] == ["Doe, Jane"]
    assert data["affiliations"] == ["Example University"]
    assert data["funders"] == ["Grant Org (Award: A-1)"]


def test_fetch_crossref_data_retries_once_on_429(tmp_path, monkeypatch):
    client = _new_client(tmp_path)

    calls = {"count": 0, "slept": 0}

    def _fake_get(*_args, **_kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            return FakeResponse(429, headers={"Retry-After": "1"})
        return FakeResponse(200, payload={"message": {"publisher": "Retry OK"}})

    monkeypatch.setattr(client.SESSION, "get", _fake_get)
    monkeypatch.setattr(api_module.time, "sleep", lambda sec: calls.__setitem__("slept", sec))

    data = client.fetch_crossref_data("10.1000/test-429")

    assert calls["count"] == 2
    assert calls["slept"] == 1.0
    assert data["publisher"] == "Retry OK"


def test_fetch_crossref_data_returns_empty_dict_on_non_200(tmp_path, monkeypatch):
    client = _new_client(tmp_path)

    monkeypatch.setattr(
        client.SESSION,
        "get",
        lambda *_args, **_kwargs: FakeResponse(404, payload={"status": "not found"}),
    )

    assert client.fetch_crossref_data("10.1000/test-404") == {}


def test_fetch_unpaywall_data_uses_safe_json_dict_for_list_payload(tmp_path, monkeypatch):
    client = _new_client(tmp_path)

    monkeypatch.setattr(
        client.SESSION,
        "get",
        lambda *_args, **_kwargs: FakeResponse(200, payload=[{"oa_status": "gold"}]),
    )

    data = client.fetch_unpaywall_data("10.1000/test-upw")
    assert data == {"oa_status": "gold"}


def test_harvest_crossref_handles_empty_lists_safely(tmp_path, monkeypatch):
    client = _new_client(tmp_path)
    payload = {
        "message": {
            "items": [
                {
                    "DOI": "10.1000/EmptyLists",
                    "title": [],
                    "container-title": [],
                    "ISSN": [],
                    "published-print": {"date-parts": [[]]},
                }
            ],
            "next-cursor": "*",
        }
    }
    monkeypatch.setattr(client.SESSION, "get", lambda *_a, **_kw: FakeResponse(200, payload=payload))

    results = client.harvest_crossref("2026-01-01", "2026-01-31", "Example University")
    assert len(results) == 1
    assert results[0]["doi"] == "10.1000/emptylists"
    assert results[0]["title"] == "No Title"
    assert results[0]["date"] == "2026-01-01"


def test_harvest_openalex_passes_api_key_when_configured(tmp_path, monkeypatch):
    client = _new_client(tmp_path, openalex_key="secret-oa-key")
    seen_params = []

    def _fake_get(url, params=None, **_kw):
        seen_params.append((url, dict(params or {})))
        return FakeResponse(200, payload={"results": [], "meta": {}})

    monkeypatch.setattr(client.SESSION, "get", _fake_get)
    client.harvest_openalex("2026-01-01", "2026-01-31", "Example University")

    assert seen_params
    assert all(p.get("api_key") == "secret-oa-key" for _, p in seen_params)


def test_parse_retry_after_handles_numeric_and_http_date():
    assert api_module._parse_retry_after("3", 1) == 3.0
    assert api_module._parse_retry_after("999", 1) == 60.0
    # Past HTTP-date clamps to minimum 0.5s rather than raising
    assert api_module._parse_retry_after("Wed, 21 Oct 2015 07:28:00 GMT", 5) == 0.5
    assert api_module._parse_retry_after("not-a-date", 5) == 5
