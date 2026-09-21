---
name: "career-path-mapper"
description: "Use when creating or updating the directional career path records (door size, pay, branches, two-year plan, evidenced project, fit rules) for the Indian engineering graduates platform from market_stat data."
---

# Career path mapper

You maintain the `path` records that the platform's matcher scores against a graduate's profile. Each path is a direction an Indian engineering graduate can realistically take in the next two years, described honestly: how big the door is, what it pays, who it suits, and what gets you in.

## Inputs

- `market_stats.json` from the india-grad-market-research skill (every number you use must cite a `market_stat.id`).
- The current `paths.json`, if one exists. Update in place; do not rewrite text that is still true.

## The nine paths (add a tenth only with evidence of a distinct door)

1. `ai_native_software` (AI application dev, data engineering, MLOps, platform, cybersecurity at GCCs, product companies, startups)
2. `it_services` (mass recruiters)
3. `semiconductor_electronics` (design, verification, DFT, fab process, ATMP)
4. `ev_automotive` (EV design, battery/BMS, embedded, manufacturing)
5. `defence_aerospace_space`
6. `infrastructure_energy` (civil, EPC, railways, metro, solar/wind, grid)
7. `government_psu` (ESE, SSC JE, RRB JE, PSU via GATE, state PSCs)
8. `higher_studies` (M.Tech, MS abroad, MBA)
9. `startups_and_non_engineering` (startup roles, founding, sales engineering, ops, banking and fintech ops, analytics)

## Rules

- Door size is people per year, with the `market_stat.id` it comes from and a one-line caveat when the number is a projection ("Adecco projection, 85-90% indirect jobs").
- Pay band is fresher CTC in LPA, low to high, with source id. Never a single number.
- `branches` lists which branches realistically enter, with a fit score 0-3 per branch (CS, IT, ECE, EE, Mech, Civil, Chem, other).
- `tiers_realistic` says honestly whether tier-2/3 graduates get in without a top-college brand, and what substitutes for it.
- `two_year_plan` is four half-year blocks. Each block: what to learn, what to build, what evidence to produce, which employers or exams to target. Concrete tools and topics, not "learn AI".
- `evidenced_project_spec` is one project the graduate can explain for 30 minutes: scope, the domain it lives in, how agents are used in building it, what "done" looks like, how it is verified.
- `entry_requirements` is what employers actually screen for now, taken from hiring reports and job postings, not from college syllabi.
- `fallback_path_id` names the adjacent path sharing at least 60% of the preparation.
- `employers` is 10 named employers or exam bodies hiring freshers in this path, each with a source URL seen in the last six months.
- `fit_rules` are matcher inputs: for each profile field (branch, college_tier, graduation_year, state, relocation, financial_runway_months, interests, constraints) a weight and the values that raise or lower the score, with a plain-English reason string the platform can show the graduate.
- The AI section of every path states which routine tasks in this path agents now do and which human-owned skills therefore matter more. Do not write "AI will not take your job" platitudes; describe the task shift.
- Where a path's door is shrinking, say so in the first sentence.

## Output

`paths.json`: an array of path objects with the fields above, plus `last_reviewed` and `changed_fields`. Then a brief (under 250 words): which paths changed, why, and which `market_stat` gaps blocked an update.

Also emit `questions.json` if it does not exist: about ten profile questions (branch, tier, year, state, relocation, runway, interests, constraints, current preparation, what they enjoyed most in college), each mapped to a profile field and the paths it discriminates between.