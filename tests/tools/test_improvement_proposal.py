"""Tests for tools/improvement_proposal.py.

Confirms the mechanism is safe-by-construction: it only ever writes a
markdown file describing an issue, never touches approval.py, never edits
other source files, and never runs git.
"""

import os

import pytest

from tools.improvement_proposal import list_proposals, propose


def test_propose_writes_markdown_with_required_fields(tmp_path):
    result = propose(
        observed_issue="model.provider 'ollama' is not recognised",
        evidence="hermes doctor: ✗ model.provider 'ollama' is not a recognised provider",
        likely_cause="provider registry lacks an 'ollama' alias",
        proposed_fix="switch to provider: custom with the existing base_url",
        files_likely_affected=["~/.hermes/config.yaml"],
        risk_level="low",
        test_plan="re-run hermes doctor",
        approval_question="Switch provider to custom?",
        proposals_dir=str(tmp_path),
    )
    assert result["applied"] is False
    assert result["committed"] is False
    assert os.path.isfile(result["file_path"])

    content = open(result["file_path"], encoding="utf-8").read()
    for expected in (
        "Observed issue", "Evidence", "Likely cause", "Proposed fix",
        "Files likely affected", "Test plan", "Approval question",
        "model.provider 'ollama'", "NOT applied, NOT committed",
    ):
        assert expected in content


def test_propose_refuses_approval_py_as_affected_file(tmp_path):
    with pytest.raises(ValueError, match="approval.py"):
        propose(
            observed_issue="x", evidence="x", likely_cause="x", proposed_fix="x",
            files_likely_affected=["tools/approval.py"],
            proposals_dir=str(tmp_path),
        )
    assert list_proposals(str(tmp_path)) == []


def test_invalid_risk_level_rejected(tmp_path):
    with pytest.raises(ValueError, match="risk_level"):
        propose(
            observed_issue="x", evidence="x", likely_cause="x", proposed_fix="x",
            risk_level="extreme",
            proposals_dir=str(tmp_path),
        )


def test_list_proposals_returns_only_proposal_files(tmp_path):
    (tmp_path / "not-a-proposal.txt").write_text("noise")
    propose("issue one", "ev", "cause", "fix", proposals_dir=str(tmp_path))
    propose("issue two", "ev", "cause", "fix", proposals_dir=str(tmp_path))
    files = list_proposals(str(tmp_path))
    assert len(files) == 2
    assert all(os.path.basename(f).startswith("IP-") for f in files)


def test_default_dir_is_repo_relative_and_gitignored():
    from tools.improvement_proposal import DEFAULT_PROPOSALS_DIR

    assert DEFAULT_PROPOSALS_DIR == "improvement_proposals"


def test_propose_never_imports_git_or_write_file_tools():
    """Static guard: the module has no code path to git or source-editing tools."""
    import inspect

    import tools.improvement_proposal as mod

    src = inspect.getsource(mod)
    for forbidden in ("subprocess", "import git", "tools.approval", "tools.file_tools"):
        assert forbidden not in src
