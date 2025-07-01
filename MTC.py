# MTC.py
"""
Client for interacting with the MultiTankcard (MTC) system.

This module provides the MTCClient class, which handles authentication,
session management, fetching transaction history, and submitting
reimbursement claims to the MTC platform. It is designed to be used
by applications that automate interactions with MTC, such as for
submitting EV charging session reimbursements.

The client manages cookies, CSRF tokens, and API versioning by
fetching necessary information from MTC's web resources.
"""

import os
import requests
import time
import logging
import re
from typing import Dict, Tuple, Any
from urllib.parse import unquote
from dotenv import load_dotenv
from datetime import datetime, timedelta, timezone
import sys # Required for logging to stdout in test_mtc_client

# For debugging purposes, can be removed or commented out in production
# from requests_toolbelt.utils import dump

# Module-level logger
# It's good practice to get the logger by the module's name.
# Applications using this module can then configure this logger.
logger = logging.getLogger(__name__)

# API version patterns for different endpoints.
# These patterns are used to extract dynamic API version strings from MTC's JavaScript files.
# Each key is an internal identifier for an endpoint, and the value is a tuple containing:
# 1. The regex pattern to find the API version.
# 2. The name of the JavaScript file where this pattern is expected.
API_PATTERNS: Dict[str, Tuple[str, str]] = {
    "appstoreurls": (
        'GetAppStoreUrls", "screenservices/OnTheMoveMultiTankcard_CW/ActionGetAppStoreUrls", "([^"]+)"',
        "OnTheMoveMultiTankcard_CW.controller.js",
    ),
    "login": (
        'AppLogin", "screenservices/OtmAcc_Account/ActionAppLogin", "([^"]+)"',
        "OtmAcc_Account.controller.js",
    ),
    "transactions": (
        'DataActionGetTransactions", "screenservices/OtmTrx_Transactions/Screen/Overview/DataActionGetTransactions", "([^"]+)"',
        "OtmTrx_Transactions.Screen.Overview.mvc.js",
    ),
    "submit": (
        'Claim_Create", "screenservices/OtmTrx_Transactions/Claim/ClaimForm/ActionClaim_Create", "([^"]+)"',
        "OtmTrx_Transactions.Claim.ClaimForm.mvc.js",
    ),
}

# Initial CSRF token used for pre-login requests.
# This value was observed during the initial handshake with the MTC system
# and is required for the first few calls before a dynamic token is issued post-login.
INITIAL_CSRF_TOKEN: str = "T6C+9iB49TLra4jEsMeSckDMNhQ="


