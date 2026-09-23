// Browser smoke test for the built site against a mocked backend. Exits 1 on any failure.
// Usage: node scripts/smoke.js [path/to/dist/index.html]
const { chromium } = require('playwright'); const fs = require('fs'); const path = require('path');
const DIST = process.argv[2] || path.join(__dirname, '..', 'dist', 'index.html');
const fail = (m) => { console.error('SMOKE FAIL:', m); process.exit(1); };
(async () => {
  const b = await chromium.launch(process.env.PW_CHROMIUM ? { executablePath: process.env.PW_CHROMIUM } : {}); const ctx = await b.newContext({ viewport: { width: 390, height: 844 } });
  const store = { user: { email: 't@x.in', name: 'Test', public: { enabled: false }, role: 'student', handles: {}, mentor: null }, evidence: {}, checks: {}, notes: [], checks_available: { python: 11, sql: 11, verilog_systemverilog: 11, digital_design_fundamentals: 11, embedded_c: 11, git_github: 11, matlab_simulink: 11, control_systems: 11 } }; const events = [];
  const mkItems = () => [1, 2, 3, 4, 5, 6].map(i => ({ id: 'python_0' + i, level: i < 5 ? 'applied' : 'basic', q: 'What does this print?\n\nprint(' + i + ' * 2)', options: [String(i * 2), String(i), String(i + 2), 'Error'] }));
  const student = () => ({ id: 's1', name: 'Asha', email: 'asha@x.in', college: 'GEC', primary_path: 'it_services', answers: { branch: 'CS', college_tier: '3' }, created: '2026-08-01T00:00:00Z', last_active: '2026-09-20T00:00:00Z', handles: { github: 'asha' }, focus_skill: null, public: { enabled: false }, evidence: { python: { type: 'repo', url: 'https://github.com/asha/p', added: '2026-09-01T00:00:00Z', self_level: 3, verify: { status: 'verified', detail: 'Your repository, 12 commits over 5 days.', facts: { commits: 12, commit_days: 5 } } } }, checks: { python: { score: 2, n: 6, pct: 33, predicted: 5, gap: 3, at: '2026-09-02T00:00:00Z', attempts: 1 } }, flags: [{ id: 'overconfident', text: 'python: predicted 5/6, scored 2/6' }], notes: [], link_status: 'active' });
  await ctx.route('https://api.test/**', async (route) => { const req = route.request(); const p = new URL(req.url()).pathname; const m = req.method(); let body = {}; try { body = req.postDataJSON() || {}; } catch (e) {}
    const ok = o => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(o) });
    if (p === '/events') { events.push(body.event); return ok({ ok: true }); }
    if (p === '/auth/start') return ok({ ok: true, session: 'S' }); if (p === '/auth/verify') return ok({ ok: true, id_token: 'T', refresh_token: 'R', user: store.user });
    if (p === '/me' && m === 'GET') return ok(store); if (p === '/me' && m === 'PUT') { Object.assign(store.user, body); return ok({ ok: true, user: store.user }); }
    if (p === '/me/evidence') { if (body.remove) delete store.evidence[body.skill_id]; else store.evidence[body.skill_id] = { ...body.evidence, added: '2026-01-01T00:00:00Z' }; return ok({ ok: true }); }
    if (p === '/me/public') { store.user.public = { enabled: body.enabled, slug: body.slug || 'test-1' }; return ok({ ok: true, public: store.user.public }); }
    if (p === '/me/evidence/verify') return ok({ ok: true, verify: { status: 'partial', detail: 'seen' } });
    if (p === '/me/mentor' && m === 'POST') { store.user.mentor = body.decline ? null : { id: 'm1', name: 'Ravi', status: 'active', since: '2026-09-21T00:00:00Z' }; return ok({ ok: true, mentor: store.user.mentor }); }
    if (p === '/me/mentor' && m === 'DELETE') { store.user.mentor = null; return ok({ ok: true }); }
    if (p.startsWith('/checks/') && m === 'GET') return ok({ skill_id: 'python', items: mkItems(), n: 6, last: null });
    if (p.startsWith('/checks/') && m === 'POST') { const r = { score: 4, n: 6, pct: 67, predicted: body.predicted, gap: body.predicted - 4, at: '2026-09-21T00:00:00Z', attempts: 1 }; store.checks.python = r; return ok({ ok: true, result: r, review: mkItems().map((it, i) => ({ id: it.id, q: it.q, your: it.options[1], correct: it.options[0], ok: i < 4, explain: 'Multiply.' })) }); }
    if (p === '/mentor/students' && m === 'GET') return ok({ students: [student()], mentor_code: 'ABC234', role: 'mentor' });
    if (p === '/mentor/students/s1' && m === 'GET') return ok({ student: student() });
    if (p.startsWith('/mentor/students/s1/')) { events.push('mentor_action:' + p.split('/').pop()); return ok({ ok: true, review: { verdict: body.verdict }, student: student() }); }
    if (p === '/admin/overview') return ok({ users: 3, roles: { student: 2, mentor: 1 }, active_7d: 2, paths: { it_services: 2 }, proofs: 4, proof_status: { verified: 2, partial: 1, unverified: 1 }, checks_taken: 1, mentor_links: 1, pending_links: 0, funnel: { '7d': { start: 5, quiz_done: 3 }, '30d': { start: 9 }, all: { start: 12 } }, sessions: { '7d': 5, '30d': 9, all: 12 }, version: '0.4.0' });
    if (p === '/admin/users' && m === 'GET') return ok({ users: [{ id: 's1', email: 'asha@x.in', name: 'Asha', role: 'student', primary_path: 'it_services', last_active: '2026-09-20T00:00:00Z', mentor: { name: 'Ravi', status: 'active' } }, { id: 'm1', email: 'ravi@x.in', name: 'Ravi', role: 'mentor', mentor_code: 'ABC234' }], total: 2 });
    if (p.startsWith('/admin/users/') && m === 'PUT') return ok({ ok: true });
    if (p === '/admin/assign') return ok({ ok: true, mentor: { status: 'pending' } });
    if (p === '/admin/digest') return ok({ mentors: [{}], students: [], email_enabled: false });
    if (p.startsWith('/public/')) return ok(store); return route.fulfill({ status: 404, body: '{}' }); });
  const p = await ctx.newPage(); const errs = []; p.on('pageerror', e => errs.push(e.message)); p.on('console', m => { if (m.type() === 'error' && !/ERR_TUNNEL|ERR_NAME|fonts|net::/.test(m.text())) errs.push(m.text()); });
  const tmp = path.join(require('os').tmpdir(), 'nextrung-smoke.html');
  fs.writeFileSync(tmp, fs.readFileSync(DIST, 'utf8').replace('__ENDPOINT__', 'https://api.test/'));
  const SHOTS = process.env.SHOTS; const shot = async (n) => { if (SHOTS) await p.screenshot({ path: path.join(SHOTS, n + '.png'), fullPage: true }); };
  const go = async (h) => { await p.goto('file://' + tmp + h); await p.waitForTimeout(400); await shot(h.replace(/[^a-z0-9]+/gi, '_')); return p.evaluate(() => document.querySelector('#app').innerText); };
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
  await go('#/me/plan'); const plan3 = await p.evaluate(() => { document.querySelectorAll('details').forEach(d => d.open = true); return document.querySelector('#app').innerText; }); if (!/verified|seen|link broken|checking|not checked/.test(plan3)) fail('proof card missing verification label');
  const chk = await p.$('[data-check]'); if (!chk) fail('no "check where I stand" button on the plan'); const cs = await chk.getAttribute('data-check'); await chk.click(); await p.waitForTimeout(500);
  let ct = await p.evaluate(() => document.querySelector('#app').innerText); if (!/how many of the 6/i.test(ct)) fail('check intro/predict missing');
  await p.click('#pred button[data-n="5"]'); await shot('check_predict'); await p.click('#go'); await p.waitForTimeout(300); await shot('check_items');
  for (let i = 0; i < 6; i++) { const b = await p.$$('.copt'); await b[i * 4].click(); await p.waitForTimeout(80); }
  await p.click('#submit'); await p.waitForTimeout(500); ct = await p.evaluate(() => document.querySelector('#app').innerText); if (!/You got 4 of 6/.test(ct) || !/Where it went wrong/.test(ct)) fail('check result missing: ' + ct.slice(0, 200)); await shot('check_result');
  await go('#/me/settings'); await p.fill('#handles input[name=github]', 'tester'); await p.click('#handles button[type=submit]'); await p.waitForTimeout(300);
  await p.fill('#m-link input[name=code]', 'abc234'); await p.click('#m-link button[type=submit]'); await p.waitForTimeout(400); const st = await go('#/me/settings'); if (!/Your mentor:?\s*Ravi/i.test(st)) fail('mentor link not shown in settings: ' + st.slice(0, 300));
  store.user.role = 'mentor'; store.user.mentor_code = 'ABC234'; const mp = await go('#/mentor'); if (!/Your students · 1/.test(mp) || !/predicted 5\/6/.test(mp)) fail('mentor list missing student or flags: ' + mp.slice(0, 300));
  const ms = await go('#/mentor/s1'); if (!/Standing/.test(ms) || !/self: alone/.test(ms) || !/2\/6 \(predicted 5, over\)/.test(ms)) fail('mentor student page missing standing: ' + ms.slice(0, 400));
  await p.click('[data-rev="redo"]'); await p.fill('form[data-rf="python"] input[name=comment]', 'Add tests'); await p.click('form[data-rf="python"] button[type=submit]'); await p.waitForTimeout(400); if (!events.includes('mentor_action:review')) fail('review not sent');
  await p.fill('#note textarea', 'Do the SQL check this week'); await p.click('#note button[type=submit]'); await p.waitForTimeout(400); if (!events.includes('mentor_action:note')) fail('note not sent');
  store.user.role = 'admin'; const ad = await go('#/admin'); if (!/Funnel/.test(ad) || !/asha@x.in/.test(ad) || !/accounts/.test(ad)) fail('admin page missing: ' + ad.slice(0, 300));
  store.user.role = 'student';
  const jobs = await go('#/me/jobs'); if (!/Open the ad/.test(jobs)) fail('jobs for me missing ads');
  await go('#/me/settings'); await p.check('#pub'); await p.waitForTimeout(500); const pub = await go('#/p/test-1'); if (!/Proof/.test(pub)) fail('public page missing');
  for (const r of ['#/paths', '#/path/it_services', '#/jobs', '#/sources', '#/about']) { const t = await go(r); if (t.length < 200) fail('thin page ' + r); }
  if (/\b(supply|demand|paths|gap)\.[a-z_]+\./.test(await go('#/path/it_services'))) fail('stat id leaked into path page');
  await p.click('#lang'); const hi = await go('#/quiz/0'); if (!/हम क्यों पूछते हैं: [^A-Za-z]{5}/.test(hi)) fail('Hindi quiz leaks English in why-we-ask');
  const need = ['start', 'quiz_done', 'results_viewed', 'path_picked', 'plan_viewed', 'signin_started', 'signed_in', 'proof_added', 'public_on', 'check_started', 'check_done', 'handle_set', 'mentor_linked', 'mentor_review', 'mentor_note', 'admin_viewed'];
  for (const e of need) if (!events.includes(e)) fail('event not fired: ' + e);
  if (errs.length) fail('console/page errors: ' + errs.join(' | '));
  console.log('SMOKE OK · events:', events.filter(e => !/^mentor_action/.test(e)).length, '· pages: 19');
  await b.close();
})().catch(e => fail(e.message));
