// Browser smoke test for the built site against a mocked backend. Exits 1 on any failure.
// Usage: node scripts/smoke.js [path/to/dist/index.html]
const { chromium } = require('playwright'); const fs = require('fs'); const path = require('path');
const DIST = process.argv[2] || path.join(__dirname, '..', 'dist', 'index.html');
const fail = (m) => { console.error('SMOKE FAIL:', m); process.exit(1); };
(async () => {
  const b = await chromium.launch(); const ctx = await b.newContext({ viewport: { width: 390, height: 844 } });
  const store = { user: { email: 't@x.in', name: 'Test', public: { enabled: false } }, evidence: {} }; const events = [];
  await ctx.route('https://api.test/**', async (route) => { const req = route.request(); const p = new URL(req.url()).pathname; const m = req.method(); let body = {}; try { body = req.postDataJSON() || {}; } catch (e) {}
    const ok = o => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(o) });
    if (p === '/events') { events.push(body.event); return ok({ ok: true }); }
    if (p === '/auth/start') return ok({ ok: true, session: 'S' }); if (p === '/auth/verify') return ok({ ok: true, id_token: 'T', refresh_token: 'R', user: store.user });
    if (p === '/me' && m === 'GET') return ok(store); if (p === '/me' && m === 'PUT') { Object.assign(store.user, body); return ok({ ok: true, user: store.user }); }
    if (p === '/me/evidence') { if (body.remove) delete store.evidence[body.skill_id]; else store.evidence[body.skill_id] = { ...body.evidence, added: '2026-01-01T00:00:00Z' }; return ok({ ok: true }); }
    if (p === '/me/public') { store.user.public = { enabled: body.enabled, slug: body.slug || 'test-1' }; return ok({ ok: true, public: store.user.public }); }
    if (p.startsWith('/public/')) return ok(store); return route.fulfill({ status: 404, body: '{}' }); });
  const p = await ctx.newPage(); const errs = []; p.on('pageerror', e => errs.push(e.message)); p.on('console', m => { if (m.type() === 'error' && !/ERR_TUNNEL|ERR_NAME|fonts|net::/.test(m.text())) errs.push(m.text()); });
  const tmp = path.join(require('os').tmpdir(), 'nextrung-smoke.html');
  fs.writeFileSync(tmp, fs.readFileSync(DIST, 'utf8').replace('__ENDPOINT__', 'https://api.test/'));
  const go = async (h) => { await p.goto('file://' + tmp + h); await p.waitForTimeout(400); return p.evaluate(() => document.querySelector('#app').innerText); };
  const home = await go('#/'); if (!/Free\. No course/.test(home)) fail('home missing free line');
  await p.evaluate(() => { answers = { branch: 'ECE', college_tier: '3', graduation_year: '2026', region: 'south', relocation: 'anywhere_india', runway_months: '4_12', interests: ['hardware_electronics'], priority: 'meaningful_domain', preparation: ['core_tools'], enjoyed: 'labs_hardware' }; saveAnswers(); });
  const res = await go('#/results'); if (!/1\. /.test(res) || !/Get my 90-day plan/.test(res)) fail('results cards missing');
  const first = await p.$('.card.rank a.btn'); const href = await first.getAttribute('href'); const plan = await go(href); if (!/Month 1/.test(plan) || !/This week/i.test(plan)) fail('plan preview missing Month 1 / This week');
  await p.click('#save-plan'); await p.waitForTimeout(300); if (!/signin/.test(await p.evaluate(() => location.hash))) fail('save did not route to sign-in');
  await p.fill('#email', 't@x.in'); await p.click('#send'); await p.waitForTimeout(300); await p.fill('#code', '12345678'); await p.click('#verify'); await p.waitForTimeout(900);
  if (!/me/.test(await p.evaluate(() => location.hash))) fail('sign-in did not route to me');
  const plan2 = await go('#/me/plan'); if (!/add proof/i.test(plan2)) fail('me/plan missing add proof');
  const add = await p.$('[data-add]'); const sid = await add.getAttribute('data-add'); await add.click(); await p.waitForTimeout(150); await p.fill(`form.evf[data-s="${sid}"] input[name=url]`, 'https://github.com/x/y'); await p.click(`form.evf[data-s="${sid}"] button[type=submit]`); await p.waitForTimeout(700);
  const me = await go('#/me'); if (!/%/.test(me)) fail('ring not shown after first proof');
  const jobs = await go('#/me/jobs'); if (!/Open the ad/.test(jobs)) fail('jobs for me missing ads');
  await go('#/me/settings'); await p.check('#pub'); await p.waitForTimeout(500); const pub = await go('#/p/test-1'); if (!/Proof/.test(pub)) fail('public page missing');
  for (const r of ['#/paths', '#/path/it_services', '#/jobs', '#/sources', '#/about']) { const t = await go(r); if (t.length < 200) fail('thin page ' + r); }
  if (/\b(supply|demand|paths|gap)\.[a-z_]+\./.test(await go('#/path/it_services'))) fail('stat id leaked into path page');
  await p.click('#lang'); const hi = await go('#/quiz/0'); if (!/हम क्यों पूछते हैं: [^A-Za-z]{5}/.test(hi)) fail('Hindi quiz leaks English in why-we-ask');
  const need = ['start', 'quiz_done', 'results_viewed', 'path_picked', 'plan_viewed', 'signin_started', 'signed_in', 'proof_added', 'public_on'];
  for (const e of need) if (!events.includes(e)) fail('event not fired: ' + e);
  if (errs.length) fail('console/page errors: ' + errs.join(' | '));
  console.log('SMOKE OK · events:', events.length, '· pages: 14');
  await b.close();
})().catch(e => fail(e.message));
