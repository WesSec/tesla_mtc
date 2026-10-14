# Changelog

All notable changes to this project are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project follows [Semantic Versioning](https://semver.org/).

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
