#!/usr/bin/env python3
"""Validate NextRung data before a build. Exits non-zero on any error. Run: python3 scripts/validate.py"""
import json, glob, re, sys, pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
errors, warnings = [], []
E = errors.append
W = warnings.append

def load(p):
    try:
        return json.load(open(p))
    except Exception as ex:
        E(f"{p}: invalid JSON ({ex})"); return None

# ---- market stats ----
stats = load(ROOT / 'data/market_stats.json') or []
sid = [s['id'] for s in stats]
if len(sid) != len(set(sid)): E('market_stats: duplicate ids ' + str([i for i in sid if sid.count(i) > 1]))
for s in stats:
    for k in ('id', 'metric', 'unit', 'period', 'confidence', 'as_of'):
        if k not in s: E(f"market_stats {s.get('id')}: missing {k}")
    if not s['id'].startswith('gap.') and not s.get('source_url'): E(f"market_stats {s['id']}: no source_url")
    if s.get('confidence') not in ('official', 'industry', 'media', 'low'): E(f"market_stats {s['id']}: bad confidence")
STAT_IDS = set(sid)

# ---- questions ----
q = load(ROOT / 'data/questions.json') or {'questions': []}
allowed = {}
for x in q['questions']:
    allowed[x['field']] = {str(o['value']) for o in x['options']}
    if not x.get('why'): W(f"question {x['id']}: no 'why'")
    if not x.get('prompt_hi'): W(f"question {x['id']}: no Hindi prompt")

# ---- skills ----
skills = []
for f in sorted(glob.glob(str(ROOT / 'data/skills/*.json'))):
    skills += load(f) or []
kid = [s['id'] for s in skills]
if len(kid) != len(set(kid)): E('skills: duplicate ids ' + str(sorted({i for i in kid if kid.count(i) > 1})))
for s in skills:
    if not re.match(r'^[a-z0-9_]+$', s['id']): E(f"skill {s['id']}: id not snake_case")
    if s.get('category') not in ('foundation', 'software', 'ai_data', 'hardware', 'core_eng', 'domain', 'professional', 'exam'): E(f"skill {s['id']}: bad category")
    if not 2 <= len(s.get('learning', [])) <= 3: E(f"skill {s['id']}: needs 2 or 3 learning options, has {len(s.get('learning', []))}")
    for l in s.get('learning', []):
        if not str(l.get('url', '')).startswith('http'): E(f"skill {s['id']}: learning url missing")
        if not l.get('proof_of_done'): E(f"skill {s['id']}: learning option without proof_of_done")
        if re.search(r'\b(complete|finish)\b.*\bcourse\b', str(l.get('proof_of_done', '')), re.I): W(f"skill {s['id']}: proof_of_done sounds like 'finish the course'")
        if not isinstance(l.get('hours'), (int, float)): E(f"skill {s['id']}: learning hours missing")
    if not s.get('learning') or not s['learning'][0].get('free', False): W(f"skill {s['id']}: first learning option is not free")
SKILL_IDS = set(kid)

# ---- paths ----
paths = []
for f in sorted(glob.glob(str(ROOT / 'data/paths/*.json'))):
    p = load(f)
    if not p: continue
    paths.append(p)
    for k in ('id', 'name', 'tagline', 'door_state', 'door_size', 'pay_band', 'branches', 'tiers_realistic', 'entry_requirements', 'two_year_plan', 'evidenced_project_spec', 'ai_task_shift', 'fallback_path_id', 'employers', 'fit_rules', 'base_score', 'skills'):
        if k not in p: E(f"path {p.get('id')}: missing {k}")
    if p.get('door_state') not in ('growing', 'steady', 'shrinking'): E(f"path {p['id']}: bad door_state")
    if len(p.get('two_year_plan', [])) != 4: E(f"path {p['id']}: two_year_plan needs 4 blocks")
    if not 0 <= p.get('base_score', -1) <= 10: E(f"path {p['id']}: base_score out of range")
    for r in p.get('fit_rules', []):
        if r['field'] not in allowed or str(r['value']) not in allowed[r['field']]: E(f"path {p['id']}: fit_rule {r['field']}={r['value']} not in questions.json")
        if not -3 <= r['score'] <= 3: E(f"path {p['id']}: fit_rule score out of range")
        if not r.get('reason'): E(f"path {p['id']}: fit_rule without reason")
    for x in p.get('skills', []):
        if x['id'] not in SKILL_IDS: E(f"path {p['id']}: skill {x['id']} not in taxonomy")
        if x.get('level') not in ('required', 'preferred'): E(f"path {p['id']}: skill {x['id']} bad level")
    if not 8 <= len(p.get('skills', [])) <= 18: W(f"path {p['id']}: {len(p.get('skills', []))} skills (expected 8 to 18)")
    for e in p.get('employers', []):
        if not str(e.get('source_url', '')).startswith('http'): E(f"path {p['id']}: employer {e.get('name')} without URL")
    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k == 'stat_ids':
                    for i in v:
                        if i not in STAT_IDS: E(f"path {p['id']}: stat id {i} not in market_stats")
                else: walk(v)
        elif isinstance(o, list):
            for v in o: walk(v)
    walk(p)
