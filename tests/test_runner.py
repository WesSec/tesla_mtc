"""web/runner.py: reading the tesla_mtc log, and the safety checks of the web interface.

All data is fictional; nothing is sent to Tesla or MTC.
"""
import http.client
import io
import json
import logging
import os
import socket
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from types import SimpleNamespace

import pytest
from tqdm import tqdm

import healthcheck
import main
import runner
from conftest import mtc_transaction, tesla_history, tesla_session, utc_days_ago

NEW_ID = "00000000-0000-4000-8000-000000000011"
DUPLICATE_ID = "00000000-0000-4000-8000-000000000012"
NO_INVOICE_ID = "00000000-0000-4000-8000-000000000013"
OLD_ID = "00000000-0000-4000-8000-000000000014"


# --- The log of a real main.py run, read by the web interface -------------------------

@pytest.fixture
def dry_run_log(monkeypatch, tmp_path, make_mtc_client):
    """Runs main.main() in DRY mode against fake Tesla and MTC data and returns its log lines,
    formatted exactly as main.py configures logging."""
    monkeypatch.setenv("MODE", "DRY")
    monkeypatch.setenv("MAX_SESSIONS", "4")
    monkeypatch.setattr(main, "__file__", str(tmp_path / "main.py"))  # no skip.txt
    history = tesla_history(
        tesla_session(NEW_ID, total=18.4, kwh=41.2),
        tesla_session(DUPLICATE_ID),
        tesla_session(NO_INVOICE_ID, invoice=False),
        tesla_session(OLD_ID, days=90),
    )
    monkeypatch.setattr(main.TeslaChargingAPI, "get_charging_history", lambda self: history)
    monkeypatch.setattr(main.TeslaChargingAPI, "get_invoice_pdf", lambda self, content_id: b"%PDF")
    monkeypatch.setattr(main.TeslaChargingAPI, "convert_pdf_to_base64_jpeg", lambda self, pdf: "aW1n")
    client = make_mtc_client([mtc_transaction(utc_days_ago(5), claim_note=DUPLICATE_ID)])
    monkeypatch.setattr(main, "MTCClient", lambda: client)

    stream, root, handlers = io.StringIO(), logging.getLogger(), []

    def basic_config(level, format, datefmt):
        handler = logging.StreamHandler(stream)
        handler.setFormatter(logging.Formatter(format, datefmt))
        handlers.append(handler)
        root.addHandler(handler)
        root.setLevel(level)

    monkeypatch.setattr(main.logging, "basicConfig", basic_config)
    old_level = root.level
    try:
        main.main()
    finally:
        for handler in handlers:
            root.removeHandler(handler)
        root.setLevel(old_level)
    assert handlers, "main() no longer calls logging.basicConfig"
    return stream.getvalue().splitlines()


def test_web_interface_understands_a_dry_run(dry_run_log):
    parsed = runner.parse_log(dry_run_log)

    assert parsed["found"] == 4
    assert [s["status"] for s in parsed["sessions"]] == ["would", "duplicate", "noinvoice", "tooold"]
    new = parsed["sessions"][0]
    assert new["location"] == "Supercharger Teststad NL"
    assert new["kwh"] == 41.2
    assert new["amount"] == 18.4
    assert parsed["sessions"][3]["detail"] == ("key_days", "d_tooold", "90")
    assert not parsed["crashed"]


def test_status_summary_of_a_dry_run(dry_run_log):
    snap = {"running": False, "mode": "DRY", "sessions": 4, "exit_code": 0,
            "started": datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc),
            "finished": datetime(2026, 1, 1, 10, 1, tzinfo=timezone.utc)}

    status = runner.summary(snap, runner.parse_log(dry_run_log))

    assert status["ok"] is True
    assert status["sessions"] == 4
    assert status["would_submit"] == 1
    assert status["amount_would_submit"] == 18.4
    assert status["duplicates"] == 1
    assert status["too_old"] == 1
    assert status["failed"] == 1  # the session without an invoice


@pytest.mark.parametrize("lang, expected", [("nl", "Proefrun klaar"), ("en", "Test run finished")])
def test_result_page_renders(dry_run_log, lang, expected):
    snap = {"running": False, "mode": "DRY", "sessions": 4, "exit_code": 0, "log": dry_run_log,
            "started": datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc),
            "finished": datetime(2026, 1, 1, 10, 1, tzinfo=timezone.utc)}

    page = runner.render(snap, lang, 4)

    assert expected in page
    assert "Supercharger Teststad NL" in page


