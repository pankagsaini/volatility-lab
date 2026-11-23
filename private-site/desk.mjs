import { createHash, randomBytes } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { extname, join } from 'node:path';

const cookieName = '__Host-vl_session';
const day = 86400;
const digest = value => createHash('sha256').update(value).digest('hex');
const cookie = (value, lifetime) => cookieName + '=' + value +
  '; Path=/; Secure; HttpOnly; SameSite=Lax' + (lifetime === undefined ? '' : '; Max-Age=' + lifetime);
const types = {'.html':'text/html; charset=utf-8','.json':'application/json','.js':'text/javascript','.css':'text/css','.csv':'text/csv'};
const asset = /^\/(?:index\.html|live_state\.json|pricing-inputs\.json|paper-ledger\.json|latest-snapshot\.json|research-v1-replay\.csv|scripts\/[a-z\d.-]+\.(?:js|css)|replay\/[a-z\d-]+\.json)$/i;

function result(body, status = 200, headers = {}) {
  return new Response(body, {status, headers:{
    'Cache-Control':'private, no-store', 'Netlify-CDN-Cache-Control':'no-store',
    'X-Content-Type-Options':'nosniff', 'Referrer-Policy':'no-referrer',
    'X-Frame-Options':'DENY', ...headers
  }});
}
const go = (location, headers) => result(null, 303, {Location:location,...headers});

async function readAsset(dir, file) {
  try { return await readFile(join(dir,file)); }
  catch (error) { if (error.code === 'ENOENT') return null; throw error; }
}

export function privateDesk({ sessions, ownerEmail, ownerId, identity, collector, collectorKey, bundle, request = fetch, now = Date.now }) {
  async function identityPost(path, body) {
    return request(identity + path, {method:'POST', body, redirect:'error', signal:AbortSignal.timeout(10000),
      headers:{'Content-Type':body instanceof URLSearchParams ? 'application/x-www-form-urlencoded' : 'application/json'}});
  }

  async function login(form) {
    const email = String(form.get('email') || '').trim().toLowerCase();
    const password = form.get('password');
    const fail = () => go('/login?status=invalid');
    if (email !== ownerEmail || typeof password !== 'string' || !password) return fail();
    const auth = await identityPost('/token', new URLSearchParams({grant_type:'password',username:email,password}));
    if (!auth.ok) return auth.status >= 500 || auth.status === 429 ? go('/login?status=unavailable') : fail();
    const { access_token: token } = await auth.json();
    if (!token) return fail();
    const verified = await request(identity + '/user', {redirect:'error', signal:AbortSignal.timeout(10000),
      headers:{Authorization:'Bearer ' + token}});
    if (!verified.ok) return verified.status >= 500 ? go('/login?status=unavailable') : fail();
    const user = await verified.json();
    if (user.id !== ownerId || user.email?.toLowerCase() !== ownerEmail || !(user.confirmed_at || user.email_confirmed_at)) return fail();
    const remember = form.get('remember') === 'yes';
    const lifetime = remember ? 30 * day : day;
    const id = randomBytes(32).toString('hex');
    await sessions.setJSON(digest(id), {userId:user.id, expires:now()+lifetime*1000});
    return go('/', {'Set-Cookie':cookie(id, remember ? lifetime : undefined)});
  }

  async function market(search) {
    const target = new URL(collector);
    const action = search.get('action') || 'latest';
    if (!['latest','closing_risk'].includes(action)) return result(null,400);
    target.searchParams.set('action',action);
    for (const name of ['since','day','_cb']) if (search.has(name)) target.searchParams.set(name,search.get(name));
    if (collectorKey) target.searchParams.set('key',collectorKey);
    const upstream = await request(target,{signal:AbortSignal.timeout(35000)});
    if (!upstream.ok) return result(null,502);
    return result(upstream.body,200,{'Content-Type':'application/json'});
  }

  return async function handle(req) {
    try {
      const url = new URL(req.url);
      const path = url.pathname;
      const isPage = path === '/' || path === '/index.html' || path === '/login';
      const id = req.headers.get('cookie')?.match(/(?:^|;\s*)__Host-vl_session=([a-f0-9]{64})(?:;|$)/)?.[1];
      if (req.method === 'POST') {
        if (req.headers.get('origin') !== url.origin) return result(null,403);
        if (path === '/auth/logout') {
          if (id) await sessions.delete(digest(id));
          return go('/login',{'Set-Cookie':cookie('',0),'Clear-Site-Data':'"cache", "storage"'});
        }
        if (!['/auth/login','/auth/invite'].includes(path)) return result(null,404);
        if (!ownerEmail || (path === '/auth/login' && !ownerId)) return result('Private login is being configured.',503);
        if (!req.headers.get('content-type')?.startsWith('application/x-www-form-urlencoded')) return result(null,415);
        const body = await req.text();
        if (body.length > 8192) return result(null,413);
        const form = new URLSearchParams(body);
        if (path === '/auth/login') return await login(form);
        const password = form.get('password'), token = form.get('token');
        if (!token || !password || password.length < 12) return go('/login?status=invite');
        const response = await identityPost('/verify',JSON.stringify({type:'signup',token,password}));
        return go('/login?status=' + (response.ok ? 'setup' : 'invite'));
      }
      if (!['GET','HEAD'].includes(req.method)) return result(null,405);
      const record = id && await sessions.get(digest(id),{type:'json'});
      const allowed = !!(ownerEmail && ownerId && record && record.userId === ownerId && record.expires > now());
      if (record && record.expires <= now()) await sessions.delete(digest(id));
      if (!allowed || path === '/login') {
        if (!isPage) return result(null,401);
        if (allowed) return go('/');
        return result(req.method === 'HEAD' ? null : await readAsset(bundle,'login.html'),200,{
          'Content-Type':types['.html'],
          'Content-Security-Policy':"default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
        });
      }
      if (path === '/api/snapshot') return await market(url.searchParams);
      if (path === '/api/paper-ledger') {
        const upstream = await request('https://raw.githubusercontent.com/pankagsaini/volatility-lab/paper-feed/paper-ledger.json',{signal:AbortSignal.timeout(10000)});
        return result(upstream.ok ? upstream.body : null,upstream.ok ? 200 : 502,{'Content-Type':'application/json'});
      }
      const file = path === '/' ? '/index.html' : path;
      if (!asset.test(file)) return result(null,404);
      const data = await readAsset(bundle,file.slice(1));
      if (!data && file === '/latest-snapshot.json') return await market(new URLSearchParams());
      if (!data) return result(null,404);
      return result(req.method === 'HEAD' ? null : data,200,{'Content-Type':types[extname(file)]});
    } catch { return result('Service temporarily unavailable.',503); }
  };
}
