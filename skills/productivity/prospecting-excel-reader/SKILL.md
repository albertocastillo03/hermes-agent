---
name: prospecting-excel-reader
description: "Use when reading a prospecting Excel file in the standard Hermes 12-column format (Accion, Contact name, Mobile, ..., Employees in LinkedIn). Detects contact sheets, normalizes each row into the standard JSON object with provenance, parses the Accion column into NC/NTD/RLL/MANUAL_REVIEW/NONE, and routes unknown actions to manual review. Never invents missing data."
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [prospecting, excel, xlsx, sales, leads, normalization, accion]
    related_skills: [prospecting-action-router, prospecting-excel-writer, prospecting-excel-evals]
---

# Prospecting Excel Reader

## Overview

Reads prospecting workbooks in the standard Hermes format and turns each
contact row into a structured object ready for routing. The format contract
(columns, required fields, `Accion` semantics, output rules) lives in
[docs/hermes/workflows/prospecting_excel_workflow.md](../../../docs/hermes/workflows/prospecting_excel_workflow.md)
— this skill is the *reading and normalization* half of that contract.

Everything is local and read-only: the repo's stdlib xlsx reader
(`tools/read_extract.py`), no network, no external enrichment, no secrets.

## When to Use

- The user uploads or points at a prospecting `.xlsx` and asks to process,
  triage, route, or summarize it.
- A workflow needs the calling list as structured data (per-row actions).
- Don't use for: arbitrary spreadsheets (only the 12-column prospecting
  schema), writing/exporting Excel (that is `tools/sales_prospector.py::
  export_leads_xlsx`), or enriching contacts with external data (never done
  from this skill).

## Step 1 — Detect contact sheets

Open the workbook with the repo reader and keep only sheets whose header row
is exactly the 12 standard columns, in order:

`Accion, Contact name, Mobile, Mobile 2, Company name, Job title, Work email,
Work email 2, LinkedIn profile, Industry, Sub industry, Employees in LinkedIn`

```python
import zipfile
from tools.read_extract import _shared_strings, _workbook_sheets, _workbook_rels, \
    _sheet_part, _sheet_rows
from tools.sales_prospector import EXPORT_COLUMNS  # the 12 canonical headers

def read_contact_sheets(path):
    """{sheet_name: [row_cells, ...]} for sheets matching the 12-col schema."""
    out = {}
    with zipfile.ZipFile(path) as zf:
        names = set(zf.namelist())
        shared = _shared_strings(zf, names)
        rels = _workbook_rels(zf, names)
        for name, state, rid in _workbook_sheets(zf):
            if state in {"hidden", "veryHidden"}:
                continue
            part = _sheet_part(rels.get(rid, ""))
            if part not in names:
                continue
            rows = _sheet_rows(zf.read(part), shared)
            header = [c.strip() for c in rows[0]] if rows else []
            if header == EXPORT_COLUMNS:      # exact match, in order
                out[name] = rows
    return out
```

Done when: every sheet is either in the result dict or intentionally ignored
(non-matching header) — no sheet is half-read.

For a quick *human-readable* look instead of structured parsing, `read_file`
on the `.xlsx` auto-extracts all visible sheets as text.

## Step 2 — Normalize rows

For each contact sheet, rows 2..N map to this object (blank cell → `""`,
never `null`, never a placeholder):

```json
{
  "source_file": "<workbook filename>",
  "sheet_name": "<sheet name>",
  "row_number": 2,
  "original_action": "<raw Accion cell, untrimmed content preserved>",
  "normalized_action": "NC | NTD | RLL | MANUAL_REVIEW | NONE",
  "action_payload": {"note": "<free text after the action token, if any>"},
  "contact": {
    "name": "...",
    "mobile": "...",
    "mobile_2": "...",
    "work_email": "...",
    "work_email_2": "...",
    "linkedin_profile": "..."
  },
  "company": {
    "name": "...",
    "industry": "...",
    "sub_industry": "...",
    "employees_linkedin": "..."
  },
  "job_title": "...",
  "notes": []
}
```

Rules (full detail in the workflow doc):

