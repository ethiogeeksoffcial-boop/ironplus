"""
verify_service.py — Iron Plus Gym payment verification microservice

Wraps the ethiobank-receipts library behind a small Flask API that
backend/submitPayment.js calls to confirm a bank transfer or Telebirr
payment actually happened before a membership gets activated.

This replaces the verify_service.py skeleton referenced in the audit.

--------------------------------------------------------------------
SETUP
--------------------------------------------------------------------
    pip install ethiobank-receipts flask requests pdfplumber beautifulsoup4 --break-system-packages

    # BOA support additionally needs a real browser:
    pip install selenium --break-system-packages
    # + a Chrome/Chromium binary and matching chromedriver on this machine.
    # If that's not set up, BOA requests are automatically routed to
    # needs_review instead of crashing the service — see BOA_AVAILABLE below.

    # If the pip package isn't published/reachable, drop the vendored
    # copy from the ethiobank-receipts skill's scripts/ethiobank_receipts
    # folder into a "vendor/" directory next to this file — the import
    # fallback below will pick it up automatically.

--------------------------------------------------------------------
RUN
--------------------------------------------------------------------
    python verify_service.py
    # listens on http://localhost:5000 by default

--------------------------------------------------------------------
CONTRACT (assumed — see note at the bottom of this file)
--------------------------------------------------------------------
POST /verify
    { "bank": "cbe", "reference": "<FT number, receipt URL, or Telebirr ID>",
      "account": "<only for CBE if reference is an FT number, not a URL>",
      "expected_amount": 3200 }

    -> 200 { "status": "paid" | "needs_review",
             "reason": null | "amount_mismatch" | "extractor_error" | ...,
             "bank": "cbe", "reference": "...",
             "extracted_amount": 3200.0, "expected_amount": 3200,
             "amount_matched": true, "extracted": { ...raw fields... } }

    -> 400 { "error": "..." }   (bad request — missing/malformed input)

GET /health
    -> 200 { "ok": true, "boa_available": true, "time": "..." }
"""

import logging
import os
import re
import sys
from datetime import datetime, timezone

from flask import Flask, request, jsonify

# ---------------------------------------------------------------------------
# Import the extractors. Prefer the pip package; fall back to a vendored
# copy (see SETUP above) if it isn't installed.
# ---------------------------------------------------------------------------
try:
    from ethiobank_receipts.extractors import cbe, dashen, awash, zemen, tele
except ImportError:
    VENDORED_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor")
    if os.path.isdir(VENDORED_PATH):
        sys.path.insert(0, VENDORED_PATH)
    from ethiobank_receipts.extractors import cbe, dashen, awash, zemen, tele

# BOA needs Selenium + a real Chrome binary — import it separately so the
# rest of the service still works if that stack isn't set up on this box yet.
try:
    from ethiobank_receipts.extractors import boa
    BOA_AVAILABLE = True
    BOA_IMPORT_ERROR = None
except Exception as e:  # missing selenium, missing chromedriver, etc.
    boa = None
    BOA_AVAILABLE = False
    BOA_IMPORT_ERROR = str(e)

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("verify_service")

AMOUNT_TOLERANCE = float(os.environ.get("AMOUNT_TOLERANCE_ETB", "1.0"))

# ---------------------------------------------------------------------------
# TLS hardening — ethiobank_receipts/download.py's download_pdf_from_url()
# defaults to verify_ssl=False. cbe, dashen, and zemen are the three
# extractors that go through it (awash uses its own requests.Session with
# default verification; tele uses the shared session, also default-verified).
# See ethiobank-receipts skill, hard constraint #4 — flip this to True
# before any of this touches real payments.
# ---------------------------------------------------------------------------
def _harden_tls():
    from ethiobank_receipts.download import download_pdf_from_url as _orig

    def _secure(url, verify_ssl=True):
        return _orig(url, verify_ssl=verify_ssl)

    for module in (cbe, dashen, zemen):
        if hasattr(module, "download_pdf_from_url"):
            module.download_pdf_from_url = _secure
    log.info("TLS verification enforced for cbe/dashen/zemen PDF downloads")


try:
    _harden_tls()
except Exception as e:
    log.warning("Could not harden TLS verification, continuing with library defaults: %s", e)

# ---------------------------------------------------------------------------
# Which field holds the amount for each bank's raw output.
# See ethiobank-receipts skill -> references/field-reference.md
# ---------------------------------------------------------------------------
AMOUNT_FIELDS = {
    "cbe": ["transferred_amount"],
    "dashen": ["amount", "total"],
    "zemen": ["Total Amount Paid", "Settled Amount"],
    "awash": ["Amount"],
    "boa": ["Total Amount", "Transferred Amount"],
    "tele": ["total_paid"],
}

