# Sales Prospector dry-run pipeline (v1)

This is the **first** Sales Prospector + Atlas + Excel Analyst + Mercury +
Kronos dry-run pipeline for hermes-agent. It chains five mock personas and one
gatekeeper end-to-end, entirely in-process:

```
Sales Prospector → Atlas → Excel Analyst → Mercury → Kronos → Cerberus
```

| Persona | Role in the pipeline |
| --- | --- |
| **Sales Prospector** | Generates mock prospects from the query. |
| **Atlas** | Enriches each prospect with mock firmographics (size band, region, confidence). |
| **Excel Analyst** | Scores/ranks prospects and computes aggregate analytics. |
| **Mercury** | Drafts outreach emails (dry-run — nothing is sent). |
| **Kronos** | Proposes follow-up calendar events (dry-run — nothing is created). |
| **Cerberus** | Gatekeeper — collects the side-effecting steps as structured `approval_required` actions. |

## v1 guarantees

- **Deterministic** — the same query always produces byte-identical output. All
  variation is seeded from a SHA-256 of the query; dates come from a fixed
  anchor, never the wall clock.
- **Local & mock-only** — no network, no secrets. It does **not** call
  Lusha/Apollo/Apify, does **not** send email, and does **not** touch a
  calendar. Emails use the reserved `.example` TLD so nothing can resolve or be
  delivered.
- **Dry-run** — nothing is executed. Mercury emails and Kronos events are
  *proposed only* and surface under `approvals_required`.
- **Approval engine untouched** — Cerberus returns structured
  `approval_required` actions in the response and deliberately does **not**
  import or modify [`tools/approval.py`](../tools/approval.py). A caller may
  later route these actions through the existing approval engine.

## Where the code lives

- Core workflow/service: [`tools/sales_prospector.py`](../tools/sales_prospector.py)
  — the pipeline is a standalone module, independent of the web server. Entry
  point: `run_dry_run(query) -> dict`.
- HTTP endpoint (thin wrapper): `POST /v1/sales/prospect/dry-run` in
  [`gateway/platforms/api_server.py`](../gateway/platforms/api_server.py).

## Using the service directly

```python
from tools.sales_prospector import run_dry_run

result = run_dry_run({
    "company": "Indra",
    "sector": "IT consulting",
    "geography": "Spain",
    "campaign_goal": "book a meeting with IT decision makers",
    "count": 3,
})
result["dry_run"]             # True
result["request_context"]     # echoes company/sector/geography/campaign_goal + derived target_role
result["approvals_required"]  # structured send_email / schedule_event actions
```

## Using the endpoint

```bash
curl -s http://localhost:8642/v1/sales/prospect/dry-run \
  -H "Authorization: Bearer $API_SERVER_KEY" \
  -H "Content-Type: application/json" \
  -d '{"company": "Indra", "sector": "IT consulting", "geography": "Spain", "campaign_goal": "book a meeting with IT decision makers", "count": 3}'
```

### Request body (all fields optional)

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `company` | string | `"our team"` | The org running the campaign (the sender). |
| `sector` | string | `"software"` | Target sector/industry for prospects. |
| `geography` | string | `"Remote"` | Where prospects are located. |
| `campaign_goal` | string | `"generate qualified leads"` | Outreach objective; the target job title is *derived* from it. |
| `count` | int | `3` | Clamped to `1..25`. |

The input is preserved and echoed back under both `query` and `request_context`
(never silently defaulted). The prospect job title is derived from
`campaign_goal` — e.g. a goal mentioning "IT decision makers" yields the target
role `IT Director`. Titles are pluralized correctly in the generated copy
("Head of Sales" → "Heads of Sales", never "Head of Saless").

### Response shape

```jsonc
{
  "object": "hermes.sales_prospector.dry_run",
  "dry_run": true,
  "mock_only": true,
  "pipeline": ["sales_prospector", "atlas", "excel_analyst", "mercury", "kronos", "cerberus"],
  "query": { "company": "...", "sector": "...", "geography": "...", "campaign_goal": "...", "count": 3 },
  "request_context": { "company": "...", "sector": "...", "geography": "...", "campaign_goal": "...", "target_role": "IT Director" },
  "stages": [ { "persona": "...", "status": "ok", "produced": 3, "description": "..." } ],
  "prospects": [ { "id": "prospect_01", "fit_score": 42, "rank": 1, "enrichment": { "...": "..." } } ],
  "analysis": { "average_fit_score": 42.67, "top_prospect_id": "prospect_03", "ranking": [ "..." ] },
  "approvals_required": [
    {
      "action_id": "action_email_01",
      "type": "send_email",
      "persona": "mercury",
      "status": "approval_required",
      "dry_run": true,
      "summary": "Send outreach email to ...",
      "payload": { "delivered": false, "...": "..." }
    }
  ],
  "warnings": [],
  "notes": "Deterministic mock-only dry-run. No external services were contacted..."
}
```

