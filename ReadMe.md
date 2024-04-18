# Tesla MTC Reimbursement tool
Since we've all been waiting for years for Tesla to support MTC cards (and vice versa), we're stuck with manually uploading invoices using an app with 1* in the app store. This tool helps you with that

⚠️⚠️I consider this app very alpha/unstable/spaghetti, there is a check mechanism built in so you can check your values before the actual submit is done, but everything you do/submit is at your own risk⚠️⚠️

## What does it do
- Extracts all useful information from the tesla invoice
- Submits the reimbursement to MTC
- Detects if you already submitted the invoice (using this tool), so no duplicates :)


## What doesn't it do (yet)
- Currently it only supports zip files containing multiple invoices
- There is no check on the country of the invoice. So it will currently always submit as if the invoice was from NL, I don't think this will become an issue, you can change the `isforeign` boolean and `country` in `submit.py`. Country codes are included in `submit.py`
- Run on non desktop environment machines (raspberry pi etc), because it will show you the actual pdf before sending out.
- No clue yet, let me know!

## Instructions
`git clone`  
`cd tesla_mtc`  
`pip3 install requirements.txt`  
- Open `main.py` with your fav text editor
- Obtain cookies and csrf values (see below)
- Set IBAN
- Copy your zip file to the tesla_mtc folder 
  
Run `python3 main.py <filename.zip>`
- You should be prompted to confirm each reimbursement, it will also show you the pdf using a renderer for you to check the values
  - If you want to skip the pdf pop up, comment out line 55 (`.show()`)
  - If you fully trust this piece of spaghetti and don't want any confirmation, comment out lines 77 to 81 (the click part)


### Obtaining your Cookies
The cookies for this application are valid for 365 days, reversing and programming the login sequence was too much off a hassle, so you'll have to obtain the cookies yourself. 

- Open a browser (instructions are for edge, but chrome is very the same)
- Visit https://mtc.outsystemsenterprise.com/MultiTankcard/
- Press F12
- Open network tab
- Enable Preserve log
- Enable Disable cache
- Log into the app and swipe/click to reimbursements
- In F12 you should see plenty of post request
- From one of these post requests, Check the request headers (i suggest checking the raw tickbox)
- In these request headers you should find 2 headers, `Cookies` and `X-CSRFToken`
  - For cookies, copy and paste `osVisit`, `osVisitor`, `nr1Users` and `nr2Users`
  - Also copy the value of `X-CSRFToken`
  - In main.py replace the `<SEE INSTRUCTIONS ABOVE>` with your values
- Voila, you should be good for another 365 days

[!["Buy Me A Coffee"](https://www.buymeacoffee.com/assets/img/custom_images/orange_img.png)](https://www.buymeacoffee.com/wessec)