SUPPORTED_BANKS = {"cbe", "dashen", "awash", "zemen", "boa", "tele"}


def _parse_amount(raw):
    """'3,200.00 ETB' -> 3200.0. Returns None if it can't be parsed."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    cleaned = re.sub(r"[^\d.]", "", str(raw))
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _extract_amount(bank, data):
    for field in AMOUNT_FIELDS.get(bank, []):
        if isinstance(data, dict) and data.get(field):
            amount = _parse_amount(data[field])
            if amount is not None:
                return amount
    return None


def _run_extractor(bank, reference, account=None):
    """Dispatch to the right extractor. Raises ValueError on bad input shape,
    or lets the extractor's own exception (network/parsing) bubble up."""
    if bank == "cbe":
        if reference.startswith("http"):
            return cbe.extract_cbe_receipt_info(reference)
        if not account:
            raise ValueError("CBE reference given as an FT number also needs 'account'")
        return cbe.extract_cbe_receipt_info_from_ft(reference, account)

    if bank == "dashen":
        if not reference.startswith("http"):
            raise ValueError("Dashen needs the full receipt URL")
        return dashen.extract_dashen_receipt_data(reference)

    if bank == "zemen":
        if not reference.startswith("http"):
            raise ValueError("Zemen needs the full receipt URL")
        return zemen.extract_zemen_receipt_data(reference)

    if bank == "awash":
        if not reference.startswith("http"):
            raise ValueError("Awash needs the full receipt URL")
        return awash.extract_awash_receipt_data(reference)

    if bank == "boa":
        if not BOA_AVAILABLE:
            raise RuntimeError(f"BOA verification unavailable on this server: {BOA_IMPORT_ERROR}")
        if not reference.startswith("http"):
            raise ValueError("BOA needs the full receipt URL")
        return boa.extract_boa_receipt_data(reference)

    if bank == "tele":
        # accepts a bare receipt ID or a full URL — the extractor handles both
        return tele.extract_tele_receipt_data(reference)

    raise ValueError(f"Unsupported bank: {bank}")


@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "ok": True,
        "boa_available": BOA_AVAILABLE,
        "time": datetime.now(timezone.utc).isoformat(),
    })


@app.route("/verify", methods=["POST"])
def verify():
    body = request.get_json(silent=True) or {}
    bank = str(body.get("bank", "")).strip().lower()
    reference = str(body.get("reference", "")).strip()
    account = body.get("account")
    expected_amount = body.get("expected_amount")

    if not bank or not reference:
        return jsonify({"error": "bank and reference are required"}), 400

    if bank not in SUPPORTED_BANKS:
        # never trust the frontend's dropdown alone — validate here too
        return jsonify({"status": "needs_review", "reason": "unsupported_bank", "bank": bank}), 200

    log.info("verify request: bank=%s reference=%s...", bank, reference[:40])

    try:
        data = _run_extractor(bank, reference, account=account)
    except ValueError as e:
        # malformed input from the client — this is a 400, not a payment outcome
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        # network error, bank changed their page markup, site is down, etc.
        # never fail silently or reject outright — route to a human instead
        log.warning("extractor error for %s: %s", bank, e)
        return jsonify({
            "status": "needs_review",
            "reason": "extractor_error",
            "bank": bank,
            "reference": reference,
            "error": str(e),
        }), 200

    if not data or not any(v for v in data.values()):
        return jsonify({
            "status": "needs_review",
            "reason": "no_fields_extracted",
            "bank": bank,
            "reference": reference,
        }), 200

    extracted_amount = _extract_amount(bank, data)
    amount_matched = None
    if expected_amount is not None and extracted_amount is not None:
        try:
            amount_matched = abs(float(expected_amount) - extracted_amount) <= AMOUNT_TOLERANCE
        except (TypeError, ValueError):
            amount_matched = None

    if expected_amount is not None and extracted_amount is None:
        status, reason = "needs_review", "amount_not_found"
    elif amount_matched is False:
        status, reason = "needs_review", "amount_mismatch"
    else:
        status, reason = "paid", None

    return jsonify({
        "status": status,
        "reason": reason,
        "bank": bank,
        "reference": reference,
        "extracted_amount": extracted_amount,
        "expected_amount": expected_amount,
        "amount_matched": amount_matched,
        "extracted": data,
    }), 200


if __name__ == "__main__":
    port = int(os.environ.get("VERIFY_SERVICE_PORT", "5000"))
    app.run(host="127.0.0.1", port=port, debug=False)
