---
name: ntd-vault-capture
description: "Use when a prospecting row's Accion is NTD (not the target decision maker): capture the real decision-maker hints for future prospecting via the memory tool. Extracts names from parentheses and department hints after '>', links them to the company with full provenance, marks single first names as partial/LOW confidence, and never invents names, emails, phones, or LinkedIn URLs."
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [prospecting, ntd, memory, decision-maker, capture, sales]
    related_skills: [prospecting-action-router, prospecting-excel-reader]
---

# NTD Vault Capture

## Overview

`NTD` means the person called is **not the (target) decision maker** — but the
call often reveals who is: `NTD (Alberto)`, `NTD (Narciso y Vicente García)`,
`NTD > Financiero`. This skill turns those fragments into durable memory
entries (the `memory` tool, `tools/memory_tool.py`) so the next prospecting
pass targets the right person. Extraction logic:
`tools/prospecting_excel.py::extract_ntd_targets`. There is no vault/CRM tool
in the repo — memory *is* the capture store for v1; a future CRM route is a
documented placeholder only.

## When to Use

- Routed here by `prospecting-action-router` for rows with
  `normalized_action == "NTD"`.
- Don't use for: rows whose action isn't NTD, or for enriching the captured
  target with external data (never done — no LinkedIn/Lusha lookups).

## Extraction rules

From the `Accion` free text after the `NTD` token:

| Pattern | Captured as |
| --- | --- |
| `(Alberto)` | name `Alberto`, `partial: true`, `confidence: LOW` |
| `(Narciso y Vicente García)` | two names, split on ` y `/` e `/commas; single-word → partial/LOW, multi-word → MEDIUM |
| `> Financiero` (no parentheses) | `department_hint: "Financiero"` |
| nothing extractable | route to manual review — do not save an empty capture |

- **Split carefully**: `Vicente García` is one person; the split happens only
  on explicit separators, never inside a multi-word name.
- **Never invent**: no completing `Alberto` to a full name, no constructing
  emails/phones/LinkedIn URLs for the target. What was said is all there is.

## Capture format

Each capture is proposed as a Cerberus `approval_required` action
(`type: memory_write`) by the router; after approval, save via the `memory`
tool with:

- `company` — the row's Company name (the link key)
- `names` (with `partial`/`confidence`) and/or `department_hint`
- `source_contact` — who was actually called
- provenance: `sheet_name`, `row_number`, `original_action`

**Dedup before saving**: the router already skips a `(company, target)` pair
seen earlier in the same run; also check existing memory for the same pair
before writing. Prefer updating an existing entry (e.g. raising confidence
when a partial name gains a surname) over adding a near-duplicate.

Done when: every NTD row is either one proposed capture, a dedup skip, or a
manual-review entry — and no capture was written without approval.

## Common Pitfalls

1. **Saving empty captures.** `NTD` with no note has nothing to capture —
   manual review, not a blank memory entry.
2. **Merging distinct people.** `Narciso y Vicente García` is two targets; do
   not save "Narciso y Vicente García" as one name.
3. **Upgrading confidence silently.** `Alberto` stays partial/LOW until a
   later source confirms the full identity.
4. **Duplicating per owner sheet.** The same row appears on master and owner
   sheets; capture once per `(company, target)`.
5. **Writing memory pre-approval.** The proposal is the dry-run; the memory
   write happens only after the approval gate.

## Verification Checklist

- [ ] Every NTD row accounted for: capture proposed, dedup-skipped, or manual review
- [ ] Names split correctly; multi-word names intact
- [ ] Single first names marked `partial: true`, `confidence: LOW`
- [ ] Capture linked to company with sheet/row/action provenance
- [ ] No invented names, emails, phones, or URLs
- [ ] Memory written only after explicit approval
