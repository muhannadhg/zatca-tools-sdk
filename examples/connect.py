"""Connecting a real EGS unit to ZATCA, and every start after that.

The first run onboards the unit and stores its credentials; later runs load
them and carry the invoice chain on. Run it as is against the sandbox:

    python connect.py

For simulation or production, set your own details and an OTP from the Fatoora
portal (it is valid for one hour):

    ZATCA_ENV=simulation ZATCA_OTP=123456 python connect.py

This example keeps its state in a local file to stay short. In your system,
keep the credentials in a secret manager and the chain in your database.
"""

import json
import os
from pathlib import Path

from zatca_tools import Zatca

ENVIRONMENT = os.environ.get("ZATCA_ENV", "sandbox")
STATE = Path(f"zatca-unit-{ENVIRONMENT}.json")  # holds a private key: never commit it

# Your company, as registered with ZATCA. In the sandbox the SDK uses ZATCA's test seller.
SELLER = None if ENVIRONMENT == "sandbox" else {
    "vat_number": os.environ["ZATCA_VAT_NUMBER"],
    "name": os.environ["ZATCA_SELLER_NAME"],
    "cr_number": os.environ["ZATCA_CR_NUMBER"],
    "address": {
        "street": os.environ["ZATCA_STREET"],
        "building_number": os.environ["ZATCA_BUILDING"],
        "district": os.environ["ZATCA_DISTRICT"],
        "city": os.environ["ZATCA_CITY"],
        "postal_code": os.environ["ZATCA_POSTAL_CODE"],
    },
}

if STATE.exists():
    # Every start after the first: the saved credentials, and where the chain stands.
    saved = json.loads(STATE.read_text())
    zatca = Zatca(ENVIRONMENT, seller=SELLER, credentials=saved["credentials"], chain=saved["chain"])
else:
    # Once per unit.
    zatca = Zatca(ENVIRONMENT, seller=SELLER)
    credentials = zatca.onboard(otp=os.environ.get("ZATCA_OTP"))  # the sandbox needs no OTP
    print("Onboarded; the certificate expires", credentials.expires_at)


def save_state() -> None:
    STATE.write_text(json.dumps({"credentials": zatca.credentials.export(), "chain": zatca.chain.to_dict()}))


invoice = zatca.create_invoice({
    "type": "simplified",
    "number": f"INV-{zatca.chain.icv + 1:05d}",
    "prices_include_vat": True,
    "items": [{"name": "Coffee", "quantity": 2, "unit_price": 18}],
})
save_state()  # the chain moves with every invoice signed — save it before sending

result = zatca.submit(invoice)
print(invoice.number, result.status, result.error.message if result.error else "")
