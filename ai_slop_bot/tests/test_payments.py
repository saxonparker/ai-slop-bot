"""Exercise payment verification, retries, atomic credits, and sandbox isolation."""

import base64
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import time
from decimal import Decimal
from unittest.mock import MagicMock, patch

import boto3
from boto3.dynamodb.types import TypeDeserializer
from botocore.exceptions import ClientError
from botocore.stub import Stubber
import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import payment_handler
import payments
import budget
import prompts
from paypal_api import PayPal, PayPalError


@pytest.fixture(autouse=True)
def configuration(monkeypatch):
    for key, value in {
        "PAYPAL_ENVIRONMENT": "sandbox", "PAYPAL_CLIENT_ID": "test-client",
        "PAYPAL_CLIENT_SECRET": "test-secret", "PAYPAL_WEBHOOK_ID": "webhook-test",
        "PAYMENTS_TABLE_NAME": "payments-sandbox", "PAYMENTS_LEDGER_TABLE_NAME": "ledger-sandbox",
        "PAYMENTS_BASE_URL": "https://example.com/payments/sandbox", "PAYMENTS_ENABLED": "true",
        "AWS_DEFAULT_REGION": "us-east-2", "AWS_ACCESS_KEY_ID": "test",
        "AWS_SECRET_ACCESS_KEY": "test", "AWS_EC2_METADATA_DISABLED": "true",
    }.items():
        monkeypatch.setenv(key, value)


@pytest.fixture
def purchase():
    return {
        "id": "a" * 64, "user": "alice", "amount": Decimal("10.00"),
        "status": "pending", "order_id": "ORDER123", "environment": "sandbox",
        "expires_at": int(time.time()) + 3600,
    }


def completed_order(purchase):
    return {
        "id": purchase["order_id"], "intent": "CAPTURE", "status": "COMPLETED",
        "purchase_units": [{
            "custom_id": purchase["id"], "amount": {"currency_code": "USD", "value": "10.00"},
            "payments": {"captures": [{
                "id": "CAPTURE123", "status": "COMPLETED", "final_capture": True,
                "amount": {"currency_code": "USD", "value": "10.00"},
            }]},
        }],
    }


def http_event(action, payload=None):
    return {
        "requestContext": {"http": {"method": "POST"}},
        "rawPath": "/payments/sandbox/" + action, "body": json.dumps(payload or {}),
    }


@pytest.mark.parametrize("amount", ["-10", "0", "0.99", "500.01", "NaN", "Infinity", "1.0000000000000000001",
                                    "-Infinity", "1.001", "not-money", None, True, "1e1000"])
def test_invalid_amounts_never_create_checkout(amount):
    with patch("payments._table") as table, pytest.raises(ValueError):
        payments.create_checkout("alice", amount)
    table.assert_not_called()


@pytest.mark.parametrize("amount,expected", [(1, "1.00"), ("12.34", "12.34"), (500, "500.00")])
def test_amounts_use_exact_cents(amount, expected):
    assert str(payments.amount_in_dollars(amount)) == expected


def test_checkout_only_records_pending_purchase():
    with patch("payments._table") as table, patch("payments.boto3.client") as client:
        url = payments.create_checkout("alice", "10")
    token = url.split("#")[1]
    item = table.return_value.put_item.call_args.kwargs["Item"]
    assert item["id"] == hashlib.sha256(token.encode()).hexdigest()
    assert item["status"] == "pending" and item["user"] == "alice"
    assert item["amount"] == Decimal("10.00")
    assert token not in str(item) and "?" not in url
    client.assert_not_called()


def test_disabled_checkout_does_not_write(monkeypatch):
    monkeypatch.setenv("PAYMENTS_ENABLED", "false")
    with patch("payments._table") as table, pytest.raises(ValueError, match="not enabled"):
        payments.create_checkout("alice", "10")
    table.assert_not_called()


def sandbox_response(*, url="https://test.example/payments/sandbox/checkout#token", status=200):
    return {"StatusCode": 200, "Payload": io.BytesIO(json.dumps({
        "statusCode": status, "body": json.dumps({"url": url}),
    }).encode())}