def test_tqdm_progress_is_read():
    """The page shows Tesla progress from tqdm's output (tqdm is not pinned)."""
    out = io.StringIO()
    for _ in tqdm(range(3), desc="Pulling tesla information and invoices", file=out, mininterval=0):
        pass
    # The runner reads the output in text mode, where tqdm's carriage returns end a line
    lines = out.getvalue().replace("\r", "\n").splitlines()

    assert runner.parse_log(lines)["progress"] == (3, 3)


def test_crash_and_sensitive_lines():
    lines = [
        "2026-01-01 10:00:00 [INFO] New dynamic CSRF token configured for session: abc123",
        "2026-01-01 10:00:01 [ERROR] Process failed: boom",
        "Traceback (most recent call last):",
    ]

    parsed = runner.parse_log(lines)

    assert parsed["crashed"]
    assert all("abc123" not in line for line in runner.clean_lines(lines))


# --- HTTP: submitting stays off, other websites are refused, the token is checked -----

@pytest.fixture
def server(monkeypatch):
    started = []

    def fake_start_run(mode, sessions):
        started.append((mode, sessions))
        return server.accept

    monkeypatch.setattr(runner, "start_run", fake_start_run)
    monkeypatch.setattr(runner, "ALLOW_LIVE", False)
    monkeypatch.setattr(runner, "TOKEN", "")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), runner.Handler)
    threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()
    server = SimpleNamespace(port=httpd.server_address[1], started=started, accept=True)
    yield server
    httpd.shutdown()
    httpd.server_close()


def call(server, method, path, headers=None, body=None):
    conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
    try:
        conn.request(method, path, body=body, headers=headers or {})
        response = conn.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        conn.close()


def test_test_run_starts(server):
    status, _, body = call(server, "POST", "/run?mode=DRY&sessions=3")

    assert status == 202
    assert json.loads(body)["started"] is True
    assert server.started == [("DRY", 3)]


def test_submitting_is_refused_until_enabled(server):
    status, _, _ = call(server, "POST", "/run?mode=LIVE&sessions=3")

    assert status == 403
    assert server.started == []


def test_submitting_when_enabled(server, monkeypatch):
    monkeypatch.setattr(runner, "ALLOW_LIVE", True)

    status, _, _ = call(server, "POST", "/run?mode=LIVE&sessions=3")

    assert status == 202
    assert server.started == [("LIVE", 3)]


def test_second_run_while_busy_is_refused(server):
    server.accept = False

    status, _, _ = call(server, "POST", "/run?mode=DRY")

    assert status == 409


@pytest.mark.parametrize("headers", [
    {"Sec-Fetch-Site": "cross-site"},
    {"Origin": "http://other.example"},
])
def test_requests_from_other_websites_are_refused(server, headers):
    status, _, _ = call(server, "POST", "/run?mode=DRY", headers)

    assert status == 403
    assert server.started == []


def test_same_origin_form_post_is_accepted(server):
    host = f"127.0.0.1:{server.port}"
    status, _, _ = call(server, "POST", "/run?mode=DRY", {"Origin": f"http://{host}", "Host": host})

    assert status == 202


@pytest.mark.parametrize("query", ["mode=TEST", "sessions=0", "sessions=21", "sessions=two"])
def test_invalid_parameters(server, query):
    status, _, _ = call(server, "POST", f"/run?{query}")

    assert status == 400
    assert server.started == []


def test_large_body_is_refused(server):
    status, _, _ = call(server, "POST", "/run", {"Content-Type": "application/x-www-form-urlencoded"},
                        "x" * (runner.MAX_BODY + 1))

    assert status == 413


@pytest.mark.parametrize("path, headers, expected", [
    ("/status", {}, 401),
    ("/status?token=wrong", {}, 401),
    ("/status?token=test-token", {}, 200),
    ("/status", {"Authorization": "Bearer test-token"}, 200),
    ("/status", {"Authorization": "Bearer wrong"}, 401),
])
def test_token(server, monkeypatch, path, headers, expected):
    monkeypatch.setattr(runner, "TOKEN", "test-token")

    status, _, _ = call(server, "GET", path, headers)

    assert status == expected


