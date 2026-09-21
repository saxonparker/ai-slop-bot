"""Pending checkouts and exactly-once crediting of verified PayPal captures."""

import hashlib
import json
import os
import re
import secrets
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

import boto3
from boto3.dynamodb.types import TypeSerializer
from botocore.config import Config
from botocore.exceptions import ClientError

from paypal_api import PayPalError


def create_sandbox_checkout(user, amount):
    """Invoke only the isolated sandbox Lambda; never switch the bot's environment."""
    amount = amount_in_dollars(amount)
    try:
        response = boto3.client("lambda", config=Config(read_timeout=20, connect_timeout=3)).invoke(
            FunctionName="ai-slop-payments-sandbox", InvocationType="RequestResponse",
            Payload=json.dumps({"action": "create_test_checkout", "user": user, "amount": str(amount)}).encode(),
        )
    except ClientError as exc:
        raise ValueError("PayPal Sandbox is unavailable or not deployed yet. Regular -pay is unchanged.") from exc
    result = json.loads(response["Payload"].read())
    if response.get("FunctionError") or result.get("statusCode") != 200:
        raise ValueError("PayPal Sandbox is not ready. Complete its app and webhook setup before testing.")
    link = json.loads(result["body"]).get("url", "")
    url = urlsplit(link)
    if url.scheme != "https" or url.path != "/payments/sandbox/checkout" or not url.fragment:
        raise ValueError("The test checkout did not return a sandbox link.")
    return link


def create_live_checkout(user, amount):
    """A real credit purchase must never be routed to Sandbox."""
    if environment() != "live":
        raise ValueError("Live PayPal checkout is not configured correctly. Contact Saxon.")
    return create_checkout(user, amount)


def environment():
    """Require a named environment; never guess live versus sandbox."""
    value = os.environ.get("PAYPAL_ENVIRONMENT")
    if value not in ("sandbox", "live"):
        raise ValueError("Payments are not enabled yet. Contact Saxon for credits.")
    return value


def amount_in_dollars(value):
    """Reject negative, nonfinite, fractional-cent, and excessive purchases."""
    try:
        amount = Decimal(str(value))
        if (not amount.is_finite() or not Decimal("1") <= amount <= Decimal("500")
                or amount != amount.quantize(Decimal("0.01"))):
            raise ValueError
        return amount.quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("Choose a USD amount from $1 to $500, with at most two decimal places.") from exc


def _table():
    environment()
    return boto3.resource("dynamodb").Table(os.environ["PAYMENTS_TABLE_NAME"])


def get_purchase(purchase_id):
    """Read a checkout consistently from this environment's payment table."""
    item = _table().get_item(Key={"id": purchase_id}, ConsistentRead=True).get("Item")
    if item and item.get("environment") != environment():
        raise ValueError("This checkout belongs to a different payment environment.")
    return item


def create_checkout(user, amount):
    """Called by the bot, or an IAM-authorized sandbox Lambda invocation."""
    env = environment()
    if os.environ.get("PAYMENTS_ENABLED") != "true":
        raise ValueError("Payments are not enabled yet. Contact Saxon for credits.")
    amount = amount_in_dollars(amount)
    if not isinstance(user, str) or not user.strip() or len(user) > 100:
        raise ValueError("Invalid credit recipient.")
    base_url = os.environ["PAYMENTS_BASE_URL"].rstrip("/")
    if not base_url.startswith("https://"):
        raise ValueError("Checkout requires HTTPS.")
    token = secrets.token_urlsafe(32)
    purchase_id = hashlib.sha256(token.encode()).hexdigest()
    _table().put_item(Item={
        "id": purchase_id, "environment": env, "user": user, "amount": amount,
        "status": "pending", "created_at": int(time.time()),
        # Keep order creation retries within PayPal's six-hour idempotency window.
        # Paid records are permanent; no TTL may delete the replay protection.
        "expires_at": int(time.time()) + 3600,
    }, ConditionExpression="attribute_not_exists(id)")
    # A URL fragment is not transmitted in HTTP requests or Referer headers.
    return f"{base_url}/checkout#{token}"


def from_token(token):
    """Resolve a checkout capability without persisting its plaintext token."""
    if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
        raise ValueError("Invalid checkout link. Request a new link from the bot.")
    item = get_purchase(hashlib.sha256(token.encode()).hexdigest())
    if not item:
        raise ValueError("Checkout not found. Request a new link from the bot.")
    return item


def public_status(purchase):
    """Return only the information needed by the checkout browser."""
    return {
        "status": purchase["status"], "amount": str(purchase["amount"]),
        "environment": purchase["environment"],
    }


