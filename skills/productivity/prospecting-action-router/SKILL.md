---
name: prospecting-action-router
description: "Use when routing parsed prospecting rows to their next workflow by Accion code: NC → nc-email-introduction, NTD → ntd-vault-capture, RLL → rll-recall-reminder, unknown → manual review, blank → no action. Always dry-run first; every side effect (email, reminder, memory write) is emitted as a Cerberus-style approval_required action and executed only after explicit approval."
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [prospecting, routing, dry-run, approvals, sales, accion]
    related_skills: [prospecting-excel-reader, nc-email-introduction, ntd-vault-capture, rll-recall-reminder]
---

# Prospecting Action Router

## Overview

Takes the normalized rows produced by `prospecting-excel-reader` and routes
each to its next workflow — as a **dry-run first, always**. The executable
router is `tools/prospecting_excel.py::route_rows`; semantics are defined in
[docs/hermes/workflows/prospecting_excel_workflow.md](../../../docs/hermes/workflows/prospecting_excel_workflow.md).

Routing table (confirmed v1 semantics):

| `normalized_action` | Meaning | Route |
| --- | --- | --- |
| `NC` | No contesta — didn't pick up | `nc-email-introduction` (draft intro email) |
| `NTD` | Not the (target) decision maker | `ntd-vault-capture` (memory capture) |
| `RLL` | Recall later at indicated date/time | `rll-recall-reminder` (cronjob reminder) |
| `MANUAL_REVIEW` | Not confidently NC/NTD/RLL | manual review queue |
| `NONE` | Blank — not yet worked | no action |

## When to Use

- After reading a prospecting workbook, when the user asks to "process",
  "route", or "action" the list.
- Don't use for: executing the routed actions directly (that's each target
  skill, after approval), or for files outside the 12-column schema.

## Step 1 — Dry-run

```python
from tools.prospecting_excel import normalize_rows, route_rows, sheet_reference_date

rows, notes = normalize_rows(path)
ref = sheet_reference_date(rows[0]["sheet_name"])   # or the user's call date
summary = route_rows(rows, ref)
```

`summary` is the execution summary — show it to the user before anything else:

```json
{
  "dry_run": true,
  "total_rows": 0,
  "nc_email_count": 0,
  "ntd_capture_count": 0,
  "rll_reminder_count": 0,
  "manual_review_count": 0,
  "no_action_count": 0,
  "proposed_actions": [],
  "manual_review": [],
  "call_lists": []
}
```

Done when: counts add up (`nc_email + ntd_capture + rll_reminder +
manual_review + no_action + dedup-skipped = total_rows`) and every
`proposed_actions` entry has `status: approval_required` and `dry_run: true`.

Rows are grouped by action type; rows that *fail* their route (NC with no
usable email, NTD with no extractable target, RLL with an ambiguous date) fall
into `manual_review` with a reason — never dropped, never guessed.

## Step 2 — Approval gate

Nothing executes from the dry-run. Before email, reminder creation, memory
writes, bulk processing, or any external action, present the proposed actions
and get explicit approval — the Cerberus `approval_required` pattern from
`tools/sales_prospector.py` (the command-approval engine `tools/approval.py`
is not modified and not involved). Only approved actions move to Step 3.

## Step 3 — Execute approved actions

Hand each approved action to its workflow skill: `send_email` →
`nc-email-introduction` (himalaya), `create_reminder` → `rll-recall-reminder`
(cronjob tool), `memory_write` → `ntd-vault-capture` (memory tool). Calendar
and CRM routes have no tools yet — those stay in manual review; do not
improvise integrations.

Done when: every approved action is executed or reported failed, every
rejected action is left untouched, and the user gets a final execution
summary mirroring the dry-run counts.

## Common Pitfalls

1. **Skipping the dry-run.** Never execute on first pass, even for one row —
   the summary is how the user catches bad parses before side effects.
2. **Executing on partial approval.** Approval of the *summary* is not
   approval of each action type; bulk approval must be explicit.
3. **Guessing failed routes.** No usable email / no target / vague date →
   `manual_review` with a reason, not a best-effort action.
4. **Losing the dedup rule.** Duplicate NTD targets for the same company are
   skipped by the router; don't re-add them at execution time.
5. **Reference-date drift.** Relative RLL dates resolve against the sheet date
   (from the sheet name) or the user-provided call date — never `today()`.

## Verification Checklist

- [ ] Dry-run summary shown before any execution
- [ ] Counts reconcile to `total_rows`
- [ ] All proposed actions are `approval_required` + `dry_run: true`
- [ ] No email/reminder/memory write happened without explicit approval
- [ ] Manual-review rows carry a reason and provenance (sheet, row, action)
- [ ] `tools/approval.py` untouched
