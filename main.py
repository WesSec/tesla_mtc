import os
import requests
import base64
from dotenv import load_dotenv
import uuid
from pdf2image import convert_from_bytes
import logging
import time
from MTC import MTCClient


# Load environment variables
load_dotenv()

# GraphQL query for charging history
CHARGING_HISTORY_QUERY = """
query getChargingHistoryV2($pageNumber: Int!, $sortBy: String, $sortOrder: SortByEnum, $latestSession: Boolean) {
  me {
    charging {
      historyV2(
        pageNumber: $pageNumber
        sortBy: $sortBy
        sortOrder: $sortOrder
        latestSession: $latestSession
      ) {
        data {
          countryCode
          programType
          billingType
          vin
          isMsp
          credit {
            distance
            distanceUnit
          }
          chargingPackage {
            distance
            distanceUnit
            energyApplied
          }
          chargingVoucher {
            voucherValue
          }
          invoices {
            fileName
            contentId
            invoiceType
          }
          chargeSessionId
          siteLocationName
          chargeStartDateTime
          chargeStopDateTime
          unlatchDateTime
          fees {
            sessionFeeId
            feeType
            payorUid
            amountDue
            currencyCode
            pricingType
            usageBase
            usageTier1
            usageTier2
            usageTier3
            usageTier4
            rateBase
            rateTier1
            rateTier2
            rateTier3
            rateTier4
            totalTier1
            totalTier2
            totalTier3
            totalTier4
            uom
            isPaid
            uid
            totalBase
            totalDue
            netDue
            status
            showPeriods
            periods {
              sessionFeePeriodId
              startDateTime
              stopDateTime
              actualQuantity
              rate
            }
          }
          vehicleMakeType
          sessionId
          surveyCompleted
          surveyType
          postId
          cabinetId
          din
          isDcEnforced
          siteAmenities
          siteEntryLocation {
            latitude
            longitude
          }
          siteAddress {
            street
            streetNumber
            city
            district
            state
            countryCode
            country
            postalCode
          }
          sessionSource
          additionalNotes {
            left
            right
          }
        }
        totalResults
        hasMoreData
        pageNumber
      }
    }
  }
}
"""

class TeslaAuth:
    def __init__(self):
        self.auth_url = os.getenv('TESLA_AUTH_URL', 'https://auth.tesla.com/oauth2/v3')
        self.refresh_token = os.getenv('TESLA_REFRESH_TOKEN')
        self.client_id = os.getenv('TESLA_CLIENT_ID', 'ownerapi')
        self.scope = "openid offline_access vehicle_device_data vehicle_cmds vehicle_charging_cmds"
        
    def get_new_access_token(self):
        """Get new access token using refresh token"""
        try:
            data = {
                'grant_type': 'refresh_token',
                'client_id': self.client_id,
                'refresh_token': self.refresh_token,
                'scope': self.scope
            }
            
            response = requests.post(
                f"{self.auth_url}/token",
                data=data
            )
            response.raise_for_status()
            
            tokens = response.json()
            return {
                'access_token': tokens['access_token'],
                'refresh_token': tokens.get('refresh_token', self.refresh_token),  # Some responses might not include a new refresh token
                'expires_in': tokens['expires_in']
            }
        except requests.exceptions.RequestException as e:
            logging.error(f"Error getting new access token: {e}")
            raise

