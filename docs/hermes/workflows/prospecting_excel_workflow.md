# Hermes Prospecting Excel Workflow (v1)

The standard contract for prospecting Excel files that flow through Hermes:
what the format is, how to read it, how to normalize rows, how to interpret the
`Accion` column, and how to write the same format back out.

This is the shared reference for the prospecting workflow pack. The first skill
built on it is [`prospecting-excel-reader`](../../../skills/productivity/prospecting-excel-reader/SKILL.md).
Related pipeline: the Sales Prospector dry-run ([docs/sales-prospector-dry-run.md](../../sales-prospector-dry-run.md))
already *produces* files in this exact format via `tools/sales_prospector.py::export_leads_xlsx`.

## The standard format

A prospecting workbook contains one or more **contact sheets**. Each contact
sheet has a single header row (row 1) with exactly these 12 columns, in this
order (A→L):

| # | Column | Required | Meaning |
|---|--------|----------|---------|
| 1 | `Accion` | optional (often blank) | Action/outcome code for the row, written manually during calling (e.g. `NC`, `RLL`). Drives routing. Blank = not yet worked. |
| 2 | `Contact name` | **required** | Person's full name. A row without a contact name is not a contact row. |
| 3 | `Mobile` | optional | Primary phone. May be blank. |
| 4 | `Mobile 2` | optional | Secondary phone. May be blank. |
| 5 | `Company name` | **required** | Employer/organization. |
| 6 | `Job title` | optional | Role at the company. |
| 7 | `Work email` | optional | Primary work email. |
| 8 | `Work email 2` | optional | Secondary work email. |
| 9 | `LinkedIn profile` | optional | Plain URL string. Never a formula or rich hyperlink object. |
| 10 | `Industry` | optional | Broad industry bucket. |
| 11 | `Sub industry` | optional | Narrower sector label. |
| 12 | `Employees in LinkedIn` | optional | Company size as shown on LinkedIn (band like `51-200` or a number). Keep as text. |

**Required** means: the row is only a valid contact row if the cell is
non-blank. Everything else is optional — blank is a normal, expected value.

## Identifying the active prospecting sheets

- A sheet is a **contact sheet** iff its row 1 matches the 12 headers above
  (exact strings, case-sensitive, in order). Sheets that don't match (notes,
  pivots, dashboards) are ignored — never guessed at.
- Naming convention (produced by `export_leads_xlsx`, expected on inputs):
  - Master sheet: `Todos los contactos YYYYMMDD` — every contact.
  - Owner sheets: `<Owner> - YYYYMMDD` (e.g. `David - 20260105`) — the same
    rows split by owner/operator.
- When both a master and owner sheets are present, **owner sheets are the
  working copies**: read all matching sheets, keep `sheet_name` on every row,
  and treat the master as reference. Do not deduplicate across sheets by
  guessing — a row's identity is `(sheet_name, row_number)`.

## Reading rules

Read with the repo's stdlib reader (`tools/read_extract.py` — the same code
behind the `read_file` tool's `.xlsx` support). No external service, no
`openpyxl` dependency.

1. **Preserve provenance.** Every normalized row carries `source_file`,
   `sheet_name`, and the 1-based `row_number` from the sheet (header = row 1,
   first contact = row 2).
2. **Preserve original values.** Keep the raw cell string for anything you
   normalize (`original_action` next to `normalized_action`; trimmed but
   otherwise untouched contact fields). The output file must be able to
   reproduce the input values.
3. **Blank cells** → empty string `""` in the normalized object. Never `null`
   for a mapped column, never a placeholder like `N/A`, never invented data.
4. **Skip rules:** skip fully blank rows; skip rows with a blank
   `Contact name` but record them in `notes` at file level for manual review
   if they contain any other data.

## Normalization (never invent data)

- **Phones** (`Mobile`, `Mobile 2`): trim whitespace; collapse internal
  spaces/dots/dashes only if the remaining string is digits and an optional
  leading `+`. Do **not** add country codes, do **not** reformat to E.164 if
  the country is unknown, do **not** fabricate a number from partial digits.
  If a value doesn't look like a phone, keep it verbatim and add a note.
- **Emails** (`Work email`, `Work email 2`): trim and lowercase the domain
  part only. If the string has no `@`, keep it verbatim and add a note. Never
  guess a domain or complete a truncated address.
- **LinkedIn profile**: keep as the plain string found in the cell. Don't
  resolve, expand, follow, or validate the URL (no network).
- **Names/companies/titles**: trim outer whitespace only. No case-folding, no
  translation, no expansion of abbreviations.

## The `Accion` column

`Accion` is manually written during calling. Normalization maps the raw token
(trimmed, uppercased, habitual trailing `/ . , ; :` stripped — so `NC/` and
`NC /` are `NC`) to one of five codes. Semantics confirmed with the workflow
owner:

