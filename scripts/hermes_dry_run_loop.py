#!/usr/bin/env python3
"""Hermes operator dry-run loop (sales-prospecting workflow).

The smallest possible version of the loop:

    task -> dry-run plan -> existing agents/skills/tools -> proposed actions
    -> Cerberus marks risky steps approval_required -> no side effect happens
    -> a run log records what worked / failed, and failures become a local
       improvement proposal (never auto-applied, never auto-committed).

Runs entirely in-process against ``tools/sales_prospector.run_dry_run`` — no
gateway process required. Pass ``--url`` to instead exercise a running API
server's ``POST /v1/sales/prospect/dry-run`` (see
docs/hermes/runtime/local_startup.md for how to start one).

Usage:
    python scripts/hermes_dry_run_loop.py --company Indra --sector "IT consulting" \\
        --geography Spain --campaign-goal "book a meeting with IT decision makers" \\
        --export-xlsx

    # Against a live API server instead of in-process:
    python scripts/hermes_dry_run_loop.py --url http://127.0.0.1:8642 \\
        --api-key "$API_SERVER_KEY" --company Indra ...

Every invocation appends one line to ``logs/dry_run_loop.jsonl`` (repo-relative,
git-ignored) recording the input, outcome, and a short summary — this is the
"observe what breaks" trail. A raised exception is caught, logged, and turned
into a proposal file under ``improvement_proposals/`` instead of crashing
silently or being swallowed.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DEFAULT_LOG_PATH = os.path.join("logs", "dry_run_loop.jsonl")


def _build_query(args: argparse.Namespace) -> dict:
    query = {}
    if args.company:
        query["company"] = args.company
    if args.sector:
        query["sector"] = args.sector
    if args.geography:
        query["geography"] = args.geography
    if args.campaign_goal:
        query["campaign_goal"] = args.campaign_goal
    if args.count:
        query["count"] = args.count
    if args.export_xlsx:
        query["export_xlsx"] = True
    return query


def _run_in_process(query: dict) -> dict:
    """Run the dry-run pipeline directly (no HTTP, no gateway needed)."""
    from tools.sales_prospector import run_dry_run

    result = run_dry_run(query)
    if query.get("export_xlsx"):
        from tools.sales_prospector import export_leads_xlsx
        result["export"] = export_leads_xlsx(result)
    return result


def _run_over_http(url: str, api_key: str, query: dict) -> dict:
    import urllib.error
    import urllib.request

    body = json.dumps(query).encode("utf-8")
    req = urllib.request.Request(
        url.rstrip("/") + "/v1/sales/prospect/dry-run",
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            **({"Authorization": f"Bearer {api_key}"} if api_key else {}),
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 - loopback only by convention
        return json.loads(resp.read().decode("utf-8"))


def _summarize(result: dict) -> dict:
    approvals = result.get("approvals_required", [])
    return {
        "dry_run": result.get("dry_run"),
        "mock_only": result.get("mock_only"),
        "prospect_count": len(result.get("prospects", [])),
        "approvals_required_count": len(approvals),
        "approval_types": sorted({a.get("type") for a in approvals}),
        "all_gated": all(a.get("status") == "approval_required" for a in approvals),
        "export_file": result.get("export", {}).get("file_path"),
    }


def _append_log(log_path: str, entry: dict) -> None:
    os.makedirs(os.path.dirname(log_path) or ".", exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, sort_keys=True) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--company", default="")
    parser.add_argument("--sector", default="")
    parser.add_argument("--geography", default="")
    parser.add_argument("--campaign-goal", default="")
    parser.add_argument("--count", type=int, default=0)
    parser.add_argument("--export-xlsx", action="store_true")
    parser.add_argument("--url", default="", help="Base URL of a running API server (omit to run in-process)")
    parser.add_argument("--api-key", default=os.environ.get("API_SERVER_KEY", ""))
    parser.add_argument("--log-path", default=DEFAULT_LOG_PATH)
    parser.add_argument("--quiet", action="store_true", help="Suppress the human-readable summary")
    args = parser.parse_args()

    query = _build_query(args)
    started_at = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")

    try:
        if args.url:
            result = _run_over_http(args.url, args.api_key, query)
        else:
            result = _run_in_process(query)
        summary = _summarize(result)
        _append_log(args.log_path, {
            "timestamp": started_at,
            "mode": "http" if args.url else "in_process",
            "query": query,
            "outcome": "ok",
            "summary": summary,
        })
        if not args.quiet:
            print(json.dumps({"outcome": "ok", "summary": summary}, indent=2))
        return 0

    except Exception as exc:  # noqa: BLE001 - this loop's job is to capture, not to propagate
        tb = traceback.format_exc()
        _append_log(args.log_path, {
            "timestamp": started_at,
            "mode": "http" if args.url else "in_process",
            "query": query,
            "outcome": "failed",
            "error": str(exc),
        })

        from tools.improvement_proposal import propose

        proposal = propose(
            observed_issue=f"hermes_dry_run_loop raised {type(exc).__name__}: {exc}",
            evidence=tb,
            likely_cause="(not yet diagnosed — captured automatically from a loop failure)",
            proposed_fix="(not yet drafted — review the evidence and fill this in before approving)",
            files_likely_affected=["tools/sales_prospector.py", "scripts/hermes_dry_run_loop.py"],
            risk_level="medium",
            test_plan="Reproduce with the same --company/--sector/... flags; add a regression test once root-caused.",
            approval_question="Investigate and approve a fix for this failure?",
        )
        if not args.quiet:
            print(json.dumps({"outcome": "failed", "error": str(exc),
                              "improvement_proposal": proposal}, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
