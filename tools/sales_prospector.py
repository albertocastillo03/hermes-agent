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
import os
from typing import Any, Dict, List

# Ordered persona pipeline. Kept as data so the response can echo it back.
PIPELINE = ["sales_prospector", "atlas", "excel_analyst", "mercury", "kronos", "cerberus"]

# Default local export folder for the Excel Analyst lead-list export. Relative to
# the current working directory; nothing here ever leaves the machine.
DEFAULT_EXPORT_DIR = os.path.join("exports", "sales_prospector")

# Visible columns of the exported commercial calling list, in exact order. This
# mirrors the real prospecting/calling sheet — internal analytics fields
# (rank/fit_score/dry_run/approval_status) are deliberately NOT shown here; they
# remain in the dry-run JSON only.
EXPORT_COLUMNS = [
    "Accion", "Contact name", "Mobile", "Mobile 2", "Company name", "Job title",
    "Work email", "Work email 2", "LinkedIn profile", "Industry", "Sub industry",
    "Employees in LinkedIn",
]

# Sensible per-column widths (Excel character units), aligned with EXPORT_COLUMNS.
_EXPORT_COL_WIDTHS = [22, 22, 16, 16, 24, 24, 30, 30, 34, 20, 20, 18]

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
    sector = query["sector"]
    broad_industry = _broad_industry(sector)
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
            "sector": sector,
            "sub_industry": sector,
            "industry": broad_industry,
            "geography": query["geography"],
            "source": "mock",
            # Contact-identifying fields are intentionally left blank in dry-run:
            # we never invent phone numbers or call LinkedIn. A real pipeline may
            # populate these; the exporter reads them if present.
        })
    return prospects


# Deterministic broad-industry bucket derived from the campaign sector. Whole-word
# keyword match; falls back to a title-cased copy of the sector. Purely a
# classification label — no external taxonomy service is contacted.
_INDUSTRY_KEYWORDS: List[tuple[str, str]] = [
    ("information technology", "Information Technology"),
    ("it", "Information Technology"),
    ("software", "Information Technology"),
    ("saas", "Information Technology"),
    ("tech", "Information Technology"),
    ("technology", "Information Technology"),
    ("healthcare", "Healthcare"),
    ("health", "Healthcare"),
    ("pharma", "Healthcare"),
    ("pharmaceutical", "Healthcare"),
    ("financial", "Financial Services"),
    ("finance", "Financial Services"),
    ("banking", "Financial Services"),
    ("bank", "Financial Services"),
    ("fintech", "Financial Services"),
    ("insurance", "Financial Services"),
    ("marketing", "Marketing & Advertising"),
    ("advertising", "Marketing & Advertising"),
    ("retail", "Retail"),
    ("ecommerce", "Retail"),
    ("manufacturing", "Manufacturing"),
]


def _broad_industry(sector: str) -> str:
    padded = " " + "".join(ch.lower() if ch.isalnum() else " " for ch in sector) + " "
    for keyword, industry in _INDUSTRY_KEYWORDS:
        if f" {keyword} " in padded:
            return industry
    return sector.title()


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


# ---------------------------------------------------------------------------
# Excel Analyst — local commercial calling-list .xlsx export
# ---------------------------------------------------------------------------
#
# A "real" Excel export using only the standard library. An .xlsx is a ZIP of
# XML parts, so we build a fully valid, styled workbook by hand — matching the
# repo's dependency-free approach to xlsx (see tools/read_extract.py, which
# reads xlsx the same way). No third-party library, no network, no secrets.
#
# The output mirrors the real prospecting/calling sheet: the 12 EXPORT_COLUMNS,
# a blue (#0070C0) header with white bold centered wrapped text, a frozen header
# row, and sensible column widths. Internal analytics fields stay in the JSON.
# The workbook is a plain local artifact; nothing is sent anywhere.

# Fixed ZIP timestamp so identical input yields byte-identical files (the DOS
# epoch; deliberately not the wall clock, matching the pipeline's determinism).
_ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)

# Header fill color (ARGB: opaque #0070C0) and the styled-header cell index.
_HEADER_FILL_ARGB = "FF0070C0"
_HEADER_STYLE_ID = 1
_HEADER_ROW_HEIGHT = 30  # tall header, like the reference sheet

_STYLES_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
    '<fonts count="2">'
    '<font><sz val="11"/><name val="Calibri"/></font>'
    f'<font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Calibri"/></font>'
    "</fonts>"
    '<fills count="3">'
    '<fill><patternFill patternType="none"/></fill>'
    '<fill><patternFill patternType="gray125"/></fill>'
    f'<fill><patternFill patternType="solid"><fgColor rgb="{_HEADER_FILL_ARGB}"/>'
    '<bgColor indexed="64"/></patternFill></fill>'
    "</fills>"
    '<borders count="1"><border/></borders>'
    '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
    '<cellXfs count="2">'
    '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
    '<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" '
    'applyFont="1" applyFill="1" applyAlignment="1">'
    '<alignment horizontal="center" vertical="center" wrapText="1"/></xf>'
    "</cellXfs>"
    '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
    "</styleSheet>"
)