def test_bot_invokes_only_sandbox_lambda_without_changing_live_environment(monkeypatch):
    monkeypatch.setenv("PAYPAL_ENVIRONMENT", "live")
    monkeypatch.setenv("PAYMENTS_ENABLED", "false")
    with patch("payments.boto3.client") as client, patch("payments._table") as table:
        client.return_value.invoke.return_value = sandbox_response()
        link = payments.create_sandbox_checkout("alice", "10")
    table.assert_not_called()
    assert "/payments/sandbox/checkout#" in link
    args = client.return_value.invoke.call_args.kwargs
    assert args["FunctionName"] == "ai-slop-payments-sandbox"
    assert args["InvocationType"] == "RequestResponse"
    assert json.loads(args["Payload"]) == {"action": "create_test_checkout", "user": "alice", "amount": "10.00"}
    assert os.environ["PAYPAL_ENVIRONMENT"] == "live"
    assert os.environ["PAYMENTS_ENABLED"] == "false"


def test_test_checkout_reports_missing_sandbox_without_falling_back():
    with patch("payments.boto3.client") as client, patch("payments.create_checkout") as create:
        client.return_value.invoke.side_effect = ClientError({"Error": {"Code": "ResourceNotFoundException"}}, "Invoke")
        with pytest.raises(ValueError, match="not deployed"):
            payments.create_sandbox_checkout("alice", 10)
    create.assert_not_called()


@pytest.mark.parametrize("response", [
    {"url": "https://test.example/payments/live/checkout#token"},
    {"url": "http://test.example/payments/sandbox/checkout#token"},
    {"url": "https://test.example/payments/sandbox/checkout"},
    {"status": 400},
])
def test_test_checkout_rejects_non_sandbox_or_failed_responses(response):
    with patch("payments.boto3.client") as client, pytest.raises(ValueError):
        client.return_value.invoke.return_value = sandbox_response(**response)
        payments.create_sandbox_checkout("alice", 10)


def test_live_purchase_rejects_sandbox_configuration():
    with patch("payments.create_checkout") as create, pytest.raises(ValueError, match="Live PayPal"):
        payments.create_live_checkout("alice", 10)
    create.assert_not_called()


@pytest.mark.parametrize("enabled", ["false", "true"])
def test_payment_reminders_match_current_live_flow(monkeypatch, enabled):
    monkeypatch.setenv("PAYMENTS_ENABLED", enabled)
    reminder = budget.get_payment_required_message(-10)
    prompt = prompts.get_payment_prompt("text")
    if enabled == "true":
        assert "payment is confirmed" in reminder and "payment is confirmed" in prompt
    else:
        assert "Venmo link" in reminder and "Venmo link" in prompt
        assert "confirmed" not in reminder and "confirmed" not in prompt
    assert "-pay-test" not in reminder and "-pay-test" not in prompt


def test_token_lookup_checks_environment(purchase):
    purchase["environment"] = "live"
    with patch("payments._table") as table, pytest.raises(ValueError, match="different"):
        table.return_value.get_item.return_value = {"Item": purchase}
        payments.from_token("a" * 43)


def test_order_creation_expires_before_idempotency_window(purchase):
    del purchase["order_id"]
    purchase["expires_at"] = 1
    api = MagicMock()
    with pytest.raises(ValueError, match="expired"):
        payments.create_order(purchase, api)
    api.create_order.assert_not_called()


def test_retry_reuses_order(purchase):
    api = MagicMock()
    assert payments.create_order(purchase, api) == "ORDER123"
    api.create_order.assert_not_called()


def test_existing_order_cannot_start_new_checkout_after_expiry(purchase):
    purchase["expires_at"] = 1
    with pytest.raises(ValueError, match="expired"):
        payments.create_order(purchase, MagicMock())


def test_create_order_records_provider_id(purchase):
    del purchase["order_id"]
    api = MagicMock()
    api.create_order.return_value = {"id": "ORDER456"}
    with patch("payments._table") as table:
        assert payments.create_order(purchase, api) == "ORDER456"
    assert table.return_value.update_item.call_args.kwargs["ExpressionAttributeValues"] == {":order": "ORDER456"}


@pytest.mark.parametrize("order_status,capture_status", [
    ("CREATED", "COMPLETED"), ("PAYER_ACTION_REQUIRED", "COMPLETED"),
    ("VOIDED", "COMPLETED"), ("COMPLETED", "PENDING"), ("COMPLETED", "DECLINED"),
    ("COMPLETED", "REFUNDED"),
])
def test_unpaid_or_pending_orders_never_credit(purchase, order_status, capture_status):
    api = MagicMock()
    order = completed_order(purchase)
    order["status"] = order_status
    order["purchase_units"][0]["payments"]["captures"][0]["status"] = capture_status
    api.get_order.return_value = order
    with patch("payments.credit_capture") as credit:
        assert payments.settle(purchase, api)["status"] == "pending"
    credit.assert_not_called()
    api.capture_order.assert_not_called()


