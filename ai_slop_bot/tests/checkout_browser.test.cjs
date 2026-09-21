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
      findEligibleMethods: async () => {
        window.eligibilityChecked = true;
        return {isEligible: () => true};
      },
      createPayPalOneTimePaymentSession: session('paypal'),
      createVenmoOneTimePaymentSession: session('venmo'),
    };
  }};
  if (window.paypal) window.paypal.v6 = sdk;
  else window.paypal = sdk;
`;

function gate(t) {
  let release;
  const promise = new Promise(resolve => { release = resolve; });
  t.after(() => release());
  return {promise, release};
}

async function waitForCheckout(page) {
  await page.waitForFunction(() => !document.getElementById('buttons').hidden || (
    document.getElementById('status').textContent &&
    document.getElementById('status').textContent !== 'Loading payment options...'
  ));
}

async function checkout(t, {
  legacySdk = false, brokenSdk = false, statusGate, sdkGate,
  initialStatus = 'pending', statusError, waitUntilReady = true,
} = {}) {
  const context = await browser.newContext();
  t.after(() => context.close());
  const page = await context.newPage();
  page.setDefaultTimeout(5000);
  const pageErrors = [];
  page.on('pageerror', error => pageErrors.push(error.message));
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
    if (action === 'status') {
      if (statusGate) await statusGate.promise;
      if (statusError) return route.fulfill({status: 400, json: {error: statusError}});
    }
    return route.fulfill({json: action === 'order' ? {orderId: 'ORDER123'} : {
      amount: '10.00', status: action === 'capture' ? state.capture : initialStatus, environment: 'sandbox',
    }});
  });
  await page.route('https://www.sandbox.paypal.com/web-sdk/v6/core', async route => {
    if (sdkGate) await sdkGate.promise;
    return route.fulfill({contentType: 'application/javascript',
      body: brokenSdk ? 'window.paypal = {};' : sdkScript});
  });
  await page.goto('https://checkout.example/payments/sandbox/checkout#test-token', {waitUntil: 'domcontentloaded'});
  if (waitUntilReady) await waitForCheckout(page);
  return {page, calls, state, pageErrors};
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

test('SDK and eligibility load while checkout validation is pending, with buttons hidden', async t => {
  const statusGate = gate(t);
  const {page, calls} = await checkout(t, {statusGate, waitUntilReady: false});
  await page.waitForFunction(() => window.eligibilityChecked);
  assert.equal(await page.locator('#status').textContent(), 'Loading payment options...');
  assert.equal(await page.locator('paypal-button').isVisible(), false);
  assert.equal(await page.locator('venmo-button').isVisible(), false);
  assert.deepEqual(calls.map(call => call.action), ['status']);
  statusGate.release();
  await waitForCheckout(page);
  assert.equal(await page.locator('#status').textContent(), '');
  assert.equal(await page.locator('paypal-button').isVisible(), true);
});

test('amount appears while the SDK is still loading', async t => {
  const sdkGate = gate(t);
  const {page} = await checkout(t, {sdkGate, waitUntilReady: false});
  await page.waitForFunction(() => document.getElementById('amount').textContent.includes('$10.00'));
  assert.equal(await page.locator('#status').textContent(), 'Loading payment options...');
  assert.equal(await page.locator('paypal-button').isVisible(), false);
  sdkGate.release();
  await waitForCheckout(page);
  assert.equal(await page.locator('#status').textContent(), '');
  assert.equal(await page.locator('paypal-button').isVisible(), true);
});

for (const [name, options, expected] of [
  ['paid checkout', {initialStatus: 'credited'}, /sandbox ledger only/],
  ['invalid checkout', {statusError: 'Checkout not found.'}, /Checkout not found/],
]) {
  test(`${name} does not wait for PayPal or show buttons when it eventually loads`, async t => {
    const sdkGate = gate(t);
    const {page, calls} = await checkout(t, {...options, sdkGate});
    assert.match(await page.locator('#status').textContent(), expected);
    sdkGate.release();
    await page.waitForFunction(() => window.eligibilityChecked);
    assert.match(await page.locator('#status').textContent(), expected);
    assert.equal(await page.locator('paypal-button').isVisible(), false);
    assert.equal(await page.locator('venmo-button').isVisible(), false);
    assert.deepEqual(calls.map(call => call.action), ['status']);
  });

  test(`${name} handles an SDK failure before validation without losing its result`, async t => {
    const statusGate = gate(t);
    const {page, calls, pageErrors} = await checkout(t, {
      ...options, statusGate, brokenSdk: true, waitUntilReady: false,
    });
    await page.waitForFunction(() => window.paypal && !window.paypal.createInstance);
    // Let the script's onload handler and rejection handling run before validation completes.
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => resolve())));
    assert.equal(await page.locator('#status').textContent(), 'Loading payment options...');
    statusGate.release();
    await waitForCheckout(page);
    assert.match(await page.locator('#status').textContent(), expected);
    assert.equal(await page.locator('paypal-button').isVisible(), false);
    assert.deepEqual(pageErrors, []);
    assert.deepEqual(calls.map(call => call.action), ['status']);
  });
}
