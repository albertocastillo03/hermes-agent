---
name: prospecting-excel-writer
description: "Use when writing contacts back out as a prospecting Excel in the standard Hermes 12-column format (Accion ... Employees in LinkedIn). Reuses export_leads_xlsx, preserves original Accion values (including manual-review ones), phones/emails/LinkedIn as text, and validates that the output is re-readable by prospecting-excel-reader."
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [prospecting, excel, xlsx, export, sales, leads]
    related_skills: [prospecting-excel-reader, prospecting-action-router]
---

# Prospecting Excel Writer

## Overview

Writes normalized prospecting rows back to disk in the exact standard format —
same 12 columns, same order, same styling — so the output of any Hermes
processing step is a drop-in replacement for the input file. The format
contract lives in
[docs/hermes/workflows/prospecting_excel_workflow.md](../../../docs/hermes/workflows/prospecting_excel_workflow.md);
the executable path is `tools/prospecting_excel.py::write_rows_xlsx`, a thin
wrapper over `tools/sales_prospector.py::export_leads_xlsx` (stdlib writer:
blue `#0070C0` header, frozen row, `Todos los contactos YYYYMMDD` master sheet
plus optional owner sheets).

## When to Use

- After processing rows read by `prospecting-excel-reader` and the result must
  go back to the user as an Excel file.
- Producing a manual-review workbook.
- Don't use for: ad-hoc spreadsheets in other schemas, or CSV output.

## How to write

```python
from tools.prospecting_excel import write_rows_xlsx

meta = write_rows_xlsx(rows, date_tag="20260105")   # rows = normalized objects
assert meta["round_trip_validated"]                  # output re-readable
```

Rules the wrapper enforces — hold them if you ever write another path:

1. **Columns**: exactly `Accion, Contact name, Mobile, Mobile 2, Company name,
   Job title, Work email, Work email 2, LinkedIn profile, Industry,
   Sub industry, Employees in LinkedIn` — in that order, nothing added or
   dropped.
2. **Accion preserved**: column A gets each row's `original_action` verbatim —
   including unknown/`MANUAL_REVIEW` values like `llamar lunes`. Never blanked,
   never "corrected".
3. **Everything is text**: phones, `Employees in LinkedIn` bands, and LinkedIn
   URLs are written as plain strings (the writer emits `inlineStr` cells), so
   Excel can't eat leading `+`/zeros or turn URLs into hyperlink objects.
4. **No deletions**: rows and their notes ride through unless the user
   explicitly asked to drop them. Row count in = row count out.
5. **Round-trip validation** (built in): after writing, the file is re-read
   with `read_contact_sheets`; a missing main sheet or row-count mismatch
   raises `ValueError`. Don't skip this by writing xlsx by hand.

Output lands in `./exports/sales_prospector/` (git-ignored) unless
`export_dir` says otherwise; filenames are deterministic (content-derived, no
wall clock). Rows carrying an `owner` field are additionally split into
`<Owner> - YYYYMMDD` sheets while the master keeps every row.

## Common Pitfalls

1. **Blanking Accion on export.** The old exporter default is `""`; when
   writing processed rows always populate `accion` from `original_action`.
2. **Writing normalized values over originals.** Column values come from the
   preserved fields; only the trim/separator cleanups sanctioned by the
   workflow doc may differ from input bytes.
3. **Reordering rows silently.** `rank` (input order) drives output order —
   sort only when the user asked for it.
4. **Hand-rolling a second xlsx writer.** One writer exists
   (`export_leads_xlsx`); a parallel implementation will drift on styling and
   sheet naming.
5. **Skipping validation.** If `round_trip_validated` isn't in the returned
   metadata, the file was not checked against the reader.

## Verification Checklist

- [ ] Header row is the exact 12 columns, in order
- [ ] Every input row present in the master sheet (count matches)
- [ ] `Accion` values byte-identical to `original_action`, including unknowns
- [ ] Phones/emails/LinkedIn intact as plain text
- [ ] `round_trip_validated: true` in the returned metadata
- [ ] File written locally under the export dir; nothing uploaded
