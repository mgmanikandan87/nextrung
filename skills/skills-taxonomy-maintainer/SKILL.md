---
name: skills-taxonomy-maintainer
description: Use when adding or refreshing NextRung's skills taxonomy and free-first learning options (data/skills/*.json) or the skills arrays on path records; every learning URL is opened before it is listed.
---

# Skills taxonomy maintainer

You maintain `data/skills/*.json` (merged into `data/skills.json` by the build) and the `skills` array on each `data/paths/<id>.json`. The taxonomy is the hub: paths, learning options, job requirements and a graduate's proof all reference skill ids.

## Read first
- `data/SKILLS_SCHEMA.md` (shape and rules), `data/skills.json` (current ids; never rename an id, jobs and user proof reference them), the path files you touch.

## Rules
- 2 or 3 learning options per skill, free first. Prefer NPTEL/SWAYAM, freeCodeCamp, CS50, FutureSkills Prime, AWS Skill Builder, Microsoft Learn, Kaggle Learn, Google, The Odin Project, roadmap.sh, official docs, HDLBits, Chips to Startup, MathWorks Onramp, exam bodies' official pages. Paid only when no free equivalent exists, marked `free: false`.
- Open every URL (WebFetch) and keep it only if the page is the resource named. A JS-only shell that shows no course content does not count; use the provider's canonical course page (e.g. `nptel.ac.in/courses/<id>`) instead. Drop, never guess.
- `hours` is the learner's estimate to the proof, not the advertised course length. `proof_of_done` is an artefact (repo, live URL, certificate id, score screenshot), never "finish the course".
- Names as employers write them in postings so job requirements map cleanly.
- A path's `skills` array has 8 to 14 entries with `level` required (employers filter on it) or preferred, `weight` 1 to 3. Required skills are what a fresher posting for that path asks for in most ads; check `data/jobs/<path>.json` requirement frequencies before changing levels.
- Never delete a skill that a job requirement or path references; mark it `deprecated: true` and add a `replaced_by` id instead.

## Refresh run (quarterly)
1. Re-open every learning URL; replace dead ones. Record replacements in the brief.
2. Count, per path, which skill ids appear in this quarter's job requirements; propose level/weight changes with the counts.
3. Add new skills only when 3+ postings in a path ask for something the taxonomy lacks.
4. Run `python3 scripts/validate.py`; it must pass.

## Output
Changed JSON files, then a brief (under 250 words): URLs replaced, skills added or deprecated, level changes with counts, validator result.
