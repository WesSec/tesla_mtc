"""Web interface for tesla_mtc: start a run from your phone and see a readable result.

Standard library only. Runs main.py on demand and stays running itself, so a Docker
container does not stop after every run. See docs/web-runner.md.

GET  /              -> page with test-run / submit buttons and a formatted result
GET  /?lang=nl      -> switch language (en/nl), remembered in a cookie
POST /run?mode=DRY  -> test run, nothing is submitted (default)
POST /run?mode=LIVE -> actually submit claims (only when RUNNER_ALLOW_LIVE=true)
     &sessions=5    -> number of recent charging sessions to check (MAX_SESSIONS)
GET  /status        -> JSON with status and summary (handy for Home Assistant)
GET  /logo/mtc      -> optional MTC logo: put mtc-logo.svg/png/webp next to this file
"""
import html
import json
import os
import re
import shlex
import subprocess
import sys
import threading
from datetime import datetime, timezone
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

try:
    from zoneinfo import ZoneInfo
    LOCAL_TZ = ZoneInfo(os.environ.get("RUNNER_TZ") or os.environ.get("TZ") or "Europe/Amsterdam")
except Exception:
    LOCAL_TZ = None  # no timezone data in the container

RUNNER_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.environ.get("RUNNER_APP_DIR", os.path.dirname(RUNNER_DIR))
RUN_CMD = (shlex.split(os.environ["RUNNER_CMD"], posix=os.name != "nt")
           if os.environ.get("RUNNER_CMD") else [sys.executable, "-u", "main.py"])
PORT = int(os.environ.get("RUNNER_PORT", "8080"))
TOKEN = os.environ.get("RUNNER_TOKEN", "")
# Submitting for real stays off until the user explicitly enables it
ALLOW_LIVE = os.environ.get("RUNNER_ALLOW_LIVE", "false").strip().lower() in ("1", "true", "yes")
MAX_LOG_LINES = 1000
MODES = ("DRY", "LIVE")
LANGS = ("en", "nl")
# tesla_mtc only reads the first page of Tesla's charging history, so keep this modest
SESSION_CHOICES = (1, 3, 5, 10, 20)
SESSION_LIMIT = max(SESSION_CHOICES)


def default_sessions():
    try:
        return min(max(int(os.environ.get("MAX_SESSIONS", "1")), 1), SESSION_LIMIT)
    except ValueError:
        return 1


state = {"running": False, "mode": None, "sessions": None, "started": None,
         "finished": None, "exit_code": None, "log": []}
lock = threading.Lock()


def now():
    return datetime.now(LOCAL_TZ or timezone.utc)


def run_script(mode, sessions):
    with lock:
        state.update(running=True, mode=mode, sessions=sessions, started=now(),
                     finished=None, exit_code=None, log=[])
    proc = subprocess.Popen(
        RUN_CMD, cwd=APP_DIR, env=dict(os.environ, MODE=mode, MAX_SESSIONS=str(sessions)),
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
    )
    for line in proc.stdout:
        print(line, end="", flush=True)  # also visible in the container log
        with lock:
            state["log"].append(line.rstrip("\n"))
            del state["log"][:-MAX_LOG_LINES]
    proc.wait()
    with lock:
        # entrypoint.sh pipes into tee, so a Python error does not set its exit code
        failed = any("Traceback" in l or "LIVE mode blocked" in l for l in state["log"])
        code = proc.returncode if proc.returncode or not failed else 1
        state.update(running=False, exit_code=code, finished=now())


def start_run(mode, sessions):
    with lock:
        if state["running"]:
            return False
        state["running"] = True
    threading.Thread(target=run_script, args=(mode, sessions), daemon=True).start()
    return True


# --- Translations ---------------------------------------------------------------

