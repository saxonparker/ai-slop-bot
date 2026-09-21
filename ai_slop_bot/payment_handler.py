"""Public checkout endpoints and authenticated PayPal notifications."""

import base64
import json
import os
from pathlib import Path

import payments
from paypal_api import PayPal


def _response(status, payload, content_type="application/json"):
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": content_type, "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer", "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
        },
        "body": json.dumps(payload) if content_type == "application/json" else payload,
    }


def handler(event, _context):
    """No request bodies, checkout tokens, or payer details are logged."""
    # Explicit route dispatch keeps each endpoint's trust boundary visible.
    # pylint: disable=too-many-return-statements,too-many-branches
    try:
        if "requestContext" not in event:
            # Only direct, IAM-authorized Lambda invocations can create test links.
            if payments.environment() != "sandbox" or event.get("action") != "create_test_checkout":
                return _response(403, {"error": "Direct invocation is sandbox-only."})
            return _response(200, {"url": payments.create_checkout(event["user"], event["amount"])})

        method = event.get("httpMethod") or event["requestContext"].get("http", {}).get("method")
        path = event.get("rawPath") or event.get("path", "")
        base_path = "/payments/" + payments.environment()
        if method == "GET" and path == base_path + "/checkout":
            config = {
                "clientId": os.environ["PAYPAL_CLIENT_ID"],
                "environment": payments.environment(), "basePath": base_path,
            }
            html = Path(__file__).with_name("checkout.html").read_text(encoding="utf-8")
            html = html.replace("__PAYMENT_CONFIG__", json.dumps(config).replace("<", "\\u003c"))
            return _response(200, html, "text/html; charset=utf-8")
        if method != "POST" or path not in {
                base_path + "/status", base_path + "/order", base_path + "/capture", base_path + "/webhook"}:
            return _response(404, {"error": "Not found."})
        raw = event.get("body") or ""
        if event.get("isBase64Encoded"):
            raw = base64.b64decode(raw, validate=True).decode("utf-8")
        if len(raw) > 100_000:
            return _response(413, {"error": "Request too large."})
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("Expected a JSON object.")
        if path == base_path + "/webhook":
            api = PayPal()
            headers = {key.lower(): value for key, value in (event.get("headers") or {}).items()}
            if not api.verify_webhook(headers, raw):
                return _response(401, {"error": "Invalid payment notification signature."})
            payments.webhook(payload, api)
            # Log only the verified event ID so delivery can be checked without payer data.
            print(f"PAYMENT WEBHOOK: accepted {str(payload.get('id', 'unknown'))[:100]!r}")
            return _response(200, {"received": True})
        purchase = payments.from_token(payload.get("token"))
        if path == base_path + "/status":
            return _response(200, payments.public_status(purchase))
        if path == base_path + "/order":
            return _response(200, {"orderId": payments.create_order(purchase, PayPal())})
        return _response(200, payments.settle(purchase, PayPal()))
    except (ValueError, KeyError) as exc:
        # Key errors can indicate missing configuration; do not expose its names.
        message = str(exc) if isinstance(exc, ValueError) else "Checkout is not configured yet."
        return _response(400, {"error": message})
    except Exception as exc:  # pylint: disable=broad-except
        print(f"PAYMENT ERROR: {type(exc).__name__}")
        # 5xx makes PayPal retry transient failures, including failed ledger writes.
        return _response(503, {"error": "Payment verification is temporarily unavailable. Retry this checkout."})
