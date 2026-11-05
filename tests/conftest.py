"""Shared test setup: no real .env, no real network, and fake MTC/Tesla data.

All data in these tests is fictional.
"""
from datetime import datetime, timedelta, timezone

import dotenv
import pytest
import requests

# Never read a real .env: main.py and MTC.py call load_dotenv() when imported or constructed.
dotenv.load_dotenv = lambda *args, **kwargs: False

import MTC  # noqa: E402  (must come after the load_dotenv patch)

# Settings the code reads from the environment; cleared so every test starts from the defaults
ENV_VARS = (
    "MODE", "MAX_SESSIONS", "MAX_SESSION_AGE_DAYS", "MTC_MAX_RECORDS", "LOOKBACK_PERIOD",
    "UNKNOWN_COUNTRY_ACTION", "IBAN", "MTC_USERNAME", "MTC_PASSWORD", "TESLA_VIN",
    "TESLA_REFRESH_TOKEN", "LOG_LEVEL", "RUNNER_TOKEN", "RUNNER_ALLOW_LIVE",
)

TZ_NL = timezone(timedelta(hours=2))


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MTC_USERNAME", "test-user")
    monkeypatch.setenv("MTC_PASSWORD", "test-password")
    monkeypatch.setenv("IBAN", "NL00TEST0123456789")


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise RuntimeError("Network access is disabled in tests")
    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", refuse)


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(MTC.time, "sleep", lambda seconds: None)


def days_ago(days):
    """A charging session start time as Tesla provides it: local time with an offset."""
    return (datetime.now(TZ_NL) - timedelta(days=days)).replace(microsecond=0).isoformat()


def utc_days_ago(days):
    """A date as MTC returns it in its transaction list."""
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


def mtc_transaction(when, claim_note="", note=""):
    return {"DateTransaction": when, "ClaimNote": claim_note, "Note": note}


def tesla_session(session_id, country="NL", currency="EUR", total=18.4, kwh=41.2, invoice=True, days=10):
    """One item of Tesla's charging history (getChargingHistoryV2)."""
    return {
        "chargeSessionId": session_id,
        "countryCode": country,
        "siteLocationName": f"Supercharger Teststad {country}",
        "siteAddress": {"country": f"Testland {country}"},
        "chargeStartDateTime": days_ago(days),
        "fees": [
            {"feeType": "PARKING", "usageBase": 5, "totalDue": 2.5, "currencyCode": currency},
            {"feeType": "CHARGING", "usageBase": kwh, "totalDue": total, "currencyCode": currency},
        ],
        "invoices": [{"fileName": "invoice.pdf", "contentId": f"content-{session_id}"}] if invoice else [],
    }


def tesla_history(*sessions):
    return {"data": {"me": {"charging": {"historyV2": {"data": list(sessions)}}}}}


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


class FakeMTCSession:
    """Stands in for MTCClient.session: answers the transactions list and claim submissions."""

    def __init__(self, transactions=(), submit_results=()):
        self.transactions = list(transactions)
        self.submit_results = list(submit_results)
        self.cookies = {"osVisitor": "test-visitor"}
        self.headers = {}
        self.calls = []

    def post(self, url, json=None, headers=None):
        self.calls.append((url, json))
        if url.endswith("/DataActionGetTransactions"):
            return FakeResponse({"data": {"Transactions": {"List": self.transactions}}})
        if url.endswith("/ActionClaim_Create"):
            result = self.submit_results.pop(0)
            if isinstance(result, Exception):
                raise result
            return FakeResponse({"data": result})
        raise AssertionError(f"Unexpected MTC call: {url}")

    def payloads(self, endpoint):
        return [payload for url, payload in self.calls if url.endswith(endpoint)]


@pytest.fixture
def make_mtc_client(monkeypatch):
    """Builds an MTCClient that is 'logged in' and talks to a FakeMTCSession."""
    def factory(transactions=(), submit_results=()):
        monkeypatch.setattr(MTC.MTCClient, "login", lambda self: True)
        monkeypatch.setattr(MTC.MTCClient, "_get_api_version", lambda self, key: f"api-{key}")
        client = MTC.MTCClient()
        client.csrf_token = "test-csrf"
        client.session = FakeMTCSession(transactions, submit_results)
        return client
    return factory


def claim(session_id="00000000-0000-4000-8000-000000000001", when=None, **overrides):
    """A processed charging session as main.py hands it to MTCClient."""
    data = {
        "datetime": when or days_ago(10),
        "location": "Supercharger Teststad",
        "chargeSessionId": session_id,
        "kwh_charged": 41.2,
        "total_price": 18.4,
        "currency": "EUR",
        "invoice_jpeg_base64": "aW1hZ2U=",
        "countryCode": "NL",
        "isForeign": False,
        "countryId": "NL",
    }
    data.update(overrides)
    return data
