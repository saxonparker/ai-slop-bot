# PayPal and Venmo credits

Start with Sandbox. A Live app is not needed for sandbox testing. PayPal's
[sandbox guide](https://developer.paypal.com/sandbox-testing/overview/)
explains the test environment; use a **Personal sandbox buyer account** to buy
from the **Business sandbox account** associated with your Merchant app.

The existing `/slop-bot -pay <amount>` keeps its immediate credit and Venmo link
until `PAYPAL_LIVE_ENABLED` is explicitly set to `true` and the bot is redeployed.
Sandbox testing uses a separate flag: **`/slop-bot -pay-test 10`**. The test flag
always invokes the sandbox Lambda, even after live PayPal is enabled. Combining
`-pay` and `-pay-test` in one command is rejected before either can run.

For the new checkout flow, the application creates pending purchases, creates/captures orders on the
server, and credits only a completed capture matching the stored USD amount
and purchase. The browser cannot supply a recipient, amount, or order to credit.
A verified `CHECKOUT.ORDER.APPROVED` webhook can finish a purchase if the buyer
closes the page after approval. `PAYMENT.CAPTURE.COMPLETED` also reconciles it.
Approval alone, cancellation, pending captures, and failed captures earn no credit.

## Sandbox setup

1. Save GitHub Actions repository secrets `PAYPAL_SANDBOX_CLIENT_ID` and
   `PAYPAL_SANDBOX_CLIENT_SECRET`. These must belong to the same Sandbox
   Merchant app. Do not use real account credentials in a sandbox checkout.
2. Once this code is on `main`, run **Actions → Test PayPal Sandbox → Run workflow**.
   Keep the default test user `sandbox-tester` and amount `10.00` initially.
   The workflow authenticates with PayPal and deploys a separate test API,
   payment table, ledger, Lambda, IAM role, and Terraform state. It does not
   deploy the Slack bot or touch its production ledger.
3. Copy the webhook URL from the workflow's summary. In **PayPal Developer →
   Apps & Credentials → Sandbox → your app → Webhooks**, add that URL and select:
   - `CHECKOUT.ORDER.APPROVED`
   - `PAYMENT.CAPTURE.COMPLETED`
4. Save the resulting webhook ID as GitHub secret `PAYPAL_SANDBOX_WEBHOOK_ID`.
   This is the listener's ID, not the app's client ID or an individual event ID.
5. Run **Test PayPal Sandbox** again to enable its checkout. Once the bot update
   has also deployed, run **`/slop-bot -pay-test 10`** in Slack to get a test link
   for your user. The workflow summary also contains a test link for its selected
   `test_user`. Links expire after one hour; run the test command again for a new one.
6. Select PayPal and sign in with the Personal sandbox buyer credentials from
   **Testing Tools → Sandbox Accounts**. The page must say **Sandbox test**.

All test credits go to `ai-slop-ledger-sandbox`. The sandbox Lambda has no
permission to write the real `ai-slop-ledger`; `/slop-bot -u` never includes
sandbox credits. The bot receives permission to invoke only the sandbox checkout
Lambda for this flag; it does not switch its own payment environment or ledger.
The existing `-pay` flow stays active throughout sandbox setup and testing.

The workflow needs the repository's existing AWS deployment secrets and IAM
permissions to manage the sandbox resources and invoke its Lambda. Its secrets
are passed to Terraform as sensitive values. As with the existing provider keys,
the Terraform state contains these credentials; keep the state bucket private.

## Acceptance checks before live

Use a new test checkout for each independent case:

| Test | Expected result |
| --- | --- |
| Open a $10 checkout without paying | No ledger credit |
| Cancel checkout | No ledger credit |
| Complete payment | Exactly one $10 sandbox ledger entry |
| Reload the completed checkout or click status repeatedly | Still exactly one entry |
| Resend that real app event from PayPal's webhook event history | Still exactly one entry |
| Close the checkout after approval | Verified webhook completes capture/credit |
| Decline or leave a capture pending | No credit until a completed capture is confirmed |
| Inspect the real bot balance | Unchanged by every sandbox test |
| Use regular `-pay` while the live PayPal switch is off | Existing immediate credit and Venmo link |
| Run `-pay-test` before Sandbox is ready | Setup message; no real credit; regular `-pay` still works |

In AWS's DynamoDB console, query `ai-slop-ledger-sandbox` by `user` to inspect
the credit, its `paypal_capture_id`, and its `paypal_order_id`. Each completed
purchase has one corresponding entry in `ai-slop-payments-sandbox` with status
`credited`. Preserve payment and capture records: they prevent duplicate credits.

Use events from actual sandbox checkouts. PayPal's standalone webhook simulator
does not support the postback verification endpoint used here; simulated events
are intentionally rejected. See [PayPal's webhook verification guide](https://developer.paypal.com/api/rest/webhooks/rest/).

Venmo appears when PayPal reports it is eligible for the customer/device.
Its sandbox behavior differs from live checkout: desktop QR checkout is not
available in Sandbox, and settlement/disbursement are not simulated. PayPal's
[Venmo testing guide](https://developer.paypal.com/venmo/test) describes supported
flows and decline amounts, including `$12.34` for insufficient funds. Validate
the PayPal flow first, then Venmo on desktop/mobile where offered.

## Going live after the sandbox checks

1. Create a **Live → Merchant** app linked to your PayPal Business account.
2. Save repository secrets `PAYPAL_CLIENT_ID` and `PAYPAL_CLIENT_SECRET`.
3. Save `SLACK_SIGNING_SECRET` from the Slack app's **Basic Information → App
   Credentials → Signing Secret**. This authenticates requests before trusting
   credit recipients or admin commands. The bot token is a different secret.
4. Deploy with the GitHub repository variable `PAYPAL_LIVE_ENABLED` absent or
   set to `false`. The normal deployment provisions the live payment endpoint
   while keeping `-pay` on its existing Venmo flow. The Terraform output `paypal_webhook_url`
   is the listener URL.
5. Register that URL in the **Live** PayPal app for the same two webhook events.
   Save its ID as `PAYPAL_WEBHOOK_ID`.
6. Set repository **variable** `PAYPAL_LIVE_ENABLED=true` and deploy again.
   Terraform rejects enabling live purchases without the PayPal credentials,
   webhook ID, and Slack signing secret.
7. Make one small real purchase from a separate buyer account. Confirm the
   actual captured payment in PayPal and the matching bot credit before
   announcing general availability. Sandbox success cannot verify real
   settlement or every live Venmo flow.

Step 6 is the explicit cutover: `-pay` begins requiring verified payment before
adding credits. Before it, `-pay` retains the existing Venmo honor-system behavior.
Sandbox app credentials and webhook registration cannot trigger this switch.
Existing credits, generation limits, and admin adjustments still work.
Admin reports and credit adjustments must use the slash command; a mention's
editable display name cannot authorize an admin operation.
Each dollar captured purchases one dollar of bot credit; PayPal processing fees
reduce the merchant's proceeds, not the credited amount.

Refunds, reversals, and disputes currently require a manual negative admin
adjustment after reconciliation with PayPal. They are not automatically deducted
from a user's bot balance by this initial checkout integration.

## Local checks

From `ai_slop_bot`, run `.venv/bin/pytest tests/ -q`. Payment tests mock PayPal
and use botocore's DynamoDB request validation; they never move real money.
From the repository root, run `npm ci`, `npx playwright install --with-deps chromium`,
then `npm run test:checkout` for browser regression tests. These mock the PayPal
SDK and payment endpoints while exercising real browser behavior, including
HTML element IDs that become global properties. Both deployment workflows run them.
The GitHub sandbox workflow and the buyer acceptance checks above are the
separate integration test with PayPal and AWS.