| `normalized_action` | Meaning (confirmed) | Routing target |
|---|---|---|
| `NC` | **No Contesta** — did not pick up the phone | [`nc-email-introduction`](../../../skills/productivity/nc-email-introduction/SKILL.md): draft an intro email (himalaya skill, approval-gated). |
| `NTD` | **Not the (target) decision maker** | [`ntd-vault-capture`](../../../skills/productivity/ntd-vault-capture/SKILL.md): capture the real target in memory; row leaves active calling but stays in the output. |
| `RLL` | **Recall later** — call again at the indicated date/time | [`rll-recall-reminder`](../../../skills/productivity/rll-recall-reminder/SKILL.md): schedule via the `cronjob` tool; date/time from the cell in `action_payload`. |
| `NONE` | blank cell — not yet worked | No action; stays in the active calling list unchanged. |
| `MANUAL_REVIEW` | anything not *confidently* NC/NTD/RLL | Preserved verbatim and routed to a human. Never dropped, never guessed. |

Cells may contain a code plus free text (e.g. `RLL viernes 10:00`,
`NTD (Alberto)`). The first whitespace-separated token is matched against the
table; the remainder goes to `action_payload.note` untouched. If the first
token (after trailing-separator stripping) is not an exact code match — e.g.
`RLL?`, `ntd/nc`, `llamar lunes` — the whole cell is `MANUAL_REVIEW`.

The executable reference for parsing, routing, prioritization, and the
writer round-trip is `tools/prospecting_excel.py`, pinned by the eval suite
`tests/tools/test_prospecting_excel_evals.py`
([`prospecting-excel-evals`](../../../skills/productivity/prospecting-excel-evals/SKILL.md)).
Routing orchestration:
[`prospecting-action-router`](../../../skills/productivity/prospecting-action-router/SKILL.md)
(dry-run first; side effects only as Cerberus `approval_required` actions).
Writing back:
[`prospecting-excel-writer`](../../../skills/productivity/prospecting-excel-writer/SKILL.md).

## Routing (v1 — what exists vs. placeholders)

Each normalized row routes on `normalized_action`. Use what the repo already
has; where nothing exists yet, the route is a **documented placeholder** — the
row is emitted to the manual-review output, not silently handled:

- **Approvals** — any side-effecting route (sending email, scheduling) must
  surface as a structured `approval_required` action first. Follow the
  Cerberus pattern from `tools/sales_prospector.py` (see
  [docs/sales-prospector-dry-run.md](../../sales-prospector-dry-run.md)); the
  command-approval engine is `tools/approval.py` and stays untouched.
- **Reminders / callbacks** — exists: the `cronjob` tool
  (`tools/cronjob_tools.py`; contract in
  [docs/chronos-managed-cron-contract.md](../../chronos-managed-cron-contract.md)).
- **Email** — exists as an approval-gated skill: `skills/email/himalaya`
  (IMAP/SMTP CLI). No email is ever sent without an explicit approval step.
- **Memory** — exists: the `memory` tool (`tools/memory_tool.py`) for durable
  facts learned about accounts/contacts.
- **Calendar** — does **not** exist. Placeholder: emit a proposed-event object
  (same shape Kronos produces in the dry-run pipeline) into the manual-review
  output.
- **CRM / vault** — do **not** exist as tools. CRM: placeholder, keep rows in
  the Excel as the system of record. Secrets (`hermes_cli/secrets_cli.py`) are
  out of scope: this workflow never reads or writes secrets.

## Writing output (same format back out)

- Use `tools/sales_prospector.py::export_leads_xlsx` conventions (stdlib
  writer): exact 12 headers in row 1, header style blue `#0070C0` / white bold
  centered wrapped, tall frozen header row, sensible column widths.
- Sheet naming: master `Todos los contactos YYYYMMDD`; owner sheets
  `<Owner> - YYYYMMDD`.
- Row values come from the **original** (preserved) values, not the normalized
  forms, except deliberate cleanups covered by the normalization rules.
- Rows whose action was `MANUAL_REVIEW` are always carried through to the
  output — a separate review sheet or the master sheet with their original
  `Accion` intact — so no row is ever lost between input and output.
- Output files are written locally under `./exports/sales_prospector/`
  (git-ignored). Nothing is uploaded anywhere.

## Invariants (every implementation must hold these)

1. Row count in = row count out (worked + unworked + manual review).
2. Any cell not deliberately transformed is byte-identical in the output.
3. Unknown `Accion` values are never dropped, never guessed: `MANUAL_REVIEW`.
4. No invented data: no phones, emails, URLs, or company facts that were not
   in the input.
5. No external calls: no Lusha/Apollo/Apify/LinkedIn/Gmail/Outlook/Calendar
   APIs from this workflow; email only via the approval-gated himalaya skill.
6. Side effects only through an explicit approval step.