@pytest.mark.parametrize("field,value", [
    ("order_id", "OTHER"), ("intent", "AUTHORIZE"), ("custom_id", "other"),
    ("order_amount", "9.99"), ("order_currency", "EUR"),
    ("capture_amount", "9.99"), ("capture_currency", "EUR"), ("final_capture", False),
    ("multiple_captures", True),
])
def test_mismatched_payments_never_credit(purchase, field, value):
    api = MagicMock()
    order = completed_order(purchase)
    unit = order["purchase_units"][0]
    capture = unit["payments"]["captures"][0]
    if field == "order_id":
        order["id"] = value
    elif field == "intent":
        order["intent"] = value
    elif field == "custom_id":
        unit["custom_id"] = value
    elif field.startswith("order_"):
        unit["amount"]["value" if field.endswith("amount") else "currency_code"] = value
    elif field.startswith("capture_"):
        capture["amount"]["value" if field.endswith("amount") else "currency_code"] = value
    elif field == "final_capture":
        capture[field] = value
    else:
        unit["payments"]["captures"].append(copy.deepcopy(capture))
    api.get_order.return_value = order
    with patch("payments.credit_capture") as credit, pytest.raises(ValueError):
        payments.settle(purchase, api)
    credit.assert_not_called()


def test_completed_payment_credits_and_does_not_capture_twice(purchase):
    api = MagicMock()
    api.get_order.return_value = completed_order(purchase)
    with patch("payments.credit_capture") as credit:
        assert payments.settle(purchase, api)["status"] == "credited"
    credit.assert_called_once_with(purchase, "CAPTURE123")
    api.capture_order.assert_not_called()


@pytest.mark.parametrize("lost_response", [False, True])
def test_approval_captures_then_checks_actual_completion(purchase, lost_response):
    api = MagicMock()
    completed = completed_order(purchase)
    api.get_order.side_effect = [{**completed, "status": "APPROVED"}, completed]
    if lost_response:
        api.capture_order.side_effect = PayPalError("Response lost")
    with patch("payments.credit_capture") as credit:
        assert payments.settle(purchase, api)["status"] == "credited"
    api.capture_order.assert_called_once_with("ORDER123", purchase["id"])
    credit.assert_called_once()


def test_failed_capture_does_not_credit(purchase):
    api = MagicMock()
    api.get_order.return_value = {**completed_order(purchase), "status": "APPROVED"}
    api.capture_order.side_effect = PayPalError("Declined")
    with patch("payments.credit_capture") as credit, pytest.raises(PayPalError):
        payments.settle(purchase, api)
    credit.assert_not_called()


def test_atomic_credit_contains_purchase_and_capture_replay_guards(purchase):
    client = boto3.client("dynamodb")
    captured = []
    client.meta.events.register("before-parameter-build.dynamodb.TransactWriteItems",
                                lambda params, **_: captured.append(copy.deepcopy(params)))
    with Stubber(client) as stub, patch("payments.boto3.client", return_value=client):
        stub.add_response("transact_write_items", {})
        payments.credit_capture(purchase, "CAPTURE123")
        stub.assert_no_pending_responses()
    writes = captured[0]["TransactItems"]
    assert len(writes) == 3
    assert "#s = :pending" in writes[0]["Update"]["ConditionExpression"]
    assert "environment = :env" in writes[0]["Update"]["ConditionExpression"]
    assert writes[1]["Put"]["Item"]["id"] == {"S": "capture#CAPTURE123"}
    assert writes[1]["Put"]["ConditionExpression"] == "attribute_not_exists(id)"
    ledger = writes[2]["Put"]
    assert ledger["TableName"] == "ledger-sandbox"
    deserialize = TypeDeserializer().deserialize
    item = {key: deserialize(value) for key, value in ledger["Item"].items()}
    assert item["user"] == "alice" and item["amount"] == Decimal("10.00")
    assert item["paypal_capture_id"] == "CAPTURE123"
    assert item["timestamp"].endswith("#paypal#CAPTURE123")