TEXT = {
    "en": {
        "subtitle": "Claim your Supercharger sessions",
        "dry": "Test run", "live": "Submit claims", "live_now": "Submit now",
        "hint": "A test run shows what would be submitted, without sending anything to MTC.",
        "pick_label": "Recent charging sessions to check",
        "scope": "last {n} sessions", "scope_one": "last session",
        "chip_DRY": "TEST", "chip_LIVE": "LIVE",
        "running": "Running",
        "step_start": "Starting", "step_fetch": "Fetching sessions and invoices from Tesla",
        "step_login": "Logged in to MTC", "step_session": "Processing session {i} of {n}",
        "live_off": "Submitting is switched off. Check a test run first, then set "
                    "<code>RUNNER_ALLOW_LIVE=true</code> and restart to enable it.",
        "blocked_title": "Submitting blocked",
        "blocked_text": "The start script blocked submitting: the file "
                        "<code>allow/ALLOW_LIVE</code> is missing. Create it to allow submitting claims.",
        "failed_title": "Run failed",
        "failed_text": "{when}. Check the messages and the technical log below.",
        "dry_title": "Test run finished",
        "dry_ready": "{n} {claims} ({amount}) ready to submit.",
        "dry_none": "{when}. Nothing new to submit.",
        "live_title": "Done",
        "live_done": "{when}. {n} {claims} submitted ({amount}).",
        "live_none": "{when}. Nothing new to submit.",
        "no_run": "No runs since the container started. Start with a test run.",
        "claim": "claim", "claims": "claims", "session": "session", "sessions_word": "sessions",
        "stat_sessions": "Charging sessions", "stat_submitted": "Submitted",
        "stat_would": "Ready to submit", "stat_duplicate": "Already claimed",
        "stat_attention": "Needs attention",
        "sessions": "Charging sessions", "notes": "Messages", "log": "Technical log",
        "st_submitted": "Submitted", "st_would": "Ready to submit",
        "st_duplicate": "Already claimed", "st_failed": "Failed",
        "st_tooold": "Check by hand",
        "d_tooold": "{days} days old: not submitted automatically. Check MTC and submit by hand "
                    "if it was not claimed yet.",
        "st_noinvoice": "No invoice", "st_busy": "Processing…",
        "d_noinvoice": "No invoice found at Tesla",
        "d_limit": "MTC daily limit reached, tried another date",
        "skiplist": "{n} {sessions} on the skip list (skip.txt), always skipped.",
        "confirm_title": "Submit claims to MultiTankCard?",
        "confirm_known": "{n} {claims} totalling {amount} will be submitted. "
                         "This cannot be undone.",
        "confirm_none": "The last test run found nothing new to submit. Submit anyway?",
        "confirm_unknown": "New claims among your last {n} charging sessions will be "
                           "submitted to MTC. Tip: do a test run first to see exactly "
                           "what will be submitted.",
        "confirm_unknown_one": "Your most recent charging session will be submitted to MTC "
                               "if it has not been claimed yet. Tip: do a test run first.",
        "cancel": "Cancel",
        "months": ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                   "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
    },
    "nl": {
        "subtitle": "Supercharger-sessies declareren",
        "dry": "Proefrun", "live": "Declareren", "live_now": "Nu declareren",
        "hint": "Een proefrun laat zien wat er ingediend zou worden, "
                "zonder iets naar MTC te sturen.",
        "pick_label": "Aantal recente laadsessies om te controleren",
        "scope": "laatste {n} sessies", "scope_one": "laatste sessie",
        "chip_DRY": "PROEF", "chip_LIVE": "LIVE",
        "running": "Bezig",
        "step_start": "Opstarten", "step_fetch": "Laadsessies en facturen ophalen bij Tesla",
        "step_login": "Ingelogd bij MTC", "step_session": "Sessie {i} van {n} verwerken",
        "live_off": "Declareren staat uit. Controleer eerst een proefrun, zet dan "
                    "<code>RUNNER_ALLOW_LIVE=true</code> en herstart om het aan te zetten.",
        "blocked_title": "Declareren geblokkeerd",
        "blocked_text": "Het startscript blokkeerde declareren: het bestand "
                        "<code>allow/ALLOW_LIVE</code> ontbreekt. Maak het aan om declareren toe te staan.",
        "failed_title": "Run mislukt",
        "failed_text": "{when}. Bekijk de meldingen en de technische log hieronder.",
        "dry_title": "Proefrun klaar",
        "dry_ready": "{n} {claims} ({amount}) klaar om in te dienen.",
        "dry_none": "{when}. Er is niets nieuws om in te dienen.",
        "live_title": "Klaar",
        "live_done": "{when}. {n} {claims} ingediend ({amount}).",
        "live_none": "{when}. Er was niets nieuws om in te dienen.",
        "no_run": "Nog geen run sinds de container opstartte. Begin met een proefrun.",
        "claim": "declaratie", "claims": "declaraties", "session": "sessie",
        "sessions_word": "sessies",
        "stat_sessions": "Laadsessies", "stat_submitted": "Ingediend",
        "stat_would": "Klaar om in te dienen", "stat_duplicate": "Al gedeclareerd",
        "stat_attention": "Aandacht nodig",
        "sessions": "Laadsessies", "notes": "Meldingen", "log": "Technische log",
        "st_submitted": "Ingediend", "st_would": "Klaar om in te dienen",
        "st_duplicate": "Al gedeclareerd", "st_failed": "Mislukt",
        "st_tooold": "Handmatig controleren",
        "d_tooold": "{days} dagen oud: niet automatisch ingediend. Controleer in MTC en dien "
                    "handmatig in als hij nog niet gedeclareerd is.",
        "st_noinvoice": "Geen factuur", "st_busy": "Bezig…",
        "d_noinvoice": "Geen factuur gevonden bij Tesla",
        "d_limit": "Daglimiet bij MTC bereikt, andere datum geprobeerd",
        "skiplist": "{n} {sessions} op de skiplijst (skip.txt), die worden altijd overgeslagen.",
        "confirm_title": "Declaraties indienen bij MultiTankCard?",
        "confirm_known": "{n} {claims} met een totaal van {amount} worden ingediend. "
                         "Dit kun je niet terugdraaien.",
        "confirm_none": "De laatste proefrun vond niets nieuws om in te dienen. "
                        "Toch declareren?",
        "confirm_unknown": "Nieuwe declaraties uit je laatste {n} laadsessies worden bij "
                           "MTC ingediend. Tip: doe eerst een proefrun om precies te zien "
                           "wat er ingediend wordt.",
        "confirm_unknown_one": "Je meest recente laadsessie wordt bij MTC ingediend als die "
                               "nog niet gedeclareerd is. Tip: doe eerst een proefrun.",
        "cancel": "Annuleren",
        "months": ["jan", "feb", "mrt", "apr", "mei", "jun",
                   "jul", "aug", "sep", "okt", "nov", "dec"],
    },
}


