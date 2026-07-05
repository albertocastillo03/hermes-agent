---
name: rll-recall-reminder
description: "Use when a prospecting row's Accion is RLL (recall later): schedule a call-back reminder via the cronjob tool — only after approval. Resolves dates like '8/7 12h' and 'viernes 10:00' against the sheet date (never today, never invented); ambiguous dates route to manual review. Builds a prioritized call list when several contacts share a slot (decision-maker title → company size → direct email → clearer time → urgent notes)."
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [prospecting, rll, reminders, cronjob, call-list, sales]
    related_skills: [prospecting-action-router, prospecting-excel-reader]
---

# RLL Recall Reminder

## Overview

`RLL` means **recall later** — call the contact again at the date/time noted in
the cell (`RLL 8/7 12h`, `RLL viernes 10:00`). This skill turns those notes
into reminders using the repo's `cronjob` tool (`tools/cronjob_tools.py`;
contract in
[docs/chronos-managed-cron-contract.md](../../../docs/chronos-managed-cron-contract.md)),
after approval. Date parsing:
`tools/prospecting_excel.py::parse_rll_when`; prioritization:
`rll_priority_key`. There is no calendar tool in the repo — calendar events
are a documented placeholder routed to manual review, not improvised.

## When to Use

- Routed here by `prospecting-action-router` for rows with
  `normalized_action == "RLL"`.
- Don't use for: non-RLL follow-ups, or scheduling without a resolvable date.

## Date resolution (never invent)

- **Reference date** = the sheet date from the sheet name
  (`... 20260105` → 2026-01-05) or a user-provided call date. Never
  `today()` — determinism and correctness both depend on the sheet's own
  timeline.
- Supported forms: `D/M` (`8/7` → next 8 July on/after the reference date),
  `D/M/YY[YY]`, Spanish weekdays (`viernes` → next Friday strictly after the
  reference date), times as `HH:MM` or `NNh` (`12h` → 12:00).
- **Ambiguous → manual review**: no resolvable date (`RLL`, `RLL en unos
  días`), unrecognized leftovers, or a time with no date. A reminder is never
  scheduled on a guessed date.

## Prioritized call list

When several contacts land on the same `(date, time)` slot, the router emits a
`call_lists` entry ordering them by, in sequence:

1. **Decision-maker titles first** — CEO, Founder, Managing Director, CFO,
   COO, Partner, Owner (case-insensitive substring of Job title).
2. **Larger companies first** — upper bound of `Employees in LinkedIn`
   (`201-500` → 500).
3. **Direct email first** — contacts with a work email before those without.
4. **Clearer slots first** — explicit `HH:MM` before date-only.
5. **Urgent notes** — `urgente`/`urgent`/`asap`/`!!` in the note raise
   priority.

Ties keep sheet/row order (deterministic).

## Creating reminders (only after approval)

Each schedulable row is proposed as a Cerberus `approval_required` action
(`type: create_reminder`) carrying date, time, contact, and provenance. Only
after explicit approval are reminders registered via the `cronjob` tool — one
job per approved recall, payload referencing the source sheet/row so the
outcome can be written back to the Excel.

Done when: every RLL row is a proposed reminder or a manual-review entry
("RLL with ambiguous date/time"), call lists exist for every shared slot, and
zero cronjobs were created without approval.

## Common Pitfalls

1. **Resolving against today.** `viernes` on a three-week-old sheet means that
   sheet's Friday, not this week's. Always the sheet/user reference date.
2. **Guessing vague dates.** "en unos días" is not schedulable — manual
   review keeps the human in control of the callback promise.
3. **Scheduling pre-approval.** The proposal is the dry-run; `cronjob`
   creation happens only after the gate.
4. **Flat call lists.** Same-slot contacts must come out in priority order,
   not sheet order — the operator calls top-down under time pressure.
5. **Creating calendar events.** No calendar tool exists; that route is a
   placeholder → manual review.

## Verification Checklist

- [ ] Reference date taken from the sheet name or the user, never today
- [ ] Every RLL row → proposed reminder or manual review, none guessed
- [ ] Proposed actions carry date/time/contact/provenance, `approval_required`
- [ ] Shared slots produce a prioritized `call_lists` entry (rules 1–5)
- [ ] Zero reminders created without explicit approval
