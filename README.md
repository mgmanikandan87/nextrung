# Disha for Engineers

Evidence-first directional guidance for Indian engineering graduates in the AI era. Open source.

India produces about 8 lakh B.E./B.Tech graduates a year (AISHE 2023-24). IT services, the door most of them were told to walk through, hired 1.6 lakh freshers in FY24, down from 4 lakh in FY22, and the top five firms plan under 1 lakh for FY27. AI agents now do the routine work that was the first rung. At least 6 lakh graduates a year need a direction that is not "join an IT services company". This project gives them one, with the numbers and where they came from.

## What it does

A graduate answers ten questions. A rules engine (not a language model) scores nine paths on realism plus fit: a base score for how many fresher seats the door has this year, plus human-written fit rules shown on every path page. The top three come with the rules that fired, a two-year plan, one evidenced project, employers hiring freshers now, and a fallback path that shares most of the preparation.

The nine paths: AI-native software · IT services · Semiconductor and electronics · EV and automotive · Defence, aerospace and space · Infrastructure and energy · Government and PSU · Higher studies · Startups and non-engineering roles.

## Repository layout

```
data/
  market_stats.json     108 sourced statistics; every record has a URL, period, confidence, and contradictions noted
  paths/<id>.json       one record per path (door size, pay, branches, plan, project, AI task shift, employers, fit rules)
  paths.json            merged by the build
  questions.json        the ten profile questions and their value ids
  PATH_SCHEMA.md        the path record contract
site/template.html      the single-page app (vanilla JS, no build tooling); data is inlined at build time
scripts/build.py        builds site/index.html (for claude.ai artifact) and dist/index.html (any static host)
skills/                 the three agent skills that maintain the content
```

## The three agent skills

Each is a `SKILL.md` any Claude agent (or another agent runner) can load.

- `india-grad-market-research` refreshes `market_stats.json`: opens the primary page for every figure, records confidence, keeps both sides of a contradiction, emits a gap instead of a guess. Run quarterly.
- `career-path-mapper` maintains the nine path records from the stats: door size, pay band, two-year plan, evidenced project, employers with a page seen in the last six months, and the matcher's fit rules.
- `grad-guide-writer` writes graduate-facing pages in plain English for a tier-2/3 reader, every number linked to its stat id.

Intended loop: a scheduled run of the research skill opens a pull request with changed numbers; maintainers review; the mapper updates paths; the site rebuilds.

## Build

```
python3 scripts/build.py
```

No dependencies. `dist/index.html` runs from any static host or a local file.

## Matcher

`score(path) = base_score + sum(score of every fit rule matched by the profile)`. Multi-select fields (interests, preparation) match each selected value. Rules live in each path file with a plain-English reason; the site shows them. Tuned so far on five sample profiles (see `scripts/`); tuning is a review decision, not a model output.

## Honesty rules

- No number without a URL. Gaps are recorded as gaps.
- Projections are labelled projections; "1 million semiconductor jobs by 2026" sits next to the minister's own 50,000 to 60,000.
- Two credible sources that disagree are both shown (AICTE fill rate 75% vs 84%; Naukri fresher +15% vs foundit -9%).
- A shrinking door is called shrinking in the first sentence.

## Contributing

Fix a number with a source. Add an employer with a fresher-hiring page from the last six months. Challenge a fit rule with a reason. Translate a path page into Hindi, Tamil, Telugu or Kannada (keep numbers and exam names in English). Placement officers, hiring managers and recent graduates are the reviewers this project needs most.

## Licence

Code: MIT. Content and data: CC BY 4.0.