@pytest.mark.parametrize("same_capture", [True, False])
def test_duplicate_transaction_is_success_only_if_already_credited(purchase, same_capture):
    client = boto3.client("dynamodb")
    current = {**purchase, "status": "credited", "capture_id": "CAPTURE123" if same_capture else "OTHER"}
    with Stubber(client) as stub, patch("payments.boto3.client", return_value=client), \
            patch("payments.get_purchase", return_value=current):
        stub.add_client_error("transact_write_items", "TransactionCanceledException")
        if same_capture:
            payments.credit_capture(purchase, "CAPTURE123")
        else:
            with pytest.raises(ClientError):
                payments.credit_capture(purchase, "CAPTURE123")
        stub.assert_no_pending_responses()


def test_transient_database_failure_is_not_reported_as_success(purchase):
    client = boto3.client("dynamodb")
    with Stubber(client) as stub, patch("payments.boto3.client", return_value=client):
        stub.add_client_error("transact_write_items", "ProvisionedThroughputExceededException")
        with pytest.raises(ClientError):
            payments.credit_capture(purchase, "CAPTURE123")


def test_sandbox_cannot_write_live_ledger(purchase, monkeypatch):
    monkeypatch.setenv("PAYMENTS_LEDGER_TABLE_NAME", "ai-slop-ledger")
    with patch("payments.boto3.client") as client, pytest.raises(ValueError, match="sandbox ledger"):
        payments.credit_capture(purchase, "CAPTURE123")
    client.assert_not_called()


def test_credited_purchase_retry_never_calls_paypal(purchase):
    purchase["status"] = "credited"
    api = MagicMock()
    assert payments.settle(purchase, api)["status"] == "credited"
    assert api.mock_calls == []


def test_invalid_webhook_signature_never_reaches_ledger():
    with patch("payment_handler.PayPal") as api, patch("payment_handler.payments.webhook") as webhook:
        api.return_value.verify_webhook.return_value = False
        result = payment_handler.handler(http_event("webhook", {"event_type": "PAYMENT.CAPTURE.COMPLETED"}), None)
    assert result["statusCode"] == 401
    webhook.assert_not_called()


def test_webhook_base64_preserves_original_body():
    raw = '{ "event_type": "PAYMENT.CAPTURE.COMPLETED", "resource": {} }'
    event = http_event("webhook")
    event.update(body=base64.b64encode(raw.encode()).decode(), isBase64Encoded=True,
                 headers={"PayPal-Transmission-Id": "transmission"})
    with patch("payment_handler.PayPal") as api, patch("payments.webhook"):
        api.return_value.verify_webhook.return_value = True
        result = payment_handler.handler(event, None)
    assert result["statusCode"] == 200
    api.return_value.verify_webhook.assert_called_once_with({"paypal-transmission-id": "transmission"}, raw)


def test_database_error_returns_retryable_webhook_response():
    with patch("payment_handler.PayPal") as api, patch("payments.webhook", side_effect=RuntimeError("database")):
        api.return_value.verify_webhook.return_value = True
        result = payment_handler.handler(http_event("webhook"), None)
    assert result["statusCode"] == 503


def test_browser_cannot_choose_credit_recipient_amount_or_order(purchase):
    with patch("payments.from_token", return_value=purchase) as lookup, \
            patch("payments.settle", return_value={"status": "pending"}) as settle, \
            patch("payment_handler.PayPal"):
        result = payment_handler.handler(http_event("capture", {
            "token": "checkout-token", "user": "attacker", "amount": 500, "order_id": "FOREIGN",
        }), None)
    assert result["statusCode"] == 200
    lookup.assert_called_once_with("checkout-token")
    assert settle.call_args.args[0] == purchase


def test_public_api_cannot_mint_checkout_links():
    with patch("payments.create_checkout") as create:
        result = payment_handler.handler(http_event("create_test_checkout", {
            "action": "create_test_checkout", "user": "alice", "amount": "10",
        }), None)
    assert result["statusCode"] == 404
    create.assert_not_called()


def test_direct_live_invocation_cannot_mint_checkout(monkeypatch):
    monkeypatch.setenv("PAYPAL_ENVIRONMENT", "live")
    with patch("payments.create_checkout") as create:
        result = payment_handler.handler({"action": "create_test_checkout", "user": "alice", "amount": 10}, None)
    assert result["statusCode"] == 403
    create.assert_not_called()


