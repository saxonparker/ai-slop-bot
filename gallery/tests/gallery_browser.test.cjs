const {before, after, test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('playwright');

let browser;
before(async () => { browser = await chromium.launch({headless: true}); });
after(async () => { if (browser) await browser.close(); });

const photo = 'dalle/a_"great"_cat_🏆_ABC.jpeg';
const video = 'dalle/surfing_dog_XYZ.mp4';
const other = 'dalle/a_sleepy_cat_DEF.jpeg';
const defaultKeys = [photo, video, other];
const cloudFrontOrigin = 'https://d2jagmvo7k5q5j.cloudfront.net';
const s3Origin = 'https://dallepics.s3.us-east-2.amazonaws.com';
// Use the deployed origin policy so a hosting URL cannot pass browser tests
// while Terraform prevents that same page from reading or saving selections.
const apiGateway = fs.readFileSync(path.join(__dirname, '..', '..', 'terraform', 'api_gateway.tf'), 'utf8');
const allowedOrigins = [...apiGateway.match(/allow_origins\s*=\s*\[([\s\S]*?)\]/)[1].matchAll(/"([^"]+)"/g)].map(match => match[1]);
const manifest = {
  [photo]: {user: 'alice', channel: 'cats', model: 'image-model'},
  [video]: {user: 'bob', channel: 'videos', model: 'video-model'},
  [other]: {user: 'bob', channel: 'cats', model: 'image-model'},
};

async function gallery(t, {keys = defaultKeys, featured = [], hash = '', mobile = false, failRead = false, origin = cloudFrontOrigin} = {}) {
  const context = await browser.newContext({viewport: mobile ? {width: 390, height: 844} : {width: 1280, height: 900}});
  t.after(() => context.close());
  const page = await context.newPage();
  page.setDefaultTimeout(5000);
  const state = {featured: new Set(featured), failRead, failWrite: false, writes: [], delayWrite: 0};
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  t.after(() => assert.deepEqual(errors, []));
  const sdk = `
    window.AWS = {
      config: {}, CognitoIdentityCredentials: function() {},
      S3: function() {
        this.listObjectsV2 = (params, callback) => callback(null, {
          Contents: ${JSON.stringify(keys.map((Key, index) => ({Key, Size: 1024, LastModified: new Date(2026, 8, 21 - index).toISOString()})))},
          IsTruncated: false,
        });
        this.getObject = (params, callback) => callback(null, {Body: ${JSON.stringify(JSON.stringify(manifest))}});
      }
    };
  `;
  await page.route('**/*', async route => {
    const req = route.request();
    const url = new URL(req.url());
    if (url.hostname === 'sdk.amazonaws.com') {
      return route.fulfill({contentType: 'application/javascript', body: sdk});
    }
    if (url.hostname === 'api.example') {
      const headers = {'Access-Control-Allow-Methods': 'GET, PUT, OPTIONS', 'Access-Control-Allow-Headers': 'content-type'};
      if (allowedOrigins.includes(origin)) headers['Access-Control-Allow-Origin'] = origin;
      if (req.method() === 'OPTIONS') return route.fulfill({status: 204, headers});
      if (req.method() === 'GET') return route.fulfill({headers, status: state.failRead ? 503 : 200,
        json: state.failRead ? {error: 'Unavailable'} : {keys: [...state.featured]}});
      const body = req.postDataJSON();
      state.writes.push(body);
      if (state.delayWrite) await new Promise(resolve => setTimeout(resolve, state.delayWrite));
      if (state.failWrite) return route.fulfill({headers, status: 503, json: {error: 'Unavailable'}});
      if (body.featured) state.featured.add(body.key);
      else state.featured.delete(body.key);
      return route.fulfill({headers, json: body});
    }
    if (url.pathname === '/config.json') return route.fulfill({json: {
      hallOfFameUrl: 'https://api.example/gallery/hall-of-fame',
    }});
    if (url.origin === origin && url.pathname === '/index.html') return route.fulfill({contentType: 'text/html',
      body: fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8')});
    if (url.pathname.endsWith('.mp4')) return route.fulfill({contentType: 'video/mp4', body: ''});
    return route.fulfill({contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg" width="500" height="500"><rect width="500" height="500" fill="#746449"/><circle cx="250" cy="210" r="100" fill="#dcc599"/></svg>'});
  });
  await page.goto(origin + '/index.html' + hash);
  await page.waitForFunction(() => galleryLoaded && !hallLoading);
  return {page, state};
}

test('any visitor can add a photo, persist it across reloads, and remove it from Hall of Fame', async t => {
  const {page, state} = await gallery(t);
  assert.equal(await page.locator('.media-item').count(), 3);
  await page.locator('.media-item').first().getByRole('button', {name: 'Add to Hall of Fame'}).click();
  await page.waitForFunction(() => hallKeys.size === 1 && hallPending.size === 0);
  assert.deepEqual(state.writes, [{key: photo, featured: true}]);
  assert.equal(await page.locator('#modal').evaluate(el => el.classList.contains('open')), false);
  await page.getByRole('button', {name: 'Hall of Fame', exact: true}).click();
  assert.equal(new URL(page.url()).hash, '#hall-of-fame');
  assert.equal(await page.locator('.media-item').count(), 1);
  await page.reload();
  await page.waitForFunction(() => galleryLoaded && hallReady && !hallLoading);
  assert.equal(await page.locator('.media-item').count(), 1);
  await page.getByRole('button', {name: 'Remove from Hall of Fame'}).click();
  await page.waitForFunction(() => hallKeys.size === 0 && hallPending.size === 0);
  assert.match(await page.locator('#grid').textContent(), /No Hall of Fame picks yet/);
  await page.getByRole('button', {name: 'All', exact: true}).click();
  assert.equal(await page.locator('.media-item').count(), 3);
  assert.deepEqual(state.writes[1], {key: photo, featured: false});
});

test('Hall of Fame includes photos and videos and respects search/user/channel filters', async t => {
  const {page} = await gallery(t, {featured: [photo, video], hash: '#hall-of-fame'});
  assert.equal(await page.locator('.media-item').count(), 2);
  assert.equal(await page.locator('.media-item video').count(), 1);
  await page.locator('#searchUser').selectOption('bob');
  assert.equal(await page.locator('.media-item').count(), 1);
  assert.match(await page.locator('.media-item').textContent(), /surfing dog/);
  await page.locator('#searchUser').selectOption('');
  await page.locator('#searchChannel').selectOption('cats');
  assert.equal(await page.locator('.media-item').count(), 1);
  await page.locator('#searchPrompt').fill('absent');
  assert.match(await page.locator('#grid').textContent(), /No results found/);
  await page.locator('#searchPrompt').fill('great');
  assert.equal(await page.locator('.media-item').count(), 1);
  assert.match(await page.locator('.media-item img').getAttribute('alt'), /"great"/);
});

test('viewer removal advances to the next pick and closes after the last removal', async t => {
  const {page} = await gallery(t, {featured: [photo, video], hash: '#hall-of-fame'});
  await page.locator('.media-item').first().locator('.thumb-container').click();
  await page.locator('#modalActions').getByRole('button', {name: 'Remove from Hall of Fame'}).click();
  await page.waitForFunction(() => hallKeys.size === 1 && hallPending.size === 0);
  assert.equal(await page.locator('#modalMedia video').count(), 1);
  assert.match(await page.locator('#modalTitle').textContent(), /surfing dog/);
  await page.locator('#modalActions').getByRole('button', {name: 'Remove from Hall of Fame'}).click();
  await page.waitForFunction(() => modalIndex === -1 && hallPending.size === 0);
  assert.equal(await page.locator('#modal').evaluate(el => el.classList.contains('open')), false);
  assert.equal(await page.evaluate(() => document.body.style.overflow), '');
});

test('failed saves preserve membership, show an error, and can be retried', async t => {
  const {page, state} = await gallery(t, {featured: [photo], hash: '#hall-of-fame'});
  state.failWrite = true;
  await page.getByRole('button', {name: 'Remove from Hall of Fame'}).click();
  await page.waitForFunction(() => hallPending.size === 0 && document.getElementById('hallStatus').classList.contains('error'));
  assert.equal(await page.locator('.media-item').count(), 1);
  assert.equal(state.featured.has(photo), true);
  assert.match(await page.locator('#hallStatus').textContent(), /Could not save/);
  state.failWrite = false;
  await page.getByRole('button', {name: 'Remove from Hall of Fame'}).click();
  await page.waitForFunction(() => hallKeys.size === 0 && hallPending.size === 0);
});

test('read failures keep the gallery usable and have an explicit retry', async t => {
  const {page, state} = await gallery(t, {failRead: true, featured: [photo]});
  assert.equal(await page.locator('.media-item').count(), 3);
  assert.equal(await page.locator('.card-actions button:disabled').count(), 3);
  await page.getByRole('button', {name: 'Hall of Fame', exact: true}).click();
  assert.match(await page.locator('#grid').textContent(), /unavailable/);
  state.failRead = false;
  await page.getByRole('button', {name: 'Retry Hall of Fame'}).click();
  await page.waitForFunction(() => hallReady && !hallLoading);
  assert.equal(await page.locator('.media-item').count(), 1);
});

test('removing the last item on a page clamps pagination without hiding remaining picks', async t => {
  const keys = Array.from({length: 25}, (_, i) => `dalle/cat_${i}_ABC.jpeg`);
  const {page} = await gallery(t, {keys, featured: keys, hash: '#hall-of-fame'});
  await page.locator('#perPage').selectOption('24');
  await page.getByRole('button', {name: /Next/}).click();
  assert.equal(await page.locator('.media-item').count(), 1);
  await page.getByRole('button', {name: 'Remove from Hall of Fame'}).click();
  await page.waitForFunction(() => hallKeys.size === 24 && hallPending.size === 0);
  assert.equal(await page.locator('.media-item').count(), 24);
  assert.equal(await page.evaluate(() => currentPage), 1);
});

test('pending saves disable duplicate clicks, and Slack changes refresh when returning to the page', async t => {
  const {page, state} = await gallery(t);
  state.delayWrite = 250;
  await page.locator('.card-actions button').first().click();
  assert.equal(await page.locator('.card-actions button').first().isDisabled(), true);
  await page.waitForFunction(() => hallPending.size === 0);
  assert.equal(state.writes.length, 1);
  state.featured.add(video);
  await page.evaluate(() => window.dispatchEvent(new Event('focus')));
  await page.waitForFunction(() => hallKeys.size === 2 && !hallLoading);
  assert.equal(await page.locator('.badge-hall').count(), 2);
});

test('mobile Hall of Fame remains within the viewport and allows removal', async t => {
  const {page} = await gallery(t, {mobile: true, featured: [photo, video], hash: '#hall-of-fame'});
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  assert.equal(await page.getByRole('button', {name: 'Hall of Fame', exact: true}).isVisible(), true);
  if (process.env.GALLERY_SCREENSHOT) await page.screenshot({path: process.env.GALLERY_SCREENSHOT, fullPage: true});
  await page.locator('.card-actions button').first().click();
  await page.waitForFunction(() => hallKeys.size === 1 && hallPending.size === 0);
  assert.equal(await page.locator('.media-item').count(), 1);
});

test('the direct S3 gallery URL can load, add and remove Hall of Fame picks', async t => {
  const {page, state} = await gallery(t, {origin: s3Origin});
  assert.equal(await page.evaluate(() => hallReady), true);
  await page.locator('.card-actions button').first().click();
  await page.waitForFunction(() => hallKeys.size === 1 && hallPending.size === 0);
  assert.deepEqual(state.writes, [{key: photo, featured: true}]);
  await page.getByRole('button', {name: 'Hall of Fame', exact: true}).click();
  await page.getByRole('button', {name: 'Remove from Hall of Fame'}).click();
  await page.waitForFunction(() => hallKeys.size === 0 && hallPending.size === 0);
  assert.equal(await page.locator('.media-item').count(), 0);
  assert.deepEqual(state.writes[1], {key: photo, featured: false});
});