class MTCClient:
    """
    A client for interacting with the MultiTankcard (MTC) web application.

    Manages session authentication, retrieves API versions, fetches transaction
    history for deduplication, and submits reimbursement claims.

    Attributes:
        base_url (str): The base URL for the MTC OutSystems enterprise server.
        csrf_token (str): The current CSRF token for session-authenticated requests.
                          Starts with INITIAL_CSRF_TOKEN and is updated after login.
        username (str): MTC username, loaded from the .env file.
        password (str): MTC password, loaded from the .env file.
        session (requests.Session): The session object used for making HTTP requests,
                                    maintaining cookies and headers across requests.
        _api_versions (Dict[str, str]): A cache for fetched API endpoint versions to avoid
                                       redundant lookups.
        module_version (str): The global module version token for the MTC application,
                              fetched during initialization.
        lookback_period_months (int): Number of months to look back when fetching
                                      transactions for duplicate checking. Loaded from .env.
        visit_id (str): Stores the 'osVisit' cookie value, obtained during session init.
        visitor_id (str): Stores the 'osVisitor' cookie value, obtained during session init.
    """

    def __init__(self) -> None:
        """
        Initializes the MTCClient.

        This involves loading configuration from .env, setting up the HTTP session,
        and attempting to log in to the MTC platform. If login fails, an error
        is logged, and the client might be in a non-functional state.
        """
        load_dotenv()  # Load environment variables from .env file

        self.base_url: str = "https://mtc.outsystemsenterprise.com"
        self.csrf_token: str = INITIAL_CSRF_TOKEN # Start with the hardcoded pre-login token
        self.username: str = os.getenv("MTC_USERNAME", "")
        self.password: str = os.getenv("MTC_PASSWORD", "")

        self.session: requests.Session = self._initialize_session_headers()
        self._api_versions: Dict[str, str] = {}
        self.module_version: str = ""
        self.visit_id: str = ""
        self.visitor_id: str = ""

        try:
            self.lookback_period_months: int = int(os.getenv("LOOKBACK_PERIOD", "6"))
        except ValueError:
            logger.warning(
                "Invalid LOOKBACK_PERIOD in .env (must be an integer). Defaulting to 6 months."
            )
            self.lookback_period_months = 6

        if not self.username or not self.password:
            logger.critical(
                "MTC_USERNAME or MTC_PASSWORD not found in .env file. "
                "MTCClient cannot operate without credentials."
            )
            # Consider raising a custom exception like ConfigurationError here
            # to prevent the application from proceeding with a non-functional client.
            return

        # Attempt to log in upon initialization.
        if not self.login():
            logger.error(
                "MTC Client initialization failed: Could not log in to MTC. "
                "Subsequent operations will likely fail."
            )

    def _initialize_session_headers(self) -> requests.Session:
        """
        Creates and configures a requests.Session object with default HTTP headers
        mimicking a browser session.

        Returns:
            requests.Session: The configured session object.
        """
        session = requests.Session()
        session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
                "Accept": "application/json",
                "Content-Type": "application/json; charset=UTF-8",
                "sec-ch-ua-platform": "Windows",
                "sec-ch-ua": '"Chromium";v="136", "Google Chrome";v="136", "Not.A/Brand";v="99"',
                "sec-ch-ua-mobile": "?0",
                "Origin": self.base_url,
                "Referer": f"{self.base_url}/MultiTankcard/Login",  # Default Referer, updated contextually
                "Accept-Language": "en-US,en;q=0.9",
                "OutSystems-client-env": "browser",
            }
        )
        return session

    def _get_api_version(self, endpoint_key: str) -> str:
        """
        Retrieves the specific API version string for a given MTC endpoint.

        API versions are dynamically fetched by parsing MTC's JavaScript files.
        Fetched versions are cached in `self._api_versions` to prevent redundant requests.

        Args:
            endpoint_key: A key (e.g., 'login', 'submit') corresponding to an entry
                          in the `API_PATTERNS` dictionary.

        Returns:
            The API version string for the specified endpoint.

        Raises:
            ValueError: If the `endpoint_key` is not defined in `API_PATTERNS` or
                        if the API version pattern cannot be found in the JS file.
            requests.exceptions.RequestException: If fetching the JavaScript file fails.
        """
        if endpoint_key in self._api_versions:
            return self._api_versions[endpoint_key]

        if endpoint_key not in API_PATTERNS:
            logger.error(f"Attempted to get API version for unknown endpoint: {endpoint_key}")
            raise ValueError(f"Invalid endpoint key provided for API version retrieval: {endpoint_key}")

        pattern, js_file_name = API_PATTERNS[endpoint_key]
        js_url = f"{self.base_url}/MultiTankcard/scripts/{js_file_name}"

        try:
            response = self.session.get(
                js_url,
                headers={
                    "Accept": "*/*",
                    "Sec-Fetch-Mode": "no-cors",
                    "Referer": f"{self.base_url}/MultiTankcard/"
                },
                verify=False # It's good practice to verify SSL. Set to False only if server has known cert issues.
            )
            response.raise_for_status()
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to fetch JS file {js_url} for API version: {e}")
            raise

        match = re.search(pattern, response.text)
        if not match:
            logger.error(
                f"Could not find API version pattern in {js_file_name} for endpoint '{endpoint_key}'. "
                f"Pattern: {pattern}"
            )
            raise ValueError(f"API version for '{endpoint_key}' not found in {js_file_name}.")

        api_version = match.group(1)
        self._api_versions[endpoint_key] = api_version
        logger.debug(f"Fetched and cached API version for '{endpoint_key}': {api_version}")
        return api_version

    def _perform_pre_login_calls(self) -> str:
        """
        Performs the initial sequence of HTTP calls required before attempting the main login.

        This involves:
        1. Fetching `moduleversioninfo` to get a `versionToken` and initial session cookies
           (`osVisit`, `osVisitor`).
        2. Calling `ActionGetAppStoreUrls` to prime other necessary cookies
           (`nr1Users`, `nr2Users` with an initial CSRF token) using `INITIAL_CSRF_TOKEN`.

        Returns:
            The main module version token (`versionToken`) required for subsequent API calls.

        Raises:
            ValueError: If essential tokens or cookies cannot be obtained.
            requests.exceptions.RequestException: For network or HTTP errors.
        """
        try:
            current_epoch_ms = int(time.time() * 1000)
            module_version_url = f"{self.base_url}/MultiTankcard/moduleservices/moduleversioninfo?{current_epoch_ms}"
            
            # Note: verify=False was in original user code due to "Outsystem/MTC Sucks".
            # This implies potential SSL certificate issues on the MTC server.
            # For security, verify=True is preferred. If False is necessary, it's a known risk.
            # Based on later success, verify=True might work for this call or it was changed to True.
            # Let's assume verify=True is the goal, but acknowledge the original note.
            # The provided code had verify=False for this specific call, then verify=True later.
            # For consistency and security, let's try True here as well, if it breaks, it indicates server issue.
            # Reverting to verify=False for this specific call as per context of it working before.
            response = self.session.get(module_version_url, verify=False)
            response.raise_for_status()

            self.visit_id = self.session.cookies.get("osVisit", "")
            self.visitor_id = self.session.cookies.get("osVisitor", "")
            if not (self.visit_id and self.visitor_id):
                logger.error("Failed to obtain 'osVisit' or 'osVisitor' cookies during pre-login.")
                raise ValueError("'osVisit'/'osVisitor' cookies are missing after moduleversioninfo call.")

            logger.debug(f"Pre-login: osVisit='{self.visit_id}', osVisitor='{self.visitor_id}'")
            
            module_version_data = response.json()
            module_version = module_version_data.get("versionToken")
            if not module_version:
                logger.error("'versionToken' not found in moduleversioninfo response.")
                raise ValueError("Failed to obtain module version token from moduleversioninfo.")
            logger.debug(f"Pre-login: Fetched module version token: {module_version}")

            app_store_payload = {
                "versionInfo": {"moduleVersion": module_version, "apiVersion": self._get_api_version("appstoreurls")},
                "viewName": "*", 
                "inputParameters": {}
            }
            app_store_response = self.session.post(
                f"{self.base_url}/MultiTankcard/screenservices/OnTheMoveMultiTankcard_CW/ActionGetAppStoreUrls",
                json=app_store_payload,
                headers={
                    "X-CSRFToken": INITIAL_CSRF_TOKEN,
                    "Referer": f"{self.base_url}/MultiTankcard/Transactions"
                }
            )
            app_store_response.raise_for_status()
            logger.debug("Pre-login: ActionGetAppStoreUrls call successful.")
            logger.debug(f"Cookies after GetAppStoreUrls: {self.session.cookies.get_dict()}")
            return module_version

        except requests.exceptions.RequestException as e:
            logger.error(f"HTTP error during pre-login calls: {e}")
            raise
        except (KeyError, ValueError) as e: 
            logger.error(f"Data error during pre-login calls (e.g., missing JSON key): {e}")
            raise

    def login(self) -> bool:
        """
        Authenticates the client session with the MTC system.

        Orchestrates the login by:
        1. Executing pre-login calls via `_perform_pre_login_calls`.
        2. Sending the login request with credentials.
        3. On success, extracts the new dynamic CSRF token from the `nr2Users` cookie
           and updates `self.csrf_token` and session headers.

        Returns:
            True if login was successful and CSRF token updated, False otherwise.
        """
        try:
            self.module_version = self._perform_pre_login_calls()

            login_payload = {
                "versionInfo": {"moduleVersion": self.module_version, "apiVersion": self._get_api_version("login")},
                "viewName": "CommonMTC.Login", 
                "inputParameters": {
                    "Username": self.username,
                    "Password": self.password,
                    "KeepMeLoggedIn": True
                },
            }

            response = self.session.post(
                f"{self.base_url}/MultiTankcard/screenservices/OtmAcc_Account/ActionAppLogin",
                json=login_payload,
                headers={
                    "X-CSRFToken": INITIAL_CSRF_TOKEN, 
                    "Referer": f"{self.base_url}/MultiTankcard/Login"
                }
            )
            response.raise_for_status()
            result = response.json()

            if result.get("data", {}).get("Result") is not True:
                error_messages = result.get("data", {}).get("ErrorMessages", {}).get("List", [])
                error_text = error_messages[0].get("MessageText") if error_messages else "Unknown login error from API."
                logger.error(f"MTC Login failed: {error_text}")
                logger.debug(f"Full login failure response: {result}")
                return False

            logger.info("MTC Login successful.")

            nr2_users_cookie = self.session.cookies.get("nr2Users")
            if not nr2_users_cookie:
                logger.error("'nr2Users' cookie not found after successful login. Cannot retrieve CSRF token.")
                return False

            match = re.search(r"crf%3d(.*?)(?:%3b|$)", nr2_users_cookie)
            if not match:
                logger.error("Could not extract CSRF token pattern from 'nr2Users' cookie.")
                logger.debug(f"nr2Users cookie content: {nr2_users_cookie}")
                return False

            encoded_csrf_value = match.group(1)
            self.csrf_token = unquote(encoded_csrf_value)
            
            self.session.headers.update({"X-CSRFToken": self.csrf_token})
            logger.info(f"New dynamic CSRF token configured for session: {self.csrf_token}")
            
            return True

        except requests.exceptions.RequestException as e:
            logger.error(f"HTTP error during login process: {e}")
            if e.response is not None:
                logger.debug(f"Login error HTTP response content: {e.response.text}")
        except Exception as e:
            logger.error(f"An unexpected error occurred during login: {e}", exc_info=True)
        
        return False

    def _is_daily_limit_error(self, error_message: str) -> bool:
        """
        Checks if an MTC API error message indicates a daily submission limit.

        Args:
            error_message: The error message string from an API response.

        Returns:
            True if the message suggests a daily limit error, False otherwise.
        """
        return "deze transactie overschrijdt de voor uw pas" in error_message.lower()

    def submit_reimbursement(self, claim_data: Dict[str, Any], max_attempts: int = 3) -> Tuple[bool, str]:
        """
        Submits a reimbursement claim to MTC.

        Steps:
        1. Validates session; re-logins if needed.
        2. Fetches recent transactions for duplicate checking using `chargeSessionId`.
        3. If no duplicate and not DRY mode, submits the claim.
        4. Handles daily submission limits by retrying with earlier dates.

        Args:
            claim_data: Claim details:
                - "datetime" (str): ISO 8601 datetime string.
                - "chargeSessionId" (str): Unique session ID.
                - "total_price" (float): Transaction total.
                - "kwh_charged" (float): Energy in kWh.
                - "location" (str): Charging location.
                - "invoice_jpeg_base64" (str): Base64 JPEG invoice.
            max_attempts: Max retries for daily limit errors.

        Returns:
            Tuple (bool, str): Success status and a descriptive message.
        """
        if not self.session.cookies.get("osVisitor") or \
           not self.csrf_token or \
           self.csrf_token == INITIAL_CSRF_TOKEN:
            logger.warning("Session invalid before submission. Attempting re-login.")
            if not self.login():
                return False, "Authentication required for submission, and re-login failed."
        
        try:
            original_claim_dt = datetime.fromisoformat(claim_data["datetime"])
        except ValueError:
            logger.error(f"Invalid 'datetime' in claim_data: '{claim_data['datetime']}'. Must be ISO 8601.")
            return False, f"Invalid datetime format: {claim_data['datetime']}"

        current_submission_dt = original_claim_dt

        for attempt_num in range(max_attempts):
            logger.info(
                f"Submission attempt {attempt_num + 1}/{max_attempts} for session "
                f"ID '{claim_data['chargeSessionId']}' with date {current_submission_dt.date()}"
            )
            try:
                # --- 1. Fetch recent transactions for duplicate checking ---
                now_utc = datetime.now(timezone.utc)
                start_dt_lookback = now_utc.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
                for _ in range(self.lookback_period_months):
                    first_of_current_month = start_dt_lookback.replace(day=1)
                    start_dt_lookback = first_of_current_month - timedelta(days=1)
                    start_dt_lookback = start_dt_lookback.replace(day=1)

                start_date_filter_api_str = start_dt_lookback.strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'
                end_date_filter_api_str = now_utc.strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'
                input_param_start_api_str = start_dt_lookback.strftime('%Y-%m-%d 00:00:00')
                input_param_end_api_str = now_utc.strftime('%Y-%m-%d 23:59:59')

                transactions_payload = {
                    "versionInfo": {"moduleVersion": self.module_version, "apiVersion": self._get_api_version('transactions')},
                    "viewName": "MainFlowMTC.Transactions",
                    "screenData": {"variables": {
                        "ShowSharePopup": False,
                        "InputParameterString": f"{input_param_start_api_str}|{input_param_end_api_str}|0",
                        "MaxRecords": 50, "IsFirstLoad": True, "IsLoadMore": False,
                        "PopupValues": {"IconClassName":"","Title":"","Content":"","ButtonText":"","ButtonEventPayload":"","AlternativeLinkText":"","AlternativeLinkPayload":"","SecondAlternativeText":"","SecondAlternativeLinkPayload":""},
                        "IsShowNoClaimsPopup": False,
                        "TransactionTypeIdCurrentFilter": "", "_transactionTypeIdCurrentFilterInDataFetchStatus": 1,
                        "StartDateTimeCurrentFilter": start_date_filter_api_str, "_startDateTimeCurrentFilterInDataFetchStatus": 1,
                        "EndDateTimeCurrentFilter": end_date_filter_api_str, "_endDateTimeCurrentFilterInDataFetchStatus": 1,
                        "ForceRefreshList": 0, "_forceRefreshListInDataFetchStatus": 1
                    }}
                }
                
                logger.debug(f"Fetching transactions for duplicate check. Payload snippet: viewName='{transactions_payload['viewName']}'")
                response = self.session.post(
                    f"{self.base_url}/MultiTankcard/screenservices/OtmTrx_Transactions/Screen/Overview/DataActionGetTransactions",
                    json=transactions_payload,
                    headers={"Referer": f"{self.base_url}/MultiTankcard/Transactions"}
                )
                response.raise_for_status()
                transactions_data = response.json()

                if "exception" in transactions_data:
                    api_exception_msg = transactions_data['exception'].get('message', str(transactions_data['exception']))
                    logger.error(f"API error fetching transactions for duplicate check: {api_exception_msg}")
                    return False, f"API error fetching transactions: {api_exception_msg}"

                existing_transactions = transactions_data.get("data", {}).get("Transactions", {}).get("List", [])
                for trx in existing_transactions:
                    if trx.get("ClaimNote") == claim_data["chargeSessionId"]:
                        msg = (f"Duplicate claim found for session ID {claim_data['chargeSessionId']} "
                               f"(Location: {claim_data['location']}). Skipping submission.")
                        logger.info(msg)
                        return True, msg

                # --- 2. Submit the new claim if not a duplicate ---
                if os.getenv("MODE", "").upper() == "DRY":
                    msg = (f"[DRY RUN] Would submit claim: Location='{claim_data['location']}', "
                           f"Amount=€{claim_data['total_price']:.2f}, Date='{current_submission_dt.isoformat()}', "
                           f"SessionID='{claim_data['chargeSessionId']}'")
                    logger.info(msg)
                    return True, msg # End here for DRY RUN

                # Prepare DateTransaction in UTC 'Z' format with milliseconds for the API
                utc_submission_dt = current_submission_dt.astimezone(timezone.utc)
                date_transaction_for_api = utc_submission_dt.strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'

                claim_payload = {
                    "versionInfo": {"moduleVersion": self.module_version, "apiVersion": self._get_api_version('submit')},
                    "viewName": "MainFlowMTC.NewClaim", # Critical: From successful HAR
                    "inputParameters": {
                        "ClaimNew": {
                            "TransactionTypeId": "EV", "Iban": os.getenv("IBAN", ""),
                            "Amount": f"{claim_data['total_price']:.2f}", # Format as string with 2 decimals
                            "DateTransaction": date_transaction_for_api, # UTC Z-formatted string
                            "Mileage": 0, # Number, not string
                            "IsForeign": False, "CountryId": "NL", "IsReplacement": False,
                            "Quantity": str(claim_data["kwh_charged"]), # String
                            "Description": claim_data["chargeSessionId"], # This is used as ClaimNote by MTC
                            "ProductCode": "10" # For electricity
                        },
                        "Attachment": {
                            "MimeType": "", # Empty string, as per successful HAR
                            "Binary": claim_data["invoice_jpeg_base64"]
                        }
                    }
                }

                logger.info(f"Submitting claim with transaction date {date_transaction_for_api}")
                logger.debug(f"Claim submission payload snippet: viewName='{claim_payload['viewName']}', DateTransaction='{date_transaction_for_api}'")
                response = self.session.post(
                    f"{self.base_url}/MultiTankcard/screenservices/OtmTrx_Transactions/Claim/ClaimForm/ActionClaim_Create",
                    json=claim_payload,
                    headers={"Referer": f"{self.base_url}/MultiTankcard/NewClaim"} # Critical: From successful HAR
                )
                response.raise_for_status()
                result = response.json()

                if result.get("data", {}).get("Success"):
                    msg = (f"Successfully submitted claim: Location='{claim_data['location']}', "
                           f"Amount=€{claim_data['total_price']:.2f}, Submitted Date='{date_transaction_for_api}'")
                    logger.info(msg)
                    return True, msg
                else:
                    error_message = result.get("data", {}).get("ErrorMessage", "Unknown error during submission.")
                    if self._is_daily_limit_error(error_message):
                        logger.warning(
                            f"Daily limit reached for date {current_submission_dt.date()}. Error: {error_message}"
                        )
                        if attempt_num < max_attempts - 1:
                            current_submission_dt -= timedelta(days=1)
                            logger.info(f"Retrying with new date: {current_submission_dt.date()}")
                            time.sleep(1) # Brief pause
                            continue # To the next iteration of the for loop (next attempt)
                        else:
                            logger.error("Max attempts reached for daily limit retries. Submission failed.")
                            return False, f"Failed after {max_attempts} attempts due to daily limit: {error_message}"
                    else: # Non-daily-limit error
                        logger.error(f"Claim submission failed: {error_message} (Date tried: {current_submission_dt.isoformat()})")
                        logger.debug(f"Full submission failure response: {result}")
                        return False, f"Claim submission failed: {error_message}"
            
            except requests.exceptions.RequestException as e:
                logger.error(f"HTTP error on submission attempt {attempt_num + 1} for date {current_submission_dt.date()}: {e}")
                if e.response is not None:
                    logger.debug(f"Error HTTP response content: {e.response.text}")
                if attempt_num == max_attempts - 1: # If this was the last attempt
                    return False, f"HTTP error on final submission attempt: {e}"
                time.sleep(1) # Wait before next attempt for HTTP errors too
            except Exception as e:
                logger.error(f"Unexpected error on submission attempt {attempt_num + 1} for date {current_submission_dt.date()}: {e}", exc_info=True)
                if attempt_num == max_attempts - 1:
                    return False, f"Unexpected error on final submission attempt: {e}"
                # For unexpected errors, we might not want to retry with a different date,
                # but the loop structure will continue unless we explicitly return or break.
                # For now, let it try again if it's not the last attempt.

        # If the loop completes all attempts without a successful submission or explicit return
        logger.error(f"Failed to submit claim for session ID '{claim_data['chargeSessionId']}' after {max_attempts} attempts.")
        return False, f"Failed to submit claim after {max_attempts} attempts."