## Excel Analyst — local commercial calling-list `.xlsx` export

The Excel Analyst exports the dry-run prospects to a **real, local** Excel
workbook shaped like the actual prospecting/calling sheet (not the internal
analytics view). `export_leads_xlsx(result)` takes the dict from `run_dry_run`
and writes a genuinely valid, styled `.xlsx` you can open in Excel or
LibreOffice.

Still fully **dry-run and mock-only**: it reads the in-memory result and writes
one local file. It does **not** contact Lusha/Apollo/Apify/LinkedIn/Gmail/
Outlook/Google Calendar, send email, create calendar events, or access secrets.
**Phone numbers and LinkedIn URLs are never invented** — unknown contact fields
are left blank. The workbook is built with the **standard library only** (an
`.xlsx` is a ZIP of XML parts) — no new dependency, matching how the repo already
reads `.xlsx`.

```python
from tools.sales_prospector import run_dry_run, export_leads_xlsx

result = run_dry_run({"company": "Indra", "sector": "IT consulting",
                      "geography": "Spain",
                      "campaign_goal": "book a meeting with IT decision makers"})
meta = export_leads_xlsx(result)   # optional: export_dir=..., date_tag="YYYYMMDD"
# meta == {
#   "file_path": "/abs/.../exports/sales_prospector/leads_indra_9747b98c.xlsx",
#   "row_count": 3,                       # contacts on the master sheet
#   "dry_run": True,
#   "created_by": "excel_analyst",
#   "columns": [...12 commercial columns...],
#   "sheets": ["Todos los contactos 20260105"],
#   "main_sheet": "Todos los contactos 20260105",
# }
```

### Columns (exact order)

`Accion`, `Contact name`, `Mobile`, `Mobile 2`, `Company name`, `Job title`,
`Work email`, `Work email 2`, `LinkedIn profile`, `Industry`, `Sub industry`,
`Employees in LinkedIn`.

The internal analytics fields (`rank`, `fit_score`, `dry_run`,
`approval_status`) are **not** shown in the Excel — they remain available in the
`run_dry_run` JSON result only.

Mapping from a prospect: `Contact name` ← `full_name`, `Company name` ←
`company`, `Job title` ← `title`, `Work email` ← `email`, `Industry` ←
broad industry, `Sub industry` ← `sector`, `Employees in LinkedIn` ← mock size
band. `Accion` is blank (reserved for manual call notes); `Mobile`, `Mobile 2`,
`Work email 2`, `LinkedIn profile` are blank in dry-run unless a real pipeline
supplies them (LinkedIn stays a plain URL string).

### Sheets and formatting

- **Main sheet** — `Todos los contactos <YYYYMMDD>`. The date tag is
  deterministic (derived from the pipeline's fixed anchor date → `20260105`);
  pass `date_tag=` to override.
- **Owner split** — if prospects carry an `owner`/`operator` field, one sheet per
  owner is added (`<owner> - <YYYYMMDD>`, e.g. `David - 20260105`) while the
  master sheet keeps every contact. With no owner field, only the master sheet is
  created.
- **Header row** — blue fill `#0070C0`, white bold text, centered + wrapped, tall
  row, and **frozen** so it stays visible while scrolling. Columns have sensible
  widths.
- **Output folder** — defaults to `./exports/sales_prospector/` (git-ignored;
  exports are local artifacts).
- **Filename** — deterministic, derived from the company and sheet content (no
  wall clock), so identical input produces a byte-identical file.

The export is a **separate function** from `run_dry_run`; the pipeline itself
performs no file I/O and remains deterministic.

## Tests

- [`tests/tools/test_sales_prospector.py`](../tests/tools/test_sales_prospector.py)
  — determinism, mock-only/no-side-effects, Cerberus approval contract.
- [`tests/tools/test_sales_prospector_export.py`](../tests/tools/test_sales_prospector_export.py)
  — `.xlsx` export: columns, metadata, valid-workbook round-trip, determinism.
- [`tests/gateway/test_api_server_sales_prospector.py`](../tests/gateway/test_api_server_sales_prospector.py)
  — endpoint auth, validation, and dry-run contract.
