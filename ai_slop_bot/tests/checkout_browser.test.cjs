const {before, after, test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('playwright');

let browser;
before(async () => { browser = await chromium.launch({headless: true}); });
after(async () => { if (browser) await browser.close(); });

// Match PayPal's v6 namespace contract: an existing window.paypal causes v6
// to be attached as window.paypal.v6. Use a real browser so HTML element IDs
// participate in window's named properties, which a plain JS mock misses.
const sdkScript = `
  for (const name of ['paypal', 'venmo']) {
    customElements.define(name + '-button', class extends HTMLElement {
      connectedCallback() { this.textContent = name; }
    });
  }
  const callbacks = {};
  const session = method => options => {
    callbacks[method] = options;
    window.checkoutCallbacks = callbacks;
    return {async start(settings, orderPromise) {
      window.startedOrder = await orderPromise;
    }};
  };
  const sdk = {createInstance: async () => {
    window.sdkInitialized = true;
    return {
      findEligibleMethods: async () => ({isEligible: () => true}),
      createPayPalOneTimePaymentSession: session('paypal'),
      createVenmoOneTimePaymentSession: session('venmo'),
    };
  }};
  if (window.paypal) window.paypal.v6 = sdk;
  else window.paypal = sdk;
`;

async function checkout(t, {legacySdk = false, brokenSdk = false} = {}) {
  const context = await browser.newContext();
  t.after(() => context.close());
  const page = await context.newPage();
  page.setDefaultTimeout(5000);
  const calls = [];
  const state = {capture: 'pending'};
  if (legacySdk) await page.addInitScript(() => { window.paypal = {version: '5'}; });
  let html = fs.readFileSync(path.join(__dirname, '..', 'checkout.html'), 'utf8');
  html = html.replace('__PAYMENT_CONFIG__', JSON.stringify({
    clientId: 'test-client', environment: 'sandbox', basePath: '/payments/sandbox',
  }));
  await page.route('https://checkout.example/**', async route => {
    const action = new URL(route.request().url()).pathname.split('/').pop();
    if (action === 'checkout') return route.fulfill({contentType: 'text/html', body: html});
    calls.push({action, body: route.request().postDataJSON()});
    return route.fulfill({json: action === 'order' ? {orderId: 'ORDER123'} : {
      amount: '10.00', status: action === 'capture' ? state.capture : 'pending', environment: 'sandbox',
    }});
  });
  await page.route('https://www.sandbox.paypal.com/web-sdk/v6/core', route => route.fulfill({
    contentType: 'application/javascript', body: brokenSdk ? 'window.paypal = {};' : sdkScript,
  }));
  await page.goto('https://checkout.example/payments/sandbox/checkout#test-token');
  await page.waitForFunction(() => window.sdkInitialized || document.getElementById('status').textContent);
  return {page, calls, state};
}

test('payment buttons render with browser named properties enabled', async t => {
  const {page} = await checkout(t);
  assert.equal(await page.locator('#status').textContent(), '');
  assert.equal(await page.locator('paypal-button').isVisible(), true);
  assert.equal(await page.locator('venmo-button').isVisible(), true);
  assert.equal(await page.evaluate(() => window.paypal instanceof Element), false);
});

test('v6 initializes when another PayPal SDK already owns window.paypal', async t => {
  const {page} = await checkout(t, {legacySdk: true});
  assert.equal(await page.locator('#status').textContent(), '');
  assert.equal(await page.locator('paypal-button').isVisible(), true);
  assert.equal(await page.evaluate(() => window.paypal.version), '5');
});

test('cancellation, pending capture and completed capture show the correct state', async t => {
  const {page, calls, state} = await checkout(t);
  assert.equal(await page.locator('#status').textContent(), '');
  await page.locator('paypal-button').click();
  await page.waitForFunction(() => window.startedOrder);
  assert.deepEqual(await page.evaluate(() => window.startedOrder), {orderId: 'ORDER123'});
  await page.evaluate(() => window.checkoutCallbacks.paypal.onCancel());
  assert.match(await page.locator('#status').textContent(), /cancelled/);
  assert.equal(calls.filter(call => call.action === 'capture').length, 0);
  await page.evaluate(() => window.checkoutCallbacks.paypal.onApprove({orderId: 'UNTRUSTED'}));
  assert.match(await page.locator('#status').textContent(), /not confirmed/);
  state.capture = 'credited';
  await page.locator('#check').click();
  await page.waitForFunction(() => document.getElementById('status').textContent.includes('confirmed.'));
  assert.match(await page.locator('#status').textContent(), /sandbox ledger only/);
  assert.equal(await page.locator('paypal-button').isVisible(), false);
  assert.ok(calls.every(call => JSON.stringify(call.body) === JSON.stringify({token: 'test-token'})));
});

test('unexpected SDK response shows a useful retry message', async t => {
  const {page} = await checkout(t, {brokenSdk: true});
  assert.match(await page.locator('#status').textContent(), /PayPal could not initialize/);
  assert.doesNotMatch(await page.locator('#status').textContent(), /is not a function/);
});
