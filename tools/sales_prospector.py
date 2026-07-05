"""Sales Prospector dry-run pipeline (deterministic, local, mock-only).

This is the **first** end-to-end dry-run of the sales-prospecting workflow. It
chains five mock personas and one gatekeeper, entirely in-process:

    Sales Prospector → Atlas → Excel Analyst → Mercury → Kronos → Cerberus

Public input fields (all optional; the campaign context is preserved and echoed
back, never ignored):

* ``company``       — the org running the campaign (the sender), e.g. "Indra".
* ``sector``        — the target sector/industry, e.g. "IT consulting".
* ``geography``     — where prospects are located, e.g. "Spain".
* ``campaign_goal`` — the outreach objective, e.g. "book a meeting with IT
  decision makers". The target job title is *derived* from this.
* ``count``         — how many mock prospects to generate (1..25, default 3).

Design constraints (v1):

* **Deterministic** — the same ``query`` always produces byte-identical output.
  All "randomness" is derived from a SHA-256 seed of the normalized query, and
  every timestamp is offset from a fixed anchor date (never the wall clock).
* **Local & mock-only** — no network, no secrets, no external providers. It does
  NOT call Lusha/Apollo/Apify, does NOT send email, and does NOT touch a
  calendar. Every prospect/email/event is synthetic; emails use the reserved
  ``.example`` TLD so nothing can accidentally resolve or be delivered.
* **Dry-run** — nothing is executed. The side-effecting steps (Mercury emails,
  Kronos events) are *proposed only*. **Cerberus** collects them as structured
  ``approval_required`` actions in the response. It deliberately does NOT import
  or modify ``tools/approval.py``; the existing approval engine is untouched and
  a caller may later route these actions through it.

The public entry point is :func:`run_dry_run`.
"""

from __future__ import annotations

import hashlib
from typing import Any, Dict, List

# Ordered persona pipeline. Kept as data so the response can echo it back.
PIPELINE = ["sales_prospector", "atlas", "excel_analyst", "mercury", "kronos", "cerberus"]

# Fixed anchor so scheduled dates are reproducible regardless of when this runs.
# (Deliberately NOT time.time()/datetime.now() — determinism over freshness.)
_ANCHOR_DATE = "2026-01-05"  # a Monday

# Deterministic mock pools. No PII — invented names on reserved domains.
_FIRST_NAMES = ["Ada", "Bruno", "Chika", "Diego", "Esme", "Farid", "Greta", "Hodei"]
_LAST_NAMES = ["Okafor", "Rossi", "Nakamura", "Silva", "Kaur", "Haddad", "Berg", "Ibarra"]
_COMPANIES = ["Northwind", "Acme", "Globex", "Initech", "Umbra", "Vantage", "Contoso", "Meridian"]
_SIZE_BANDS = ["1-10", "11-50", "51-200", "201-500", "501-1000"]
_REGIONS = ["EMEA", "AMER", "APAC", "LATAM"]

# Deterministic mapping from a campaign goal to the target job title. The first
# keyword that appears as a whole word in the goal wins; falls back to a generic
# title. Multi-word keys are matched as phrases.
_ROLE_KEYWORDS: List[tuple[str, str]] = [
    ("information technology", "IT Director"),
    ("it", "IT Director"),
    ("cto", "CTO"),
    ("cio", "CIO"),
    ("engineering", "Head of Engineering"),
    ("product", "Head of Product"),
    ("marketing", "Marketing Director"),
    ("sales", "Head of Sales"),
    ("finance", "Finance Director"),
    ("procurement", "Procurement Manager"),
    ("operations", "Operations Director"),
    ("human resources", "Head of HR"),
    ("hr", "Head of HR"),
]
_DEFAULT_TARGET_ROLE = "Decision Maker"

MAX_PROSPECTS = 25


# ---------------------------------------------------------------------------
# Deterministic helpers
# ---------------------------------------------------------------------------

def _seed(query: Dict[str, Any]) -> int:
    """Stable integer seed derived from the normalized query."""
    basis = "|".join(
        f"{k}={query.get(k)!r}"
        for k in ("company", "sector", "geography", "campaign_goal", "count")
    )
    digest = hashlib.sha256(basis.encode("utf-8")).hexdigest()
    return int(digest, 16)


