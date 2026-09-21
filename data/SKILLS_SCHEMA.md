# Skills taxonomy schema (v0.2)

`data/skills/<group>.json` files, merged by the build into `data/skills.json`. Each skill is the hub that paths, learning units, job requirements and a graduate's evidence all reference.

```json
{
  "id": "python",                       // snake_case, stable, globally unique across all files
  "name": "Python",
  "category": "foundation",             // foundation | software | ai_data | hardware | core_eng | domain | professional | exam
  "description": "one line: what being able to do this looks like",
  "evidence_types": ["repo", "deployed_url", "cert", "score", "document"],   // what a graduate can attach as proof
  "learning": [
    {"platform": "NPTEL", "title": "Programming, Data Structures and Algorithms using Python", "url": "https://...", "free": true, "hours": 40,
     "proof_of_done": "one line: the artefact the learner must produce (a repo, a certificate id, a deployed URL, a score)"}
  ],
  "paths": ["ai_native_software", "it_services"]   // paths where this skill appears in the plan
}
```

Rules:
- 2 or 3 learning options per skill, free first. Allowed platforms and typical URLs: NPTEL/SWAYAM (nptel.ac.in, swayam.gov.in, onlinecourses.nptel.ac.in), FutureSkills Prime (futureskillsprime.in), freeCodeCamp, CS50 (cs50.harvard.edu), Coursera/edX audit tracks, Google (developers.google.com, skillshop, grow.google), AWS Skill Builder (skillbuilder.aws), Microsoft Learn, Kaggle Learn, MIT OCW, The Odin Project, roadmap.sh, Chips to Startup (c2s.gov.in), ISRO/DRDO/GATE official pages, YouTube channels only when they are the canonical course (e.g. CS50, Andrej Karpathy). Paid options only when no free equivalent exists; mark `free: false`.
- Every URL must be opened (WebFetch) and load as the course or page named. A URL that fails is dropped, never guessed.
- `hours` is the learner's estimate to the proof of done, not the course's advertised length.
- `proof_of_done` is always an artefact, never "complete the course".
- Keep names as employers write them in postings ("SQL", "REST APIs", "Verilog", "AutoCAD"), so job requirements map cleanly.

Path files gain a `skills` array: `[{"id": "python", "level": "required" | "preferred", "weight": 1-3}]`. `required` = employers filter on it; `preferred` = raises the offer probability. 8 to 14 skills per path.
