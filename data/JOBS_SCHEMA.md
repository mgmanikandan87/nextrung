# Job postings schema (v0.2)

`data/jobs/<path_id>.json`: a JSON array of fresher / entry-level postings seen this quarter, each mapped to the skills taxonomy so the platform can compute "you meet n of m requirements at this employer".

```json
{
  "id": "indeed:JOBSEARCH_57",            // source:source_id, stable within a run
  "path_id": "ai_native_software",
  "title": "Junior AI Engineer",
  "company": "mhtechin",
  "location": "Bengaluru, Karnataka",
  "posted_on": "2026-07-09",
  "url": "https://to.indeed.com/aa7ntxbfdz7z",     // keep intact, never strip parameters
  "fresher": true,                                 // 0 to 1 year, trainee, GET, fresher, entry, new grad, campus
  "experience_text": "0-1 years",                  // as written, or null
  "salary_text": null,                             // as written, or null
  "requirements": [
    {"text": "Python", "skill_id": "python"},
    {"text": "Exposure to LangChain or LlamaIndex", "skill_id": "llm_apis_and_agents"},
    {"text": "Knowledge of Ayurveda products", "skill_id": null}   // no taxonomy match: keep the text, skill_id null
  ],
  "source": "indeed",
  "seen_on": "2026-09-21"
}
```

Rules:
- Only postings that a 2026 graduate can apply to: fresher, 0 to 1 years, trainee, graduate engineer trainee, entry level, new grad, campus. Drop anything asking 2+ years.
- `requirements` come from the posting's own text (the details page), 4 to 12 items, each mapped to the closest skill id in `data/skills.json` or `null`. Never invent a requirement the posting did not state.
- One posting per (company, title); prefer the most recent.
- Every `url` must be the posting's own apply/view link as returned by the source.
