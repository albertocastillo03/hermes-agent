---
name: prospecting-excel-evals
description: "Use when changing any prospecting Excel skill or tools/prospecting_excel.py: run and extend the golden eval suite (tests/tools/test_prospecting_excel_evals.py) covering Accion parsing variants (NC/, NTD (Alberto), RLL 8/7 12h...), manual-review routing, call-list prioritization, and the writer→reader round trip."
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [prospecting, evals, tests, regression, accion]
    related_skills: [prospecting-excel-reader, prospecting-excel-writer, prospecting-action-router]
---

# Prospecting Excel Evals

## Overview

The workflow pack's behavior is pinned by executable evals in
`tests/tools/test_prospecting_excel_evals.py`, run with the repo's normal
pytest setup. They are the source of truth for edge-case behavior — when prose
in a skill and an eval disagree, fix one of them in the same change.

```bash
.venv/bin/python -m pytest tests/tools/test_prospecting_excel_evals.py -q
```

## When to Use

- Before and after editing any `prospecting-*` skill, `ntd-vault-capture`,
  `nc-email-introduction`, `rll-recall-reminder`, or
  `tools/prospecting_excel.py`.
- When the user reports a mis-parsed `Accion` value — add it as an eval first,
  then fix.

## What is covered (golden scenarios)

| Scenario | Expected |
| --- | --- |
| `NC` | `NC` |
| `NC/`, `NC /` | `NC` (habitual trailing separators stripped) |
| `NTD (Alberto)` | `NTD`; name Alberto, partial, LOW confidence |
| `NTD (Narciso y Vicente García)` | two targets; multi-word name kept intact |
| `NTD > Financiero` | `NTD`; department hint, no names |
| `RLL 8/7 12h` | `RLL`; 2026-07-08 12:00 vs sheet date 2026-01-05 |
| `RLL viernes 10:00` | `RLL`; next Friday after sheet date, 10:00 |
| `RLL en unos días`, bare `RLL` | manual review (ambiguous — no invented dates) |
| `llamar lunes`, `RLL?`, `ntd/nc` | `MANUAL_REVIEW` (not confidently NC/NTD/RLL) |
| several RLL in one slot | prioritized call list (DM title → size → email → clarity → urgency) |
| writer output | re-read by the reader; `Accion` values, including unknowns, byte-identical |

Also pinned: router dry-run counts reconcile to `total_rows`; every proposed
action is `approval_required` + `dry_run: true`; routing is deterministic.

## Adding an eval

1. Reproduce the real cell value verbatim in a test (don't paraphrase it).
2. Assert the *normalized* outcome and, where relevant, the route.
3. If behavior must change, change `tools/prospecting_excel.py` and the
   affected SKILL.md in the same commit — evals, code, and prose move
   together.

Done when: the new eval fails before the fix, passes after, and the full file
passes.

## Common Pitfalls

1. **Testing the paraphrase.** `NC /` and `NC/` are different cells; pin the
   exact string the operator typed.
2. **Loosening manual review.** Making an ambiguous case "just work" by
   guessing violates the workflow contract — extend the parser only for forms
   that are unambiguous.
3. **Fixing code without the eval.** A bug that arrives without a pinned
   regression returns.

## Verification Checklist

- [ ] `pytest tests/tools/test_prospecting_excel_evals.py -q` passes
- [ ] New edge cases pinned with the verbatim cell value
- [ ] Code, evals, and skill prose updated together
