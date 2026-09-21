---
name: fresher-jobs-ingest
description: Use when refreshing NextRung's fresher job postings (data/jobs/<path>.json), mapping each posting's requirements onto the skills taxonomy so the site can show per-employer match.
---

# Fresher jobs ingest

You produce `data/jobs/<path_id>.json` for each of the nine paths: fresher / entry-level postings seen this quarter, each with requirements mapped to `data/skills.json` ids.

## Read first
- `data/JOBS_SCHEMA.md` (shape and rules), `data/skills.json` (ids to map to), `data/paths.json` (path ids and names).

## Sources, in order
1. An Indeed connector if the session has one (`search_jobs` with `country_code: "IN"`, then `get_job_details` for the description). It rate-limits; when it does, do not retry more than twice.
2. Company career pages and ATS pages (Workday, SuccessFactors, Oracle, Lever, Greenhouse), opened with WebFetch. Source = `career_page`.
3. Official recruitment notifications for government/PSU and higher-studies "openings" (UPSC, SSC, RRB, PSUs, ISRO, GATE, COAP, CCMT, DAAD). Source = `notification`.
4. Aggregators (Internshala, off-campus job blogs) only when the official page is JS-gated; keep the official apply URL as `url` and note the mirror in `experience_text`. Source = `internshala` or `career_page`.

## Rules
- Fresher only: 0 to 1 year, trainee, GET, entry level, new grad, campus. Drop 2+ years.
- Requirements come from the posting's own text, 4 to 12 items, each mapped to the closest skill id or `null`. Never invent one. Dedupe (company, title).
- `posted_on` as ISO date when the source gives one; otherwise null (never guess). `seen_on` = today.
- Keep `url` intact. Record `salary_text` and `experience_text` as written.
- 8 to 15 postings per path; for government_psu and higher_studies, 6 to 12 notifications.
- Flag in the brief: unpaid or sub-₹15,000/month "fresher" ads, and any path where fewer than 8 postings were found.

## After writing
Run `python3 scripts/validate.py`; every non-null skill_id must exist. Then the brief (under 250 words): counts per path, share of requirements mapped, sources used, rate limits hit, gaps.
