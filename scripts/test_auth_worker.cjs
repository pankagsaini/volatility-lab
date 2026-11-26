// Offline checks of the Cloudflare auth worker: the public landing page, its
// crawl policy and the gate that still protects the desk and its artifacts.
// No network access: the origin fetch is stubbed.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { pathToFileURL } = require('node:url');

const WORKER_PATH = path.join(__dirname, '../cloudflare/volatilitylab-auth-worker.js');
const ENV = {
  AUTH_SECRET: 'test-secret-that-is-long-enough-for-hmac',
  ALLOWED_USERS: JSON.stringify({ 'desk@example.com': 'correct-horse-battery' }),
  CI_READ_TOKEN: 'ci-token',
};

const originCalls = [];
const ORIGIN_DESK = '<!doctype html><title>desk workspace</title><p>private</p>';

function installOriginStub() {
  globalThis.fetch = async request => {
    const url = typeof request === 'string' ? request : request.url;
    originCalls.push(url);
    if (/\.(?:png|ico|svg)$/.test(new URL(url).pathname)) {
      return new Response('asset-bytes', {
        status: 200,
        headers: { 'Content-Type': 'application/octet-stream', 'X-Origin': 'pages' },
      });
    }
    return new Response(ORIGIN_DESK, {
      status: 200,
      headers: { 'Content-Type': 'text/html; charset=utf-8', 'X-Origin': 'pages' },
    });
  };
}

function req(pathname, init = {}) {
  return new Request(`https://volatilitylab.in${pathname}`, init);
}

function cookieFrom(response) {
  const raw = response.headers.get('Set-Cookie') || '';
  const match = raw.match(/__Host-vl_auth=[^;]+/);
  assert.ok(match, 'login must set the session cookie');
  return match[0];
}

