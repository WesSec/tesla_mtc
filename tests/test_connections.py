"""Connections to MTC and Tesla: certificates are always verified, and every call has a timeout.

Nothing here connects to a server.
"""
import re
from pathlib import Path

import pytest
import requests
from requests.adapters import HTTPAdapter

import main
import MTC
from util import HTTP_TIMEOUT

ROOT = Path(__file__).resolve().parent.parent
MTC_URL = "https://mtc.outsystemsenterprise.com/MultiTankcard/moduleservices/moduleversioninfo?1"


@pytest.fixture
def client(monkeypatch):
    """A real MTCClient with its real session; login is skipped."""
    monkeypatch.setattr(MTC.MTCClient, "login", lambda self: True)
    return MTC.MTCClient()


def mtc_pool(session):
    """The urllib3 connection pool requests would use for an MTC call."""
    request = requests.Request("GET", MTC_URL).prepare()
    return session.get_adapter(MTC_URL).get_connection_with_tls_context(request, verify=session.verify)


# --- MTC certificate ------------------------------------------------------------------

def test_mtc_certificate_is_verified_for_mtc_nl(client):
    pool = mtc_pool(client.session)

    assert client.session.verify is True
    assert pool.cert_reqs == "CERT_REQUIRED"
    assert pool.assert_hostname == MTC.MTC_CERTIFICATE_NAME == "mtc.nl"


def test_other_hosts_get_the_normal_check(client):
    adapter = client.session.get_adapter("https://mtc.outsystemsenterprise.com.example.org/")

    assert not isinstance(adapter, MTC.MTCAdapter)


def test_falls_back_to_the_normal_check_when_mtc_fixes_its_certificate(client, monkeypatch):
    tried = []

    def send(adapter, request, **kwargs):
        tried.append(adapter.certificate_name)
        if adapter.certificate_name:
            raise requests.exceptions.SSLError("certificate is not valid for 'mtc.nl'")
        response = requests.Response()
        response.status_code = 200
        return response

    monkeypatch.setattr(MTC.MTCAdapter, "send", send)

    response = client._first_request(MTC_URL)

    assert response.status_code == 200
    assert tried == ["mtc.nl", None]
    # The rest of the session uses the normal check, which still verifies the certificate
    pool = mtc_pool(client.session)
    assert pool.cert_reqs == "CERT_REQUIRED"
    assert pool.assert_hostname is None


def test_stops_when_neither_check_passes(client, monkeypatch):
    def send(adapter, request, **kwargs):
        raise requests.exceptions.SSLError("certificate verify failed")

    monkeypatch.setattr(MTC.MTCAdapter, "send", send)

    with pytest.raises(requests.exceptions.SSLError):
        client._first_request(MTC_URL)


def test_verification_is_never_switched_off():
    for name in ("main.py", "MTC.py"):
        source = (ROOT / name).read_text(encoding="utf-8")
        assert not re.search(r"verify\s*=\s*False", source), f"{name} switches certificate verification off"
        assert "disable_warnings" not in source, f"{name} hides certificate warnings"


# --- Timeouts -------------------------------------------------------------------------

@pytest.fixture
def sent(monkeypatch):
    """Records the keyword arguments of every request that reaches the network layer."""
    calls = []

    def send(adapter, request, **kwargs):
        calls.append(kwargs)
        response = requests.Response()
        response.status_code = 200
        response._content = b"{}"
        return response

    monkeypatch.setattr(HTTPAdapter, "send", send)
    return calls


def test_mtc_calls_have_a_default_timeout(client, sent):
    client.session.get(MTC_URL)
    client.session.post(MTC_URL, json={})

    assert [call["timeout"] for call in sent] == [HTTP_TIMEOUT, HTTP_TIMEOUT]


def test_an_explicit_mtc_timeout_is_kept(client, sent):
    client.session.get(MTC_URL, timeout=3)

    assert sent[0]["timeout"] == 3


def test_tesla_calls_have_a_timeout(monkeypatch):
    calls = []

    def fake(method):
        def call(url, **kwargs):
            calls.append((method, url.rsplit("/", 1)[-1], kwargs.get("timeout")))
            response = requests.Response()
            response.status_code = 200
            response._content = b'{"access_token": "test", "expires_in": 3600}'
            return response
        return call

    monkeypatch.setattr(main.requests, "post", fake("POST"))
    monkeypatch.setattr(main.requests, "get", fake("GET"))
    api = main.TeslaChargingAPI()

    api.get_charging_history()
    api.get_invoice_pdf("content-1")

    assert calls == [
        ("POST", "token", HTTP_TIMEOUT),      # access token
        ("POST", "graphql", HTTP_TIMEOUT),    # charging history
        ("GET", "content-1", HTTP_TIMEOUT),   # invoice
    ]
