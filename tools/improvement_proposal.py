"""Local-only improvement-proposal capture.

When something in a dry-run loop fails or looks wrong, this module writes a
structured, human-readable proposal to disk for the operator to review. It is
deliberately inert: it can only **write a markdown file describing** an
observed issue and a suggested fix. It never edits source files, never runs
git, and never touches ``tools/approval.py`` or any safety/approval code —
those changes, if any, are made by a human (or a future agent turn) only
after reading the proposal and deciding to act.

Proposals accumulate under ``./improvement_proposals/`` (repo-relative,
git-ignored — mirrors how ``exports/`` holds generated Excel files) unless a
different directory is given.
"""

from __future__ import annotations

import datetime
import hashlib
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

DEFAULT_PROPOSALS_DIR = "improvement_proposals"

_RISK_LEVELS = ("low", "medium", "high")


@dataclass
class ImprovementProposal:
    """One observed-issue -> proposed-fix record. Nothing here executes anything."""

    observed_issue: str
    evidence: str
    likely_cause: str
    proposed_fix: str
    files_likely_affected: List[str] = field(default_factory=list)
    risk_level: str = "low"
    test_plan: str = ""
    approval_question: str = ""
    # Metadata, filled in by propose() if not given explicitly.
    proposal_id: str = ""
    created_at: str = ""

    def __post_init__(self) -> None:
        if self.risk_level not in _RISK_LEVELS:
            raise ValueError(f"risk_level must be one of {_RISK_LEVELS}, got {self.risk_level!r}")

    def to_markdown(self) -> str:
        files = "\n".join(f"- `{f}`" for f in self.files_likely_affected) or "- (none identified)"
        return f"""# Improvement Proposal {self.proposal_id}

**Created:** {self.created_at}
**Risk level:** {self.risk_level}
**Status:** pending approval — NOT applied, NOT committed

## Observed issue
{self.observed_issue}

## Evidence
{self.evidence}

## Likely cause
{self.likely_cause}

## Proposed fix
{self.proposed_fix}

## Files likely affected
{files}

## Test plan
{self.test_plan or "(not specified)"}

## Approval question
{self.approval_question or "Approve this change?"}

---
*This file was generated automatically by a dry-run loop. No code was changed,
no commit was made, and no safety/approval logic was touched. A human must
explicitly approve before any of the above is implemented.*
"""


def _proposal_id(observed_issue: str, created_at: str) -> str:
    digest = hashlib.sha256(f"{observed_issue}|{created_at}".encode("utf-8")).hexdigest()[:8]
    date_part = created_at.split("T")[0].replace("-", "")
    return f"IP-{date_part}-{digest}"


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:60] or "issue"


def propose(
    observed_issue: str,
    evidence: str,
    likely_cause: str,
    proposed_fix: str,
    *,
    files_likely_affected: Optional[List[str]] = None,
    risk_level: str = "low",
    test_plan: str = "",
    approval_question: str = "",
    proposals_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """Write one improvement proposal to disk and return its metadata.

    This function only ever writes a new markdown file. It does not import or
    call anything from ``tools/write_file`` semantics on source files, does not
    shell out to git, and does not read or modify ``tools/approval.py`` or any
    other approval/safety code — by construction, it has no code path that
    could do so.
    """
    # SAFETY-RAIL (guards the constructor contract, not a runtime bypass):
    # a proposal must never claim approval.py as a file it wants changed.
    affected = list(files_likely_affected or [])
    if any("approval.py" in f for f in affected):
        raise ValueError(
            "Refusing to propose changes to approval.py — safety/approval code "
            "is out of scope for automatic improvement proposals."
        )

    created_at = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    proposal_id = _proposal_id(observed_issue, created_at)
    proposal = ImprovementProposal(
        observed_issue=observed_issue,
        evidence=evidence,
        likely_cause=likely_cause,
        proposed_fix=proposed_fix,
        files_likely_affected=affected,
        risk_level=risk_level,
        test_plan=test_plan,
        approval_question=approval_question,
        proposal_id=proposal_id,
        created_at=created_at,
    )

    target_dir = proposals_dir or DEFAULT_PROPOSALS_DIR
    os.makedirs(target_dir, exist_ok=True)
    file_path = os.path.join(target_dir, f"{proposal_id}-{_slug(observed_issue)}.md")
    with open(file_path, "w", encoding="utf-8") as fh:
        fh.write(proposal.to_markdown())

    return {
        "proposal_id": proposal_id,
        "file_path": os.path.abspath(file_path),
        "risk_level": risk_level,
        "applied": False,
        "committed": False,
    }


def list_proposals(proposals_dir: Optional[str] = None) -> List[str]:
    """Return paths of all proposal files currently on disk (newest last)."""
    target_dir = proposals_dir or DEFAULT_PROPOSALS_DIR
    if not os.path.isdir(target_dir):
        return []
    names = sorted(f for f in os.listdir(target_dir) if f.startswith("IP-") and f.endswith(".md"))
    return [os.path.join(target_dir, n) for n in names]
