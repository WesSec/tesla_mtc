"""main.py: turning Tesla's charging history into claims (all data fictional)."""
import builtins

import pytest

import main
from conftest import tesla_history as history, tesla_session

NL_ID = "00000000-0000-4000-8000-000000000001"
DE_ID = "00000000-0000-4000-8000-000000000002"
NO_ID = "00000000-0000-4000-8000-000000000003"
CH_ID = "00000000-0000-4000-8000-000000000004"


@pytest.fixture
def tesla(monkeypatch, tmp_path):
    """A TeslaChargingAPI with a fake history; skip.txt is looked up in tmp_path."""
    monkeypatch.setattr(main, "__file__", str(tmp_path / "main.py"))
    monkeypatch.setenv("MAX_SESSIONS", "20")
    api = main.TeslaChargingAPI()
    api.history = history()
    api.get_charging_history = lambda: api.history
    api.get_invoice_pdf = lambda content_id: f"%PDF {content_id}".encode()
    api.convert_pdf_to_base64_jpeg = lambda pdf: "jpeg:" + pdf.decode()
    api.skip_file = tmp_path / "skip.txt"
    return api


def ids(sessions):
    return [s["chargeSessionId"] for s in sessions]


def test_dutch_session(tesla):
    tesla.history = history(tesla_session(NL_ID))

    [session] = tesla.process_charging_sessions()

    assert session["chargeSessionId"] == NL_ID
    assert session["kwh_charged"] == 41.2  # from the CHARGING fee, not the parking fee
    assert session["total_price"] == 18.4
    assert session["currency"] == "EUR"
    assert session["isForeign"] is False
    assert session["countryId"] == "NL"
    assert session["invoice_jpeg_base64"] == f"jpeg:%PDF content-{NL_ID}"


def test_supported_foreign_country_uses_the_mtc_code(tesla):
    tesla.history = history(tesla_session(DE_ID, country="DE"))

    [session] = tesla.process_charging_sessions()

    assert session["isForeign"] is True
    assert session["countryId"] == "D"


@pytest.mark.parametrize("action, expected", [("SKIP", []), ("DUTCH", [NO_ID])])
def test_unknown_country(tesla, monkeypatch, action, expected):
    monkeypatch.setenv("UNKNOWN_COUNTRY_ACTION", action)
    tesla.history = history(tesla_session(NO_ID, country="NO"))

    sessions = tesla.process_charging_sessions()

    assert ids(sessions) == expected
    if sessions:
        assert sessions[0]["isForeign"] is False
        assert sessions[0]["countryId"] == "NL"


@pytest.mark.parametrize("answer, expected", [("1", [NO_ID]), ("2", [])])
def test_unknown_country_prompt(tesla, monkeypatch, answer, expected):
    monkeypatch.setattr(builtins, "input", lambda prompt="": answer)
    tesla.history = history(tesla_session(NO_ID, country="NO"))

    assert ids(tesla.process_charging_sessions()) == expected


def test_unknown_country_without_terminal_is_skipped(tesla, monkeypatch):
    def no_terminal(prompt=""):
        raise EOFError
    monkeypatch.setattr(builtins, "input", no_terminal)
    tesla.history = history(tesla_session(NO_ID, country="NO"), tesla_session(NL_ID))

    assert ids(tesla.process_charging_sessions()) == [NL_ID]


def test_foreign_currency_is_skipped(tesla):
    # Switzerland is a supported country, but Tesla bills it in CHF
    tesla.history = history(tesla_session(CH_ID, country="CH", currency="CHF"), tesla_session(NL_ID))

    assert ids(tesla.process_charging_sessions()) == [NL_ID]


def test_skip_list(tesla):
    tesla.skip_file.write_text(f"# claimed by hand\n{NL_ID}\n\n", encoding="utf-8")
    tesla.history = history(tesla_session(NL_ID), tesla_session(DE_ID, country="DE"))

    assert ids(tesla.process_charging_sessions()) == [DE_ID]


def test_max_sessions(tesla, monkeypatch):
    monkeypatch.setenv("MAX_SESSIONS", "2")
    tesla.history = history(*(tesla_session(f"session-{i}") for i in range(5)))

    assert ids(tesla.process_charging_sessions()) == ["session-0", "session-1"]


def test_session_without_invoice_is_kept_for_reporting(tesla):
    tesla.history = history(tesla_session(NL_ID, invoice=False))

    [session] = tesla.process_charging_sessions()

    assert session["invoice_jpeg_base64"] is None


def test_unexpected_response_gives_no_sessions(tesla):
    tesla.history = {"errors": [{"message": "Unauthorized"}]}

    assert tesla.process_charging_sessions() == []
