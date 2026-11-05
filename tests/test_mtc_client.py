"""MTCClient: duplicate check, safety limits and the claim that is sent (all data fictional)."""
from datetime import datetime, timedelta, timezone

import pytest

import MTC
from conftest import FakeResponse, claim, days_ago, mtc_transaction, utc_days_ago

SESSION = "00000000-0000-4000-8000-000000000001"
OTHER = "00000000-0000-4000-8000-000000000099"
DAILY_LIMIT = "Deze transactie overschrijdt de voor uw pas ingestelde limiet per dag."


# --- Duplicate check -----------------------------------------------------------------

@pytest.mark.parametrize("field", ["claim_note", "note"])
def test_session_already_claimed_is_not_submitted(make_mtc_client, field):
    client = make_mtc_client([mtc_transaction(utc_days_ago(9), **{field: f"Tesla {SESSION}"})])

    ok, message = client.submit_reimbursement(claim(SESSION))

    assert ok
    assert "Duplicate claim found" in message
    assert client.session.payloads("/ActionClaim_Create") == []


def test_new_session_in_dry_mode_is_only_reported(make_mtc_client, monkeypatch):
    monkeypatch.setenv("MODE", "DRY")
    client = make_mtc_client([mtc_transaction(utc_days_ago(9), claim_note=OTHER)])

    ok, message = client.submit_reimbursement(claim(SESSION))

    assert ok
    assert "[DRY RUN] Would submit claim" in message
    assert client.session.payloads("/ActionClaim_Create") == []


def test_duplicate_check_reaches_back_before_an_old_session(make_mtc_client, monkeypatch):
    """LOOKBACK_PERIOD=1 must not hide the claim of a session that is 200 days old."""
    monkeypatch.setenv("MODE", "DRY")
    monkeypatch.setenv("LOOKBACK_PERIOD", "1")
    monkeypatch.setenv("MAX_SESSION_AGE_DAYS", "0")
    client = make_mtc_client()
    session_start = days_ago(200)

    client.submit_reimbursement(claim(SESSION, when=session_start))

    variables = client.session.payloads("/DataActionGetTransactions")[0]["screenData"]["variables"]
    lookback_start = datetime.fromisoformat(variables["StartDateTimeCurrentFilter"].replace("Z", "+00:00"))
    earliest_claim = datetime.fromisoformat(session_start) - timedelta(days=MTC.CLAIM_DATE_MARGIN_DAYS)
    assert lookback_start <= earliest_claim
    assert variables["MaxRecords"] == MTC.DEFAULT_MAX_RECORDS


# --- Cut-off transaction list (MTC_MAX_RECORDS) --------------------------------------

def test_full_list_that_may_miss_the_claim_blocks_submitting(make_mtc_client, monkeypatch):
    monkeypatch.setenv("MODE", "DRY")
    monkeypatch.setenv("MTC_MAX_RECORDS", "3")
    # Three transactions, all newer than the session: its claim may have been cut off
    client = make_mtc_client([mtc_transaction(utc_days_ago(d)) for d in (1, 2, 3)])

    ok, message = client.submit_reimbursement(claim(SESSION, when=days_ago(30)))

    assert not ok
    assert "could not be checked for duplicates" in message


def test_full_list_that_reaches_past_the_session_is_fine(make_mtc_client, monkeypatch):
    monkeypatch.setenv("MODE", "DRY")
    monkeypatch.setenv("MTC_MAX_RECORDS", "3")
    client = make_mtc_client([mtc_transaction(utc_days_ago(d)) for d in (1, 20, 40)])

    ok, message = client.submit_reimbursement(claim(SESSION, when=days_ago(30)))

    assert ok
    assert "[DRY RUN]" in message


def test_full_list_still_finds_a_duplicate(make_mtc_client, monkeypatch):
    monkeypatch.setenv("MTC_MAX_RECORDS", "2")
    client = make_mtc_client([mtc_transaction(utc_days_ago(1), claim_note=SESSION),
                              mtc_transaction(utc_days_ago(2))])

    ok, message = client.submit_reimbursement(claim(SESSION, when=days_ago(30)))

    assert ok
    assert "Duplicate claim found" in message


# --- MAX_SESSION_AGE_DAYS ------------------------------------------------------------

def test_session_older_than_the_limit_is_not_submitted(make_mtc_client, monkeypatch):
    monkeypatch.setenv("MODE", "DRY")
    client = make_mtc_client()

    ok, message = client.submit_reimbursement(claim(SESSION, when=days_ago(90)))

    assert not ok
    assert "is 90 days old" in message


