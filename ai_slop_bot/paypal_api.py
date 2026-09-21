"""Small server-side PayPal client; credentials never go to the browser."""

import json
import os
import re
import time

import requests


class PayPalError(RuntimeError):
    """A payment API failure that is safe to describe to a customer."""


class PayPal:
    """Use only the explicitly configured PayPal environment."""

    def __init__(self):
        self.environment = os.environ["PAYPAL_ENVIRONMENT"]
        self.host = {
            "sandbox": "https://api-m.sandbox.paypal.com",
            "live": "https://api-m.paypal.com",
        }[self.environment]
        self.client_id = os.environ["PAYPAL_CLIENT_ID"]
        self.secret = os.environ["PAYPAL_CLIENT_SECRET"]
        self.token = None
        self.token_expires = 0

    def _access_token(self):
        if self.token and time.time() < self.token_expires:
            return self.token
        response = requests.post(
            self.host + "/v1/oauth2/token",
            auth=(self.client_id, self.secret),
            data={"grant_type": "client_credentials"}, timeout=(3, 10), allow_redirects=False,
        )
        if not response.ok:
            raise PayPalError("PayPal is unavailable. Please try again shortly.")
        data = response.json()
        self.token = data["access_token"]
        self.token_expires = time.time() + max(0, int(data["expires_in"]) - 60)
        return self.token

    def request(self, method, path, *, data=None, request_id=None, raw=None):
        """Do not follow any URLs supplied by a browser or a webhook."""
        headers = {
            "Authorization": f"Bearer {self._access_token()}",
            "Content-Type": "application/json",
            "Prefer": "return=representation",
        }
        if request_id:
            headers["PayPal-Request-Id"] = request_id
        body = raw if raw is not None else json.dumps(data) if data is not None else None
        try:
            response = requests.request(
                method, self.host + path, headers=headers,
                data=body.encode("utf-8") if body is not None else None,
                timeout=(3, 15), allow_redirects=False,
            )
        except requests.RequestException as exc:
            raise PayPalError("PayPal did not respond. Please retry this checkout.") from exc
        if not response.ok:
            # Do not include the response, payer details, or authorization header in logs.
            raise PayPalError("PayPal could not finish this request. Please retry this checkout.")
        return response.json()

    def create_order(self, purchase):
        """Bind the expected amount and internal purchase ID on PayPal's server."""
        return self.request("POST", "/v2/checkout/orders", request_id="order-" + purchase["id"][:32], data={
            "intent": "CAPTURE",
            "purchase_units": [{
                "custom_id": purchase["id"],
                "invoice_id": purchase["id"],
                "description": "AI Slop credits",
                "amount": {"currency_code": "USD", "value": str(purchase["amount"])},
            }],
        })

    @staticmethod
    def _order_path(order_id):
        if not isinstance(order_id, str) or not re.fullmatch(r"[A-Z0-9]{1,32}", order_id):
            raise ValueError("Invalid PayPal order.")
        return "/v2/checkout/orders/" + order_id

    def get_order(self, order_id):
        """Read an order from this merchant's authenticated PayPal API."""
        return self.request("GET", self._order_path(order_id))

    def capture_order(self, order_id, purchase_id):
        """Capture an approved order, with a stable idempotency key."""
        return self.request("POST", self._order_path(order_id) + "/capture",
                            request_id="cap-" + purchase_id[:32], data={})

    def verify_webhook(self, headers, raw_body):
        """Ask PayPal to verify a real app event against our configured listener."""
        webhook_id = os.environ.get("PAYPAL_WEBHOOK_ID")
        if not webhook_id:
            raise PayPalError("Payment notifications are not configured yet.")
        fields = {
            "auth_algo": "paypal-auth-algo", "cert_url": "paypal-cert-url",
            "transmission_id": "paypal-transmission-id",
            "transmission_sig": "paypal-transmission-sig",
            "transmission_time": "paypal-transmission-time",
        }
        if any(not headers.get(header) for header in fields.values()):
            return False
        payload = {field: headers[header] for field, header in fields.items()}
        payload["webhook_id"] = webhook_id
        # PayPal requires the original webhook JSON, including its formatting.
        raw = json.dumps(payload)[:-1] + ', "webhook_event":' + raw_body + "}"
        result = self.request("POST", "/v1/notifications/verify-webhook-signature", raw=raw)
        return result.get("verification_status") == "SUCCESS"