def pick_lang(query_lang, cookie_lang, accept_language):
    for candidate in (query_lang, cookie_lang):
        if candidate in LANGS:
            return candidate
    prefs = []
    for part in (accept_language or "").split(","):
        tag, _, q = part.strip().partition(";q=")
        try:
            prefs.append((float(q or 1), tag.split("-")[0].lower()))
        except ValueError:
            continue
    for _, tag in sorted(prefs, key=lambda p: -p[0]):
        if tag in LANGS:
            return tag
    return "en"


class Fmt:
    """Language-aware formatting of text, numbers, amounts and dates."""

    def __init__(self, lang):
        self.lang, self.t = lang, TEXT[lang]

    def __call__(self, key, **kw):
        return self.t[key].format(**kw)

    def plural(self, n, one, many):
        return self.t[one] if n == 1 else self.t[many]

    def num(self, v, digits=1):
        s = f"{v:,.{digits}f}"
        return s.replace(",", "_").replace(".", ",").replace("_", ".") if self.lang == "nl" else s

    def euro(self, v):
        return ("€ " if self.lang == "nl" else "€") + self.num(v, 2)

    def when(self, d):
        """Run time; the browser rewrites it in the viewer's own timezone (see LOCAL_TIME_JS)."""
        if not d:
            return ""
        return (f'<time datetime="{d.isoformat()}">'
                f'{d.day} {self.t["months"][d.month - 1]} {d:%H:%M}</time>')

    def session_when(self, iso):
        try:
            d = datetime.fromisoformat(iso)  # Tesla provides local time with offset
        except (TypeError, ValueError):
            return iso or ""
        d = d.astimezone(LOCAL_TZ) if LOCAL_TZ else d
        return f"{d.day} {self.t['months'][d.month - 1]} {d.year} · {d:%H:%M}"


# --- Turn the tesla_mtc log into something readable ----------------------------

