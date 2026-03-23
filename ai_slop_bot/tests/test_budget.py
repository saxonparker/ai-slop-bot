"""Tests for budget tracking module."""

import sys
from decimal import Decimal
from unittest.mock import MagicMock, patch

sys.path.append(".")

import budget


# ── Venmo link generation ───────────────────────────────────────────────────

@patch.dict("os.environ", {"VENMO_USERNAME": "Saxon-Parker"})
def test_generate_venmo_link():
    link = budget.generate_venmo_link(5.00)
    assert "venmo.com/Saxon-Parker" in link
    assert "txn=pay" in link
    assert "amount=5.00" in link
    assert "note=AI%20Slop%20credits" in link


@patch.dict("os.environ", {"VENMO_USERNAME": "Test-User"})
def test_generate_venmo_link_custom_username():
    link = budget.generate_venmo_link(10.50, note="custom note")
    assert "venmo.com/Test-User" in link
    assert "amount=10.50" in link
    assert "note=custom%20note" in link


# ── Snarky messages ─────────────────────────────────────────────────────────

def test_snarky_message_positive_balance():
    assert budget._get_snarky_message(5.00) == ""


def test_snarky_message_zero_balance():
    assert budget._get_snarky_message(0.00) == ""


def test_snarky_message_slightly_negative():
    msg = budget._get_snarky_message(-0.50)
    assert "red" in msg


def test_snarky_message_moderately_negative():
    msg = budget._get_snarky_message(-2.00)
    assert "shameless" in msg


def test_snarky_message_very_negative():
    msg = budget._get_snarky_message(-7.00)
    assert "freeloader" in msg


def test_snarky_message_deeply_negative():
    msg = budget._get_snarky_message(-20.00)
    assert "deadbeat" in msg


# ── Balance calculation ─────────────────────────────────────────────────────

@patch("budget.usage.get_total_cost")
@patch("budget.boto3")
def test_get_balance_credits_minus_costs(mock_boto3, mock_total_cost):
    mock_table = MagicMock()
    mock_boto3.resource.return_value.Table.return_value = mock_table
    mock_table.query.return_value = {"Items": [
        {"amount": Decimal("10.00")},
        {"amount": Decimal("5.00")},
    ]}
    mock_total_cost.return_value = 3.50

    balance = budget.get_balance("testuser")
    assert balance == 11.50  # 15.00 - 3.50


@patch("budget.usage.get_total_cost")
@patch("budget.boto3")
def test_get_balance_no_credits(mock_boto3, mock_total_cost):
    mock_table = MagicMock()
    mock_boto3.resource.return_value.Table.return_value = mock_table
    mock_table.query.return_value = {"Items": []}
    mock_total_cost.return_value = 2.00

    balance = budget.get_balance("testuser")
    assert balance == -2.00


# ── add_credit ──────────────────────────────────────────────────────────────

@patch("budget.get_balance", return_value=15.00)
@patch("budget.boto3")
def test_add_credit_writes_item(mock_boto3, mock_balance):
    mock_table = MagicMock()
    mock_boto3.resource.return_value.Table.return_value = mock_table

    new_bal = budget.add_credit("testuser", 10.00, source_user="testuser", note="Venmo payment")

    mock_table.put_item.assert_called_once()
    item = mock_table.put_item.call_args.kwargs["Item"]
    assert item["user"] == "testuser"
    assert item["amount"] == Decimal("10.00")
    assert item["type"] == "payment"
    assert item["source_user"] == "testuser"
    assert new_bal == 15.00


@patch("budget.get_balance", return_value=5.00)
@patch("budget.boto3")
def test_add_credit_admin_adjustment(mock_boto3, mock_balance):
    mock_table = MagicMock()
    mock_boto3.resource.return_value.Table.return_value = mock_table

    budget.add_credit("targetuser", 5.00, source_user="saxon.parker", note="Admin adjustment")

    item = mock_table.put_item.call_args.kwargs["Item"]
    assert item["type"] == "adjustment"
    assert item["source_user"] == "saxon.parker"


# ── get_last_payment ────────────────────────────────────────────────────────

@patch("budget.boto3")
def test_get_last_payment_returns_most_recent(mock_boto3):
    mock_table = MagicMock()
    mock_boto3.resource.return_value.Table.return_value = mock_table
    mock_table.query.return_value = {"Items": [
        {"timestamp": "2026-03-23T14:00:00Z", "amount": Decimal("10.00")},
    ]}

    result = budget.get_last_payment("testuser")
    assert result == ("2026-03-23T14:00:00Z", 10.00)


@patch("budget.boto3")
def test_get_last_payment_no_records(mock_boto3):
    mock_table = MagicMock()
    mock_boto3.resource.return_value.Table.return_value = mock_table
    mock_table.query.return_value = {"Items": []}

    result = budget.get_last_payment("testuser")
    assert result is None


# ── get_balance_display ─────────────────────────────────────────────────────

@patch("budget.get_last_payment", return_value=("2026-03-20T10:00:00Z", 10.00))
@patch("budget.get_balance", return_value=7.50)
def test_balance_display_positive(mock_bal, mock_last):
    display = budget.get_balance_display("testuser")
    assert ":large_green_circle:" in display
    assert "$7.50" in display
    assert "$10.00" in display
    assert "2026-03-20" in display


@patch("budget.get_last_payment", return_value=None)
@patch("budget.get_balance", return_value=-3.00)
def test_balance_display_negative_no_payments(mock_bal, mock_last):
    display = budget.get_balance_display("testuser")
    assert ":red_circle:" in display
    assert "$-3.00" in display
    assert "Last payment" not in display
    assert "_" in display  # snarky message in italics
