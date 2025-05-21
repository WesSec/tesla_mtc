import os
import requests
import time
import logging
import re
from typing import Dict, Tuple
from urllib.parse import unquote  # Import unquote
from dotenv import load_dotenv
from datetime import datetime, timedelta
import pytz
import sys

# For debugging purposes
from requests_toolbelt.utils import dump
from datetime import timezone


# API version patterns for different endpoints
API_PATTERNS = {
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


class MTCClient:
    """
    Client for interacting with the MultiTankcard (MTC) system.
    Handles authentication, session management, and reimbursement submissions.
    """

    def __init__(self):
        """Initialize the MTC client with configuration and session setup."""
        load_dotenv()
        try:
            self.lookback_period_months = int(
                os.getenv("LOOKBACK_PERIOD", "6")
            )  # Default to 6 months if not set
        except ValueError:
            logging.warning("Invalid LOOKBACK_PERIOD in .env, defaulting to 6 months.")
            self.lookback_period_months = 6
        self.base_url = "https://mtc.outsystemsenterprise.com"
        # Default CSRF token, will be updated after initial calls and login
        self.csrf_token = "T6C+9iB49TLra4jEsMeSckDMNhQ="
        self.username = os.getenv("MTC_USERNAME")
        self.password = os.getenv("MTC_PASSWORD")
        self.session = self._initialize_session_headers()
        self._api_versions: Dict[str, str] = {}  # Type hint for clarity
        self.module_version: str = ""  # Initialize module_version

        # Attempt to login upon initialization
        if not self.login():
            logging.error("MTC Client initialization failed due to login error.")
            # Depending on desired behavior, you might want to raise an exception here
            # raise ConnectionError("Failed to login during MTCClient initialization")

    def _initialize_session_headers(self) -> requests.Session:
        """
        Initialize a requests session with required headers.

        Returns:
            requests.Session: Configured session object
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
                "Referer": f"{self.base_url}/MultiTankcard/Login",  # Default Referer
                "Accept-Language": "en-US,en;q=0.9",
                "OutSystems-client-env": "browser",
            }
        )
        return session

    def _get_api_version(self, endpoint: str) -> str:
        """
        Get API version for a specific endpoint from JS files.

        Args:
            endpoint: Key identifying the endpoint ('login', 'transactions','appstoreurls', or 'submit')

        Returns:
            str: API version string

        Raises:
            ValueError: If API version cannot be found
        """
        if endpoint in self._api_versions:
            return self._api_versions[endpoint]

        pattern, js_file = API_PATTERNS[endpoint]
        # Update Referer for fetching JS files if necessary, though likely not critical
        # For this specific request type, a generic referer or none might also work.
        js_url = f"{self.base_url}/MultiTankcard/scripts/{js_file}"
        try:
            response = self.session.get(
                js_url,
                headers={
                    "Accept": "*/*",
                    "Sec-Fetch-Mode": "no-cors",
                    "Referer": f"{self.base_url}/MultiTankcard/",
                },
                verify=True,  # Generally good to verify SSL, set to False if specific issues exist, sometimes MTC uses shitty certs
            )
            response.raise_for_status()
        except requests.exceptions.RequestException as e:
            logging.error(f"Failed to fetch JS file {js_url}: {e}")
            raise ValueError(
                f"Could not fetch API version source for {endpoint} from {js_file}"
            ) from e

        match = re.search(pattern, response.text)
        if not match:
            logging.error(
                f"Could not find API version pattern in {js_file} for endpoint {endpoint}"
            )
            raise ValueError(f"Could not find API version for {endpoint}")

        self._api_versions[endpoint] = match.group(1)
        logging.debug(f"API version for {endpoint}: {self._api_versions[endpoint]}")
        return self._api_versions[endpoint]

    def _perform_initial_calls(self) -> str:
        """
        Perform initial calls to get session cookies and module version.
        This replaces the main logic of _initialize_session.

        Returns:
            str: Module version token

        Raises:
            ValueError: If required cookies or module version cannot be obtained
        """
        try:
            # Step 1: Get initial cookies (osVisit, osVisitor) and module version
            current_epoch = int(time.time() * 1000)
            # Using verify=False as in original code, acknowledge potential security risk
            response = self.session.get(
                f"{self.base_url}/MultiTankcard/moduleservices/moduleversioninfo?{current_epoch}",
                verify=True,  # Per original code due to "Outsystem/MTC Sucks"
            )
            response.raise_for_status()
            logging.debug(
                f"Initial moduleversioninfo response status: {response.status_code}"
            )
            logging.debug(
                f"Initial moduleversioninfo cookies: {self.session.cookies.get_dict()}"
            )

            self.visit_id = self.session.cookies.get("osVisit")
            self.visitor_id = self.session.cookies.get("osVisitor")

            if not self.visit_id or not self.visitor_id:
                logging.error("Failed to obtain osVisit or osVisitor cookies.")
                raise ValueError(
                    "Failed to obtain required cookies (osVisit, osVisitor)"
                )

            logging.info(f"Got osVisit: {self.visit_id}, osVisitor: {self.visitor_id}")

            module_version_data = response.json()
            module_version = module_version_data.get("versionToken")
            if not module_version:
                logging.error(
                    f"Failed to get versionToken from moduleversioninfo response: {module_version_data}"
                )
                raise ValueError("Failed to obtain module version token")

            logging.info(f"Got module version token: {module_version}")

            # Step 2: Fetch module info (optional if not strictly needed for subsequent steps, but good to keep if part of sequence)
            # If this call is not strictly necessary before GetAppStoreUrls, it can be removed or conditionally called.
            # Based on HAR, it seems this might not be immediately before ActionGetAppStoreUrls.
            # For now, keeping it as per original implied sequence.
            # response = self.session.get(
            #     f"{self.base_url}/MultiTankcard/moduleservices/moduleinfo?{module_version}",
            #     # headers={"Referer": f"{self.base_url}/MultiTankcard/Transactions"} # Example, adjust if needed
            # )
            # response.raise_for_status()
            # logging.debug(f"Module info response status: {response.status_code}")

            # Step 3 & 4 equivalent: GetAppStoreUrls and GetSiteProperties to ensure session is fully primed
            # These calls also set/confirm the initial CSRF token in cookies (nr1Users, nr2Users)
            # The self.csrf_token is initially hardcoded and used for these.
            self._get_app_store_urls(
                module_version
            )  # This call uses the initial self.csrf_token
            self._get_site_properties_for_sync(
                module_version
            )  # This also uses the initial self.csrf_token

            # After ActionGetAppStoreUrls, nr2Users cookie is set.
            # The CSRF token in this cookie should match the initial hardcoded one.
            # No need to parse it here as the login call will use the initial hardcoded one,
            # and then we'll parse the NEW token AFTER login.

            return module_version

        except requests.exceptions.RequestException as e:
            logging.error(f"HTTP error during initial calls: {e}")
            raise
        except ValueError as e:  # Catch specific ValueErrors from this function
            logging.error(f"Data error during initial calls: {e}")
            raise
        except Exception as e:
            logging.error(f"Unexpected error initializing session: {e}")
            # data = dump.dump_all(response) # dump response if available
            # print(data.decode('utf-8'))
            raise

    def _get_app_store_urls(self, module_version: str):
        """Fetch app store URLs (part of session initialization)."""
        url = f"{self.base_url}/MultiTankcard/screenservices/OnTheMoveMultiTankcard_CW/ActionGetAppStoreUrls"
        payload = {
            "versionInfo": {
                "moduleVersion": module_version,
                "apiVersion": self._get_api_version("appstoreurls"),
            },
            "viewName": "*",  # As per HAR
            "inputParameters": {},
        }
        headers = {
            "X-CSRFToken": self.csrf_token,  # Uses the current (initially hardcoded) CSRF token
            "Referer": f"{self.base_url}/MultiTankcard/Transactions",  # As per HAR
        }
        response = self.session.post(url, headers=headers, json=payload)
        response.raise_for_status()
        logging.debug(f"GetAppStoreUrls response status: {response.status_code}")
        logging.debug(
            f"Cookies after GetAppStoreUrls: {self.session.cookies.get_dict()}"
        )
        # nr2Users cookie set here contains the initial CSRF token (e.g., T6C+9i...)
        # We don't need to parse it yet, as the login request will use this initial token.

    def _get_site_properties_for_sync(self, module_version: str):
        """Fetch site properties (part of session initialization)."""
        # This call was not explicitly in the user's HAR sequence for login,
        # but keeping it if it's deemed necessary by original MTC.py logic.
        # The login API version is used here as per original code.
        url = f"{self.base_url}/MultiTankcard/screenservices/OnTheMoveMultiTankcard_CW/ActionGetSitePropertiesForSync"
        payload = {
            "versionInfo": {
                "moduleVersion": module_version,
                "apiVersion": self._get_api_version(
                    "login"
                ),  # Original used "login" API version
            },
            "viewName": "*",
            "inputParameters": {},
        }
        headers = {
            "X-CSRFToken": self.csrf_token,  # Uses the current (initially hardcoded) CSRF token
            # Referer might be f"{self.base_url}/MultiTankcard/Login" or Transactions
            "Referer": f"{self.base_url}/MultiTankcard/Login",
        }
        response = self.session.post(url, headers=headers, json=payload)
        response.raise_for_status()
        logging.debug(
            f"GetSitePropertiesForSync response status: {response.status_code}"
        )
        # logging.debug(f"Site properties response: {response.json()}")

    def login(self) -> bool:
        """
        Authenticate with the MTC system.

        Returns:
            bool: True if login successful, False otherwise
        """
        try:
            # Perform initial calls to get module version and prime cookies
            self.module_version = self._perform_initial_calls()

            # The self.csrf_token is still the initial hardcoded one here.
            # The ActionAppLogin request uses this initial token.
            # The session cookies (osVisit, osVisitor, nr1Users, nr2Users (with initial token))
            # have been set by _perform_initial_calls.

            login_payload = {
                "versionInfo": {
                    "moduleVersion": self.module_version,
                    "apiVersion": self._get_api_version("login"),
                },
                "viewName": "CommonMTC.Login",  # As per HAR
                "inputParameters": {
                    "Username": self.username,
                    "Password": self.password,
                    "KeepMeLoggedIn": True,
                },
            }

            # The X-CSRFToken header for the login request should be the initial one.
            # requests.Session automatically includes cookies.
            # We can explicitly set the X-CSRFToken header for this call.
            headers = {
                "X-CSRFToken": self.csrf_token,  # Initial CSRF token
                "Referer": f"{self.base_url}/MultiTankcard/Login",  # Referer for login
            }

            response = self.session.post(
                f"{self.base_url}/MultiTankcard/screenservices/OtmAcc_Account/ActionAppLogin",
                json=login_payload,
                headers=headers,
            )
            response.raise_for_status()
            result = response.json()

            if (
                "exception" in result
                or result.get("data", {}).get("Result") is not True
            ):
                error_message = result.get(
                    "exception",
                    result.get("data", {}).get(
                        "ErrorMessages", "Login failed, no specific error message."
                    ),
                )
                logging.error(f"MTC Login failed: {error_message}")
                # data = dump.dump_all(response) # For debugging failed login
                # print(data.decode('utf-8'))
                return False

            logging.info("MTC Login successful")

            # IMPORTANT: After successful login, new nr1Users and nr2Users cookies are set.
            # The new nr2Users cookie contains the NEW CSRF token that must be used for subsequent requests.
            nr2_users_cookie = self.session.cookies.get("nr2Users")
            if nr2_users_cookie:
                # The CSRF token in the cookie is URL encoded (e.g., %2F for /, %2B for +)
                # Example: "crf%3dQOjSV0ck2K5My3x%2f%2byrSZeNUfNA%3d..."
                match = re.search(r"crf%3d(.*?)(?:%3b|$)", nr2_users_cookie)
                if match:
                    encoded_csrf = match.group(1)
                    self.csrf_token = unquote(encoded_csrf)  # Decode the token
                    logging.info(
                        f"Successfully extracted and DECODED new CSRF token: {self.csrf_token}"
                    )
                    # Update the session's default X-CSRFToken header if you rely on it elsewhere,
                    # though explicitly setting it per request is safer.
                    self.session.headers.update({"X-CSRFToken": self.csrf_token})
                else:
                    logging.error(
                        "Failed to extract CSRF token from nr2Users cookie after login."
                    )
                    # data = dump.dump_all(response) # For debugging CSRF extraction
                    # print(data.decode('utf-8'))
                    return False  # Critical error
            else:
                logging.error("nr2Users cookie not found in session after login.")
                # data = dump.dump_all(response) # For debugging missing cookie
                # print(data.decode('utf-8'))
                return False  # Critical error

            return True  # Crucial fix: return True on successful login and CSRF update

        except requests.exceptions.RequestException as e:
            logging.error(f"HTTP error during login: {e}")
            # response_content = e.response.text if e.response else "No response content"
            # logging.error(f"Login response content: {response_content}")
            # data = dump.dump_all(e.response) # For debugging HTTP errors
            # print(data.decode('utf-8'))
        except Exception as e:
            logging.error(f"Unexpected error during login: {e}", exc_info=True)
        return False

    def _is_daily_limit_error(self, error_message: str) -> bool:
        """Check if the error is due to daily transaction limit"""
        return any(
            phrase in error_message for phrase in ["maximaal 3 transacties op een dag"]
        )

    def submit_reimbursement(
        self, claim_data: Dict, max_attempts: int = 3
    ) -> Tuple[bool, str]:
        """
        Submit a reimbursement claim to MTC.

        Args:
            claim_data: Dictionary containing claim details
            max_attempts: Maximum number of submission attempts for daily limit errors

        Returns:
            Tuple[bool, str]: (success status, message describing the result)
        """
        attempt = 0
        # Ensure claim_data["datetime"] is a string in the correct format if it's being manipulated with timedelta later
        # For now, assuming it's passed correctly as a string for the API, or as datetime object for manipulation

        # If attempt_date is a string like '2024-11-23T17:20:48+01:00', convert to datetime for manipulation
        try:
            current_submission_dt = datetime.fromisoformat(claim_data["datetime"])
        except ValueError:
            logging.error(
                f"Claim_data['datetime'] ('{claim_data['datetime']}') is not in a valid ISO format. Cannot proceed with submission."
            )
            return False, f"Invalid datetime format: {claim_data['datetime']}"

        while attempt < max_attempts:
            # Format the current attempt's date to string for the payload
            # If the API is sensitive to timezone info or needs a specific format, adjust here.
            # Example: current_submission_date_str = current_submission_dt.strftime('%Y-%m-%dT%H:%M:%S') # No timezone
            current_submission_date_str = current_submission_dt.isoformat()

            try:
                # Ensure we are logged in and have a fresh CSRF token
                # The constructor already calls login. If osVisitor is missing, it's a more severe session issue.
                if not self.session.cookies.get("osVisitor") or not self.csrf_token:
                    logging.warning(
                        "Session invalid (osVisitor or CSRF token missing). Re-attempting login."
                    )
                    if not self.login():  # This will re-run the full login logic
                        return False, "Authentication required and re-login failed"

                # Calculate date range for transaction fetching
                # Use timezone-aware datetime objects
                now_utc = datetime.now(timezone.utc)

                # Calculate the start date for the lookback period
                # A simple way to subtract months:
                start_year = now_utc.year
                start_month = now_utc.month - self.lookback_period_months
                start_day = now_utc.day  # Keep the same day if possible

                # Adjust year and month if month subtraction goes below 1
                while start_month <= 0:
                    start_month += 12
                    start_year -= 1

                # Handle cases where the day might be invalid for the new month (e.g., March 31st -> Feb 31st doesn't exist)
                # A robust way is to try creating the date and adjust if it fails, or set to day 1 of the target month.
                # For simplicity here, we'll just use the day, but for production, consider `relativedelta` or more checks.
                try:
                    start_datetime_filter_obj = now_utc.replace(
                        year=start_year,
                        month=start_month,
                        day=start_day,
                        hour=0,
                        minute=0,
                        second=0,
                        microsecond=0,
                    )
                except ValueError:  # Handles cases like trying to set Feb 30
                    # Fallback to the first day of the next month, then go back one day to get last day of target month
                    # Or simply use the first day of the calculated month
                    temp_month = start_month + 1
                    temp_year = start_year
                    if temp_month > 12:
                        temp_month = 1
                        temp_year += 1
                    start_datetime_filter_obj = now_utc.replace(
                        year=temp_year,
                        month=temp_month,
                        day=1,
                        hour=0,
                        minute=0,
                        second=0,
                        microsecond=0,
                    ) - timedelta(days=1)
                    start_datetime_filter_obj = start_datetime_filter_obj.replace(
                        hour=0, minute=0, second=0, microsecond=0
                    )

                # Format dates as expected by the API (YYYY-MM-DDTHH:MM:SS.mmmZ)
                # .isoformat() produces YYYY-MM-DDTHH:MM:SS.ffffff+HH:MM, we need to adjust it
                start_date_filter_str = (
                    start_datetime_filter_obj.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]
                    + "Z"
                )
                end_date_filter_str = (
                    now_utc.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
                )

                # InputParameterString format: "YYYY-MM-DD HH:MM:SS|YYYY-MM-DD HH:MM:SS|0"
                # These should also be UTC if the API expects consistency with the Z-terminated filters
                input_param_start_str = start_datetime_filter_obj.strftime(
                    "%Y-%m-%d 00:00:00"
                )
                input_param_end_str = now_utc.strftime(
                    "%Y-%m-%d 23:59:59"
                )  # Typically to the end of the current day

                transactions_payload = {
                    "versionInfo": {
                        "moduleVersion": self.module_version,
                        "apiVersion": self._get_api_version("transactions"),
                    },
                    "viewName": "MainFlowMTC.Transactions",  # Matched from HAR "MainFlowMTC.Transactions" vs "MainFlow.Transactions"
                    "screenData": {
                        "variables": {
                            "ShowSharePopup": False,
                            # Format from HAR: "2025-05-01 00:00:002025-05-31 23:59:590" - seems concatenated. Let's try to match.
                            # The example shows no delimiter.
                            # "InputParameterString": f"{input_param_start}{input_param_end}0",
                            # The provided python code has:
                            "InputParameterString": f"{input_param_start_str}|{input_param_end_str}|0",  # Updated
                            "MaxRecords": 50,  # Increased for better chance to find duplicates
                            "IsFirstLoad": True,
                            "IsLoadMore": False,
                            "PopupValues": {
                                "IconClassName": "",
                                "Title": "",
                                "Content": "",
                                "ButtonText": "",
                                "ButtonEventPayload": "",
                                "AlternativeLinkText": "",
                                "AlternativeLinkPayload": "",
                                "SecondAlternativeText": "",
                                "SecondAlternativeLinkPayload": "",
                            },
                            # "IsShowNoClaimsPopup": False, # Removed, not in HAR's DataActionGetTransactions's direct variables
                            "EmptyPopupValues": {  # Added from original code, might be useful for some UI state
                                "IconClassName": "",
                                "Title": "",
                                "Content": "",
                                "ButtonText": "",
                                "ButtonEventPayload": "",
                                "AlternativeLinkText": "",
                                "AlternativeLinkPayload": "",
                                "SecondAlternativeText": "",
                                "SecondAlternativeLinkPayload": "",
                            },
                            "IsShowNoClaimsPopup": False,  # As per original code
                            "TransactionTypeIdCurrentFilter": "",
                            "_transactionTypeIdCurrentFilterInDataFetchStatus": 1,
                            "StartDateTimeCurrentFilter": start_date_filter_str,  # YYYY-MM-DDTHH:MM:SS.mmmZ
                            "_startDateTimeCurrentFilterInDataFetchStatus": 1,
                            "EndDateTimeCurrentFilter": end_date_filter_str,  # YYYY-MM-DDTHH:MM:SS.mmmZ
                            "_endDateTimeCurrentFilterInDataFetchStatus": 1,
                            "ForceRefreshList": 0,  # In original code, HAR shows 0
                            "_forceRefreshListInDataFetchStatus": 1,
                        }
                    },
                }

                logging.debug(f"Transactions payload: {transactions_payload}")
                transactions_response = self.session.post(
                    f"{self.base_url}/MultiTankcard/screenservices/OtmTrx_Transactions/Screen/Overview/DataActionGetTransactions",
                    json=transactions_payload,
                    headers={
                        "X-CSRFToken": self.csrf_token,  # Use the decoded token
                        "Referer": "https://mtc.outsystemsenterprise.com/MultiTankcard/Transactions",
                    },
                )

                logging.debug(f"Transaction request sent with CSRF: {self.csrf_token}")
                # data = dump.dump_all(transactions_response) # Full dump for debugging
                # print(data.decode('utf-8'))

                if not transactions_response.ok:
                    logging.error(
                        f"Failed to fetch transactions: HTTP {transactions_response.status_code} - {transactions_response.text}"
                    )
                    # Attempt to re-login if it's an auth-like error (e.g. 401, 403)
                    if transactions_response.status_code in [401, 403]:
                        logging.info(
                            "Authentication error fetching transactions, attempting re-login..."
                        )
                        if not self.login():
                            return (
                                False,
                                "Re-login failed after transaction fetch error.",
                            )
                        # After re-login, retry the transaction fetch in the next loop iteration (or immediately)
                        # For now, we'll let the loop structure handle retries if applicable or fail.
                        # This attempt for transaction fetch will be considered failed for now.
                    return (
                        False,
                        f"Failed to fetch transactions: HTTP {transactions_response.status_code}",
                    )

                transactions_data = transactions_response.json()
                if "exception" in transactions_data:
                    return (
                        False,
                        f"API error in transactions: {transactions_data['exception']}",
                    )

                transactions = (
                    transactions_data.get("data", {})
                    .get("Transactions", {})
                    .get("List", [])
                )
                for transaction in transactions:
                    if transaction.get("ClaimNote") == claim_data["chargeSessionId"]:
                        msg = f"Skipping duplicate claim for session {claim_data['chargeSessionId']} (at {claim_data['location']} ({claim_data['kwh_charged']} kWh, €{claim_data['total_price']})"
                        logging.info(msg)
                        return True, msg

                if os.getenv("MODE") == "DRY":
                    msg = f"[DRY RUN] Would submit claim for {claim_data['location']} ({claim_data['kwh_charged']} kWh, €{claim_data['total_price']})"
                    logging.info(msg)
                    return True, msg


                if current_submission_dt.tzinfo is None:
                    # If it's naive, assume it's local time and we need to make it aware, then convert to UTC.
                    # This step depends on how current_submission_dt is initially populated.
                    # For simplicity, if claim_data["datetime"] has offset, fromisoformat() handles it.
                    # If it's naive and meant to be local, you'd localize then convert.
                    # Assuming current_submission_dt is already timezone-aware from fromisoformat():
                    utc_submission_dt = current_submission_dt.astimezone(timezone.utc)
                else:
                    utc_submission_dt = current_submission_dt.astimezone(timezone.utc)

                # Format the DateTransaction as YYYY-MM-DDTHH:MM:SS.mmmZ
                date_transaction_str = utc_submission_dt.strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'
                
            # Format the DateTransaction as YYYY-MM-DD
                # Submit the claim
                claim_payload = {
                    "versionInfo": {
                        "moduleVersion": self.module_version,
                        "apiVersion": self._get_api_version("submit"),
                    },
                    "viewName": "MainFlowMTC.NewClaim",
                    "inputParameters": {
                        "ClaimNew": {
                            "TransactionTypeId": "EV",
                            "Iban": os.getenv("IBAN", ""),
                            "Amount": str(claim_data["total_price"]),
                            "DateTransaction": date_transaction_str,
                            "Mileage": 0,
                            "IsForeign": False,
                            "CountryId": "NL",
                            "IsReplacement": False,
                            "Quantity": str(claim_data["kwh_charged"]),
                            "Description": f"{claim_data['chargeSessionId']}",
                            "ProductCode": "10",
                        },
                        "Attachment": {
                            "MimeType": "image/jpeg",
                            "Binary": claim_data["invoice_jpeg_base64"],
                        },
                    },
                }
                response = self.session.post(
                    f"{self.base_url}/MultiTankcard/screenservices/OtmTrx_Transactions/Claim/ClaimForm/ActionClaim_Create",
                    json=claim_payload,
                    headers={
                        "X-CSRFToken": self.csrf_token,
                    },
                )

                if not response.ok:
                    return False, f"HTTP error submitting claim: {response.status_code}"

                result = response.json()

                # Debug dump for claim submission
                # data = dump.dump_all(response) # dump response if available
                # print(data.decode('utf-8'))

                # Check for API-level errors
                if "error" in result or "exception" in result:
                    error_msg = result.get(
                        "error",
                        result.get("exception", "Unknown API error during submission"),
                    )
                    logging.error(f"API error submitting claim: {error_msg}")
                    return False, f"API error: {error_msg}"

                if result.get("data", {}).get("Success"):
                    msg = f"Successfully submitted claim for {claim_data['location']} ({claim_data['kwh_charged']} kWh, €{claim_data['total_price']}) with transaction date {current_submission_date_str}"
                    logging.info(msg)
                    return True, msg
                else:
                    error_msg_detail = result.get("data", {}).get(
                        "ErrorMessage", "Unknown error during claim submission"
                    )
                    if self._is_daily_limit_error(error_msg_detail):
                        attempt += 1
                        if attempt < max_attempts:
                            current_submission_dt -= timedelta(
                                days=1
                            )  # Decrement the datetime object
                            # current_submission_date_str will be updated at the start of the next loop iteration
                            logging.info(
                                f"Daily limit reached for date {current_submission_date_str}. Retrying submission {attempt + 1}/{max_attempts} with new date: {current_submission_dt.isoformat()}"
                            )
                            time.sleep(1)  # Small delay before retrying
                            continue
                        else:
                            logging.error(
                                f"Daily limit error, and max attempts reached. Last error: {error_msg_detail} on date {current_submission_date_str}"
                            )
                            return (
                                False,
                                f"Failed due to daily limit after {attempt} attempts: {error_msg_detail}",
                            )
                    else:
                        logging.error(
                            f"Failed to submit claim: {error_msg_detail} (Date tried: {current_submission_date_str})"
                        )
                        return False, f"Failed to submit claim: {error_msg_detail}"

            except Exception as e:
                logging.error(f"Unexpected error: {str(e)}")
                return False, str(e)

        logging.error(f"Failed to submit after {max_attempts} date attempts")
        return False, f"Failed to submit after {max_attempts} date attempts"


def test_mtc_client():
    """Test function for the MTCClient"""
    logging.basicConfig(
        level=logging.DEBUG,  # Use DEBUG for more detailed output during testing
        format="%(asctime)s - %(levelname)s - %(module)s - %(funcName)s - %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],  # Ensure logs go to stdout
    )

    try:
        client = MTCClient()  # Login is attempted in constructor

        # Check if login was successful implicitly by checking if critical attributes are set
        if (
            client.module_version
            and client.csrf_token
            and client.session.cookies.get("osVisitor")
        ):
            logging.info("Client initialized and login appears successful.")

            # Test fetching transactions (as part of a dummy submission dry run)
            test_claim = {
                "datetime": datetime.now(
                    pytz.timezone("Europe/Amsterdam")
                ).isoformat(),  # Use current time for testing
                "location": "Test Location, Netherlands",
                "kwh_charged": 10.5,
                "total_price": 3.50,
                "currency": "EUR",  # ensure this matches what your system expects
                "invoice_jpeg_base64": "dummy_base64_data_for_testing_deduplication_only",  # dummy data
                "chargeSessionId": f"test-session-{int(time.time())}",  # Unique ID for testing
            }

            # Temporarily set mode to DRY for testing transaction fetching and deduplication
            original_mode = os.getenv("MODE")
            os.environ["MODE"] = "DRY"
            logging.info("Testing reimbursement submission in DRY RUN mode...")

            success, message = client.submit_reimbursement(test_claim)
            logging.info(
                f"Test reimbursement submission result: Success={success}, Message='{message}'"
            )

            os.environ["MODE"] = (
                original_mode if original_mode is not None else ""
            )  # Restore mode

        else:
            logging.error(
                "MTCClient login failed during initialization in test_mtc_client."
            )

    except Exception as e:
        logging.error(f"An error occurred during test_mtc_client: {e}", exc_info=True)


if __name__ == "__main__":
    test_mtc_client()
