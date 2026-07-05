# Operator dry-run loop (sales prospecting)

The minimal loop for giving Hermes a task, observing a dry-run plan, and
capturing what needs improving — safely, with nothing external ever
happening. Implemented as one script:
[`scripts/hermes_dry_run_loop.py`](../../../scripts/hermes_dry_run_loop.py).

```
User gives Hermes a task
  ↓
Hermes creates a dry-run plan            tools/sales_prospector.run_dry_run
  ↓
Routes through existing agents/tools     Sales Prospector → Atlas → Excel
                                          Analyst → Mercury → Kronos
  ↓
Produces proposed actions                approvals_required[] (send_email,
                                          schedule_event)
  ↓
Cerberus marks risky items               every action: status =
approval_required                        "approval_required", dry_run: true
  ↓
No external side effect happens          no email sent, no event created,
                                          no external API called
  ↓
Hermes logs what worked/failed           logs/dry_run_loop.jsonl (one line
                                          per run) + improvement_proposals/
                                          on failure
```

## Running a task

In-process (no gateway needed — the pipeline is pure deterministic Python):

```bash
python scripts/hermes_dry_run_loop.py \
  --company Indra --sector "IT consulting" --geography Spain \
  --campaign-goal "book a meeting with IT decision makers" \
  --count 3 --export-xlsx
```

Against a running API server instead (see
[local_startup.md](local_startup.md)):

```bash
python scripts/hermes_dry_run_loop.py --url http://127.0.0.1:8642 \
  --api-key "$API_SERVER_KEY" \
  --company Indra --sector "IT consulting" --geography Spain \
  --campaign-goal "book a meeting with IT decision makers"
```

Both modes print the same summary shape and append the same log line —
the only difference is whether the call goes through HTTP or straight into
`tools.sales_prospector`.

## What "no side effect" means here concretely

- `send_email` actions are Mercury's drafted copy, never handed to the
  `himalaya` skill.
- `schedule_event` actions are Kronos's proposed events, never handed to the
  `cronjob` tool.
- `export_xlsx` writes a **local** `.xlsx` file under `exports/` — this is the
  one real filesystem write the loop performs, and it is the *artifact*
  (a calling-list spreadsheet), not a side effect on an external system.
- Nothing in this loop imports `himalaya`, `cronjob_tools`, `memory_tool`, or
  any network client other than the optional loopback HTTP call in `--url`
  mode.

## Logs — what worked, what failed

Every invocation appends one JSON line to `logs/dry_run_loop.jsonl`
(git-ignored):

```json
{"timestamp": "...", "mode": "in_process", "query": {...}, "outcome": "ok",
 "summary": {"dry_run": true, "mock_only": true, "prospect_count": 3,
             "approvals_required_count": 6,
             "approval_types": ["schedule_event", "send_email"],
             "all_gated": true, "export_file": "/abs/path/....xlsx"}}
```

A failed run instead logs `"outcome": "failed"` with the error message, and
also writes an improvement-proposal file (see
[improvement_proposals.md](improvement_proposals.md)) — the loop never
crashes silently and never swallows the failure without a trace.

## Extending the loop to another workflow

The script only knows about the sales-prospector pipeline today
(`_run_in_process` / `_run_over_http` call
`tools.sales_prospector.run_dry_run`). To route a different task type through
the same loop shape, add a new `--task-type` branch that calls the
equivalent `run_dry_run`-shaped function for that workflow — keep the
log/proposal wrapper (`_append_log`, the `try/except` → `propose(...)`)
shared rather than duplicating it per workflow.
