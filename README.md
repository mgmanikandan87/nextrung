# NextRung

Evidence-first directional guidance for Indian engineering graduates in the AI era. Open source.

India produces about 8 lakh B.E./B.Tech graduates a year (AISHE 2023-24). IT services, the door most of them were told to walk through, hired 1.6 lakh freshers in FY24, down from 4 lakh in FY22, and the top five firms plan under 1 lakh for FY27. AI agents now do the routine work that was the first rung. At least 6 lakh graduates a year need a direction that is not "join an IT services company". This project gives them one, with the numbers and where they came from.

## What it does

A graduate answers ten questions. A rules engine (not a language model) scores nine paths on realism plus fit: a base score for how many fresher seats the door has this year, plus human-written fit rules shown on every path page. The top three come with the rules that fired, a two-year plan, one evidenced project, employers hiring freshers now, and a fallback path that shares most of the preparation.

The nine paths: AI-native software · IT services · Semiconductor and electronics · EV and automotive · Defence, aerospace and space · Infrastructure and energy · Government and PSU · Higher studies · Startups and non-engineering roles.

With an account (email code, no password) the graduate saves the plan, adds proof links per skill, and watches the match with real fresher job ads. v0.4 adds the guardrails and the people: proof links are verified where the source allows it (GitHub commit history and ownership, LeetCode solved counts, live certificate pages), each skill has a six-question calibration check where the student predicts their score first, and a **mentor** (linked by code, or assigned by the admin with the student's consent) sees standing, flags and gaps, reviews proofs, confirms skills in person and sets the week's focus. An **admin** console shows the funnel, people, roles and mentor assignments. See `review/v0.4-mentor-and-guardrails.md` for the reasoning.

## Repository layout

```
data/
  market_stats.json     108 sourced statistics; every record has a URL, period, confidence, and contradictions noted
  paths/<id>.json       one record per path (door size, pay, branches, plan, project, AI task shift, employers, fit rules)
  paths.json            merged by the build
  questions.json        the ten profile questions and their value ids
  skills/, jobs/        skills taxonomy with free learning links; fresher job ads mapped to skill ids
  checks/<skill>.json   calibration item banks (answers never reach the browser); see CHECKS_SCHEMA.md
  PATH_SCHEMA.md        the path record contract (also SKILLS_SCHEMA.md, JOBS_SCHEMA.md, CHECKS_SCHEMA.md)
site/template.html      the single-page app (vanilla JS, no build tooling); data is inlined at build time
scripts/build.py        builds site/index.html (for claude.ai artifact), dist/index.html (any static host) and deploy/lambda/checks.json
scripts/test_handler.py offline tests for the API (in-memory DynamoDB stand-in, stubbed network)
deploy/                 deploy.sh (idempotent AWS deploy), lambda/handler.py (the API), aws-github-oidc.sh (CI role)
skills/                 the five agent skills that maintain the content
```

## The five agent skills

Each is a `SKILL.md` any Claude agent (or another agent runner) can load.

- `india-grad-market-research` refreshes `market_stats.json`: opens the primary page for every figure, records confidence, keeps both sides of a contradiction, emits a gap instead of a guess. Run quarterly.
- `career-path-mapper` maintains the nine path records from the stats: door size, pay band, two-year plan, evidenced project, employers with a page seen in the last six months, and the matcher's fit rules.
- `grad-guide-writer` writes graduate-facing pages in plain English for a tier-2/3 reader, every number linked to its stat id.
- `skills-taxonomy-maintainer` keeps `data/skills/` and each path's skills array current; every learning link opened before it is listed.
- `fresher-jobs-ingest` rebuilds `data/jobs/` each quarter from career pages, notifications and a job connector when one is available, mapping requirements to skill ids.

The loop is automated: `.github/workflows/refresh.yml` runs the skills quarterly and opens a pull request; `.github/workflows/ci.yml` validates, builds, smoke-tests and deploys on merge. See `deploy/README.md`.

## Build and test

```
python3 scripts/validate.py       # data checks incl. check banks; must pass
python3 scripts/build.py          # dist/index.html + deploy/lambda/checks.json
python3 scripts/test_handler.py   # API tests, no AWS needed
node scripts/smoke.js             # browser smoke test (needs `npm i playwright@1.49.1`; PW_CHROMIUM=/path/to/chromium to reuse a browser)
```

`dist/index.html` runs from any static host or a local file.

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

## Deployed (v0.4, 21 Sep 2026)

- Site: https://main.d1pntf15nafb3u.amplifyapp.com (AWS Amplify Hosting, ap-south-1)
- API: API Gateway HTTP API `nextrung` → Lambda `nextrung-api` → DynamoDB `nextrung` (per-user) and `nextrung-responses` (anonymous, legacy)
- Accounts: Cognito user pool `nextrung`, passwordless email OTP (Essentials tier; default sender is limited to ~50 emails/day, move to SES for scale)
- Live data: 83 skills with 229 learning options (223 free), 105 fresher postings mapped to skills (`data/jobs/`), refreshed by agent runs
- Roles: admin emails live in SSM `/nextrung/admin_emails` (comma separated); admins promote mentors from the Admin page. Optional SES sender in SSM `/nextrung/ses_from` turns on mentor/student emails and the weekly digest (`/admin/digest`).
- Redeploy: push to `main` (GitHub Actions deploys), or bundle `deploy/deploy.sh`, `deploy/lambda/handler.py`, `deploy/lambda/checks.json`, `dist/index.html` → `deploy/site/index.html`; upload to CloudShell; `bash deploy.sh`. See `deploy/README.md`.
