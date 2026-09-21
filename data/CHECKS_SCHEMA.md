# Skill check item banks (v0.4)

`data/checks/<skill_id>.json`, one file per skill in `data/skills.json`. The build copies all banks (with answers) into the Lambda bundle (`deploy/lambda/checks.json`) and inlines only `{skill_id: item_count}` into the site, so answers never reach the browser.

A check is a **calibration signal, not a certificate**: six random items from the bank, the student predicts their score first, and the gap between predicted and actual is what the mentor sees. Retakes are allowed after 24 hours.

```json
{
  "skill_id": "python",
  "version": "2026-09",
  "items": [
    {
      "id": "python_01",                       // <skill_id>_<2 digits>, unique within the file
      "level": "basic" | "applied",            // basic = can read it; applied = can predict or fix it
      "q": "What does this print?\n\nx = [1, 2, 3]\ny = x\ny.append(4)\nprint(len(x))",
      "options": ["3", "4", "Error: list is immutable", "None"],   // exactly 4, plain strings, one correct
      "answer": 1,                             // 0-based index into options
      "explain": "y is the same list object as x, so appending through y changes x."
    }
  ]
}
```

Rules for writing items:
- 10 to 14 items per skill, at least 4 `applied`. Every item must be answerable from doing the work, not from memorising a definition: prefer "what does this print / return / measure", "which line is wrong", "which query / command / setting does X", "given these readings, what is the value".
- One unambiguous correct option. Distractors must be plausible mistakes a beginner makes, never jokes, never "all of the above" / "none of the above".
- Short: question under 60 words plus a code or data snippet of at most 8 lines when needed. Options under 12 words. Use `\n` for line breaks inside `q`.
- Plain English a tier-3 graduate can read; no trick wording; Indian conventions where they matter (₹, lakh, IS codes, 230 V / 50 Hz).
- For exam-style skills (aptitude, GATE general aptitude, English) use real question shapes from the actual exam (quant, logical, reading, error spotting) and make the numbers work out cleanly.
- For tool skills (AutoCAD, STAAD, ETAP, SolidWorks, MATLAB, Excel) ask about what the tool does when you do X, which command/function achieves Y, or how to read its output, never about menu positions that change between versions.
- `explain` is one sentence that teaches the right idea, so a wrong answer is still useful.
- No copyrighted question text: write every item fresh.

`python3 scripts/validate.py` checks: skill_id exists, ids unique and prefixed, exactly 4 options, answer index in range, 6+ items, at least 3 `applied`, no duplicate questions.