def _pick(pool: List[str], seed: int, index: int) -> str:
    """Deterministically pick an element of *pool* for slot *index*."""
    return pool[(seed + index * 2654435761) % len(pool)]


def _slug(text: str) -> str:
    return "".join(ch.lower() if ch.isalnum() else "-" for ch in text).strip("-") or "n-a"


def _iso_date(day_offset: int) -> str:
    """Anchor date + *day_offset* days, as ``YYYY-MM-DD`` (pure arithmetic)."""
    y, m, d = (int(p) for p in _ANCHOR_DATE.split("-"))
    # Simple, dependency-free date add within a small horizon (days_in_month clamp).
    import datetime

    return (datetime.date(y, m, d) + datetime.timedelta(days=day_offset)).isoformat()


def _pluralize_word(word: str) -> str:
    """Pluralize a single English word with the common rules.

    Crucially, words already ending in ``s`` are left unchanged so we never
    produce doubled forms like "Sales" → "Saless".
    """
    lower = word.lower()
    if lower.endswith("s"):
        return word  # "Sales" stays "Sales" (avoids "Saless")
    if lower.endswith(("x", "z", "ch", "sh")):
        return word + "es"
    if lower.endswith("y") and len(word) > 1 and word[-2].lower() not in "aeiou":
        return word[:-1] + "ies"
    return word + "s"


def pluralize_role(role: str) -> str:
    """Pluralize a job title correctly.

    Handles "<Head> of <X>" by pluralizing the head noun ("Head of Sales" →
    "Heads of Sales") and otherwise pluralizes the final word ("IT Director" →
    "IT Directors"). Never blindly appends ``s`` to the whole string.
    """
    role = role.strip()
    if not role:
        return role
    if " of " in role:
        head, rest = role.split(" of ", 1)
        return f"{_pluralize_word(head)} of {rest}"
    words = role.split()
    words[-1] = _pluralize_word(words[-1])
    return " ".join(words)


def derive_target_role(campaign_goal: str) -> str:
    """Derive the target job title from the free-text campaign goal.

    Deterministic, keyword-based, whole-word matching. Falls back to a generic
    title when no keyword is recognized.
    """
    padded = " " + "".join(
        ch.lower() if ch.isalnum() else " " for ch in campaign_goal
    ) + " "
    for keyword, role in _ROLE_KEYWORDS:
        if f" {keyword} " in padded:
            return role
    return _DEFAULT_TARGET_ROLE


def normalize_query(raw: Any) -> Dict[str, Any]:
    """Coerce arbitrary input into the canonical public query shape.

    Preserves the caller's campaign context (``company``, ``sector``,
    ``geography``, ``campaign_goal``); unknown/missing fields fall back to
    deterministic defaults so the pipeline always has something to work with.
    """
    raw = raw if isinstance(raw, dict) else {}

    def _str(key: str, default: str) -> str:
        val = raw.get(key)
        return val.strip() if isinstance(val, str) and val.strip() else default

    count = raw.get("count", 3)
    try:
        count = int(count)
    except (TypeError, ValueError):
        count = 3
    count = max(1, min(MAX_PROSPECTS, count))

    return {
        "company": _str("company", "our team"),
        "sector": _str("sector", "software"),
        "geography": _str("geography", "Remote"),
        "campaign_goal": _str("campaign_goal", "generate qualified leads"),
        "count": count,
    }


# ---------------------------------------------------------------------------
# Personas
# ---------------------------------------------------------------------------

