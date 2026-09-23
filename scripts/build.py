#!/usr/bin/env python3
"""Build the site: inline data JSON into the template.

Outputs:
  site/index.html  - page body for the claude.ai Artifact publish (no doctype/html/head/body; the host wraps it)
  dist/index.html  - standalone page for GitHub Pages or any static host
"""
import json, pathlib, glob

ROOT = pathlib.Path(__file__).resolve().parent.parent
paths = [json.load(open(f)) for f in sorted(glob.glob(str(ROOT / 'data/paths/*.json')))]
json.dump(paths, open(ROOT / 'data/paths.json', 'w'), indent=1, ensure_ascii=False)
skills = []
for f in sorted(glob.glob(str(ROOT / 'data/skills/*.json'))):
    skills += json.load(open(f))
json.dump({'version': '0.2.0', 'skills': skills}, open(ROOT / 'data/skills.json', 'w'), indent=1, ensure_ascii=False)
jobs = []
for f in sorted(glob.glob(str(ROOT / 'data/jobs/*.json'))):
    jobs += json.load(open(f))
# Demand: for each path skill, the share of that path's fresher ads that ask for it (distinct per ad). Drives Month-1 ordering.
for p in paths:
    ads = [j for j in jobs if j.get('path_id') == p['id']]
    for x in p.get('skills', []):
        n = sum(1 for j in ads if any(r.get('skill_id') == x['id'] for r in j.get('requirements', [])))
        x['demand'] = round(n / len(ads), 2) if ads else 0
json.dump(paths, open(ROOT / 'data/paths.json', 'w'), indent=1, ensure_ascii=False)
json.dump({'version': '0.2.0', 'seen_on': '2026-09-21', 'postings': jobs}, open(ROOT / 'data/jobs.json', 'w'), indent=1, ensure_ascii=False)
# Skill checks: full banks (with answers) go only into the Lambda bundle; the site learns which skills have a check.
banks = {}
for f in sorted(glob.glob(str(ROOT / 'data/checks/*.json'))):
    b = json.load(open(f)); banks[b['skill_id']] = b
json.dump(banks, open(ROOT / 'deploy/lambda/checks.json', 'w'), ensure_ascii=False, separators=(',', ':'))
data = {
    'paths': paths,
    'checks': {k: len(v['items']) for k, v in banks.items()},
    'questions': json.load(open(ROOT / 'data/questions.json')),
    'stats': json.load(open(ROOT / 'data/market_stats.json')),
    'skills': {'version': '0.2.0', 'skills': skills},
    'jobs': {'version': '0.2.0', 'seen_on': '2026-09-21', 'postings': jobs},
}
blob = json.dumps(data, ensure_ascii=False, separators=(',', ':')).replace('</', '<\\/')
tpl = (ROOT / 'site/template.html').read_text()
assert '/*__DATA__*/' in tpl
body = tpl.replace('/*__DATA__*/', blob)
(ROOT / 'site/index.html').write_text(body)
dist = ROOT / 'dist'; dist.mkdir(exist_ok=True)
(dist / 'index.html').write_text(
    '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
    '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"></head>'
    '<body style="margin:0">\n' + body + '\n</body></html>')
print(f'built: {len(paths)} paths, {len(data["stats"])} stats, {len(skills)} skills, {len(jobs)} jobs, {len(banks)} check banks, {len(body)//1024} KB')
