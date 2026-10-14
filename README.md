# Tesla MTC Integration

Project Status: Created by @WesSec. Active maintenance, issue triage, and releases are now led by @Jochemp88.

Automatically submit Tesla charging sessions to MultiTankCard (MTC) for reimbursement. This tool integrates with both Tesla's API to fetch your charging sessions and MTC's reimbursement system to automate the submission process.

⚠️ **Use at your own risk:** Wrongfully submitting reimbursements could be seen and punished as fraud. Always verify that submissions match your actual charging sessions. ⚠️

### Security Notice

Never commit your `.env` file or share your:

- Tesla refresh token/credentials
- MTC credentials
- IBAN number 
- Vehicle VIN

## How It Works

1. Fetches recent charging sessions from your Tesla account.
2. Processes charging data, handling both Dutch and supported foreign country invoices.
3. Checks for duplicate submissions in your MTC history to prevent re-submitting.
4. For unknown countries, it prompts you to decide whether to submit as a Dutch invoice or skip.
5. Submits reimbursement claims with the required invoice PDF attached.

## Setup

1. Clone this repository
2. Copy `.env.example` to `.env`
3. Fill in your credentials and preferences in `.env`
4. Install required packages:

   ```bash
   pip install -r requirements.txt
   sudo apt install poppler-utils
   ```

## Web Interface (optional)

<img src="docs/images/3-test-run-result.jpg" alt="Web interface showing the result of a test run" width="260" align="right">

Prefer tapping a button over running a script? The web interface runs tesla_mtc in Docker (for example on a Synology NAS) and lets you start it from your phone or Home Assistant:

- test run first, then submit after a confirmation;
- one card per charging session showing whether it is ready to submit or already claimed;
- choose how many recent sessions to check;
- English and Dutch, light and dark mode;
- submitting stays switched off until you enable it.

Quick start with Docker Compose:

```bash
cp .env.example .env              # then fill it in
touch skip.txt
cp docker-compose.example.yml docker-compose.yml
docker compose up -d --build      # open http://<your-server>:8080
```

See **[docs/web-runner.md](docs/web-runner.md)** for the full setup (including Synology Container Manager), a first-use walkthrough with screenshots, and Home Assistant examples.

<br clear="right">

## Environment Variables

### Required Settings

- `TESLA_VIN`: Your Tesla vehicle identification number
- `TESLA_REFRESH_TOKEN`: Tesla API refresh token (see below)
- `IBAN`: Your bank account number for reimbursement payouts
- `MTC_USERNAME`: MTC platform username
- `MTC_PASSWORD`: MTC platform password
- `MODE`: Set to 'DRY' for testing (no actual submissions, anything !DRY will actually submit)

### Optional Settings

- `LOG_LEVEL`: App logging level (default: INFO)
- `MAX_SESSIONS`: Number of recent charging sessions to process (default: 1)
- `DEVICE_COUNTRY`: Country code (default: NL)
- `DEVICE_LANGUAGE`: Language code (default: nl)
- `TTP_LOCALE`: Locale setting (default: nl_NL)
- `UNKNOWN_COUNTRY_ACTION`: How to handle invoices from unknown countries. Use `SKIP` for automated/NAS environments, `PROMPT` for interactive, or `DUTCH` to force as local (default: PROMPT).
- `LOOKBACK_PERIOD`: Months of MTC history searched for earlier claims (default: 6). The check always reaches back far enough to cover the session being processed.
- `MTC_MAX_RECORDS`: Maximum number of MTC transactions fetched for the duplicate check (default: 500). MTC returns the newest first, so this must cover your whole `LOOKBACK_PERIOD`; a warning is logged when the maximum is reached.
- `MAX_SESSION_AGE_DAYS`: Sessions older than this are never submitted automatically, only reported, because MTC claims cannot be withdrawn (default: 60, `0` disables the limit).

## ⚠️ Notice on Foreign Currencies

This tool automatically skips charging sessions billed in currencies other than Euros (EUR) (e.g., GBP in the UK). Because MTC processes all claims as EUR, submitting foreign currencies leads to incorrect payouts. These invoices must be converted and declared manually.

## Getting Your Tesla Refresh Token

https://tesla-info.com/tesla-token.php

## Features

- **Duplicate Detection**: Automatically checks both the `ClaimNote` (Toelichting) and `Note` (Notities) fields in your MTC transaction history for the Tesla charging session ID to prevent duplicate submissions. (Tip: If you manually declare an invoice, paste the Tesla Session ID into the MTC Note field to ensure the script ignores it!).
- **Manual Override (Skip List)**: Create a `skip.txt` file in the root directory and paste the Tesla `chargeSessionId`s you want to ignore (one per line). The script will bypass them.
- **Foreign Currency Handling**: Automatically detects and skips foreign currency invoices (e.g., GBP) to prevent incorrect EUR payouts, prompting you to declare them manually.
- **Dry Run Mode**: Test the system and view planned submissions without actually sending claims to MTC (set `MODE=DRY` in your `.env`).

## Versioning

This project follows [Semantic Versioning](https://semver.org). The current version is in `version.py`, is logged at startup, and every release is tagged as `v<version>` with its changes listed in [CHANGELOG.md](CHANGELOG.md).

## Error Handling

The system includes error handling:
- Checks for API-level errors in responses
- Validates submissions before processing

## Troubleshooting

If you encounter issues:
1. Check your `.env` configuration
2. Verify your MTC credentials
3. Ensure your Tesla refresh token is valid
4. Review logs for detailed error messages
5. Try running in DRY mode first