def _xml_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _col_letter(index: int) -> str:
    """0-based column index → Excel column letters (0 → 'A', 26 → 'AA')."""
    letters = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        letters = chr(ord("A") + rem) + letters
    return letters


def _safe_sheet_name(name: str) -> str:
    """Canonical, logical sheet name: replace chars Excel forbids and cap at 31.

    Returns the plain name (spaces preserved, NOT XML-escaped) so it can be used
    verbatim as the single source of truth for both the workbook and the
    returned metadata. XML escaping happens only at serialization time.
    """
    cleaned = "".join(" " if ch in ':\\/?*[]' else ch for ch in name).strip()
    return cleaned[:31] or "Sheet1"


def _cell_xml(ref: str, value: Any, style: int = 0) -> str:
    """One cell. All commercial values are strings; ``style`` selects a cellXf."""
    s_attr = f' s="{style}"' if style else ""
    text = _xml_escape("" if value is None else str(value))
    return f'<c r="{ref}"{s_attr} t="inlineStr"><is><t xml:space="preserve">{text}</t></is></c>'


def _worksheet_xml(headers: List[str], rows: List[List[Any]], col_widths: List[int]) -> str:
    """Build one styled worksheet: frozen header row, blue header, column widths."""
    cols_xml = "".join(
        f'<col min="{i}" max="{i}" width="{w}" customWidth="1"/>'
        for i, w in enumerate(col_widths, start=1)
    )
    header_cells = "".join(
        _cell_xml(f"{_col_letter(c)}1", h, style=_HEADER_STYLE_ID)
        for c, h in enumerate(headers)
    )
    body = ""
    for r, values in enumerate(rows, start=2):
        cells = "".join(_cell_xml(f"{_col_letter(c)}{r}", v) for c, v in enumerate(values))
        body += f'<row r="{r}">{cells}</row>'
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<sheetViews><sheetView workbookViewId="0">'
        '<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>'
        '<selection pane="bottomLeft" activeCell="A2" sqref="A2"/>'
        "</sheetView></sheetViews>"
        '<sheetFormatPr defaultRowHeight="15"/>'
        f"<cols>{cols_xml}</cols>"
        f'<sheetData><row r="1" ht="{_HEADER_ROW_HEIGHT}" customHeight="1">{header_cells}</row>'
        f"{body}</sheetData>"
        "</worksheet>"
    )


def _xlsx_bytes(sheets: List[tuple], col_widths: List[int]) -> bytes:
    """Serialize a styled, multi-sheet workbook to valid .xlsx bytes (stdlib only).

    ``sheets`` is a list of ``(name, headers, rows)`` tuples; every sheet shares
    the same ``col_widths``.
    """
    import io
    import zipfile

    n = len(sheets)
    ws_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"
    ct_sheets = "".join(
        f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="{ws_type}"/>'
        for i in range(1, n + 1)
    )
    # ``name`` is already the canonical sheet name (see _build_sheets); only
    # XML-escape it for embedding so metadata and the workbook never diverge.
    wb_sheets = "".join(
        f'<sheet name="{_xml_escape(name)}" sheetId="{i}" r:id="rId{i}"/>'
        for i, (name, _, _) in enumerate(sheets, start=1)
    )
    wb_rels = "".join(
        f'<Relationship Id="rId{i}" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        f'Target="worksheets/sheet{i}.xml"/>'
        for i in range(1, n + 1)
    ) + (
        f'<Relationship Id="rId{n + 1}" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
        'Target="styles.xml"/>'
    )

    parts = {
        "[Content_Types].xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
            f"{ct_sheets}"
            "</Types>"
        ),
        "_rels/.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            "</Relationships>"
        ),
        "xl/workbook.xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            f"<sheets>{wb_sheets}</sheets>"
            "</workbook>"
        ),
        "xl/_rels/workbook.xml.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f"{wb_rels}"
            "</Relationships>"
        ),
        "xl/styles.xml": _STYLES_XML,
    }
    for i, (_, headers, rows) in enumerate(sheets, start=1):
        parts[f"xl/worksheets/sheet{i}.xml"] = _worksheet_xml(headers, rows, col_widths)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in parts.items():
            info = zipfile.ZipInfo(name, date_time=_ZIP_EPOCH)
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, content)
    return buf.getvalue()


# --- Mapping: dry-run prospect → commercial calling-list row -----------------

def _prospect_owner(p: Dict[str, Any]) -> str:
    """Owner/operator of a prospect, if the pipeline provided one (else "")."""
    owner = p.get("owner") or p.get("operator")
    return str(owner).strip() if owner else ""


