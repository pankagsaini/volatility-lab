/**
 * Volatility Lab — Native Custom-Domain Auth Gate (Cloudflare Worker)
 * ===================================================================
 * Keeps 100% of authentication on https://volatilitylab.in (zero third-party
 * domains or branding) while transparently protecting GitHub Pages behind it.
 *
 * Anonymous visitors get a small public landing page at "/" plus the brand
 * assets (icons, social card, robots.txt, sitemap.xml). The desk itself, every
 * JSON artifact and the research CSV stay behind the login gate.
 *
 * Required Cloudflare Worker Secrets (Settings -> Variables and Secrets):
 *   1. AUTH_SECRET   : Any long random string (32+ chars) used to sign cookies.
 *   2. ALLOWED_USERS : JSON object of invited email -> password pairs, e.g.:
 *                      {"pankajsaini2148@gmail.com":"YourStrongPass123!"}
 *                      (Add/remove entries anytime to invite or revoke users.)
 *
 * Route to bind (Settings -> Domains & Routes -> Add -> Route):
 *   volatilitylab.in/*   (Zone: volatilitylab.in)
 */

const COOKIE_NAME = '__Host-vl_auth';
const SESSION_TTL_SEC = 30 * 24 * 60 * 60; // 30 days
const SHORT_TTL_SEC = 24 * 60 * 60;        // 1 day (when "Remember me" unchecked)

// The public landing page is the only HTML an anonymous visitor receives. The
// desk itself, every JSON artifact and the research CSV stay behind the gate.
const PUBLIC_ASSETS = new Set([
  '/favicon.ico', '/favicon.svg', '/favicon-48.png', '/favicon-96.png', '/favicon-192.png',
  '/apple-touch-icon.png', '/icon-512.png', '/og-image.png'
]);
const PRIVATE_ARTIFACTS = [
  '/live_state.json', '/latest-snapshot.json', '/pricing-inputs.json',
  '/paper-risk-history.json', '/paper-ledger.json', '/research-v1-replay.csv'
];
const SITE_ORIGIN = 'https://volatilitylab.in';

const enc = new TextEncoder();

function toHex(buf) {
  return [...new Uint8Array(buf)].map(b => b.toString(16).padStart(2, '0')).join('');
}

async function hmacHex(secret, message) {
  const key = await crypto.subtle.importKey(
    'raw',
    enc.encode(secret),
    { name: 'HMAC', hash: 'SHA-256' },
    false,
    ['sign']
  );
  const sig = await crypto.subtle.sign('HMAC', key, enc.encode(message));
  return toHex(sig);
}