(async () => {
  installOriginStub();
  const { default: worker } = await import(pathToFileURL(WORKER_PATH).href);
  const call = (pathname, init) => worker.fetch(req(pathname, init), ENV);

  // --- Public landing page ------------------------------------------------
  const home = await call('/', { headers: { Accept: 'text/html' } });
  assert.equal(home.status, 200);
  assert.match(home.headers.get('Content-Type'), /text\/html/);
  assert.match(home.headers.get('Cache-Control'), /public/);
  const html = await home.text();
  assert.match(html, /<title>Volatility Lab — Indian index options research<\/title>/);
  assert.match(html, /Understand the curve\. Compare volatility\. Explore expiry risk\./);
  assert.match(html, /rel="canonical" href="https:\/\/volatilitylab\.in\/"/);
  assert.match(html, /name="robots" content="index,follow/);
  assert.match(html, /application\/ld\+json/);
  assert.match(html, /"@type":"Organization"/);
  assert.match(html, /https:\/\/volatilitylab\.in\/icon-512\.png/);
  assert.match(html, /property="og:image" content="https:\/\/volatilitylab\.in\/og-image\.png"/);
  assert.match(html, /rel="icon" href="\/favicon\.ico"/);
  assert.match(html, /href="\/login"/);
  assert.match(html, /NIFTY and BANKNIFTY/);
  assert.doesNotMatch(html, /NSE\b/);
  assert.doesNotMatch(html, /Quant Desk/);
  assert.doesNotMatch(html, /institutional/i);
  assert.equal(originCalls.length, 0, 'the landing page must not expose the desk origin');

  // The landing page and the desk page must advertise the same snippet, or
  // Google would see one wording from the gate and another after login.
  const siteHtml = fs.readFileSync(path.join(__dirname, '../index.html'), 'utf8');
  const siteTitle = siteHtml.match(/<title>([^<]+)<\/title>/)[1];
  const siteDescription = siteHtml.match(/<meta name="description" content="([^"]+)"/)[1];
  assert.ok(html.includes(`<title>${siteTitle}</title>`), 'landing title must match index.html');
  assert.ok(html.includes(`content="${siteDescription}"`), 'landing description must match index.html');

  const head = await call('/', { method: 'HEAD' });
  assert.equal(head.status, 200);
  assert.equal(await head.text(), '');

  const indexAlias = await call('/index.html', { headers: { Accept: 'text/html' } });
  assert.equal(indexAlias.status, 200);
  assert.match(await indexAlias.text(), /Indian index options research/);

  // --- Crawl policy -------------------------------------------------------
  const robots = await call('/robots.txt');
  assert.equal(robots.status, 200);
  assert.match(robots.headers.get('Content-Type'), /text\/plain/);
  const robotsText = await robots.text();
  assert.match(robotsText, /^User-agent: \*$/m);
  assert.match(robotsText, /^Allow: \/$/m);
  assert.match(robotsText, /^Disallow: \/login$/m);
  assert.match(robotsText, /^Disallow: \/live_state\.json$/m);
  assert.match(robotsText, /^Sitemap: https:\/\/volatilitylab\.in\/sitemap\.xml$/m);
  assert.doesNotMatch(robotsText, /^Disallow: \/$/m);

  const sitemap = await call('/sitemap.xml');
  assert.equal(sitemap.status, 200);
  assert.match(sitemap.headers.get('Content-Type'), /application\/xml/);
  const sitemapText = await sitemap.text();
  assert.match(sitemapText, /<loc>https:\/\/volatilitylab\.in\/<\/loc>/);
  assert.doesNotMatch(sitemapText, /<loc>[^<]+\/(?:login|replay)/);

  // --- Public brand assets ------------------------------------------------
  for (const asset of [
    '/favicon.ico', '/favicon.svg', '/favicon-48.png', '/favicon-96.png',
    '/favicon-192.png', '/apple-touch-icon.png', '/icon-512.png', '/og-image.png',
  ]) {
    const before = originCalls.length;
    const resp = await call(asset);
    assert.equal(resp.status, 200, `${asset} must be publicly fetchable`);
    assert.match(resp.headers.get('Cache-Control'), /public/);
    assert.equal(originCalls.length, before + 1, `${asset} must be served by the origin`);
    assert.match(originCalls.at(-1), new RegExp(asset.replace(/[.]/g, '\\.') + '$'));
  }

  // --- Anonymous requests stay gated -------------------------------------
  const artifact = await call('/live_state.json', { headers: { Accept: 'application/json' } });
  assert.equal(artifact.status, 401);
  assert.deepEqual(await artifact.json(), { error: 'Unauthorized' });

  const deepLink = await call('/replay/index.html', { headers: { Accept: 'text/html' } });
  assert.equal(deepLink.status, 303);
  assert.equal(deepLink.headers.get('Location'), '/login');

  const csv = await call('/research-v1-replay.csv');
  assert.equal(csv.status, 401);

  const login = await call('/login', { headers: { Accept: 'text/html' } });
  assert.equal(login.status, 200);
  const loginHtml = await login.text();
  assert.match(loginHtml, /<title>Sign in · Volatility Lab<\/title>/);
  assert.match(loginHtml, /name="robots" content="noindex,nofollow"/);
  assert.match(loginHtml, /Private research desk/);
  assert.doesNotMatch(loginHtml, /NSE\b/);
  assert.doesNotMatch(loginHtml, /Quant Desk/);

  // --- CI artifact reads still bypass the cookie --------------------------
  const ci = await call('/live_state.json', { headers: { 'X-CI-Read-Token': 'ci-token' } });
  assert.equal(ci.status, 200);
  assert.equal(await ci.text(), ORIGIN_DESK);

  const wrongToken = await call('/live_state.json', { headers: { 'X-CI-Read-Token': 'nope' } });
  assert.equal(wrongToken.status, 401);

  // --- Authentication unlocks the desk -----------------------------------
  const badLogin = await call('/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({ email: 'desk@example.com', password: 'wrong' }),
  });
  assert.equal(badLogin.status, 401);

  const goodLogin = await call('/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({ email: 'desk@example.com', password: 'correct-horse-battery', remember: '1' }),
  });
  assert.equal(goodLogin.status, 303);
  assert.equal(goodLogin.headers.get('Location'), '/');
  const cookie = cookieFrom(goodLogin);

  const desk = await call('/', { headers: { Accept: 'text/html', Cookie: cookie } });
  assert.equal(desk.status, 200);
  assert.equal(await desk.text(), ORIGIN_DESK);
  assert.match(desk.headers.get('Cache-Control'), /private/);

  const deskArtifact = await call('/live_state.json', { headers: { Cookie: cookie } });
  assert.equal(deskArtifact.status, 200);

  const alreadyIn = await call('/login', { headers: { Cookie: cookie } });
  assert.equal(alreadyIn.status, 303);
  assert.equal(alreadyIn.headers.get('Location'), '/');

  const logout = await call('/logout');
  assert.equal(logout.status, 303);
  assert.match(logout.headers.get('Set-Cookie') || '', /Max-Age=0/);

  console.log('Auth worker regressions passed: public landing page, crawl policy, brand assets, gated desk and CI reads.');
})().catch(error => {
  console.error(error);
  process.exit(1);
});
