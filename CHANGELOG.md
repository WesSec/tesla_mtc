# Changelog

All notable changes to this project are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project follows [Semantic Versioning](https://semver.org/).

## [1.1.0] - Unreleased

### Added
- Version number (`version.py`), logged at startup.
- `MAX_SESSION_AGE_DAYS` (default 60): older sessions are never submitted automatically,
  only reported, since MTC claims cannot be withdrawn. Set to `0` to disable.
- Log line with the number of MTC transactions and the start date used for the
  duplicate check.

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