function timingSafeEqual(a, b) {
  if (typeof a !== 'string' || typeof b !== 'string' || a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

function parseUsers(env) {
  try {
    const raw = JSON.parse(env.ALLOWED_USERS || '{}');
    const out = {};
    for (const [k, v] of Object.entries(raw)) {
      if (typeof k === 'string' && typeof v === 'string' && v.length > 0) {
        out[k.trim().toLowerCase()] = v;
      }
    }
    return out;
  } catch {
    return {};
  }
}

async function createSessionToken(email, ttlSec, secret) {
  const exp = Math.floor(Date.now() / 1000) + ttlSec;
  const payload = btoa(JSON.stringify({ email, exp })).replace(/=+$/, '');
  const sig = await hmacHex(secret, payload);
  return `${payload}.${sig}`;
}

async function verifySessionToken(cookieHeader, secret, allowedUsers) {
  if (!cookieHeader || !secret) return null;
  const match = cookieHeader.match(new RegExp(`(?:^|;\\s*)${COOKIE_NAME}=([^;]+)`));
  if (!match) return null;
  const parts = match[1].split('.');
  if (parts.length !== 2) return null;
  const [payload, sig] = parts;
  const expected = await hmacHex(secret, payload);
  if (!timingSafeEqual(sig, expected)) return null;
  try {
    const data = JSON.parse(atob(payload));
    if (!data || !data.email || !data.exp) return null;
    if (data.exp <= Math.floor(Date.now() / 1000)) return null;
    if (!Object.prototype.hasOwnProperty.call(allowedUsers, data.email)) return null;
    return data.email;
  } catch {
    return null;
  }
}

function buildCookie(value, maxAge) {
  const base = `${COOKIE_NAME}=${value}; Path=/; Secure; HttpOnly; SameSite=Lax`;
  return maxAge === undefined ? base : `${base}; Max-Age=${maxAge}`;
}

const BRAND_HEAD = `  <title>Volatility Lab — Indian index options research</title>
  <meta name="description" content="Understand the curve. Compare volatility. Explore expiry risk. Independent Indian index options research built from recorded broker quotes." />
  <link rel="canonical" href="https://volatilitylab.in/" />
  <meta name="robots" content="index,follow,max-image-preview:large" />
  <meta name="theme-color" content="#070b12" />
  <meta property="og:site_name" content="Volatility Lab" />
  <meta property="og:title" content="Volatility Lab — Indian index options research" />
  <meta property="og:description" content="Understand the curve. Compare volatility. Explore expiry risk. Independent Indian index options research built from recorded broker quotes." />
  <meta property="og:type" content="website" />
  <meta property="og:url" content="https://volatilitylab.in/" />
  <meta property="og:image" content="https://volatilitylab.in/og-image.png" />
  <meta property="og:image:width" content="1200" />
  <meta property="og:image:height" content="630" />
  <meta property="og:image:alt" content="Volatility Lab — Understand the curve. Compare volatility. Explore expiry risk." />
  <meta property="og:locale" content="en_IN" />
  <meta name="twitter:card" content="summary_large_image" />
  <meta name="twitter:title" content="Volatility Lab — Indian index options research" />
  <meta name="twitter:description" content="Understand the curve. Compare volatility. Explore expiry risk." />
  <meta name="twitter:image" content="https://volatilitylab.in/og-image.png" />
  <link rel="icon" href="/favicon.ico" sizes="48x48" />
  <link rel="icon" type="image/svg+xml" href="/favicon.svg" />
  <link rel="icon" type="image/png" sizes="96x96" href="/favicon-96.png" />
  <link rel="icon" type="image/png" sizes="192x192" href="/favicon-192.png" />
  <link rel="apple-touch-icon" sizes="180x180" href="/apple-touch-icon.png" />
  <script type="application/ld+json">
  {"@context":"https://schema.org","@graph":[
    {"@type":"WebSite","@id":"https://volatilitylab.in/#website","url":"https://volatilitylab.in/","name":"Volatility Lab",
     "description":"Independent Indian index options research built from recorded broker quotes.","inLanguage":"en-IN","publisher":{"@id":"https://volatilitylab.in/#organization"}},
    {"@type":"Organization","@id":"https://volatilitylab.in/#organization","name":"Volatility Lab","url":"https://volatilitylab.in/",
     "logo":{"@type":"ImageObject","url":"https://volatilitylab.in/icon-512.png","width":512,"height":512}},
    {"@type":"WebPage","@id":"https://volatilitylab.in/#webpage","url":"https://volatilitylab.in/","name":"Volatility Lab — Indian index options research",
     "description":"Understand the curve. Compare volatility. Explore expiry risk. Independent Indian index options research built from recorded broker quotes.",
     "isPartOf":{"@id":"https://volatilitylab.in/#website"},"about":{"@id":"https://volatilitylab.in/#organization"},"inLanguage":"en-IN"}
  ]}
  </script>`;

const BRAND_MARK = `<svg viewBox="0 0 64 64" width="44" height="44" role="img" aria-label="Volatility Lab">
        <defs><linearGradient id="vlCurve" x1="13" y1="21" x2="51" y2="21" gradientUnits="userSpaceOnUse">
          <stop offset="0" stop-color="#38bdf8"/><stop offset="1" stop-color="#a78bfa"/>
        </linearGradient></defs>
        <rect width="64" height="64" rx="14" fill="#0d1522"/>
        <rect x="1" y="1" width="62" height="62" rx="13" fill="none" stroke="#24344d" stroke-width="2"/>
        <path d="M13 21C19 47 45 47 51 21" fill="none" stroke="url(#vlCurve)" stroke-width="6" stroke-linecap="round"/>
        <circle cx="32" cy="40.5" r="4.5" fill="#f59e0b"/>
      </svg>`;

function landingPageHtml() {
  return `<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
${BRAND_HEAD}
  <style>
    * { box-sizing: border-box; }
    body {
      margin: 0; min-height: 100svh; padding: clamp(1.5rem, 5vw, 4rem) 1.25rem;
      background: radial-gradient(circle at 15% 0%, #132038 0%, #070b12 60%);
      color: #edf2fa; font: 16px/1.6 Inter, system-ui, -apple-system, "Segoe UI", sans-serif;
      display: flex; flex-direction: column; align-items: center;
    }
    main { width: min(100%, 52rem); flex: 1; }
    .brand { display: flex; align-items: center; gap: 0.75rem; }
    .brand svg { display: block; flex: none; }
    .wordmark { font-weight: 800; letter-spacing: -0.01em; font-size: 1.1rem; }
    .eyebrow {
      margin: 2.5rem 0 0.75rem; font-size: 0.72rem; font-weight: 700;
      letter-spacing: 0.14em; text-transform: uppercase; color: #38bdf8;
    }
    h1 {
      margin: 0; font-size: clamp(1.9rem, 5.2vw, 3rem); line-height: 1.15;
      letter-spacing: -0.02em; font-weight: 800; max-width: 22ch;
    }
    .lede { margin: 1.1rem 0 0; max-width: 46ch; color: #b6c2d4; font-size: 1.02rem; }
    .themes {
      list-style: none; margin: 2.5rem 0 0; padding: 0; display: grid; gap: 0.9rem;
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }
    @media (max-width: 34rem) {
      .themes { grid-template-columns: 1fr; }
    }
    .themes li {
      padding: 1.05rem 1.15rem; border-radius: 12px; background: #0d1522;
      border: 1px solid #24344d;
    }
    .themes strong { display: block; font-size: 0.95rem; margin-bottom: 0.3rem; }
    .themes span { color: #94a3b8; font-size: 0.86rem; line-height: 1.5; }
    .cta { margin: 2.5rem 0 0; }
    .cta a {
      display: inline-block; padding: 0.8rem 1.5rem; border-radius: 9px;
      background: #38bdf8; color: #08101d; font-weight: 700; text-decoration: none;
    }
    .cta a:hover { background: #7dd3fc; }
    .cta a:focus-visible { outline: 3px solid #a78bfa; outline-offset: 3px; }
    .fine { margin: 0.9rem 0 0; color: #64748b; font-size: 0.8rem; }
    footer { width: min(100%, 52rem); margin-top: 3rem; color: #64748b; font-size: 0.78rem; }
  </style>
</head>
<body>
  <main>
    <div class="brand">
      ${BRAND_MARK}
      <span class="wordmark">Volatility Lab</span>
    </div>
    <p class="eyebrow">Independent research desk</p>
    <h1>Understand the curve. Compare volatility. Explore expiry risk.</h1>
    <p class="lede">
      Indian index options research for NIFTY and BANKNIFTY, built only from quotes recorded from the broker.
      Nothing is simulated, back-filled or invented: when a quote was not captured, the value stays blank.
    </p>
    <ul class="themes">
      <li><strong>The curve</strong><span>Term structure and forward variance across the expiries that were actually quoted.</span></li>
      <li><strong>Volatility</strong><span>Implied against realised, with the provenance of every input kept alongside it.</span></li>
      <li><strong>Expiry risk</strong><span>What the sessions left before expiry imply for an open position.</span></li>
      <li><strong>Recorded replays</strong><span>Strategy outcomes marked against the quotes that were captured that day.</span></li>
    </ul>
    <p class="cta"><a href="/login">Sign in to the desk</a></p>
    <p class="fine">Invite-only access. Research and education only — not investment advice.</p>
  </main>
  <footer>volatilitylab.in</footer>
</body>
</html>`;
}

function robotsTxt() {
  return [
    'User-agent: *',
    'Allow: /',
    'Disallow: /login',
    'Disallow: /logout',
    'Disallow: /replay/',
    ...PRIVATE_ARTIFACTS.map(p => `Disallow: ${p}`),
    '',
    `Sitemap: ${SITE_ORIGIN}/sitemap.xml`,
    ''
  ].join('\n');
}

function sitemapXml() {
  return `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>${SITE_ORIGIN}/</loc>
    <changefreq>weekly</changefreq>
    <priority>1.0</priority>
  </url>
</urlset>
`;
}

function loginPageHtml(errorMsg = '') {
  return `<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <meta name="robots" content="noindex,nofollow" />
  <link rel="icon" href="/favicon.ico" sizes="48x48" />
  <link rel="icon" type="image/svg+xml" href="/favicon.svg" />
  <title>Sign in · Volatility Lab</title>
  <style>
    * { box-sizing: border-box; }
    body {
      margin: 0; min-height: 100svh; display: grid; place-items: center; padding: 1.5rem;
      background: radial-gradient(circle at top, #132038 0%, #0a101b 65%);
      color: #edf2fa; font: 15px/1.5 Inter, system-ui, -apple-system, sans-serif;
    }
    .card {
      width: min(100%, 24rem); padding: 2rem; border-radius: 14px;
      background: rgba(13, 21, 34, 0.94); border: 1px solid #24344d;
      box-shadow: 0 24px 60px rgba(0, 0, 0, 0.45);
    }
    .brand { font-size: 0.78rem; font-weight: 700; letter-spacing: 0.1em; text-transform: uppercase; color: #38bdf8; }
    h1 { margin: 0.35rem 0 0.25rem; font-size: 1.6rem; font-weight: 700; }
    .sub { margin: 0 0 1.4rem; color: #94a3b8; font-size: 0.85rem; }
    label { display: block; font-size: 0.82rem; font-weight: 600; color: #cbd5e1; margin: 0.9rem 0 0.35rem; }
    input[type="email"], input[type="password"] {
      width: 100%; padding: 0.72rem 0.85rem; border-radius: 8px;
      border: 1px solid #334155; background: #080d16; color: #f8fafc; font: inherit;
    }
    input:focus { outline: 2px solid #38bdf8; border-color: #38bdf8; }
    .row { display: flex; align-items: center; gap: 0.5rem; margin-top: 0.9rem; font-size: 0.83rem; color: #cbd5e1; }
    button {
      width: 100%; margin-top: 1.25rem; padding: 0.78rem; border: 0; border-radius: 8px;
      background: #38bdf8; color: #08101d; font: inherit; font-weight: 700; cursor: pointer;
    }
    button:hover { background: #7dd3fc; }
    .err {
      margin-top: 0.9rem; padding: 0.65rem 0.8rem; border-radius: 8px;
      background: rgba(244, 63, 94, 0.14); border: 1px solid rgba(244, 63, 94, 0.45);
      color: #fda4af; font-size: 0.83rem;
    }
    .foot { margin-top: 1.2rem; text-align: center; font-size: 0.75rem; color: #64748b; }
    .foot a { color: #7dd3fc; text-decoration: none; }
    .foot a:hover { text-decoration: underline; }
  </style>
</head>
<body>
  <main class="card">
    <div class="brand">Volatility Lab</div>
    <h1>Private research desk</h1>
    <p class="sub">Invite-only access · NIFTY &amp; BANKNIFTY index options</p>
    <form method="POST" action="/login">
      <label for="email">Invited Email</label>
      <input id="email" name="email" type="email" required autocomplete="username" />
      <label for="password">Password</label>
      <input id="password" name="password" type="password" required autocomplete="current-password" />
      <label class="row">
        <input name="remember" type="checkbox" value="1" checked />
        <span>Keep me signed in for 30 days</span>
      </label>
      ${errorMsg ? `<div class="err" role="alert">${errorMsg}</div>` : ''}
      <button type="submit">Sign in to Volatility Lab</button>
    </form>
    <div class="foot">volatilitylab.in · Authorized access only · <a href="/">About the desk</a></div>
  </main>
</body>
</html>`;
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const path = url.pathname;
    const secret = env.AUTH_SECRET || '';
    const allowedUsers = parseUsers(env);

    // 0. Allow authenticated GitHub Actions CI reads for retained JSON artifacts only
    const CI_PATHS = ['/live_state.json', '/latest-snapshot.json', '/pricing-inputs.json', '/paper-risk-history.json'];
    if (
      request.method === 'GET' &&
      CI_PATHS.includes(path) &&
      env.CI_READ_TOKEN &&
      timingSafeEqual(request.headers.get('X-CI-Read-Token') || '', env.CI_READ_TOKEN)
    ) {
      return fetch(request);
    }

    // 1. Crawler entry points: a public landing page, its robots policy and a
    //    one-URL sitemap. Everything below stays behind the gate.
    if (path === '/robots.txt') {
      return new Response(robotsTxt(), {
        headers: {
          'Content-Type': 'text/plain; charset=utf-8',
          'Cache-Control': 'public, max-age=300'
        }
      });
    }

    if (path === '/sitemap.xml') {
      return new Response(sitemapXml(), {
        headers: {
          'Content-Type': 'application/xml; charset=utf-8',
          'Cache-Control': 'public, max-age=300'
        }
      });
    }

    // 2. Brand assets stay public so search engines and link previews can fetch
    //    the icon and the social card without a session.
    if (PUBLIC_ASSETS.has(path) && (request.method === 'GET' || request.method === 'HEAD')) {
      const upstream = await fetch(request);
      const resp = new Response(upstream.body, upstream);
      resp.headers.set('Cache-Control', 'public, max-age=86400');
      return resp;
    }

    // 3. Logout endpoint (visit https://volatilitylab.in/logout anytime)
    if (path === '/logout') {
      return new Response(null, {
        status: 303,
        headers: {
          Location: '/login',
          'Set-Cookie': buildCookie('', 0),
          'Cache-Control': 'no-store'
        }
      });
    }

    // 4. Handle Login POST
    if (path === '/login' && request.method === 'POST') {
      if (!secret || Object.keys(allowedUsers).length === 0) {
        return new Response(loginPageHtml('Authentication secrets are not configured yet.'), {
          status: 503,
          headers: { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store' }
        });
      }
      const form = await request.formData();
      const email = String(form.get('email') || '').trim().toLowerCase();
      const password = String(form.get('password') || '');
      const remember = form.get('remember') === '1';

      const expectedPass = allowedUsers[email];
      if (!expectedPass || !timingSafeEqual(password, expectedPass)) {
        return new Response(loginPageHtml('Invalid email or password. Access is invite-only.'), {
          status: 401,
          headers: { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store' }
        });
      }

      const ttl = remember ? SESSION_TTL_SEC : SHORT_TTL_SEC;
      const token = await createSessionToken(email, ttl, secret);
      return new Response(null, {
        status: 303,
        headers: {
          Location: '/',
          'Set-Cookie': buildCookie(token, remember ? ttl : undefined),
          'Cache-Control': 'no-store'
        }
      });
    }

    // 5. Verify session cookie
    const authedEmail = await verifySessionToken(request.headers.get('Cookie'), secret, allowedUsers);

    if (path === '/login') {
      if (authedEmail) {
        return new Response(null, { status: 303, headers: { Location: '/' } });
      }
      return new Response(loginPageHtml(), {
        status: 200,
        headers: { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store' }
      });
    }

    if (!authedEmail) {
      // The homepage is the public landing page; the desk itself lives at /login.
      if ((path === '/' || path === '/index.html') && (request.method === 'GET' || request.method === 'HEAD')) {
        return new Response(request.method === 'HEAD' ? null : landingPageHtml(), {
          status: 200,
          headers: {
            'Content-Type': 'text/html; charset=utf-8',
            'Cache-Control': 'public, max-age=300'
          }
        });
      }
      const isHtmlPage = (request.headers.get('Accept') || '').includes('text/html');
      if (isHtmlPage) {
        return new Response(null, { status: 303, headers: { Location: '/login', 'Cache-Control': 'no-store' } });
      }
      return new Response(JSON.stringify({ error: 'Unauthorized' }), {
        status: 401,
        headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' }
      });
    }

    // 6. Authenticated: pass straight through to GitHub Pages origin
    const upstream = await fetch(request);
    const resp = new Response(upstream.body, upstream);
    resp.headers.set('Cache-Control', 'private, no-store');
    return resp;
  }
};