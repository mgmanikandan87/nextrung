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
data = {
    'paths': paths,
    'questions': json.load(open(ROOT / 'data/questions.json')),
    'stats': json.load(open(ROOT / 'data/market_stats.json')),
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
print(f'built: {len(paths)} paths, {len(data["stats"])} stats, {len(body)//1024} KB')