def _commercial_row(p: Dict[str, Any]) -> List[Any]:
    """Map a prospect to the 12 commercial columns (EXPORT_COLUMNS order).

    Contact-identifying fields absent in dry-run (mobile, second email,
    LinkedIn) are left blank — we never invent phone numbers or call LinkedIn.
    ``Accion`` is blank by default: reserved for manual call notes.
    """
    employees = p.get("employees_in_linkedin") or p.get("enrichment", {}).get(
        "company_size_band", ""
    )
    return [
        p.get("accion", ""),                                  # Accion (blank unless preserved from input)
        p.get("full_name", ""),                               # Contact name
        p.get("mobile", ""),                                  # Mobile
        p.get("mobile_2", ""),                                # Mobile 2
        p.get("company", ""),                                 # Company name
        p.get("title", ""),                                   # Job title
        p.get("email", ""),                                   # Work email
        p.get("work_email_2", ""),                            # Work email 2
        p.get("linkedin_profile", ""),                        # LinkedIn profile (plain URL)
        p.get("industry") or p.get("sector", ""),             # Industry
        p.get("sub_industry") or p.get("sector", ""),         # Sub industry
        employees,                                            # Employees in LinkedIn
    ]


def _ordered_prospects(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    return sorted(
        result.get("prospects", []),
        key=lambda p: (p.get("rank", 1_000_000), p.get("id", "")),
    )


def _date_tag(result: Dict[str, Any]) -> str:
    """Deterministic YYYYMMDD tag from the fixed anchor date (no wall clock)."""
    return _ANCHOR_DATE.replace("-", "")


def _build_sheets(result: Dict[str, Any], date_tag: str) -> List[tuple]:
    """Master sheet plus, if any owner is present, one sheet per owner.

    The master ("Todos los contactos <tag>") always holds every row. When
    prospects carry an ``owner``/``operator``, additional per-owner sheets
    ("<owner> - <tag>") are appended while the master is preserved.
    """
    prospects = _ordered_prospects(result)
    master_rows = [_commercial_row(p) for p in prospects]
    # Names are canonicalized once here so the tuple name is the single source of
    # truth shared by the workbook XML and the returned metadata.
    sheets: List[tuple] = [
        (_safe_sheet_name(f"Todos los contactos {date_tag}"), EXPORT_COLUMNS, master_rows)
    ]

    owners: List[str] = []
    for p in prospects:
        owner = _prospect_owner(p)
        if owner and owner not in owners:
            owners.append(owner)
    for owner in owners:
        rows = [_commercial_row(p) for p in prospects if _prospect_owner(p) == owner]
        sheets.append((_safe_sheet_name(f"{owner} - {date_tag}"), EXPORT_COLUMNS, rows))
    return sheets


def _export_filename(result: Dict[str, Any], sheets: List[tuple]) -> str:
    """Deterministic filename derived from the campaign + sheet content."""
    company = result.get("query", {}).get("company", "leads")
    basis = repr([(name, rows) for name, _, rows in sheets])
    digest = hashlib.sha256(basis.encode("utf-8")).hexdigest()[:8]
    return f"leads_{_slug(str(company))}_{digest}.xlsx"


def export_leads_xlsx(
    result: Dict[str, Any],
    export_dir: str | None = None,
    date_tag: str | None = None,
) -> Dict[str, Any]:
    """Export a dry-run ``result`` to a local commercial calling-list .xlsx.

    Writes a genuinely valid, styled workbook (openable in Excel/LibreOffice) to
    a safe local folder using only the standard library. Fully mock-only and
    dry-run: it reads the in-memory result, touches no external service, sends
    no email, and creates no calendar event. Phone numbers and LinkedIn URLs are
    never invented — unknown contact fields are left blank.

    The visible sheet uses the commercial columns (``EXPORT_COLUMNS``); internal
    analytics fields (rank/fit_score/dry_run/approval_status) are not shown.

    Args:
        result: the dict returned by :func:`run_dry_run`.
        export_dir: destination folder; defaults to ``./exports/sales_prospector``.
        date_tag: ``YYYYMMDD`` tag used in sheet names; defaults to a deterministic
            value derived from the fixed anchor date.

    Returns:
        Export metadata: ``file_path``, ``row_count`` (contacts on the master
        sheet), ``dry_run`` (True), ``created_by`` ("excel_analyst"),
        ``columns``, ``sheets`` (all sheet names) and ``main_sheet``.
    """
    tag = date_tag or _date_tag(result)
    sheets = _build_sheets(result, tag)
    data = _xlsx_bytes(sheets, _EXPORT_COL_WIDTHS)

    target_dir = export_dir or DEFAULT_EXPORT_DIR
    os.makedirs(target_dir, exist_ok=True)
    file_path = os.path.join(target_dir, _export_filename(result, sheets))
    with open(file_path, "wb") as fh:
        fh.write(data)

    return {
        "file_path": os.path.abspath(file_path),
        "row_count": len(sheets[0][2]),  # contacts on the master sheet
        "dry_run": True,
        "created_by": "excel_analyst",
        "columns": list(EXPORT_COLUMNS),
        "sheets": [name for name, _, _ in sheets],
        "main_sheet": sheets[0][0],
    }
