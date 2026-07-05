"""Tests for the Sales Prospector dry-run pipeline (tools/sales_prospector.py).

Focused on the v1 guarantees: deterministic, local, mock-only, dry-run, and
Cerberus surfacing side effects as structured approval_required actions without
touching the approval engine. Also pins the two manual-validation regressions:
campaign context must be honored, and titles must not be mis-pluralized.
"""

import json

from tools.sales_prospector import (
    PIPELINE,
    derive_target_role,
    normalize_query,
    pluralize_role,
    run_dry_run,
)


def _base_query():
    return {
        "company": "Indra",
        "sector": "IT consulting",
        "geography": "Spain",
        "campaign_goal": "book a meeting with IT decision makers",
    }


class TestDeterminism:
    def test_same_query_is_byte_identical(self):
        a = run_dry_run(_base_query())
        b = run_dry_run(_base_query())
        assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)

    def test_different_query_differs(self):
        a = run_dry_run(_base_query())
        b = run_dry_run({**_base_query(), "sector": "healthcare"})
        assert a["prospects"] != b["prospects"]

    def test_no_wall_clock_dependency(self):
        """Proposed event dates come from the fixed anchor, not today."""
        result = run_dry_run(_base_query())
        events = [a for a in result["approvals_required"] if a["type"] == "schedule_event"]
        assert events
        for e in events:
            assert e["payload"]["date"].startswith("2026-01")


class TestCampaignContextHonored:
    """Regression: the public input fields must be preserved, not defaulted."""

    def test_query_echoes_public_fields(self):
        q = _base_query()
        r = run_dry_run(q)
        for key in ("company", "sector", "geography", "campaign_goal"):
            assert r["query"][key] == q[key], f"{key} was not preserved"

    def test_request_context_echoes_public_fields(self):
        q = _base_query()
        r = run_dry_run(q)
        for key in ("company", "sector", "geography", "campaign_goal"):
            assert r["request_context"][key] == q[key]

    def test_defaults_are_not_applied_when_fields_present(self):
        """The old bug: output silently became software/Remote/Head of Sales."""
        r = run_dry_run(_base_query())
        assert r["query"]["sector"] != "software"
        assert r["query"]["geography"] != "Remote"

    def test_prospects_derived_from_context(self):
        q = _base_query()
        r = run_dry_run(q)
        for p in r["prospects"]:
            assert p["sector"] == q["sector"]
            assert p["geography"] == q["geography"]
            # Title derived from the campaign goal ("IT decision makers").
            assert p["title"] == "IT Director"

    def test_target_role_derived_from_goal(self):
        assert derive_target_role("book a meeting with IT decision makers") == "IT Director"
        assert derive_target_role("grow our sales pipeline") == "Head of Sales"
        assert derive_target_role("reach marketing leaders") == "Marketing Director"
        # "with" contains the substring "it" but not the whole word — must not match IT.
        assert derive_target_role("connect with founders") == "Decision Maker"

    def test_email_copy_references_company_and_goal(self):
        q = _base_query()
        r = run_dry_run(q)
        body = r["approvals_required"][0]["payload"]["body_preview"]
        assert "Indra" in body
        assert q["campaign_goal"] in body


class TestTitlePluralization:
    """Regression: 'Head of Sales' must not become 'Head of Saless'."""

    def test_no_saless_anywhere_in_output(self):
        # Force the Head-of-Sales path via a sales campaign goal.
        r = run_dry_run({**_base_query(), "campaign_goal": "grow the sales pipeline"})
        assert "Saless" not in json.dumps(r)

    def test_default_campaign_produces_no_saless(self):
        r = run_dry_run({})  # default goal falls back to generic Decision Maker
        assert "Saless" not in json.dumps(r)

    def test_pluralize_role_cases(self):
        assert pluralize_role("Head of Sales") == "Heads of Sales"
        assert pluralize_role("IT Director") == "IT Directors"
        assert pluralize_role("Decision Maker") == "Decision Makers"
        assert pluralize_role("VP Sales") == "VP Sales"  # already ends in 's'
        assert "Saless" not in pluralize_role("Head of Sales")


class TestShapeAndFlags:
    def test_top_level_flags(self):
        r = run_dry_run(_base_query())
        assert r["dry_run"] is True
        assert r["mock_only"] is True
        assert r["object"] == "hermes.sales_prospector.dry_run"
        assert r["pipeline"] == list(PIPELINE)

    def test_all_six_personas_ran(self):
        r = run_dry_run(_base_query())
        assert [s["persona"] for s in r["stages"]] == list(PIPELINE)
        assert all(s["status"] == "ok" for s in r["stages"])

    def test_count_produces_that_many_prospects(self):
        r = run_dry_run({**_base_query(), "count": 5})
        assert len(r["prospects"]) == 5

    def test_analysis_ranks_all_prospects(self):
        r = run_dry_run({**_base_query(), "count": 4})
        ranks = sorted(p["rank"] for p in r["prospects"])
        assert ranks == [1, 2, 3, 4]


class TestMockOnlyNoSideEffects:
    def test_emails_are_synthetic_and_undelivered(self):
        r = run_dry_run(_base_query())
        for p in r["prospects"]:
            assert p["email"].endswith(".example")  # reserved TLD; never delivers
            assert p["source"] == "mock"

    def test_no_email_marked_delivered(self):
        r = run_dry_run(_base_query())
        emails = [a for a in r["approvals_required"] if a["type"] == "send_email"]
        assert emails
        assert all(a["payload"]["delivered"] is False for a in emails)

    def test_no_event_marked_created(self):
        r = run_dry_run(_base_query())
        events = [a for a in r["approvals_required"] if a["type"] == "schedule_event"]
        assert events
        assert all(a["payload"]["created"] is False for a in events)


class TestCerberusApprovals:
    def test_all_actions_are_approval_required_and_dry_run(self):
        r = run_dry_run(_base_query())
        assert r["approvals_required"]
        for action in r["approvals_required"]:
            assert action["status"] == "approval_required"
            assert action["dry_run"] is True
            assert action["type"] in {"send_email", "schedule_event"}
            assert action["action_id"]
            assert action["persona"] in {"mercury", "kronos"}

    def test_does_not_import_approval_engine(self):
        """Cerberus must not pull in tools/approval.py (v1 keeps it untouched)."""
        import sys

        sys.modules.pop("tools.approval", None)
        run_dry_run(_base_query())
        assert "tools.approval" not in sys.modules


class TestNormalization:
    def test_defaults_for_empty_input(self):
        q = normalize_query({})
        assert q["count"] == 3
        assert q["company"] and q["sector"] and q["geography"] and q["campaign_goal"]

    def test_count_is_clamped(self):
        assert normalize_query({"count": 0})["count"] == 1
        assert normalize_query({"count": 9999})["count"] == 25

    def test_non_dict_input_is_tolerated(self):
        assert normalize_query(None)["count"] == 3
        assert normalize_query("garbage")["count"] == 3

    def test_bad_count_falls_back(self):
        assert normalize_query({"count": "abc"})["count"] == 3

    def test_public_fields_preserved(self):
        q = normalize_query(_base_query())
        assert q["company"] == "Indra"
        assert q["sector"] == "IT consulting"
        assert q["geography"] == "Spain"
