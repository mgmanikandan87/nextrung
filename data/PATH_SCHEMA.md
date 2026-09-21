# Path record schema (v0.1)

One JSON object per path, saved as `data/paths/<id>.json`. Every number cites a `market_stat` id from `data/market_stats.json` in a `stat_ids` array next to it. No number without a stat id; if no stat exists, write the claim as text and set `stat_ids: []` with a `caveat`.

```json
{
  "id": "semiconductor_electronics",
  "name": "Semiconductor and electronics",
  "tagline": "one line, under 90 chars, honest about the door",
  "door_state": "growing | steady | shrinking",
  "door_size": {"people_per_year": 60000, "stat_ids": ["paths.semiconductor.direct_jobs_govt_statement.2026"], "caveat": "one line: projection vs delivered, direct vs indirect"},
  "pay_band": {"low_lpa": 4, "high_lpa": 6, "stat_ids": ["paths.semiconductor.fresher_ctc_bands.2026"], "note": "one line on the spread, e.g. top design houses pay 16 to 30 for the top few"},
  "branches": {"CS": 1, "IT": 0, "ECE": 3, "EE": 3, "MECH": 1, "CIVIL": 0, "CHEM": 2, "OTHER": 0},
  "tiers_realistic": {"tier1": "text", "tier2": "text", "tier3": "text: honest yes/no and what substitutes for the brand"},
  "entry_requirements": ["4 to 7 concrete things employers screen for now"],
  "two_year_plan": [
    {"block": "H1", "title": "short", "learn": ["..."], "build": ["..."], "evidence": ["..."], "target": ["employers or exams"]},
    {"block": "H2", ...}, {"block": "H3", ...}, {"block": "H4", ...}
  ],
  "evidenced_project_spec": {"title": "", "scope": "", "domain": "", "agent_use": "how coding/research agents are used in building it", "done_looks_like": "", "verification": ""},
  "ai_task_shift": [{"task_2019": "", "now": "", "human_owns": ""}],
  "fallback_path_id": "ev_automotive",
  "employers": [{"name": "", "type": "company | exam | scheme", "location": "", "roles": "", "fresher": true, "source_url": "", "seen_on": "2026-09"}],
  "fit_rules": [
    {"field": "branch", "value": "ECE", "score": 3, "reason": "plain-English reason shown to the graduate"},
    {"field": "relocation", "value": "hometown_only", "score": -2, "reason": "..."}
  ],
  "base_score": 5,
  "stat_ids_used": ["..."],
  "last_reviewed": "2026-09-21",
  "changed_fields": ["all"]
}
```

Fields and values for `fit_rules` come from `data/questions.json`: `branch` (CS, IT, ECE, EE, MECH, CIVIL, CHEM, OTHER), `college_tier` (1, 2, 3), `graduation_year` (graduated, 2026, 2027, 2028_or_later), `region` (south, west, north, east_ne, central), `relocation` (anywhere_india, abroad_ok, within_state, hometown_only), `runway_months` (0_3, 4_12, 12_plus), `interests` (building_software, hardware_electronics, physical_systems, data_numbers, people_business, research_theory), `priority` (income_now, stability, high_pay_growth, meaningful_domain, abroad), `preparation` (dsa, projects_portfolio, gate_prep, core_tools, internship_done, none), `enjoyed` (coding_projects, labs_hardware, workshops_site, maths_analysis, organising_people, exams_theory).

Scores run -3 to +3. Write a rule only where the value changes the answer; silence means 0. Every rule carries a reason the graduate can read. `base_score` (0 to 10) reflects how many fresher seats the door has this year, so the ranking is realism plus fit, not fit alone.

Matcher: `score(path) = base_score + sum(rule.score for matched rules)`. Multi-value fields (interests, preparation) match each selected value.