def test_direct_sandbox_invocation_creates_test_link():
    with patch("payments.create_checkout", return_value="https://test/checkout#token") as create:
        result = payment_handler.handler({"action": "create_test_checkout", "user": "alice", "amount": 10}, None)
    assert result["statusCode"] == 200
    create.assert_called_once_with("alice", 10)


@pytest.mark.parametrize("event_type", ["CHECKOUT.ORDER.APPROVED", "PAYMENT.CAPTURE.COMPLETED"])
def test_verified_webhook_reconciles_server_order(purchase, event_type):
    api = MagicMock()
    api.get_order.return_value = completed_order(purchase)
    resource = {"id": "ORDER123", "supplementary_data": {"related_ids": {"order_id": "ORDER123"}}}
    with patch("payments.get_purchase", return_value=purchase), patch("payments.settle") as settle:
        payments.webhook({"event_type": event_type, "resource": resource}, api)
    settle.assert_called_once_with(purchase, api)


def test_unrelated_webhook_never_credits():
    with patch("payments.settle") as settle:
        payments.webhook({"event_type": "PAYMENT.CAPTURE.PENDING"}, MagicMock())
    settle.assert_not_called()


def test_webhook_recovers_order_after_database_write_failure(purchase):
    api = MagicMock()
    api.get_order.return_value = completed_order(purchase)
    del purchase["order_id"]
    with patch("payments.get_purchase", return_value=purchase), \
            patch("payments._table") as table, patch("payments.settle") as settle:
        payments.webhook({"event_type": "CHECKOUT.ORDER.APPROVED", "resource": {"id": "ORDER123"}}, api)
    assert settle.call_args.args[0]["order_id"] == "ORDER123"
    assert "attribute_not_exists(order_id)" in table.return_value.update_item.call_args.kwargs["ConditionExpression"]


def test_webhook_verification_posts_raw_json_and_server_webhook_id():
    api = PayPal()
    raw = '{ "resource": {"x": 1.00} }'
    headers = {"paypal-" + key: key for key in (
        "auth-algo", "cert-url", "transmission-id", "transmission-sig", "transmission-time")}
    with patch.object(api, "request", return_value={"verification_status": "SUCCESS"}) as request:
        assert api.verify_webhook(headers, raw)
    body = request.call_args.kwargs["raw"]
    assert body.endswith(raw + "}")
    assert json.loads(body)["webhook_id"] == "webhook-test"


def test_missing_webhook_headers_fail_closed():
    api = PayPal()
    with patch.object(api, "request") as request:
        assert not api.verify_webhook({}, "{}")
    request.assert_not_called()


def test_capture_timeout_can_be_reconciled_as_a_paypal_error():
    api = PayPal()
    with patch.object(api, "_access_token", return_value="test-token"), \
            patch("paypal_api.requests.request", side_effect=requests.Timeout), pytest.raises(PayPalError):
        api.capture_order("ORDER123", "a" * 64)


def test_api_uses_sandbox_and_stable_idempotency_ids(purchase):
    api = PayPal()
    assert api.host == "https://api-m.sandbox.paypal.com"
    with patch.object(api, "_access_token", return_value="test-token"), \
            patch("paypal_api.requests.request") as request:
        request.return_value.ok = True
        request.return_value.json.return_value = {"id": "ORDER123"}
        api.create_order(purchase)
        api.create_order(purchase)
    calls = request.call_args_list
    assert calls[0].kwargs["headers"]["PayPal-Request-Id"] == calls[1].kwargs["headers"]["PayPal-Request-Id"]
    assert len(calls[0].kwargs["headers"]["PayPal-Request-Id"]) <= 38
    assert calls[0].args[1].startswith("https://api-m.sandbox.paypal.com/")
    assert json.loads(calls[0].kwargs["data"])["purchase_units"][0]["amount"]["value"] == "10.00"


def test_checkout_html_contains_only_public_configuration():
    event = {"requestContext": {"http": {"method": "GET"}}, "rawPath": "/payments/sandbox/checkout"}
    response = payment_handler.handler(event, None)
    assert response["statusCode"] == 200
    assert "test-client" in response["body"] and "test-secret" not in response["body"]
    assert response["headers"]["Cache-Control"] == "no-store"


@pytest.mark.parametrize("order_id", ["../oauth2/token", "https://attacker.com/", "id?query=yes"])
def test_paypal_order_ids_cannot_change_api_path(order_id):
    with pytest.raises(ValueError):
        PayPal().get_order(order_id)
