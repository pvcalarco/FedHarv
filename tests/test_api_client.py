from pathlib import Path

import fedharv.api as api_module
from fedharv.api import APIClient


class DummyConfig:
    def __init__(self, cache_dir):
        self.CACHE_DIR = str(cache_dir)
        self.EMAIL_CONTACT = "test@example.org"
        self.CROSSREF_TOKEN = ""
        self.SHERPA_KEY = ""
        self.SCOPUS_KEY = ""


class FakeResponse:
    def __init__(self, status_code, payload=None, headers=None, text=""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.headers = headers or {}
        self.text = text

    def json(self):
        return self._payload


def _new_client(tmp_path: Path):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return APIClient(DummyConfig(cache_dir))


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
    assert calls["slept"] > 0
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
