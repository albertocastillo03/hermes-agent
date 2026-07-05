# Improvement proposals (local-only, safe-by-construction)

A mechanism for Hermes to say "this broke, here's why, here's what I'd
change" **without ever changing anything itself**. Implemented in
[`tools/improvement_proposal.py`](../../../tools/improvement_proposal.py),
used automatically by the failure path in
[`scripts/hermes_dry_run_loop.py`](../../../scripts/hermes_dry_run_loop.py),
and safe to call from anywhere else that wants to record a similar finding.

## What it is allowed to do

- Detect a failure (an exception, a test failure, an inspected log).
- Write one markdown file describing the issue, evidence, cause, and a
  proposed fix.
- Nothing else. `propose()` has no code path that edits source files, runs
  git, or reads/writes `tools/approval.py` — it only ever calls `open(...,
  "w")` on a new file under `improvement_proposals/` (git-ignored, local to
  your checkout).

Hermes (or a future agent turn) may read these files and, **only after you
explicitly approve**, act on one: edit the named files, run tests, and only
then ask to commit. `propose()` itself never does any of that, and it
actively refuses (`ValueError`) if asked to name `approval.py` as an affected
file.

## Proposal shape

Every proposal has exactly these fields (enforced by
`ImprovementProposal`):

| Field | Meaning |
| --- | --- |
| `observed_issue` | What went wrong, one or two sentences. |
| `evidence` | Log excerpt / traceback / failing test output. |
| `likely_cause` | Best-effort diagnosis — allowed to say "not yet diagnosed". |
| `proposed_fix` | What change would address it — allowed to say "not yet drafted". |
| `files_likely_affected` | Best-guess file list (never `approval.py`). |
| `risk_level` | `low` / `medium` / `high`. |
| `test_plan` | How you'd verify the fix. |
| `approval_question` | The literal yes/no question to ask the human. |

## Example

```python
from tools.improvement_proposal import propose

result = propose(
    observed_issue="model.provider 'ollama' is not a recognised provider",
    evidence="hermes doctor output: \"✗ model.provider 'ollama' is not a "
             "recognised provider (known: ..., ollama-cloud, ...)\"",
    likely_cause="config.yaml sets model.provider: \"ollama\" for local "
                 "Ollama, but the current provider registry only recognises "
                 "\"ollama-cloud\" and \"custom\"/\"lmstudio\" for local "
                 "OpenAI-compatible servers — \"ollama\" may be a removed or "
                 "renamed alias.",
    proposed_fix="Confirm with the user whether they want provider: \"custom\" "
                 "with base_url http://localhost:11434/v1 (already set), or "
                 "whether \"ollama\" should be restored as a recognised alias "
                 "in the provider registry.",
    files_likely_affected=["~/.hermes/config.yaml", "hermes_cli/config.py"],
    risk_level="low",
    test_plan="Run `hermes doctor` after the change and confirm the "
              "'model.provider' check passes.",
    approval_question="Switch config.yaml's model.provider to \"custom\" "
                       "(keeping the existing Ollama base_url), or would you "
                       "rather I look at restoring the \"ollama\" alias?",
)
# result == {"proposal_id": "IP-20260705-xxxxxxxx",
#            "file_path": "/abs/.../improvement_proposals/IP-....md",
#            "risk_level": "low", "applied": False, "committed": False}
```

This exact finding came out of the Phase 1 verification run (`hermes
doctor` flagged it) — see the Phase 4 report for the live proposal file this
produced.

## Reviewing proposals

```bash
ls improvement_proposals/
cat improvement_proposals/IP-*.md
```

There is no automatic surfacing beyond the file landing on disk — reviewing
and approving is a manual step by design.