def test_token_also_protects_starting_a_run(server, monkeypatch):
    monkeypatch.setattr(runner, "TOKEN", "test-token")

    status, _, _ = call(server, "POST", "/run?mode=DRY")

    assert status == 401
    assert server.started == []


def test_status_json(server):
    status, headers, body = call(server, "GET", "/status")

    assert status == 200
    assert headers["Content-Type"] == "application/json"
    data = json.loads(body)
    assert data["allow_live"] is False
    assert "running" in data and "would_submit" in data


def test_page_cannot_be_framed(server):
    status, headers, body = call(server, "GET", "/?lang=nl")

    assert status == 200
    assert headers["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]
    assert "Proefrun".encode() in body


# --- Runs: the command, and the time limit ---------------------------------------------

@pytest.fixture
def fresh_state(monkeypatch, tmp_path):
    monkeypatch.setattr(runner, "state", {"running": False, "mode": None, "sessions": None, "started": None,
                                          "finished": None, "exit_code": None, "log": []})
    monkeypatch.setattr(runner, "APP_DIR", str(tmp_path))
    return runner


def test_run_passes_mode_and_sessions(fresh_state, monkeypatch):
    script = "import os; print(os.environ['MODE'], os.environ['MAX_SESSIONS'], flush=True)"
    monkeypatch.setattr(runner, "RUN_CMD", [sys.executable, "-c", script])

    runner.run_script("DRY", 3)

    assert runner.state["exit_code"] == 0
    assert runner.state["log"] == ["DRY 3"]
    assert runner.state["running"] is False


def test_run_that_hangs_is_stopped(fresh_state, monkeypatch):
    script = "import time; print('started', flush=True); time.sleep(60)"
    monkeypatch.setattr(runner, "RUN_CMD", [sys.executable, "-c", script])
    monkeypatch.setattr(runner, "RUN_TIMEOUT", 1)

    started = time.monotonic()
    runner.run_script("DRY", 1)

    assert time.monotonic() - started < 30
    snap = dict(runner.state)
    assert snap["running"] is False
    assert snap["exit_code"] != 0
    assert "Run stopped: not finished after 1 seconds" in snap["log"][-1]
    parsed = runner.parse_log(snap["log"])
    assert runner.summary(snap, parsed)["ok"] is False
    assert any("Run stopped" in message for _, message in parsed["notes"])


@pytest.mark.skipif(os.name == "nt", reason="process groups are POSIX-only")
def test_hanging_run_behind_a_wrapper_script_is_stopped(fresh_state, monkeypatch):
    """Like an entrypoint.sh that pipes main.py into tee: the whole group must be stopped,
    otherwise the pipe stays open and the run never ends."""
    child = f"{sys.executable} -c \"import time; print('started', flush=True); time.sleep(60)\""
    monkeypatch.setattr(runner, "RUN_CMD", ["sh", "-c", f"{child} | cat"])
    monkeypatch.setattr(runner, "RUN_TIMEOUT", 1)

    started = time.monotonic()
    runner.run_script("DRY", 1)

    assert time.monotonic() - started < 30
    assert runner.state["exit_code"] != 0
    assert "Run stopped" in runner.state["log"][-1]


@pytest.mark.parametrize("value, expected", [(None, 900), ("120", 120), ("0", 0), ("-5", 0), ("soon", 900)])
def test_run_timeout_setting(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv("RUNNER_RUN_TIMEOUT", raising=False)
    else:
        monkeypatch.setenv("RUNNER_RUN_TIMEOUT", value)

    assert runner.run_timeout() == expected


# --- Docker HEALTHCHECK ---------------------------------------------------------------

def test_healthcheck(server, monkeypatch):
    assert healthcheck.healthy(port=server.port, token="")

    monkeypatch.setattr(runner, "TOKEN", "test-token")
    assert healthcheck.healthy(port=server.port, token="test-token")
    assert not healthcheck.healthy(port=server.port, token="")


def test_healthcheck_without_web_interface():
    with socket.socket() as sock:  # a port nobody listens on
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]

    assert not healthcheck.healthy(port=port, token="", timeout=2)
