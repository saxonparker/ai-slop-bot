"""Budget tracking: credit ledger, balance calculation, and Venmo link generation."""

import os
import urllib.parse
from datetime import datetime, timezone
from decimal import Decimal

import boto3

import usage

ADMIN_USERS = set(os.environ.get("ADMIN_USERS", "saxon").split(","))

SNARKY_MESSAGES = [
    (-1.00, "you're in the red. not a great look."),
    (-3.00, "you owe money and you're still here asking for more. shameless."),
    (-5.00, "bro you can't even afford a coffee and you're out here generating AI art. venmo saxon."),
    (-10.00, "you are genuinely robbing this man blind. pay your tab you absolute freeloader."),
    (-float("inf"), "at this point just send saxon your whole paycheck. you owe more than some people's rent. deadbeat behavior."),
]


def _get_ledger_table():
    """Get the ledger DynamoDB table resource."""
    table_name = os.environ.get("LEDGER_TABLE_NAME", "ai-slop-ledger")
    return boto3.resource("dynamodb").Table(table_name)


def add_credit(user: str, amount: float, source_user: str, note: str = "") -> float:
    """Record a credit in the ledger. Returns the new balance."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    item = {
        "user": user,
        "timestamp": now,
        "amount": Decimal(str(round(amount, 2))),
        "type": "payment" if source_user == user else "adjustment",
        "note": note,
        "source_user": source_user,
    }
    _get_ledger_table().put_item(Item=item)
    return get_balance(user)


def _get_total_credits(user: str) -> float:
    """Sum all ledger amounts for a user."""
    try:
        table = _get_ledger_table()
        response = table.query(
            KeyConditionExpression="#u = :user",
            ExpressionAttributeNames={"#u": "user"},
            ExpressionAttributeValues={":user": user},
            ProjectionExpression="amount",
        )
        return sum(float(r.get("amount", 0)) for r in response.get("Items", []))
    except Exception as exc:  # pylint: disable=broad-except
        print(f"LEDGER QUERY ERROR: {exc}")
        return 0.0


def get_balance(user: str) -> float:
    """Calculate balance: total credits - total usage costs."""
    credits = _get_total_credits(user)
    costs = usage.get_total_cost(user)
    return round(credits - costs, 2)


def get_last_payment(user: str):
    """Return (timestamp_str, amount) for the most recent payment, or None."""
    try:
        table = _get_ledger_table()
        response = table.query(
            KeyConditionExpression="#u = :user",
            ExpressionAttributeNames={"#u": "user"},
            ExpressionAttributeValues={":user": user},
            ScanIndexForward=False,
            Limit=1,
        )
        items = response.get("Items", [])
        if items:
            return (items[0]["timestamp"], float(items[0]["amount"]))
        return None
    except Exception as exc:  # pylint: disable=broad-except
        print(f"LEDGER QUERY ERROR: {exc}")
        return None


def _get_snarky_message(balance: float) -> str:
    """Return a snarky comment based on how negative the balance is."""
    if balance >= 0:
        return ""
    for threshold, message in SNARKY_MESSAGES:
        if balance >= threshold:
            return message
    return SNARKY_MESSAGES[-1][1]


def get_balance_display(user: str) -> str:
    """Return formatted Slack mrkdwn showing balance and last payment."""
    balance = get_balance(user)
    last = get_last_payment(user)

    icon = ":large_green_circle:" if balance >= 0 else ":red_circle:"
    parts = [f"{icon} *Balance:* ${balance:.2f}"]

    if last:
        ts, amount = last
        date_str = ts[:10]  # YYYY-MM-DD
        parts.append(f"*Last payment:* ${amount:.2f} on {date_str}")

    snark = _get_snarky_message(balance)
    if snark:
        parts.append(f"_{snark}_")

    return "\n".join(parts)


def generate_venmo_link(amount: float, note: str = "AI Slop credits") -> str:
    """Generate a Venmo payment deep link."""
    username = os.environ.get("VENMO_USERNAME", "Saxon-Parker")
    encoded_note = urllib.parse.quote(note)
    return f"https://venmo.com/{username}?txn=pay&amount={amount:.2f}&note={encoded_note}"