def _sales_prospector(query: Dict[str, Any], seed: int, target_role: str) -> List[Dict[str, Any]]:
    """Generate deterministic mock prospects (no Lusha/Apollo/Apify).

    Prospects are contextually derived from the campaign: they hold the
    goal-derived ``target_role``, sit in the requested ``sector`` and
    ``geography``, and work at mock target companies (distinct from the sender
    ``company``).
    """
    prospects: List[Dict[str, Any]] = []
    for i in range(query["count"]):
        first = _pick(_FIRST_NAMES, seed, i)
        last = _pick(_LAST_NAMES, seed, i + 1)
        company = _pick(_COMPANIES, seed, i + 2)
        domain = f"{_slug(company)}.example"  # reserved TLD: never resolves/delivers
        prospects.append({
            "id": f"prospect_{i + 1:02d}",
            "full_name": f"{first} {last}",
            "title": target_role,
            "company": company,
            "company_domain": domain,
            "email": f"{first.lower()}.{last.lower()}@{domain}",  # SYNTHETIC / mock
            "sector": query["sector"],
            "geography": query["geography"],
            "source": "mock",
        })
    return prospects


def _atlas(prospects: List[Dict[str, Any]], seed: int) -> None:
    """Enrich each prospect with deterministic mock firmographics (in place)."""
    for i, p in enumerate(prospects):
        size = _pick(_SIZE_BANDS, seed, i + 3)
        region = _pick(_REGIONS, seed, i + 4)
        # Confidence is deterministic and bounded — purely illustrative.
        confidence = 50 + ((seed + i * 17) % 50)
        p["enrichment"] = {
            "company_size_band": size,
            "region": region,
            "confidence": confidence,
            "source": "mock",
        }


def _excel_analyst(prospects: List[Dict[str, Any]], seed: int) -> Dict[str, Any]:
    """Score/rank prospects and produce aggregate analytics (deterministic)."""
    for i, p in enumerate(prospects):
        conf = p.get("enrichment", {}).get("confidence", 50)
        # Fit score blends confidence with a stable per-slot component.
        fit = (conf + ((seed + i * 31) % 40)) // 2
        p["fit_score"] = max(0, min(100, fit))

    ranked = sorted(prospects, key=lambda p: (-p["fit_score"], p["id"]))
    for rank, p in enumerate(ranked, start=1):
        p["rank"] = rank

    band_distribution: Dict[str, int] = {}
    for p in prospects:
        band = p.get("enrichment", {}).get("company_size_band", "unknown")
        band_distribution[band] = band_distribution.get(band, 0) + 1

    scores = [p["fit_score"] for p in prospects]
    avg = round(sum(scores) / len(scores), 2) if scores else 0
    return {
        "prospect_count": len(prospects),
        "average_fit_score": avg,
        "top_prospect_id": ranked[0]["id"] if ranked else None,
        "size_band_distribution": band_distribution,
        "ranking": [{"id": p["id"], "rank": p["rank"], "fit_score": p["fit_score"]} for p in ranked],
    }


def _mercury(
    prospects: List[Dict[str, Any]], query: Dict[str, Any], target_role: str
) -> List[Dict[str, Any]]:
    """Draft outreach emails. DRY-RUN — nothing is sent.

    Copy is grounded in the campaign context (sender ``company``, target
    ``sector``/``geography``, ``campaign_goal``) and uses correctly pluralized
    titles.
    """
    roles_plural = pluralize_role(target_role)
    drafts: List[Dict[str, Any]] = []
    for p in prospects:
        first = p["full_name"].split(" ", 1)[0]
        drafts.append({
            "prospect_id": p["id"],
            "to": p["email"],  # synthetic address, never delivered
            "subject": f"{query['company']} × {p['company']}: quick idea",
            "body_preview": (
                f"Hi {first}, I'm reaching out from {query['company']} to "
                f"{roles_plural} in {query['sector']} across {query['geography']}. "
                f"Our goal: {query['campaign_goal']}. "
                f"Would a short intro call be useful?"
            ),
            "delivered": False,
        })
    return drafts


