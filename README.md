# Tesla MTC Integration

Automatically submit Tesla charging sessions to MultiTankCard (MTC) for reimbursement. This tool integrates with both Tesla's API to fetch your charging sessions and MTC's reimbursement system to automate the submission process.

⚠️ **Use at your own risk:** Wrongfully submitting reimbursements could be seen and punished as fraud. Always verify that submissions match your actual charging sessions. ⚠️

### Security Notice
Never commit your `.env` file or share your:
- Tesla refresh token/credentials
- MTC credentials
- IBAN number 
- Vehicle VIN

[!["Buy Me A Coffee"](https://www.buymeacoffee.com/assets/img/custom_images/orange_img.png)](https://www.buymeacoffee.com/wessec)

## How It Works

1. Fetches recent charging sessions from your Tesla account
2. Processes charging data into MTC-compatible format
3. Checks for duplicate submissions using charging session IDs
4. Submits reimbursement claims with required documentation
5. Handles session management and authentication for both APIs

## Setup

1. Clone this repository
2. Copy `.env.example` to `.env`
3. Fill in your credentials and preferences in `.env`
4. Install required packages:
   ```bash
   pip install -r requirements.txt
   sudo apt install poppler-utils
   ```

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

## Getting Your Tesla Refresh Token

https://tesla-info.com/tesla-token.php

## Features

- **Duplicate Detection**: Uses charging session IDs in the comment field to prevent duplicate submissions
- **Dry Run Mode**: Test the system without making actual submissions

## Error Handling

The system includes error handling:
- Checks for API-level errors in responses
- Validates submissions before processing

## Limitations

- Only processes supercharging sessions
- Currently supports Netherlands-based submissions (But does not check for it, my lease company does not mind the country in the submission)

## Troubleshooting

If you encounter issues:
1. Check your `.env` configuration
2. Verify your MTC credentials
3. Ensure your Tesla refresh token is valid
4. Review logs for detailed error messages
5. Try running in DRY mode first
