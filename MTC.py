import os
import requests
import time
import logging
import re
from typing import Dict, Tuple
from urllib.parse import unquote
from dotenv import load_dotenv

# API version patterns for different endpoints
API_PATTERNS = {
    'login': (
        'AppLogin", "screenservices/OtmAcc_Account/ActionAppLogin", "([^"]+)"',
        'OtmAcc_Account.controller.js'
    ),
    'transactions': (
        'DataActionGetTransactions", "screenservices/OtmTrx_Transactions/Trx_Screen/Overview/DataActionGetTransactions", "([^"]+)"',
        'OtmTrx_Transactions.Trx_Screen.Overview.mvc.js'
    ),
    'submit': (
        'Claim_Create", "screenservices/OtmTrx_Transactions/Claim/ClaimForm/ActionClaim_Create", "([^"]+)"',
        'OtmTrx_Transactions.Claim.ClaimForm.mvc.js'
    )
}

class MTCClient:
    """
    Client for interacting with the MultiTankcard (MTC) system.
    Handles authentication, session management, and reimbursement submissions.
    """

    def __init__(self):
        """Initialize the MTC client with configuration and session setup."""
        load_dotenv()
        self.base_url = "https://mtc.outsystemsenterprise.com"
        self.username = os.getenv("MTC_USERNAME")
        self.password = os.getenv("MTC_PASSWORD")
        self.session = self._initialize_session_headers()
        self._api_versions = {}
        self.login()

    def _initialize_session_headers(self) -> requests.Session:
        """
        Initialize a requests session with required headers.
        
        Returns:
            requests.Session: Configured session object
        """
        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Accept": "application/json",
            "Content-Type": "application/json; charset=UTF-8",
            "sec-ch-ua-platform": "Windows",
            "sec-ch-ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
            "sec-ch-ua-mobile": "?0",
            "Origin": self.base_url,
            "Referer": f"{self.base_url}/MultiTankcard/Login",
            "Accept-Language": "en-US,en;q=0.9",
        })
        return session

    def _get_api_version(self, endpoint: str) -> str:
        """
        Get API version for a specific endpoint from JS files.
        
        Args:
            endpoint: Key identifying the endpoint ('login', 'transactions', or 'submit')
            
        Returns:
            str: API version string
        
        Raises:
            ValueError: If API version cannot be found
        """
        if endpoint in self._api_versions:
            return self._api_versions[endpoint]

        pattern, js_file = API_PATTERNS[endpoint]
        response = self.session.get(
            f"{self.base_url}/MultiTankcard/scripts/{js_file}",
            headers={"Accept": "*/*", "Sec-Fetch-Mode": "no-cors"}
        )
        response.raise_for_status()

        match = re.search(pattern, response.text)
        if not match:
            raise ValueError(f"Could not find API version for {endpoint}")

        self._api_versions[endpoint] = match.group(1)
        return self._api_versions[endpoint]

    def _initialize_session(self) -> str:
        """
        Initialize session cookies and get module version.
        
        Returns:
            str: Module version token
        
        Raises:
            ValueError: If required cookies cannot be obtained
        """
        try:
            # First request to get initial cookies
            current_epoch = int(time.time() * 1000)
            response = self.session.get(
                f"{self.base_url}/MultiTankcard/moduleservices/moduleversioninfo?{current_epoch}"
            )
            response.raise_for_status()

            # Get all Set-Cookie headers
            set_cookie_headers = response.raw.headers.getlist("Set-Cookie")

            # Parse each Set-Cookie header
            for cookie_header in set_cookie_headers:
                if "osVisit=" in cookie_header:
                    self.visit_id = cookie_header.split("osVisit=")[1].split(";")[0]
                elif "osVisitor=" in cookie_header:
                    self.visitor_id = cookie_header.split("osVisitor=")[1].split(";")[0]

            moduleversion = response.json()["versionToken"]

            if not self.visit_id or not self.visitor_id:
                raise ValueError("Failed to obtain required cookies")

            return moduleversion

        except Exception as e:
            logging.error(f"Error initializing session: {e}")
            raise

    def login(self) -> bool:
        """
        Authenticate with the MTC system.
        
        Returns:
            bool: True if login successful, False otherwise
        """
        try:
            self.module_version = self._initialize_session()
            self.session.headers.update({"X-CSRFToken": "T6C+9iB49TLra4jEsMeSckDMNhQ="})

            payload = {
                "versionInfo": {
                    "moduleVersion": self.module_version,
                    "apiVersion": self._get_api_version('login'),
                },
                "viewName": "CommonMTc.Login",
                "inputParameters": {
                    "Username": self.username,
                    "Password": self.password,
                    "KeepMeLoggedIn": True,
                },
            }

            # Perform login request
            response = self.session.post(
                f"{self.base_url}/MultiTankcard/screenservices/OtmAcc_Account/ActionAppLogin",
                json=payload,
            )
            response.raise_for_status()

            # Get the nr2Users cookie value
            nr2_cookie = response.cookies["nr2Users"]

            # URL decode the cookie value
            decoded_cookie = unquote(nr2_cookie)

            # Split by semicolon and find the crf part
            cookie_parts = decoded_cookie.split(";")
            csrf_part = next(part for part in cookie_parts if part.startswith("crf="))

            # Extract the actual CSRF value
            self.csrf_value = f"{csrf_part.split('=')[1]}="
            self.session.headers.update({"X-CSRFToken": self.csrf_value})

            result = response.json()
            success = "error" not in result
            if success:
                logging.info("Successfully logged in to MTC")
            else:
                logging.error("Failed to log in to MTC")
            return success

        except Exception as e:
            logging.error(f"Login failed: {str(e)}")
            return False

    def submit_reimbursement(self, claim_data: Dict) -> Tuple[bool, str]:
        """
        Submit a reimbursement claim to MTC.
        
        Args:
            claim_data: Dictionary containing claim details
        
        Returns:
            Tuple[bool, str]: (success status, message describing the result)
        """
        try:
            if not self.session.cookies.get("osVisit"):
                if not self.login():
                    return False, "Authentication required"

            # Check for duplicates using the transaction API version
            current_epoch = int(time.time())  # Current time in seconds
            thirty_days_ago = current_epoch - (90 * 24 * 60 * 60)  # 30 days in seconds

            transactions_payload = {
                "versionInfo": {
                    "moduleVersion": self.module_version,
                    "apiVersion": self._get_api_version('transactions')
                },
                "viewName": "MainFlow.Transactions",
                "screenData": {
                    "variables": {
                        "ShowSharePopup": False,
                        "InputParameterString": (
                            f"{time.strftime('%Y-%m-%d 00:00:00', time.gmtime(thirty_days_ago))}"
                            f"{time.strftime('%Y-%m-%d 23:59:59', time.gmtime(current_epoch))}0"
                        ),
                        "MaxRecords": 20,
                        "IsFirstLoad": False,
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
                            "SecondAlternativeLinkPayload": ""
                        },
                        "EmptyPopupValues": {
                            "IconClassName": "",
                            "Title": "",
                            "Content": "",
                            "ButtonText": "",
                            "ButtonEventPayload": "",
                            "AlternativeLinkText": "",
                            "AlternativeLinkPayload": "",
                            "SecondAlternativeText": "",
                            "SecondAlternativeLinkPayload": ""
                        },
                        "IsShowNoClaimsPopup": False,
                        "TransactionTypeIdCurrentFilter": "",
                        "_transactionTypeIdCurrentFilterInDataFetchStatus": 1,
                        "StartDateTimeCurrentFilter": time.strftime('%Y-%m-%dT%H:%M:%S.000Z', time.gmtime(thirty_days_ago)),
                        "_startDateTimeCurrentFilterInDataFetchStatus": 1,
                        "EndDateTimeCurrentFilter": time.strftime('%Y-%m-%dT%H:%M:%S.000Z', time.gmtime(current_epoch)),
                        "_endDateTimeCurrentFilterInDataFetchStatus": 1,
                        "ForceRefreshList": 0,
                        "_forceRefreshListInDataFetchStatus": 1
                    }
                }
            }

            transactions_response = self.session.post(
                f"{self.base_url}/MultiTankcard/screenservices/OtmTrx_Transactions/Trx_Screen/Overview/DataActionGetTransactions",
                json=transactions_payload,
                # headers={"X-CSRFToken": self.session.headers.get("X-CSRFToken")},
            )

            if not transactions_response.ok:
                return False, f"Failed to fetch transactions: HTTP {transactions_response.status_code}"

            transactions_data = transactions_response.json()
            if "error" in transactions_data:
                return False, f"API error in transactions: {transactions_data['error']}"

            transactions = transactions_data["data"]["Transactions"]["List"]
            for transaction in transactions:
                if transaction.get("ClaimNote") == claim_data["chargeSessionId"]:
                    msg = f"Skipping duplicate claim for session {claim_data['chargeSessionId']} at {claim_data['location']}"
                    logging.info(msg)
                    return True, msg  # Return True for duplicates since this is not an error condition

            if os.getenv("MODE") == "DRY":
                msg = f"[DRY RUN] Would submit claim for {claim_data['location']} ({claim_data['kwh_charged']} kWh, €{claim_data['total_price']})"
                logging.info(msg)
                return True, msg

            # Submit the claim
            claim_payload = {
                "versionInfo": {
                    "moduleVersion": self.module_version,
                    "apiVersion": self._get_api_version('submit')
                },
                "viewName": "MainFlow.Transactions",
                "inputParameters": {
                    "ClaimNew": {
                        "TransactionTypeId": "EV",
                        "Iban": os.getenv("IBAN"),
                        "Amount": str(claim_data["total_price"]),
                        "DateTransaction": claim_data["datetime"],
                        "Mileage": "0",
                        "IsForeign": False,
                        "CountryId": "NL",
                        "IsReplacement": False,
                        "Quantity": str(claim_data["kwh_charged"]),
                        "Description": f"{claim_data['chargeSessionId']}",
                        "ProductCode": "10"
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
            )

            if not response.ok:
                return False, f"HTTP error submitting claim: {response.status_code}"

            result = response.json()
            
            # Check for API-level errors
            if "error" in result:
                error_msg = f"API error: {result['error']}"
                logging.error(error_msg)
                return False, error_msg

            if result.get('data', {}).get('Success'):
                msg = f"Successfully submitted claim for {claim_data['location']} ({claim_data['kwh_charged']} kWh, €{claim_data['total_price']})"
                logging.info(msg)
                return True, msg
            else:
                error_msg = f"Failed to submit claim: {result.get('data', {}).get('ErrorMessage', 'Unknown error')}"
                logging.error(error_msg)
                return False, error_msg

        except Exception as e:
            error_msg = f"Error submitting claim: {str(e)}"
            logging.error(error_msg)
            return False, error_msg


def test_mtc_client():
    """Test function for the MTCClient"""
    # Configure logging
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
    )

    client = MTCClient()

    # Test login
    print("Testing login...")
    login_success = client.login()
    print(f"Login successful: {login_success}")

    # if login_success:
    #     # Test submission with dummy data
    #     test_claim = {
    #         'datetime': '2024-11-23T17:20:48+01:00',
    #         'location': 'Dordrecht, Netherlands',
    #         'kwh_charged': 16.824,
    #         'total_price': 4.87,
    #         'currency': 'EUR',
    #         'invoice_jpeg_base64': 'base64_encoded_jpeg_here'
    #     }

    # print("Testing reimbursement submission...")
    # submission_success = client.submit_reimbursement(test_claim)
    # print(f"Submission successful: {submission_success}")


if __name__ == "__main__":
    test_mtc_client()