def _kronos(
    analysis: Dict[str, Any], prospects: List[Dict[str, Any]], query: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """Propose follow-up calendar events. DRY-RUN — nothing is created."""
    by_id = {p["id"]: p for p in prospects}
    events: List[Dict[str, Any]] = []
    # One proposed follow-up per ranked prospect, spread across business days.
    for offset, entry in enumerate(analysis.get("ranking", []), start=1):
        p = by_id.get(entry["id"])
        if p is None:
            continue
        events.append({
            "prospect_id": p["id"],
            "title": f"Follow-up: {p['full_name']} — {p['company']} ({query['sector']})",
            "date": _iso_date(offset),
            "start": "10:00",
            "end": "10:30",
            "attendees": [p["email"]],  # synthetic
            "created": False,
        })
    return events


def _cerberus(emails: List[Dict[str, Any]], events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Gatekeeper: turn side-effecting steps into structured approval requests.

    Deliberately independent of ``tools/approval.py`` — v1 surfaces the
    ``approval_required`` actions in the response so the existing approval
    engine stays untouched. Every action is marked ``dry_run`` and unexecuted.
    """
    actions: List[Dict[str, Any]] = []
    for i, email in enumerate(emails, start=1):
        actions.append({
            "action_id": f"action_email_{i:02d}",
            "type": "send_email",
            "persona": "mercury",
            "status": "approval_required",
            "summary": f"Send outreach email to {email['to']}",
            "payload": email,
            "dry_run": True,
        })
    for i, event in enumerate(events, start=1):
        actions.append({
            "action_id": f"action_event_{i:02d}",
            "type": "schedule_event",
            "persona": "kronos",
            "status": "approval_required",
            "summary": f"Schedule '{event['title']}' on {event['date']}",
            "payload": event,
            "dry_run": True,
        })
    return actions


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def run_dry_run(raw_query: Any) -> Dict[str, Any]:
    """Run the full deterministic, mock-only, dry-run prospecting pipeline.

    Args:
        raw_query: mapping with optional ``company``, ``sector``, ``geography``,
            ``campaign_goal``, ``count`` keys. Anything else is normalized to
            defaults; recognized fields are preserved and echoed back.

    Returns:
        A JSON-serializable result dict. Nothing is executed; all side-effecting
        steps appear under ``approvals_required`` as ``approval_required``
        actions produced by Cerberus.
    """
    query = normalize_query(raw_query)
    seed = _seed(query)
    target_role = derive_target_role(query["campaign_goal"])

    # Echo the campaign context explicitly so callers can see it was honored.
    request_context = {
        "company": query["company"],
        "sector": query["sector"],
        "geography": query["geography"],
        "campaign_goal": query["campaign_goal"],
        "target_role": target_role,
    }

    stages: List[Dict[str, Any]] = []

    prospects = _sales_prospector(query, seed, target_role)
    stages.append({"persona": "sales_prospector", "status": "ok", "produced": len(prospects),
                   "description": "Generated mock prospects from campaign context (no external providers)."})

    _atlas(prospects, seed)
    stages.append({"persona": "atlas", "status": "ok", "produced": len(prospects),
                   "description": "Enriched prospects with mock firmographics."})

    analysis = _excel_analyst(prospects, seed)
    stages.append({"persona": "excel_analyst", "status": "ok", "produced": len(prospects),
                   "description": "Scored and ranked prospects; computed aggregates."})

    emails = _mercury(prospects, query, target_role)
    stages.append({"persona": "mercury", "status": "ok", "produced": len(emails),
                   "description": "Drafted outreach emails (dry-run, not sent)."})

    events = _kronos(analysis, prospects, query)
    stages.append({"persona": "kronos", "status": "ok", "produced": len(events),
                   "description": "Proposed follow-up events (dry-run, not created)."})

    approvals = _cerberus(emails, events)
    stages.append({"persona": "cerberus", "status": "ok", "produced": len(approvals),
                   "description": "Collected side-effects as approval_required actions."})

    return {
        "object": "hermes.sales_prospector.dry_run",
        "version": 1,
        "dry_run": True,
        "mock_only": True,
        "pipeline": list(PIPELINE),
        "query": query,
        "request_context": request_context,
        "stages": stages,
        "prospects": prospects,
        "analysis": analysis,
        "approvals_required": approvals,
        "warnings": [],
        "notes": (
            "Deterministic mock-only dry-run. No external services were contacted, "
            "no email was sent, and no calendar event was created. Approve the "
            "actions under 'approvals_required' to act on them later."
        ),
    }