ANSI = re.compile(r"\x1b\[[0-9;]*m")
LOG_LINE = re.compile(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d \[(\w+)\] (.*)$")
PROGRESS = re.compile(r"^(.*?):\s+(\d+)%\|.*?\|\s*(\d+)/(\d+)")
LOCATION = re.compile(r"^Location: (.*) on (\S+)$")
DETAILS = re.compile(r"^Details:\s+([\d.]+) kWh, (\w+) ([\d.]+)$")
HIDDEN = ("CSRF token", "SSL certificate verification is disabled")
TOO_OLD = re.compile(r"Not submitted automatically: .* is (\d+) days old")
FAILED = ("Claim submission failed", "Failed after", "Failed to submit claim",
          "final submission attempt")


def clean_lines(lines):
    """Strip ANSI colours and noisy/sensitive lines; keep only the last progress bar state."""
    out = []
    for raw in lines:
        line = ANSI.sub("", raw)
        if any(h in line for h in HIDDEN):
            continue
        if PROGRESS.match(line) and out and PROGRESS.match(out[-1]):
            out[-1] = line
            continue
        out.append(line)
    return out


def parse_log(lines):
    result = {"sessions": [], "notes": [], "progress": None, "step": None,
              "found": None, "skiplist": None, "blocked": False, "crashed": False}
    current = None
    for line in clean_lines(lines):
        if not line.strip():
            continue
        if "LIVE mode blocked" in line:
            result["blocked"] = True
        if line.startswith("Traceback") or "Process failed:" in line:
            result["crashed"] = True
        p = PROGRESS.match(line)
        if p:
            result["progress"] = (int(p.group(3)), int(p.group(4)))
            result["step"] = "fetch"
            continue
        m = LOG_LINE.match(line)
        if not m:
            if "--- Processing Session ---" in line:
                current = {"location": "?", "when": None, "kwh": None, "amount": None,
                           "currency": "EUR", "status": "busy", "detail": None}
                result["sessions"].append(current)
                result["step"] = "session"
            continue
        level, msg = m.group(1), m.group(2).strip()
        if msg.startswith("Found ") and "charging sessions" in msg:
            result["found"] = int(re.findall(r"\d+", msg)[0])
            continue
        if msg.startswith("Loaded ") and "skip.txt" in msg:
            result["skiplist"] = int(re.findall(r"\d+", msg)[0])
            continue
        if msg == "MTC Login successful.":
            result["step"] = "login"
            continue
        if current is not None:
            loc, det = LOCATION.match(msg), DETAILS.match(msg)
            if loc:
                current["location"], current["when"] = loc.group(1), loc.group(2)
                continue
            if det:
                current["kwh"], current["currency"] = float(det.group(1)), det.group(2)
                current["amount"] = float(det.group(3))
                continue
            too_old = TOO_OLD.search(msg)
            if too_old:
                current["status"] = "tooold"
                current["detail"] = ("key_days", "d_tooold", too_old.group(1))
                continue
            if "Duplicate claim found" in msg:
                current["status"] = "duplicate"
                continue
            if "[DRY RUN] Would submit claim" in msg:
                current["status"] = "would"
                continue
            if "Successfully submitted claim" in msg:
                current["status"] = "submitted"
                continue
            if "No invoice found" in msg:
                current["status"], current["detail"] = "noinvoice", ("key", "d_noinvoice")
                continue
            if "Daily limit reached" in msg:
                current["detail"] = ("key", "d_limit")
                continue
            if any(f in msg for f in FAILED):
                current["status"], current["detail"] = "failed", ("raw", msg)
                continue
            if msg.startswith(("Submission attempt", "Submitting claim", "Retrying with")):
                continue
        if level in ("WARNING", "ERROR") or "skip" in msg.lower():
            result["notes"].append((level, msg))
    return result


def summary(snap, parsed):
    sessions = parsed["sessions"]
    count = lambda s: sum(1 for x in sessions if x["status"] == s)
    total = lambda s: sum(x["amount"] or 0 for x in sessions if x["status"] == s)
    ok = snap["exit_code"] == 0 and not parsed["blocked"] and not parsed["crashed"]
    return {
        "running": snap["running"], "mode": snap["mode"], "max_sessions": snap["sessions"],
        "started": snap["started"].isoformat() if snap["started"] else None,
        "finished": snap["finished"].isoformat() if snap["finished"] else None,
        "ok": ok if snap["finished"] else None,
        "sessions": len(sessions), "submitted": count("submitted"),
        "would_submit": count("would"), "too_old": count("tooold"),
        "duplicates": count("duplicate"),
        "failed": count("failed") + count("noinvoice"),
        "amount_submitted": round(total("submitted"), 2),
        "amount_would_submit": round(total("would"), 2),
    }


# --- Page ---------------------------------------------------------------------

# Tesla logo from Simple Icons (simpleicons.org); trademark of Tesla, Inc.
TESLA_SVG = ('<svg viewBox="0 0 24 24" role="img" aria-label="Tesla"><path fill="currentColor" d="'
             'M12 5.362l2.475-3.026s4.245.09 8.471 2.054c-1.082 1.636-3.231 2.438-3.231 2.438-.146-1.439'
             '-1.154-1.79-4.354-1.79L12 24 8.619 5.034c-3.18 0-4.188.354-4.335 1.792 0 0-2.146-.795-3.229'
             '-2.43C5.28 2.431 9.525 2.34 9.525 2.34L12 5.362l-.004.002H12v-.002zm0-3.899c3.415-.03 7.326'
             '.528 11.328 2.28.535-.968.672-1.395.672-1.395C19.625.612 15.528.015 12 0 8.472.015 4.375.61 '
             '0 2.349c0 0 .195.525.672 1.396C4.674 1.989 8.585 1.435 12 1.46v.003z"/></svg>')

LOGO_TYPES = {".svg": "image/svg+xml", ".png": "image/png", ".webp": "image/webp"}


def mtc_logo_file():
    for ext in LOGO_TYPES:
        path = os.path.join(RUNNER_DIR, "mtc-logo" + ext)
        if os.path.isfile(path):
            return path
    return None


CSS = """
:root{--bg:#f4f5f7;--card:#fff;--text:#16181d;--muted:#667085;--line:#e4e7ec;
--accent:#2563eb;--accent-soft:#e8f0fe;--danger:#d92d20;--danger-soft:#fee4e2;
--ok:#067647;--ok-soft:#dcfae6;--ok-line:rgba(6,118,71,.45);--on-ok:#fff;
--warn:#b54708;--warn-soft:#fef0c7;--grey-soft:#eef0f3;--tesla:#e31937}
@media (prefers-color-scheme:dark){:root{--bg:#0f1115;--card:#181b21;--text:#eceef2;
--muted:#98a2b3;--line:#2a2f38;--accent:#5b8def;--accent-soft:#1c2a45;--danger:#f97066;
--danger-soft:#3b1c1a;--ok:#47cd89;--ok-soft:#133224;--ok-line:rgba(71,205,137,.5);
--on-ok:#0b2a1a;--warn:#fdb022;--warn-soft:#3a2a10;--grey-soft:#232730}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:15px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:640px;margin:0 auto;padding:0 16px 40px}
.hero{position:relative;margin:0 -16px 4px;padding:20px 20px 22px;color:#fff;
background:radial-gradient(120% 140% at 0% 0%,#3a0d12 0%,#16181d 55%,#0d1b33 100%)}
@media (min-width:672px){.hero{margin:16px 0 4px;border-radius:18px}}
.logos{display:flex;align-items:center;gap:12px;margin-bottom:14px}
.logo{display:flex;align-items:center;justify-content:center;width:52px;height:52px;border-radius:14px;
background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.14)}
.logo svg{width:30px;height:30px}
.logo.tesla{color:var(--tesla)}
.logo.mtc{width:auto;min-width:52px;padding:0 10px;font:800 15px/1 system-ui,sans-serif;letter-spacing:.06em}
.logo.mtc img{max-height:32px;max-width:110px}
.flow{display:flex;gap:4px;color:rgba(255,255,255,.55)}
.flow i{width:6px;height:6px;border-radius:50%;background:currentColor;animation:pulse 1.6s infinite}
.flow i:nth-child(2){animation-delay:.2s}.flow i:nth-child(3){animation-delay:.4s}
@keyframes pulse{0%,100%{opacity:.25}50%{opacity:1}}
@media (prefers-reduced-motion:reduce){.flow i{animation:none;opacity:.6}}
.hero h1{font-size:1.4em;margin:0}
.hero p{margin:2px 0 0;color:rgba(255,255,255,.7)}
.langs{position:absolute;top:16px;right:16px;display:flex;border:1px solid rgba(255,255,255,.25);border-radius:99px;overflow:hidden}
.langs a{color:#fff;text-decoration:none;font-size:.75em;font-weight:700;padding:4px 10px;opacity:.7}
.langs a.on{background:rgba(255,255,255,.18);opacity:1}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px;margin:12px 0}
.btns{display:grid;grid-template-columns:1fr 1fr;gap:10px}
button{width:100%;padding:14px 10px;font:600 1em system-ui,sans-serif;border:0;border-radius:10px;cursor:pointer}
.dry{background:var(--accent);color:#fff}
.live{background:var(--danger);color:#fff}
.ghost{background:var(--grey-soft);color:var(--text)}
button:disabled{opacity:.45;cursor:default}
.hint{color:var(--muted);font-size:.85em;margin:10px 0 0}
.hint.off{color:var(--warn)}
.hint code,.banner code{font-size:.95em}
.pick{border:0;margin:0 0 14px;padding:0}
.pick legend{font-size:.85em;color:var(--muted);margin-bottom:6px;padding:0}
.seg{position:relative;display:grid;grid-auto-flow:column;grid-auto-columns:1fr;gap:3px;
padding:3px;background:var(--grey-soft);border-radius:10px}
.seg input{position:absolute;opacity:0;pointer-events:none}
.seg label{text-align:center;padding:8px 0;border-radius:8px;font-weight:600;color:var(--muted);cursor:pointer}
.seg input:checked+label{background:var(--card);color:var(--text);box-shadow:0 1px 3px rgba(0,0,0,.18)}
.seg input:focus-visible+label{outline:2px solid var(--accent);outline-offset:1px}
.seg input:disabled+label{opacity:.5;cursor:default}
.scope{color:var(--muted);font-size:.8em;font-weight:400;margin-left:6px}
.bar{height:8px;background:var(--grey-soft);border-radius:4px;overflow:hidden;margin:10px 0 4px}
.bar>div{height:100%;background:var(--accent);transition:width .4s}
.banner{display:flex;gap:10px;align-items:flex-start}
.banner .icon{font-size:1.4em;line-height:1}
.banner b{display:block}
.ok{background:var(--ok-soft)} .err{background:var(--danger-soft)} .info{background:var(--accent-soft)}
.chip{display:inline-block;font-size:.7em;font-weight:700;padding:2px 8px;border-radius:99px;vertical-align:middle;margin-left:6px}
.chip.DRY{background:var(--accent-soft);color:var(--accent)} .chip.LIVE{background:var(--danger-soft);color:var(--danger)}
.stats{display:grid;grid-template-columns:repeat(2,1fr);gap:10px}
.stat{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px}
.stat .n{font-size:1.5em;font-weight:700}
.stat .l{color:var(--muted);font-size:.85em}
.session{display:flex;justify-content:space-between;gap:12px;padding:12px 0;border-top:1px solid var(--line)}
.session:first-of-type{border-top:0}
.session .loc{font-weight:600}
.session .meta{color:var(--muted);font-size:.85em}
.session .right{text-align:right;white-space:nowrap}
.amount{font-weight:700}
.badge{display:inline-block;font-size:.75em;font-weight:600;padding:2px 8px;border-radius:99px;margin-top:4px}
.b-submitted{background:var(--ok);color:var(--on-ok)}
.b-duplicate{color:var(--ok);box-shadow:inset 0 0 0 1px var(--ok-line)}
.b-tooold{background:var(--warn-soft);color:var(--warn)}
.detail.note{color:var(--muted)}
.b-would{background:var(--accent-soft);color:var(--accent)}
.b-busy{background:var(--grey-soft);color:var(--muted)}
.b-failed,.b-noinvoice{background:var(--danger-soft);color:var(--danger)}
.detail{font-size:.8em;color:var(--danger);margin-top:2px}
h2{font-size:1em;margin:0 0 4px}
ul.notes{margin:6px 0 0;padding-left:18px} ul.notes li{margin:4px 0}
.WARNING{color:var(--warn)} .ERROR{color:var(--danger)}
details summary{cursor:pointer;color:var(--muted)}
pre{background:var(--grey-soft);padding:12px;border-radius:8px;white-space:pre-wrap;word-break:break-word;font-size:.75em;max-height:60vh;overflow:auto}
dialog{border:0;border-radius:16px;padding:20px;max-width:min(420px,calc(100vw - 32px));background:var(--card);color:var(--text)}
dialog::backdrop{background:rgba(0,0,0,.55)}
dialog h3{margin:0 0 8px;font-size:1.1em}
dialog p{margin:0 0 16px;color:var(--muted)}
@media (max-width:360px){.btns{grid-template-columns:1fr}}
"""


BADGE_ICON = {"submitted": "✓ ", "duplicate": "✓ ", "tooold": "⏸ ", "would": "● ",
              "failed": "✕ ", "noinvoice": "✕ ", "busy": ""}

# Fills in the confirmation text for the number of sessions currently selected
CONFIRM_JS = """
function openConfirm(){
  var d=document.getElementById('confirm'),r=document.querySelector('.seg input:checked');
  var n=r?r.value:d.dataset.picked;
  d.querySelector('input[name=sessions]').value=n;
  d.querySelector('p').textContent=d.dataset.dry===n?d.dataset.known:
    n==='1'?d.dataset.unknownOne:d.dataset.unknown.replace('{n}',n);
  d.showModal();
}
document.addEventListener('DOMContentLoaded',function(){
  var fmt=new Intl.DateTimeFormat(document.documentElement.lang,
    {day:'numeric',month:'short',hour:'2-digit',minute:'2-digit',hour12:false});
  document.querySelectorAll('time[datetime]').forEach(function(t){
    t.textContent=fmt.format(new Date(t.getAttribute('datetime'))).replace(',','');
  });
});
"""


def live_trigger(label, disabled):
    disabled = disabled if ALLOW_LIVE else "disabled"
    return (f'<button type="button" class="live" {disabled} '
            f'onclick="openConfirm()">{label}</button>')


def render(snap, lang, picked):
    f, esc = Fmt(lang), html.escape
    qs = f"&token={esc(TOKEN)}" if TOKEN else ""
    parsed = parse_log(snap["log"])
    s = summary(snap, parsed)
    running = snap["running"]
    dis = "disabled" if running else ""
    chip = (f'<span class="chip {snap["mode"]}">{f("chip_" + snap["mode"])}</span>'
            f'<span class="scope">{f("scope_one") if snap["sessions"] == 1 else f("scope", n=snap["sessions"])}</span>'
            if snap["mode"] else "")
    parts = []

    mtc = ('<img src="/logo/mtc' + (f"?token={esc(TOKEN)}" if TOKEN else "") + '" alt="MultiTankCard">'
           if mtc_logo_file() else "MTC")
    lang_link = lambda code: (f'<a href="/?lang={code}{qs}" class="{"on" if code == lang else ""}" '
                              f'hreflang="{code}">{code.upper()}</a>')
    parts.append(f'<header class="hero"><nav class="langs">{lang_link("en")}{lang_link("nl")}</nav>'
                 f'<div class="logos"><span class="logo tesla">{TESLA_SVG}</span>'
                 f'<span class="flow" aria-hidden="true"><i></i><i></i><i></i></span>'
                 f'<span class="logo mtc">{mtc}</span></div>'
                 f'<h1>Tesla → MultiTankCard</h1><p>{f("subtitle")}</p></header>')

    choices = sorted(set(SESSION_CHOICES) | {picked})
    seg = "".join(f'<input type="radio" name="sessions" id="s{n}" value="{n}" {dis}'
                  f'{" checked" if n == picked else ""}><label for="s{n}">{n}</label>'
                  for n in choices)
    parts.append(f'<form class="card" method="post" action="/run?mode=DRY{qs}">'
                 f'<fieldset class="pick"><legend>{f("pick_label")}</legend>'
                 f'<div class="seg">{seg}</div></fieldset><div class="btns">'
                 f'<button class="dry" {dis}>{f("dry")}</button>{live_trigger(f("live"), dis)}'
                 f'</div><p class="hint">{f("hint")}</p>'
                 + ("" if ALLOW_LIVE else f'<p class="hint off">🔒 {f("live_off")}</p>')
                 + '</form>')

    if running:
        done, total = parsed["progress"] or (0, 0)
        pct = int(done / total * 100) if total else 5
        step = f("step_" + (parsed["step"] or "start"), i=len(parsed["sessions"]),
                 n=parsed["found"] or "?")
        if parsed["step"] == "session" and parsed["found"]:
            pct = int(len(parsed["sessions"]) / parsed["found"] * 100)
        elif parsed["step"] == "fetch" and total:
            step += f" ({done}/{total})"
        parts.append(f'<div class="card"><b>{f("running")}</b>{chip}'
                     f'<div class="bar"><div style="width:{pct}%"></div></div>'
                     f'<div class="hint">{esc(step)}…</div></div>')
    elif snap["finished"]:
        when = f.when(snap["finished"])
        if parsed["blocked"]:
            parts.append(f'<div class="card banner err"><span class="icon">⛔</span><div>'
                         f'<b>{f("blocked_title")}{chip}</b>{f("blocked_text")}</div></div>')
        elif not s["ok"]:
            parts.append(f'<div class="card banner err"><span class="icon">⚠️</span><div>'
                         f'<b>{f("failed_title")}{chip}</b>{f("failed_text", when=when)}</div></div>')
        elif snap["mode"] == "DRY" and s["would_submit"]:
            n = s["would_submit"]
            text = f("dry_ready", n=n, claims=f.plural(n, "claim", "claims"),
                     amount=f.euro(s["amount_would_submit"]))
            submit_now = (f'<div style="margin-top:10px">{live_trigger(f("live_now"), "")}</div>'
                          if ALLOW_LIVE else "")
            parts.append(f'<div class="card banner info"><span class="icon">🔎</span><div style="flex:1">'
                         f'<b>{f("dry_title")}{chip}</b>{text}{submit_now}</div></div>')
        elif snap["mode"] == "DRY":
            parts.append(f'<div class="card banner ok"><span class="icon">✅</span><div>'
                         f'<b>{f("dry_title")}{chip}</b>{f("dry_none", when=when)}</div></div>')
        else:
            n = s["submitted"]
            text = (f("live_done", when=when, n=n, claims=f.plural(n, "claim", "claims"),
                      amount=f.euro(s["amount_submitted"])) if n else f("live_none", when=when))
            parts.append(f'<div class="card banner ok"><span class="icon">✅</span><div>'
                         f'<b>{f("live_title")}{chip}</b>{text}</div></div>')
    else:
        parts.append(f'<div class="card hint" style="margin-top:12px">{f("no_run")}</div>')

    if parsed["sessions"]:
        first = (("stat_submitted", s["submitted"]) if snap["mode"] == "LIVE"
                 else ("stat_would", s["would_submit"]))
        stats = [("stat_sessions", s["sessions"]), first,
                 ("stat_duplicate", s["duplicates"]), ("stat_attention", s["failed"] + s["too_old"])]
        parts.append('<div class="stats">' + "".join(
            f'<div class="stat"><div class="n">{n}</div><div class="l">{f(k)}</div></div>'
            for k, n in stats) + "</div>")

        rows = []
        for x in parsed["sessions"]:
            meta = f.session_when(x["when"])
            if x["kwh"] is not None:
                meta += f" · {f.num(x['kwh'])} kWh"
            if x["amount"] is None:
                amount = ""
            elif x["currency"] == "EUR":
                amount = f.euro(x["amount"])
            else:
                amount = f'{x["currency"]} {f.num(x["amount"], 2)}'
            detail = ""
            if x["detail"]:
                kind, value = x["detail"][:2]
                if kind == "key_days":
                    detail = f'<div class="detail note">{esc(f(value, days=x["detail"][2]))}</div>'
                else:
                    detail = f'<div class="detail">{esc(f(value) if kind == "key" else value)}</div>'
            rows.append(f'<div class="session"><div><div class="loc">{esc(x["location"])}</div>'
                        f'<div class="meta">{esc(meta)}</div>{detail}</div>'
                        f'<div class="right"><div class="amount">{amount}</div>'
                        f'<span class="badge b-{x["status"]}">{BADGE_ICON[x["status"]]}'
                        f'{f("st_" + x["status"])}</span></div></div>')
        parts.append(f'<div class="card"><h2>{f("sessions")}</h2>' + "".join(rows) + "</div>")

    extra = []
    if parsed["skiplist"]:
        n = parsed["skiplist"]
        extra.append(f'<li>{f("skiplist", n=n, sessions=f.plural(n, "session", "sessions_word"))}</li>')
    extra += [f'<li class="{lvl}">{esc(msg)}</li>' for lvl, msg in parsed["notes"]]
    if extra:
        parts.append(f'<div class="card"><h2>{f("notes")}</h2><ul class="notes">'
                     + "".join(extra) + "</ul></div>")

    if snap["log"]:
        parts.append(f'<details class="card"><summary>{f("log")}</summary><pre>'
                     + esc("\n".join(clean_lines(snap["log"]))) + "</pre></details>")

    # Confirmation before submitting for real. The test-run result only applies when the
    # same number of sessions is selected; openConfirm() picks the matching text.
    dry_count, known = "", ""
    if snap["mode"] == "DRY" and s["ok"]:
        n = s["would_submit"]
        dry_count = str(snap["sessions"])
        known = (f("confirm_known", n=n, claims=f.plural(n, "claim", "claims"),
                   amount=f.euro(s["amount_would_submit"])) if n else f("confirm_none"))
    parts.append(f'<dialog id="confirm" data-picked="{picked}" data-dry="{dry_count}" '
                 f'data-known="{esc(known)}" data-unknown="{esc(TEXT[lang]["confirm_unknown"])}" '
                 f'data-unknown-one="{esc(TEXT[lang]["confirm_unknown_one"])}">'
                 f'<form method="post" action="/run?mode=LIVE{qs}">'
                 f'<input type="hidden" name="sessions" value="{picked}">'
                 f'<h3>{f("confirm_title")}</h3><p></p><div class="btns">'
                 f'<button type="button" class="ghost" onclick="this.closest(\'dialog\').close()">'
                 f'{f("cancel")}</button><button class="live">{f("live")}</button></div></form></dialog>')

    refresh = '<meta http-equiv="refresh" content="2">' if running else ""
    return (f'<!doctype html><html lang="{lang}"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<meta name="theme-color" content="#16181d">'
            f'<title>Tesla → MTC</title>{refresh}<style>{CSS}</style>'
            f'<script>{CONFIRM_JS}</script></head><body><main>'
            + "".join(parts) + "</main></body></html>")


class Handler(BaseHTTPRequestHandler):
    def _authorized(self):
        if not TOKEN:
            return True
        token = parse_qs(urlparse(self.path).query).get("token", [""])[0]
        return token == TOKEN or self.headers.get("Authorization") == f"Bearer {TOKEN}"

    def _send(self, code, body, ctype="text/html; charset=utf-8", headers=()):
        data = body if isinstance(body, bytes) else body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        for name, value in headers:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(data)

    def _snapshot(self):
        with lock:
            return dict(state, log=list(state["log"]))

    def _cookie(self, name):
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        return cookie[name].value if name in cookie else ""

    def _picked_sessions(self):
        value = self._cookie("sessions")
        return int(value) if value.isdigit() and 1 <= int(value) <= SESSION_LIMIT else default_sessions()

    def do_GET(self):
        if not self._authorized():
            return self._send(401, "Unauthorized")
        url = urlparse(self.path)
        if url.path == "/status":
            snap = self._snapshot()
            return self._send(200, json.dumps(dict(summary(snap, parse_log(snap["log"])),
                                                   allow_live=ALLOW_LIVE)),
                              "application/json")
        if url.path == "/logo/mtc":
            path = mtc_logo_file()
            if not path:
                return self._send(404, "Not found")
            with open(path, "rb") as fh:
                return self._send(200, fh.read(), LOGO_TYPES[os.path.splitext(path)[1]],
                                  [("Cache-Control", "max-age=86400")])
        if url.path != "/":
            return self._send(404, "Not found")
        query_lang = parse_qs(url.query).get("lang", [""])[0].lower()
        lang = pick_lang(query_lang, self._cookie("lang"), self.headers.get("Accept-Language"))
        headers = ([("Set-Cookie", f"lang={lang}; Max-Age=31536000; Path=/; SameSite=Lax")]
                   if query_lang in LANGS else [])
        self._send(200, render(self._snapshot(), lang, self._picked_sessions()), headers=headers)

    def do_POST(self):
        if not self._authorized():
            return self._send(401, "Unauthorized")
        url = urlparse(self.path)
        if url.path != "/run":
            return self._send(404, "Not found")
        length = int(self.headers.get("Content-Length") or 0)
        params = parse_qs(self.rfile.read(length).decode("utf-8", "replace") if length else "")
        params.update(parse_qs(url.query))
        mode = params.get("mode", ["DRY"])[0].upper()
        if mode not in MODES:
            return self._send(400, json.dumps({"error": "mode must be DRY or LIVE"}),
                              "application/json")
        if mode == "LIVE" and not ALLOW_LIVE:
            return self._send(403, json.dumps({"error": "submitting is disabled; set RUNNER_ALLOW_LIVE=true"}),
                              "application/json")
        raw = params.get("sessions", [str(self._picked_sessions())])[0]
        if not raw.isdigit() or not 1 <= int(raw) <= SESSION_LIMIT:
            return self._send(400, json.dumps({"error": f"sessions must be 1-{SESSION_LIMIT}"}),
                              "application/json")
        sessions = int(raw)
        started = start_run(mode, sessions)
        if "text/html" in self.headers.get("Accept", ""):
            self.send_response(303)
            self.send_header("Location", "/" + (f"?token={TOKEN}" if TOKEN else ""))
            self.send_header("Set-Cookie",
                             f"sessions={sessions}; Max-Age=31536000; Path=/; SameSite=Lax")
            self.end_headers()
        else:
            self._send(202 if started else 409,
                       json.dumps({"started": started, "mode": mode, "sessions": sessions}),
                       "application/json")

    def log_message(self, fmt, *args):
        pass  # no access-log noise (the token is in the URL)


if __name__ == "__main__":
    print(f"tesla_mtc web runner listening on port {PORT} "
          f"(submitting {'enabled' if ALLOW_LIVE else 'disabled, test runs only'})", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