- `row_number` is the sheet's 1-based row (header = 1, first contact = 2).
- **Trim-only** for names/companies/titles. Phones: strip separators only when
  what remains is digits + optional leading `+` — otherwise keep verbatim and
  append a note. Emails: lowercase the domain half only; no `@` → keep
  verbatim + note. LinkedIn: plain string, never fetched.
- Skip fully blank rows. A row with data but no `Contact name` is not a
  contact: record it in `notes` (file level) for manual review.
- **Never invent data.** No completing phone numbers, no guessing domains, no
  filling industry from world knowledge. Blank in → blank out.

Done when: every non-blank sheet row is accounted for as a normalized object,
a skipped-blank, or a no-name note.

## Step 3 — Parse `Accion`

Use `tools.prospecting_excel.parse_accion` (the executable reference, pinned
by `tests/tools/test_prospecting_excel_evals.py`): uppercase the first
whitespace-separated token, strip habitual trailing separators (`/ . , ; :` —
so `NC/` and `NC /` are `NC`), and match exactly:

| Token | `normalized_action` | Confirmed meaning |
| --- | --- | --- |
| *(blank cell)* | `NONE` | not yet worked |
| `NC` | `NC` | no contesta — did not pick up |
| `NTD` | `NTD` | not the (target) decision maker |
| `RLL` | `RLL` | recall later at the indicated date/time |
| *(anything else)* | `MANUAL_REVIEW` | not confidently NC/NTD/RLL |

- Remainder of the cell (e.g. the `viernes 10:00` in `RLL viernes 10:00`)
  goes to `action_payload.note` verbatim.
- `?` is **not** a strippable separator: `RLL?` signals uncertainty →
  `MANUAL_REVIEW`.
- `MANUAL_REVIEW` keeps the whole raw cell in `original_action` and is never
  dropped or "best-guessed" into another code.

Done when: every row has a `normalized_action` and no raw `Accion` text was
discarded.

## Step 4 — Route

Hand off by `normalized_action` per the workflow doc's routing table:
`NC`/`RLL` → follow-up via the `cronjob` tool; `NTD` → out of active calling
but kept in output; `NONE` → stays in the calling list; `MANUAL_REVIEW` → the
human review bucket. Any side-effecting route (email via the `himalaya`
skill, scheduling) goes through an explicit approval step first — emit
structured `approval_required` actions (Cerberus pattern,
`tools/sales_prospector.py`), don't act directly. Calendar/CRM routes have no
tools yet: emit to manual review, never improvise an integration.

## Common Pitfalls

1. **Header matched loosely.** `accion` ≠ `Accion`; a 13-column sheet is not
   the schema. Exact, ordered, case-sensitive match or the sheet is ignored.
2. **Losing provenance.** Downstream fixes need `(sheet_name, row_number)` to
   write results back. Carry them on every object from the start.
3. **Normalizing away the original.** If you can't reproduce the input cell
   from your output, you normalized too hard. Keep `original_action` and keep
   verbatim values whenever a cell fails its format check.
4. **Guessing unknown actions.** `RLL?`, `ntd/nc`, `llamar lunes` are all
   `MANUAL_REVIEW` — the first token must match exactly.
5. **Inventing contact data.** No country codes added to phones, no domains
   completed, no LinkedIn URLs constructed from names.
6. **Reading hidden sheets.** The repo reader skips `hidden`/`veryHidden`
   sheets; don't resurrect them manually.
7. **Deduplicating across sheets.** Master and owner sheets intentionally
   overlap. Identity is `(sheet_name, row_number)`; merging rows loses the
   per-owner working state.

## Verification Checklist

- [ ] Every visible sheet classified: contact sheet or ignored (header
      mismatch) — none skipped silently
- [ ] Normalized-row count + skipped-blank count + no-name notes = input data
      rows, per sheet
- [ ] Every object has `source_file`, `sheet_name`, `row_number`,
      `original_action`, `normalized_action`
- [ ] All `normalized_action` values ∈ {NC, NTD, RLL, MANUAL_REVIEW, NONE}
- [ ] Unknown actions preserved verbatim under `MANUAL_REVIEW`
- [ ] No field contains data absent from the input file
- [ ] No network access, no secrets touched, no side effects without an
      `approval_required` action
