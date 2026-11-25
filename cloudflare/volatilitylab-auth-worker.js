/**
 * Volatility Lab — Native Custom-Domain Auth Gate (Cloudflare Worker)
 * ===================================================================
 * Keeps 100% of authentication on https://volatilitylab.in (zero third-party
 * domains or branding) while transparently protecting GitHub Pages behind it.
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

function loginPageHtml(errorMsg = '') {
  return `<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <meta name="robots" content="noindex,nofollow" />
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
  </style>
</head>
<body>
  <main class="card">
    <div class="brand">Volatility Lab</div>
    <h1>Private Quant Desk</h1>
    <p class="sub">Invite-only access · NSE NIFTY 50 &amp; BANKNIFTY</p>
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
    <div class="foot">volatilitylab.in · Authorized access only</div>
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

    // 1. Always allow robots.txt to block search engines
    if (path === '/robots.txt') {
      return new Response('User-agent: *\nDisallow: /\n', {
        headers: { 'Content-Type': 'text/plain; charset=utf-8' }
      });
    }

    // 2. Logout endpoint (visit https://volatilitylab.in/logout anytime)
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

    // 3. Handle Login POST
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

    // 4. Verify session cookie
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
      const isHtmlPage = path === '/' || path === '/index.html' || (request.headers.get('Accept') || '').includes('text/html');
      if (isHtmlPage) {
        return new Response(null, { status: 303, headers: { Location: '/login', 'Cache-Control': 'no-store' } });
      }
      return new Response(JSON.stringify({ error: 'Unauthorized' }), {
        status: 401,
        headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' }
      });
    }

    // 5. Authenticated: pass straight through to GitHub Pages origin
    const upstream = await fetch(request);
    const resp = new Response(upstream.body, upstream);
    resp.headers.set('Cache-Control', 'private, no-store');
    return resp;
  }
};