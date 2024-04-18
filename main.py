import requests
from PIL import Image
import base64
from pdf2image import convert_from_path
from invoice2data import extract_data
from invoice2data.extract.loader import read_templates
import zipfile
import os
import sys
from submit import submit_json
import click
from pyfiglet import Figlet

# YOU MUST FILL IN THESE VARIABLES FOR THE SCRIPT TO WORK!!!
# GO TO https://mtc.outsystemsenterprise.com/ in a browser. log in, using F12 > Network tab (Make sure you disable cache!!)
# Get from the request headers the following cookie values:
# - osVisit
# - osVisitor
# - nr1Users
# - nr2Users
# And X-Csrftoken
### Cookies

osVisit = "<SEE INSTRUCTIONS ABOVE>"
osVisitor= "<SEE INSTRUCTIONS ABOVE>"
nr1Users = "<SEE INSTRUCTIONS ABOVE>"
nr2Users = "<SEE INSTRUCTIONS ABOVE>"
### CSRF Token
Csrftoken = "<SEE INSTRUCTIONS ABOVE>"
### Iban
Iban = "<YOUR IBAN, CHECK CAREFULLY>"




output_folder = "extracted_pdfs"
done_folder = "submitted"


def extract_invoice_values(pdf_path):
    templates = read_templates("templates")
    result = extract_data(pdf_path, templates=templates)
    print(result)
    return result


def convert_pdf_to_json(pdf_path, page_num=1):
    # Store Pdf with convert_from_path function
    images = convert_from_path(pdf_path)
    filename = pdf_path.split("/")[-1:][0]
    for i in range(len(images)):

        # Save pages as images in the pdf
        images[i].save(f"{done_folder}/{filename}", "JPEG")
        images[i].show()

        with open(f"{done_folder}/{filename}", "rb") as image_file:
            base64_string = base64.b64encode(image_file.read()).decode("utf-8")
            return base64_string


def extract_information(pdf_path):
    invoice_data = extract_invoice_values(pdf_path)
    # Convert pdf
    submit_json["inputParameters"]["Attachment"]["Binary"] = convert_pdf_to_json(
        pdf_path
    )
    submit_json["inputParameters"]["Claim"]["Amount"] = str(invoice_data["amount"])
    # Set submission date
    submit_json["inputParameters"]["Claim"]["DateTransaction"] = invoice_data[
        "date"
    ].strftime("%Y-%m-%dT%H:%M:%S.000Z")
    # Set charging amount in kwh
    submit_json["inputParameters"]["Claim"]["Quantity"] = str(invoice_data["charged"])
    # Set iban
    submit_json["inputParameters"]["Claim"]["Iban"] = Iban
    if click.confirm(f'Are you sure you want to submit this reimbursement? \nFactuurnummer: {str(invoice_data["invoice_number"])} \nDatum: {submit_json["inputParameters"]["Claim"]["DateTransaction"]}\nBedrag: €{submit_json["inputParameters"]["Claim"]["Amount"]}\nkWh: {submit_json["inputParameters"]["Claim"]["Quantity"]}\n', default=True):
        click.echo("Submitting")
    else:
        click.echo("Reimbursement cancelled")
        sys.exit(1)
    return submit_json


def submit_declaratie(_json_data):
    cookies = {
        "osVisit": osVisit,
        "osVisitor": osVisitor,
        "nr2Users": nr2Users,
        "nr1Users": nr1Users,
    }
    headers = {
        "Connection": "keep-alive",
        "sec-ch-ua": '"Android WebView";v="123", "Not:A-Brand";v="8", "Chromium";v="123"',
        "sec-ch-ua-mobile": "?1",
        "User-Agent": "Mozilla/5.0 (Linux; Android 9; SM-N935F Build/PPR1.180610.011; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/123.0.6312.118 Mobile Safari/537.36 OutSystemsApp v.2.19",
        "Content-Type": "application/json; charset=UTF-8",
        "Accept": "application/json",
        "X-CSRFToken": Csrftoken,
        "sec-ch-ua-platform": '"Android"',
        "Origin": "https://mtc.outsystemsenterprise.com",
        "X-Requested-With": "com.outsystemsenterprise.mtc.MultiTankcard",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
        "Referer": "https://mtc.outsystemsenterprise.com/MultiTankcard/Transactions",
        "Accept-Language": "nl-NL,nl;q=0.9,en-US;q=0.8,en;q=0.7",
    }

    submit_response = requests.post(
        "https://mtc.outsystemsenterprise.com/MultiTankcard/screenservices/OtmTrx_Transactions/Claim/ClaimForm/ActionSendNewClaimToDatabase",
        json=_json_data,
        headers=headers,
        cookies=cookies,
    )
    return submit_response




if __name__ == "__main__":
    f = Figlet(font='standard')
    print(f.renderText('Tesla MTC Reimbursement tool'))
    # Check if at least one command-line argument is provided
    if len(sys.argv) < 2:
        print("[!] Usage: python3 main.py <name of zipfile>")
        sys.exit(1)

    # Check if necessary folders exists, otherwise create them
    os.makedirs(output_folder, exist_ok=True)
    os.makedirs(done_folder, exist_ok=True)

    # Extract PDFs from the zip file
    zip_file_path = sys.argv[1]
    with zipfile.ZipFile(zip_file_path, "r") as zip_ref:
        print(f"[i] extracting {zip_file_path}")
        zip_ref.extractall(output_folder)

    # Loop through extracted PDFs
    for root, dirs, files in os.walk(output_folder):
        for file in files:
            if file.endswith(".pdf"):
                submitted_path = os.path.join(done_folder, file)
                pdf_path = os.path.join(root, file)
                if not os.path.exists(submitted_path):
                    try:
                        # Submit declaration
                        result = submit_declaratie(extract_information(pdf_path))
                        if result.status_code != 200:
                            print(result.json())
                            print("Could not log in, check your cookies and csrf token!")
                            os.remove(submitted_path)
                            sys.exit(1)
                        if result.json()["data"]["Success"] != True:
                            print(
                                f"Something went wrong with this declaration: {result.json()['data']['ErrorMessage']}"
                            )
                            # Remove from submitted folder
                            os.remove(submitted_path)
                    except Exception as e:
                        print(f"Something unexpected happened, Have you filled in your cookies??? \nError: {e}")
                        os.remove(submitted_path)
                else:
                    print(
                        f"[!] Invoice {file} already handled, if something went wrong, remove it from the submitted folder"
                    )
                # After handling the pdf, delete it from extracted_pdfs
                os.remove(pdf_path)
                os.rmdir(os.path.dirname(pdf_path))