def test_mtc_client():
    """
    Test function for the MTCClient.
    This function initializes the client, attempts login, and can be used
    to test claim submission in DRY_RUN mode with dummy data.
    """
    # Configure basic logging for testing
    # The main application (main.py) should configure logging for the whole app.
    # This is just for standalone testing of MTCClient.
    logging.basicConfig(
        level=os.getenv('LOG_LEVEL', 'DEBUG'), # Use DEBUG for more detailed output during testing
        format="%(asctime)s - %(levelname)s - %(name)s - %(funcName)s - %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)]
    )
    logger.info("--- Starting MTCClient Test ---")

    try:
        client = MTCClient()

        if client.csrf_token != INITIAL_CSRF_TOKEN and client.session.cookies.get("osVisitor"):
            logger.info("Client initialized and login appears successful (dynamic CSRF token obtained).")

            # Example: Test fetching transactions (as part of a dummy submission dry run)
            # Ensure claim_data["datetime"] is a valid ISO 8601 string.
            # Example: "2023-10-26T10:30:00+02:00" or "2023-10-26T08:30:00Z"
            test_claim_datetime_aware = datetime.now(timezone.utc) - timedelta(days=30)


            test_claim = {
                "datetime": test_claim_datetime_aware.isoformat(),
                "location": "Test Location Alpha, Netherlands",
                "kwh_charged": 12.34,
                "total_price": 5.67,
                "currency": "EUR",
                "invoice_jpeg_base64": "dummy_base64_string_for_testing_invoice_field_presence_only",
                "chargeSessionId": f"test-session-{int(time.time())}" # Unique ID for each test run
            }

            original_mode = os.getenv("MODE")
            logger.info(f"Current MODE from .env: {original_mode}")
            logger.info("Setting MODE to DRY for this test submission...")
            os.environ["MODE"] = "DRY" # Force DRY RUN for this test
            
            success, message = client.submit_reimbursement(test_claim)
            logger.info(f"Test reimbursement submission (DRY RUN) result: Success={success}, Message='{message}'")
            
            # Restore original MODE if it was set
            if original_mode is not None:
                os.environ["MODE"] = original_mode
            else:
                del os.environ["MODE"] # Clean up if it wasn't originally set
            logger.info(f"Restored MODE to: {os.getenv('MODE')}")

        else:
            logger.error(
                "MTCClient login may have failed during initialization (CSRF token is initial or osVisitor cookie missing)."
            )

    except Exception as e:
        logger.error(f"An error occurred during test_mtc_client: {e}", exc_info=True)
    logger.info("--- Finished MTCClient Test ---")


if __name__ == "__main__":
    # This allows MTC.py to be run directly for testing the MTCClient.
    # The main application logic is in main.py.
    test_mtc_client()
