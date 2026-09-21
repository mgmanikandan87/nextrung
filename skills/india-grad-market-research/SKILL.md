---
name: "india-grad-market-research"
description: "Use when refreshing sourced statistics on India's engineering graduate supply, IT/GCC/core-sector fresher demand, or AI impact on entry-level jobs; emits market_stat JSON records with URLs and confidence."
---

# India graduate market research

You produce `market_stat` records for the engineering-graduates directional-growth platform. Every number must be traceable to a page you actually opened. A number without a URL is not a result; drop it and record the gap.

## Inputs

- A metric list (below is the default; the caller may pass a subset or additions).
- Optionally the previous `market_stats.json`, so you can mark what changed.

## Method

1. For each metric, search, then OPEN the page (WebFetch) you take the number from. A search snippet is never a source.
2. Prefer sources in this order: official (AICTE dashboard, AISHE/MoE, PIB, PLFS/MoSPI, company filings and fact sheets, regulator releases) > industry body or credible analyst (NASSCOM, Zinnov, CEEW, Stanford, TeamLease, Quess, Naukri JobSpeak, foundit, Xpheno, Mercer-Mettl, Wheebox) > media reporting an official figure > edtech or aggregator blogs (use only when nothing better exists, and mark `confidence: low`).
3. Record `confidence` as `official`, `industry`, `media` or `low`. Record the period the figure describes (fiscal or calendar, with months) and the `as_of` date you fetched it.
4. When two sources disagree, keep BOTH records and add a `contradiction` note naming the other record. Never average or pick silently. Known traps: AICTE fill rate (all-programme vs B.Tech-only cuts); "1.5 million engineers a year" (includes diplomas; AISHE B.Tech out-turn is ~8 lakh); headline job projections ("1 million semiconductor jobs") vs delivered numbers; Naukri vs foundit direction disagreements; company "offers made" vs "hiring target".
5. Fiscal labels get calendar months once: "FY26 (Apr 2025 to Mar 2026)".
6. Do not enrich: no derived percentages unless you show the two source numbers and the arithmetic in `derivation`.
7. Blocked or paywalled page: try one alternate source; if still blocked, emit a `gap` record, not a guess.

## Default metric list

Supply: AICTE approved B.Tech seats and enrolment by year; fill rate; branch-wise enrolment (CS and allied, ECE, EE, Mech, Civil, other); AISHE B.E./B.Tech out-turn; diploma out-turn; India Skills Report employability (overall, B.E./B.Tech, CS/IT, diploma); Mercer-Mettl Graduate Skill Index (overall, tier-1/2/3, key roles); PLFS unemployment (overall, youth 15-29, graduates); IIT/NIT placement rates from RTI reporting.

Demand: NASSCOM industry headcount and net additions; fresher intake and headcount change for TCS, Infosys, Wipro, HCLTech, Tech Mahindra, Cognizant, Accenture India; fresher CTC at IT services, GCC, product, AI startups; GCC count, headcount, fresher share of GCC hiring; Naukri JobSpeak (overall, fresher, IT, AI/ML, GCC); foundit and Xpheno entry-level openings; TeamLease fresher hiring intent; AI impact evidence (Stanford Canaries, SignalFire, any India-specific study); latest frontier model releases relevant to coding and computer use.

Paths: semiconductor jobs (government statements vs projections, units in production, C2S trained); EV and auto hiring; defence production and private share, space startups, ISRO/DRDO/HAL/BEL intake; clean-energy and infrastructure jobs; government engineer posts per year (UPSC ESE, SSC JE, RRB JE, PSU via GATE) and GATE applicants/qualifiers; higher-studies flows (US F-1 refusals, Germany/Ireland enrolment, CAT takers); DPIIT startups and startup fresher share; NATS/NAPS apprenticeship numbers and stipend; skilling programme enrolments.

## Output

A JSON array, one object per figure:

```json
{
  "id": "supply.aishe.btech_outturn.2023_24",
  "metric": "B.E./B.Tech pass-outs",
  "value": 820000,
  "unit": "people",
  "period": "academic 2023-24",
  "geography": "India",
  "source_title": "AISHE report 2023-24, Ministry of Education",
  "source_url": "https://...",
  "confidence": "official",
  "as_of": "2026-09-21",
  "derivation": null,
  "contradiction": null,
  "changed_since_last": true
}
```

Follow the array with a short plain-text brief (under 300 words): what changed since the last run, contradictions found, and gaps. No padding, no adjectives.