class TeslaChargingAPI:
    def __init__(self):
        self.base_url = os.getenv('TESLA_API_URL', 'https://akamai-apigateway-charging-ownership.tesla.com')
        self.invoice_url = os.getenv('TESLA_INVOICE_URL', 'https://ownership.tesla.com/mobile-app/charging/invoice')
        self.vin = os.getenv('TESLA_VIN')
        self.device_country = os.getenv('DEVICE_COUNTRY', 'NL')
        self.device_language = os.getenv('DEVICE_LANGUAGE', 'nl')
        self.ttp_locale = os.getenv('TTP_LOCALE', 'nl_NL')
        
        # Initialize authentication
        self.auth = TeslaAuth()
        self.tokens = None
        self.token_expiry = 0

    def ensure_valid_token(self):
        """Ensure we have a valid access token"""
        current_time = time.time()
        
        if not self.tokens or current_time >= (self.token_expiry - 60):
            self.tokens = self.auth.get_new_access_token()
            self.token_expiry = current_time + self.tokens['expires_in']
            
        return self.tokens['access_token']

    def get_headers(self):
        """Get headers matching the Tesla app"""
        request_id = str(uuid.uuid4())
        return {
            'accept': '*/*',
            'x-tesla-user-agent': os.getenv('TESLA_USER_AGENT', 'TeslaApp/4.39.0-3019/8d0298041d/android/28'),
            'charset': 'utf-8',
            'cache-control': 'no-cache',
            'accept-language': self.device_language,
            'authorization': f'Bearer {self.ensure_valid_token()}',
            'x-txid': request_id,
            'x-request-id': request_id,
            'Content-Type': 'application/json',
            'User-Agent': 'okhttp/4.11.0'
        }

    def get_charging_history(self):
        """Fetch charging history from Tesla API"""
        try:
            params = {
                'deviceLanguage': self.device_language,
                'deviceCountry': self.device_country,
                'ttpLocale': self.ttp_locale,
                'vin': self.vin,
                'operationName': 'getChargingHistoryV2'
            }
            
            payload = {
                'query': CHARGING_HISTORY_QUERY,
                'variables': {
                    'sortBy': 'start_datetime',
                    'sortOrder': 'DESC',
                    'pageNumber': 1,
                    'latestSession': False
                },
                'operationName': 'getChargingHistoryV2'
            }
            
            response = requests.post(
                f"{self.base_url}/graphql",
                headers=self.get_headers(),
                params=params,
                json=payload
            )
            
            if response.status_code != 200:
                logging.error(f"Error response: {response.status_code} - {response.text}")
            response.raise_for_status()
            
            return response.json()
        except requests.exceptions.RequestException as e:
            logging.error(f"Error fetching charging history: {e}")
            raise

    def get_invoice_pdf(self, invoice_id):
        """Download invoice PDF from Tesla API"""
        try:
            params = {
                'deviceCountry': self.device_country,
                'deviceLanguage': self.device_language,
                'ttpLocale': self.ttp_locale,
                'vin': self.vin
            }
            
            response = requests.get(
                f"{self.invoice_url}/{invoice_id}",
                headers=self.get_headers(),
                params=params
            )
            response.raise_for_status()
            return response.content
        except requests.exceptions.RequestException as e:
            logging.error(f"Error downloading invoice: {e}")
            raise

    def convert_pdf_to_base64_jpeg(self, pdf_content):
        """Convert PDF content to base64 encoded JPEG"""
        try:
            # Convert PDF to image
            images = convert_from_bytes(pdf_content)
            if not images:
                return None
            
            # Get first page
            first_page = images[0]
            
            # Save to bytes
            import io
            img_byte_arr = io.BytesIO()
            first_page.save(img_byte_arr, format='JPEG')
            img_byte_arr = img_byte_arr.getvalue()
            
            # Convert to base64
            return base64.b64encode(img_byte_arr).decode()
        except Exception as e:
            logging.error(f"Error converting PDF to JPEG: {e}")
            return None

    def process_charging_sessions(self):
        """Process charging sessions and return structured data"""
        try:
            history = self.get_charging_history()
            
            if not history or 'data' not in history or 'me' not in history['data']:
                logging.error("Invalid response format")
                return []
            
            sessions = []
            charging_data = history['data']['me']['charging']['historyV2']['data']
            max_sessions = int(os.getenv('MAX_SESSIONS', 1))
            
            # Process limited number of sessions
            for session in charging_data[:max_sessions]:
                processed_session = {
                    'datetime': session['chargeStartDateTime'],
                    'location': session['siteLocationName'],
                    'chargeSessionId': session['chargeSessionId'],
                    'kwh_charged': 0,
                    'total_price': 0,
                    'currency': None,
                    'invoice_jpeg_base64': None
                }
                
                # Process fees
                if session.get('fees'):
                    for fee in session['fees']:
                        if fee.get('feeType') == 'CHARGING':
                            processed_session['kwh_charged'] = fee.get('usageBase', 0)
                            processed_session['total_price'] = fee.get('totalDue', 0)
                            processed_session['currency'] = fee.get('currencyCode')
                
                # Process invoice if available
                if session.get('invoices'):
                    for invoice in session['invoices']:
                        if invoice.get('contentId'):
                            try:
                                pdf_content = self.get_invoice_pdf(invoice['contentId'])
                                processed_session['invoice_jpeg_base64'] = self.convert_pdf_to_base64_jpeg(pdf_content)
                            except Exception as e:
                                logging.error(f"Error processing invoice: {e}")
                
                sessions.append(processed_session)
            
            return sessions
            
        except Exception as e:
            logging.error(f"Error processing charging sessions: {e}")
            raise


def submit_to_mtc(session_data):
    """
    Submit reimbursement to MTC using the provided session data
    Args:
        session_data: Dictionary containing:
            - datetime: ISO format datetime
            - location: charging location
            - chargeSessionID: unique session ID for checking duplicates
            - kwh_charged: amount of kWh charged
            - total_price: price in EUR
            - currency: currency code
            - invoice_jpeg_base64: base64 encoded JPEG of invoice
    Returns:
        bool: True if submission successful, False otherwise
    """
    client = MTCClient()
    return client.submit_reimbursement(session_data)

def main():
    # Set up logging with a cleaner format
    logging.basicConfig(
        level=os.getenv('LOG_LEVEL', 'INFO'),
        format='%(asctime)s [%(levelname)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    try:
        logging.info("Starting Tesla charging session processing")
        api = TeslaChargingAPI()
        sessions = api.process_charging_sessions()
        
        if not sessions:
            logging.warning("No charging sessions found to process")
            return

        logging.info(f"Found {len(sessions)} charging sessions to process")
        
        mtc_client = MTCClient()
        
        
        
        # Process each session
        for session in sessions:
            logging.info(f"Processing session: {session['location']} on {session['datetime']}, session ID: {session['chargeSessionId']}")
            logging.info(f"Details: {session['kwh_charged']} kWh, €{session['total_price']}")
            
            if not session.get('invoice_jpeg_base64'):
                logging.warning(f"No invoice available for session at {session['location']}, skipping")
                continue
            
            success, message = mtc_client.submit_reimbursement(session)
            if not success:
                logging.warning(f"Failed to process session at {session['location']}, {message}")
        
    except Exception as e:
        logging.error(f"Process failed: {str(e)}")
        raise

if __name__ == "__main__":
    main()