PATH_IDS = {p['id'] for p in paths}
for p in paths:
    if p.get('fallback_path_id') not in PATH_IDS: E(f"path {p['id']}: fallback {p.get('fallback_path_id')} unknown")
if len(paths) != 9: W(f"{len(paths)} paths (expected 9)")

# ---- jobs ----
jobs = []
for f in sorted(glob.glob(str(ROOT / 'data/jobs/*.json'))):
    jobs += load(f) or []
jid = [j['id'] for j in jobs]
if len(jid) != len(set(jid)): E('jobs: duplicate ids')
mapped = total = 0
for j in jobs:
    if j.get('path_id') not in PATH_IDS: E(f"job {j['id']}: unknown path {j.get('path_id')}")
    if not str(j.get('url', '')).startswith('http'): E(f"job {j['id']}: url missing")
    if j.get('fresher') is not True: E(f"job {j['id']}: not marked fresher")
    if not 1 <= len(j.get('requirements', [])) <= 12: W(f"job {j['id']}: {len(j.get('requirements', []))} requirements")
    for r in j.get('requirements', []):
        total += 1
        if r.get('skill_id'):
            mapped += 1
            if r['skill_id'] not in SKILL_IDS: E(f"job {j['id']}: requirement maps to unknown skill {r['skill_id']}")
if total and mapped / total < 0.6: W(f"jobs: only {mapped/total:.0%} of requirements mapped to skills")

# ---- skill checks (calibration item banks) ----
checks, n_items = 0, 0
for f in sorted(glob.glob(str(ROOT / 'data/checks/*.json'))):
    c = load(f)
    if not c: continue
    checks += 1
    sk = c.get('skill_id'); base = pathlib.Path(f).stem
    if sk != base: E(f"checks {base}: skill_id {sk!r} must equal the file name")
    if sk not in SKILL_IDS: E(f"checks {base}: unknown skill {sk}")
    items = c.get('items', [])
    if len(items) < 6: E(f"checks {base}: only {len(items)} items (need 6+)")
    if sum(1 for i in items if i.get('level') == 'applied') < 3: E(f"checks {base}: fewer than 3 applied items")
    ids = [i.get('id') for i in items]
    if len(ids) != len(set(ids)): E(f"checks {base}: duplicate item ids")
    qs = [i.get('q', '').strip().lower() for i in items]
    if len(qs) != len(set(qs)): E(f"checks {base}: duplicate questions")
    for i in items:
        n_items += 1
        if not str(i.get('id', '')).startswith(sk + '_'): E(f"checks {base}: id {i.get('id')} must start with {sk}_")
        if i.get('level') not in ('basic', 'applied'): E(f"checks {base} {i.get('id')}: level must be basic|applied")
        opts = i.get('options')
        if not isinstance(opts, list) or len(opts) != 4 or len(set(map(str, opts))) != 4: E(f"checks {base} {i.get('id')}: need exactly 4 distinct options")
        if not isinstance(i.get('answer'), int) or not 0 <= i['answer'] <= 3: E(f"checks {base} {i.get('id')}: answer must be 0..3")
        if not i.get('explain'): E(f"checks {base} {i.get('id')}: missing explain")
        if len(str(i.get('q', '')).split()) > 90: W(f"checks {base} {i.get('id')}: question is long")
        if any(re.search(r'\b(all|none) of the above\b', str(o), re.I) for o in (opts or [])): E(f"checks {base} {i.get('id')}: no 'all/none of the above'")

print(f"stats {len(stats)} · questions {len(q['questions'])} · skills {len(skills)} · paths {len(paths)} · jobs {len(jobs)} ({mapped}/{total} requirements mapped) · checks {checks} banks / {n_items} items")
for w in warnings: print('WARN', w)
for e in errors: print('ERROR', e)
print(f"{len(errors)} errors, {len(warnings)} warnings")
sys.exit(1 if errors else 0)
