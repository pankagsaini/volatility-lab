import test from 'node:test';
import assert from 'node:assert/strict';
import { resolve } from 'node:path';
import { readFile } from 'node:fs/promises';
import { privateDesk } from './desk.mjs';
const origin = 'https://desk.example';
const email = 'owner@example.test';
const user = {id:'invited-owner',email,confirmed_at:'2026-01-01'};
function fixture() {
  const rows = new Map();
  const sessions = {setJSON:(k,v)=>rows.set(k,v), get:k=>rows.get(k), delete:k=>rows.delete(k)};
  const state = {time:1000, user, visits:[]};
  const request = async (url,init) => {
    const target = new URL(url); state.visits.push(target);
    if (target.pathname.endsWith('/token')) return init.body.get('password') === 'test-password' ?
      Response.json({access_token:'test-token'}) : new Response(null,{status:400});
    if (target.pathname.endsWith('/user')) return Response.json(state.user);
    if (target.pathname.endsWith('/verify')) return new Response(null,{status:JSON.parse(init.body).type === 'signup' ? 200 : 400});
    return Response.json({status:'complete'});
  };
  const handle = privateDesk({sessions,ownerId:user.id,ownerEmail:email,identity:origin+'/identity',collector:'https://collector.example/read',
    collectorKey:'test-key',bundle:resolve('bundle'),request,now:()=>state.time});
  function call(path, {form, cookie, from=origin}={}) {
    const headers = cookie ? {cookie} : {};
    if (form) headers.origin=from;
    return handle(new Request(origin+path,{method:form?'POST':'GET',headers,body:form && new URLSearchParams(form)}));
  }
  return {call,state,rows};
}
const login = {email,password:'test-password'};
test('signed-out entry shows login and all market routes require a session',async()=>{
  const {call,state}=fixture();
  assert.match(await (await call('/')).text(),/<h1 id="title">Sign in/);
  for (const path of ['/latest-snapshot.json','/live_state.json','/paper-ledger.json','/replay/manifest.json','/api/snapshot','/api/paper-ledger','/scripts/trading-sessions.js']) {
    assert.equal((await call(path)).status,401);
  }
  assert.equal(state.visits.length,0);
});
test('invite, invalid login, different account, cross-origin submission',async()=>{
  const {call,state,rows}=fixture();
  assert.equal((await call('/auth/invite',{form:{token:'example',password:'example-long-password'}})).headers.get('location'),'/login?status=setup');
  assert.equal((await call('/auth/login',{form:login,from:'https://other.example'})).status,403);
  for (const form of [{...login,email:'other@example.test'},{...login,password:'wrong'}]) {
    assert.equal((await call('/auth/login',{form})).headers.get('location'),'/login?status=invalid');
  }
  for (const fake of [{...user,id:'other-id'},{...user,confirmed_at:null}]) {
    state.user=fake;assert.equal((await call('/auth/login',{form:login})).headers.get('set-cookie'),null);
  }
  assert.equal(rows.size,0);
});
test('password session survives reload, serves the desk and preserves collector reads',async()=>{
  const {call,state,rows}=fixture();
  const entry=await call('/auth/login',{form:{...login,remember:'yes'}});
  const header=entry.headers.get('set-cookie');assert.match(header,/HttpOnly; SameSite=Lax; Max-Age=2592000/);
  const cookie=header.split(';')[0];
  for(let i=0;i<2;i++) {
    const page=await call('/',{cookie});
    assert.equal(page.status,200);assert.equal(page.headers.get('cache-control'),'private, no-store');
    assert.match(await page.text(),/action="\/auth\/logout"/);
  }
  assert.equal((await call('/replay/manifest.json',{cookie})).status,200);
  const latest=await call('/api/snapshot?since=last&key=ignored',{cookie});
  assert.equal((await latest.json()).status,'complete');
  assert.equal(state.visits.at(-1).searchParams.get('key'),'test-key');
  assert.equal(state.visits.at(-1).searchParams.get('since'),'last');
  assert.equal((await call('/api/snapshot?action=status',{cookie})).status,400);
  assert.equal((await call('/auth/logout',{cookie,form:{}})).status,303);
  assert.equal(rows.size,0);assert.equal((await call('/latest-snapshot.json',{cookie})).status,401);
});
test('remembered sessions expire on the server and session-only cookies omit Max-Age',async()=>{
  const {call,state}=fixture();
  for(const remember of ['no','yes']) {
    const r=await call('/auth/login',{form:{...login,remember}});const header=r.headers.get('set-cookie');
    assert.equal(header.includes('Max-Age='),remember==='yes');
    state.time+=(remember==='yes'?30:1)*86400000;
    assert.equal((await call('/api/snapshot',{cookie:header.split(';')[0]})).status,401);
  }
});
test('private build routes collector and paper reads through the signed-in host',async()=>{
  const html=await readFile('bundle/index.html','utf8');
  assert.match(html,/var CLOUD_EXEC_URL = '\/api\/snapshot'/);
  assert.match(html,/const remote = '\/api\/paper-ledger'/);
  assert.match(html,/cache:'no-store',credentials:'same-origin'/);
  assert.ok(!html.includes('https://script.google.com/macros/s/'));
});