def create_order(purchase, api):
    """Create one PayPal order per purchase, retrying with the same request ID."""
    if purchase["status"] == "credited":
        raise ValueError("This purchase has already been credited.")
    if time.time() >= int(purchase["expires_at"]):
        raise ValueError("Checkout expired. Request a new link from the bot.")
    if purchase.get("order_id"):
        return purchase["order_id"]
    order = api.create_order(purchase)
    order_id = order["id"]
    try:
        _table().update_item(
            Key={"id": purchase["id"]},
            UpdateExpression="SET order_id = :order",
            ConditionExpression="attribute_not_exists(order_id) OR order_id = :order",
            ExpressionAttributeValues={":order": order_id},
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise
        raise PayPalError("Checkout is already being processed. Reload this page.") from exc
    return order_id


def _validate_order(purchase, order):
    units = order.get("purchase_units", [])
    if (order.get("id") != purchase.get("order_id") or order.get("intent") != "CAPTURE"
            or len(units) != 1 or units[0].get("custom_id") != purchase["id"]):
        raise ValueError("Payment does not match this checkout.")
    expected = {"currency_code": "USD", "value": str(purchase["amount"])}
    # Compare amounts numerically, since PayPal may normalize decimal formatting.
    money = units[0].get("amount", {})
    if (money.get("currency_code") != expected["currency_code"]
            or Decimal(money.get("value", "-1")) != purchase["amount"]):
        raise ValueError("Payment amount does not match this checkout.")
    return units[0]


def settle(purchase, api):
    """Approval alone never earns credits. Only a completed capture does."""
    if purchase["status"] == "credited":
        return public_status(purchase)
    if not purchase.get("order_id"):
        raise ValueError("Create a checkout before completing a payment.")
    order = api.get_order(purchase["order_id"])
    _validate_order(purchase, order)
    if order.get("status") == "APPROVED":
        try:
            api.capture_order(purchase["order_id"], purchase["id"])
        except PayPalError:
            # A browser and webhook can capture concurrently, or the response can
            # be lost after capture. Always reconcile against PayPal's order.
            order = api.get_order(purchase["order_id"])
            if order.get("status") != "COMPLETED":
                raise
        else:
            order = api.get_order(purchase["order_id"])
    unit = _validate_order(purchase, order)
    captures = unit.get("payments", {}).get("captures", [])
    if order.get("status") != "COMPLETED" or not captures:
        return public_status(purchase)
    if len(captures) != 1:
        raise ValueError("Unexpected payment captures. Contact Saxon.")
    capture = captures[0]
    if capture.get("status") != "COMPLETED":
        return public_status(purchase)
    amount = capture.get("amount", {})
    if (amount.get("currency_code") != "USD"
            or Decimal(amount.get("value", "-1")) != purchase["amount"]
            or not capture.get("id") or capture.get("final_capture") is not True):
        raise ValueError("Captured payment does not match this checkout.")
    credit_capture(purchase, capture["id"])
    return {**public_status(purchase), "status": "credited"}


def credit_capture(purchase, capture_id):
    """Atomically record a capture and its ledger credit, including replay guards."""
    env = environment()
    if purchase["environment"] != env:
        raise ValueError("Payment environment mismatch.")
    ledger = os.environ["PAYMENTS_LEDGER_TABLE_NAME"]
    # Defense in depth in addition to the sandbox Lambda's separate IAM role.
    if env == "sandbox" and not ledger.endswith("-sandbox"):
        raise ValueError("Sandbox payments require a sandbox ledger.")
    table_name = os.environ["PAYMENTS_TABLE_NAME"]
    serialize = TypeSerializer().serialize
    def item(values):
        return {key: serialize(value) for key, value in values.items()}
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        boto3.client("dynamodb").transact_write_items(TransactItems=[
            {"Update": {
                "TableName": table_name, "Key": item({"id": purchase["id"]}),
                "UpdateExpression": "SET #s = :credited, capture_id = :capture",
                "ConditionExpression": "#s = :pending AND order_id = :order AND environment = :env",
                "ExpressionAttributeNames": {"#s": "status"},
                "ExpressionAttributeValues": item({
                    ":credited": "credited", ":pending": "pending", ":capture": capture_id,
                    ":order": purchase["order_id"], ":env": env,
                }),
            }},
            {"Put": {
                "TableName": table_name,
                "Item": item({"id": "capture#" + capture_id, "purchase_id": purchase["id"]}),
                "ConditionExpression": "attribute_not_exists(id)",
            }},
            {"Put": {
                "TableName": ledger,
                "Item": item({
                    "user": purchase["user"], "timestamp": now + "#paypal#" + capture_id,
                    "amount": purchase["amount"], "type": "payment",
                    "source_user": purchase["user"], "note": "Verified PayPal payment",
                    "paypal_capture_id": capture_id, "paypal_order_id": purchase["order_id"],
                    "payment_environment": env,
                }),
                "ConditionExpression": "attribute_not_exists(#u)",
                "ExpressionAttributeNames": {"#u": "user"},
            }},
        ])
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "TransactionCanceledException":
            raise
        current = get_purchase(purchase["id"])
        if not current or current.get("status") != "credited" or current.get("capture_id") != capture_id:
            raise


def webhook(payload, api):
    """Called only after signature verification by the HTTP handler."""
    event_type = payload.get("event_type")
    resource = payload.get("resource", {})
    if event_type == "CHECKOUT.ORDER.APPROVED":
        order_id = resource.get("id")
    elif event_type == "PAYMENT.CAPTURE.COMPLETED":
        order_id = resource.get("supplementary_data", {}).get("related_ids", {}).get("order_id")
    else:
        return
    if not order_id:
        raise ValueError("Payment notification is missing its order.")
    order = api.get_order(order_id)
    units = order.get("purchase_units", [])
    if len(units) != 1 or not units[0].get("custom_id"):
        return  # A different integration using the same PayPal app.
    purchase = get_purchase(units[0]["custom_id"])
    if not purchase:
        return
    # If order creation reached PayPal but its database write failed, recover
    # that association from the authenticated PayPal API response.
    if not purchase.get("order_id"):
        _table().update_item(
            Key={"id": purchase["id"]}, UpdateExpression="SET order_id = :order",
            ConditionExpression="attribute_not_exists(order_id) OR order_id = :order",
            ExpressionAttributeValues={":order": order_id},
        )
        purchase["order_id"] = order_id
    _validate_order(purchase, order)
    settle(purchase, api)
