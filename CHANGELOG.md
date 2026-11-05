# Changelog

All notable changes to this project are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- A workflow that publishes a ready-made Docker image to `ghcr.io/wessec/tesla_mtc` for amd64 and
  arm64 (for example a Raspberry Pi or an ARM-based NAS) with every release, after a smoke test.

## [1.2.1] - 2026-11-05

### Security
- MTC's certificate is verified again. Verification used to be switched off because MTC's
  server presents its `*.mtc.nl` certificate under another host name. The certificate chain is
  now checked against the trusted certificate authorities and the certificate must be valid
  for `mtc.nl`; if MTC installs a certificate for its real host name, the normal check is used.
  Your MTC password and IBAN are no longer sent over an unverified connection.
- The MTC session token (CSRF token) and session cookie values are no longer written to the log.

### Fixed
- Every call to Tesla and MTC has a timeout (10 s to connect, 60 s to answer), so a server that
  stops answering ends the run with an error instead of leaving it running forever. When MTC does
  not answer after a claim was sent, that claim is never sent again in the same run (it may have
  arrived); the session is reported as failed, and the next run's duplicate check finds it if it did.
- The web interface stops a run that has not finished after `RUNNER_RUN_TIMEOUT` seconds
  (default 900) and shows it as failed, so the page and Home Assistant never stay on *Running*.

### Added
- Docker health check on the web interface (*healthy*/*unhealthy* in Docker and Synology
  Container Manager) and image labels with source and license.
- Automatic checks on every push and pull request: lint, unit tests with fictional data
  (duplicate check, safety limits, the claim sent to MTC, Tesla session handling, certificate
  checks and timeouts, and the web interface's log reading and safety checks) and a Docker build
  with a smoke test. Run them locally with `pip install -r requirements-dev.txt`, `ruff check .`
  and `pytest`.
- Issue templates that remind you to remove tokens, passwords, IBAN and VIN from logs.
- Monthly grouped dependency updates (Dependabot) and status badges in the README.

### Changed
- `tqdm` is pinned (4.70.1) like the other dependencies, so Dependabot can keep it up to date.
- `requests` updated to 2.34.2 and `python-dotenv` to 1.2.4.
- The startup warning about switched-off certificate verification is gone.

## [1.2.0] - 2026-10-14

### Added
- Web interface (`web/runner.py`, standard library only): start runs from a phone or
  Home Assistant, test run first and submit after a confirmation, a formatted result
  per charging session, choice of how many recent sessions to check, English and Dutch,
  light and dark mode, and a `/status` JSON endpoint. Submitting stays off until
  `RUNNER_ALLOW_LIVE=true`. Requests from other websites are refused (CSRF), the
  page cannot be framed, and the optional `RUNNER_TOKEN` is compared in constant time.
  See `docs/web-runner.md`.
- `Dockerfile`, `.dockerignore` and `docker-compose.example.yml`. The container keeps
  running between runs instead of stopping after each one.
- Documentation with setup instructions (Docker Compose, Synology Container Manager,
  without Docker), a first-use walkthrough with screenshots, and Home Assistant examples.

## [1.1.1] - 2026-10-14

### Changed
- `MTC_MAX_RECORDS` defaults to 400 (was 500). Measured against MTC: the transaction list
  returns exactly `MaxRecords` items, newest first, without pagination, verified up to 473.
  The default stays inside that range, so a cut-off list is always recognisable.

### Fixed
- When MTC returns the maximum number of transactions, the list may have been cut off
  before an older claim. Sessions older than the oldest returned transaction are now
  reported ("could not be checked for duplicates") instead of submitted.

## [1.1.0] - 2026-10-13

### Added
- Version number (`version.py`), logged at startup.
- `MAX_SESSION_AGE_DAYS` (default 60): older sessions are never submitted automatically,
  only reported, since MTC claims cannot be withdrawn. Set to `0` to disable.
- Log line with the number of MTC transactions and the start date used for the
  duplicate check.

### Changed
- Added Jochem Pieper as copyright holder in the MIT License.

### Fixed
- Only the 50 most recent MTC transactions were used for the duplicate check, so with
  many transactions older claims were missed and could be submitted again. The limit is
  now `MTC_MAX_RECORDS` (default 500), with a warning when it is reached.
- Sessions older than `LOOKBACK_PERIOD` were not recognised as already claimed and
  could be submitted again. The duplicate check now always looks back far enough to
  cover the session being processed, plus a margin for claims moved to an earlier
  date by MTC's daily claim limit.

## [1.0.0]

Baseline: the state of the project when maintenance moved to @Jochemp88
(commit `0df93ae`).

[1.2.0]: https://github.com/WesSec/tesla_mtc/compare/v1.1.1...v1.2.0
[1.1.1]: https://github.com/WesSec/tesla_mtc/compare/v1.1.0...v1.1.1
[1.1.0]: https://github.com/WesSec/tesla_mtc/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/WesSec/tesla_mtc/releases/tag/v1.0.0