def test_age_limit_zero_disables_the_check(make_mtc_client, monkeypatch):
    monkeypatch.setenv("MODE", "DRY")
    monkeypatch.setenv("MAX_SESSION_AGE_DAYS", "0")
    client = make_mtc_client()

    ok, message = client.submit_reimbursement(claim(SESSION, when=days_ago(90)))

    assert ok
    assert "[DRY RUN]" in message


# --- Submitting (fake MTC, nothing leaves the test) -----------------------------------

def test_claim_payload(make_mtc_client, monkeypatch):
    monkeypatch.setenv("MODE", "LIVE")
    client = make_mtc_client(submit_results=[{"Success": True}])
    session = claim(SESSION, total_price=18.4, kwh_charged=41.2, isForeign=True, countryId="D")

    ok, message = client.submit_reimbursement(session)

    assert ok
    assert "Successfully submitted claim" in message
    [payload] = client.session.payloads("/ActionClaim_Create")
    new_claim = payload["inputParameters"]["ClaimNew"]
    assert new_claim["Amount"] == "18.40"
    assert new_claim["Quantity"] == "41.2"
    assert new_claim["Description"] == SESSION  # how the duplicate check finds it later
    assert new_claim["IsForeign"] is True
    assert new_claim["CountryId"] == "D"
    assert new_claim["Iban"] == "NL00TEST0123456789"
    assert new_claim["TransactionTypeId"] == "EV"
    assert payload["inputParameters"]["Attachment"]["Binary"] == session["invoice_jpeg_base64"]
    submitted = datetime.fromisoformat(new_claim["DateTransaction"].replace("Z", "+00:00"))
    assert submitted == datetime.fromisoformat(session["datetime"])


def test_daily_limit_retries_one_day_earlier(make_mtc_client, monkeypatch):
    monkeypatch.setenv("MODE", "LIVE")
    client = make_mtc_client(submit_results=[{"Success": False, "ErrorMessage": DAILY_LIMIT},
                                             {"Success": True}])

    ok, message = client.submit_reimbursement(claim(SESSION))

    assert ok
    first, second = (datetime.fromisoformat(p["inputParameters"]["ClaimNew"]["DateTransaction"]
                                            .replace("Z", "+00:00"))
                     for p in client.session.payloads("/ActionClaim_Create"))
    assert first - second == timedelta(days=1)
    # The duplicate check runs again before every attempt
    assert len(client.session.payloads("/DataActionGetTransactions")) == 2


def test_daily_limit_gives_up_after_max_attempts(make_mtc_client, monkeypatch):
    monkeypatch.setenv("MODE", "LIVE")
    client = make_mtc_client(submit_results=[{"Success": False, "ErrorMessage": DAILY_LIMIT}] * 3)

    ok, message = client.submit_reimbursement(claim(SESSION))

    assert not ok
    assert "Failed after 3 attempts" in message


def test_other_mtc_error_is_not_retried(make_mtc_client, monkeypatch):
    monkeypatch.setenv("MODE", "LIVE")
    client = make_mtc_client(submit_results=[{"Success": False, "ErrorMessage": "Bijlage ontbreekt"}])

    ok, message = client.submit_reimbursement(claim(SESSION))

    assert not ok
    assert "Claim submission failed: Bijlage ontbreekt" in message
    assert len(client.session.payloads("/ActionClaim_Create")) == 1


def test_mtc_api_exception_stops_before_submitting(make_mtc_client, monkeypatch):
    monkeypatch.setenv("MODE", "LIVE")
    client = make_mtc_client()
    error = FakeResponse({"exception": {"message": "Session expired"}})
    client.session.post = lambda url, json=None, headers=None: error

    ok, message = client.submit_reimbursement(claim(SESSION))

    assert not ok
    assert "API error fetching transactions: Session expired" in message


# --- Settings and helpers ------------------------------------------------------------

def test_invalid_settings_fall_back_to_defaults(make_mtc_client, monkeypatch):
    monkeypatch.setenv("MTC_MAX_RECORDS", "many")
    monkeypatch.setenv("LOOKBACK_PERIOD", "half a year")
    monkeypatch.setenv("MAX_SESSION_AGE_DAYS", "-5")

    client = make_mtc_client()

    assert client.max_transaction_records == MTC.DEFAULT_MAX_RECORDS
    assert client.lookback_period_months == 6
    assert client.max_session_age_days == 0


def test_oldest_transaction_date():
    transactions = [
        {"DateTransaction": "2026-03-10T08:00:00Z"},
        {"DateTransaction": "2026-03-05T08:00:00"},  # no timezone: read as UTC
        {"DateTransaction": "not a date"},
        {"DateTransaction": None},
    ]
    assert MTC._oldest_transaction_date(transactions) == datetime(2026, 3, 5, 8, tzinfo=timezone.utc)
    assert MTC._oldest_transaction_date([]) is None
