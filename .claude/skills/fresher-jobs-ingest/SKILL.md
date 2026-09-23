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
- A posting without a date is dropped, not stored with `posted_on: null`; a career page that shows no date is still usable if the page shows "posted N days ago" or a requisition date.
- At most 2 postings per company per path (the 2026-09 set had four Airbus ads in one route), and at least 3 postings per path located in the North and 3 in the East / North-East / Central regions, or state in the brief that none were found after searching. Southern cities are over-represented otherwise and eastern students see an empty "near you" list.
- Tag every requirement line with `kind`: `skill` (maps or could map to a skill id), `degree` (eligibility such as "B.E./B.Tech in ECE"), or `behavioural` ("team player", "willingness to learn"). Only `skill` lines count toward the match; the site hides the rest behind "+N".
- Parse pay into `salary_lpa: {low, high}` (lakh per year; monthly figures ×12) whenever the text carries a number; keep `salary_text` as written.
- Flag in the brief: unpaid or sub-₹15,000/month "fresher" ads, any path where fewer than 8 postings were found, and any required plan skill that appears in fewer than 15% of the path's ads (compare with `data/paths/<id>.json` skills; the 2026-09-22 audit in review/ shows the method).

## After writing
Run `python3 scripts/validate.py`; every non-null skill_id must exist. Then the brief (under 250 words): counts per path, share of requirements mapped, sources used, rate limits hit, gaps.
