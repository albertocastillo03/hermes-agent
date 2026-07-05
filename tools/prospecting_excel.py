"""Reference implementation of the Hermes prospecting Excel workflow contract.

Executable counterpart of ``docs/hermes/workflows/prospecting_excel_workflow.md``
and the ``prospecting-*`` skills under ``skills/productivity/``. Everything here
is local, deterministic, and dry-run:

* **Read** — reuses the repo's stdlib xlsx reader (``tools/read_extract.py``).
* **Write** — reuses ``tools/sales_prospector.py::export_leads_xlsx``.
* **Route** — produces a dry-run summary whose side effects (email via the
  himalaya skill, reminders via the cronjob tool, memory writes) are emitted as
  structured ``approval_required`` actions (Cerberus pattern). Nothing is
  executed here; ``tools/approval.py`` is untouched.

Confirmed ``Accion`` semantics (v1):

* ``NC``  — No Contesta: did not pick up the phone → draft an intro email.
* ``NTD`` — Not the (target) decision maker → capture the real target in memory.
* ``RLL`` — Recall later: call again at the indicated date/time → reminder.
* blank   — not yet worked → no action.
* anything not confidently NC/NTD/RLL → ``MANUAL_REVIEW`` (never guessed).

No data is ever invented: phones/emails/URLs are preserved or left blank, and
ambiguous recall dates route to manual review instead of being guessed.
"""

from __future__ import annotations

import datetime
import re
import zipfile
from typing import Any, Dict, List, Optional, Tuple

from tools.sales_prospector import EXPORT_COLUMNS

ACTION_CODES = ("NC", "NTD", "RLL")
# Trailing separators that calling operators habitually append ("NC/", "NC /").
# NOTE: "?" is deliberately NOT stripped — "RLL?" signals uncertainty and must
# go to MANUAL_REVIEW, not be coerced into RLL.
_TRAILING_SEPARATORS = "/.,;:"

_WEEKDAYS_ES = {
    "lunes": 0, "martes": 1, "miércoles": 2, "miercoles": 2, "jueves": 3,
    "viernes": 4, "sábado": 5, "sabado": 5, "domingo": 6,
}

# Priority rule 1: decision-maker titles (checked case-insensitively).
DECISION_MAKER_TITLES = (
    "ceo", "founder", "managing director", "cfo", "coo", "partner", "owner",
)

_URGENT_RE = re.compile(r"urgente|urgent|asap|!!", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Reading (reuses tools/read_extract.py)
# ---------------------------------------------------------------------------

def read_contact_sheets(path: str) -> Dict[str, List[List[str]]]:
    """Return ``{sheet_name: rows}`` for visible sheets matching the 12-column
    prospecting schema (exact header match, in order). Other sheets are ignored."""
    from tools.read_extract import (
        _shared_strings, _workbook_sheets, _workbook_rels, _sheet_part, _sheet_rows,
    )

    out: Dict[str, List[List[str]]] = {}
    with zipfile.ZipFile(path) as zf:
        names = set(zf.namelist())
        shared = _shared_strings(zf, names)
        rels = _workbook_rels(zf, names)
        for name, state, rid in _workbook_sheets(zf):
            if state in {"hidden", "veryHidden"}:
                continue
            part = _sheet_part(rels.get(rid, ""))
            if part not in names:
                continue
            rows = _sheet_rows(zf.read(part), shared)
            header = [c.strip() for c in rows[0]] if rows else []
            if header == EXPORT_COLUMNS:
                out[name] = rows
    return out


def sheet_reference_date(sheet_name: str) -> Optional[datetime.date]:
    """Extract the YYYYMMDD tag from a sheet name (``... 20260105``), if any."""
    m = re.search(r"(20\d{2})(\d{2})(\d{2})\s*$", sheet_name.strip())
    if not m:
        return None
    try:
        return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Field normalization (never invent data)
# ---------------------------------------------------------------------------

def normalize_phone(value: str) -> Tuple[str, Optional[str]]:
    """Collapse separators only when the remainder is digits (+ optional ``+``).
    Anything else is kept verbatim with a note. Never adds country codes."""
    s = value.strip()
    if not s:
        return "", None
    compact = re.sub(r"[\s.\-()]", "", s)
    if re.fullmatch(r"\+?\d{4,}", compact):
        return compact, None
    return s, "unrecognized phone format (kept verbatim)"


def normalize_email(value: str) -> Tuple[str, Optional[str]]:
    """Trim; lowercase the domain half only. No ``@`` → verbatim + note."""
    s = value.strip()
    if not s:
        return "", None
    if "@" not in s or " " in s:
        return s, "unrecognized email format (kept verbatim)"
    local, _, domain = s.rpartition("@")
    return f"{local}@{domain.lower()}", None


# ---------------------------------------------------------------------------
# Accion parsing
# ---------------------------------------------------------------------------

def parse_accion(cell: Any) -> Tuple[str, Dict[str, Any]]:
    """Map a raw ``Accion`` cell to (normalized_action, action_payload).

    First whitespace-separated token, uppercased, with habitual trailing
    separators (``/ . , ; :``) stripped, must exactly equal NC/NTD/RLL.
    Blank → NONE. Anything else → MANUAL_REVIEW (the raw cell is preserved by
    the caller in ``original_action``)."""
    raw = "" if cell is None else str(cell)
    stripped = raw.strip()
    if not stripped:
        return "NONE", {}
    parts = stripped.split(None, 1)
    token, rest = parts[0], (parts[1] if len(parts) > 1 else "")
    cleaned = token.upper().rstrip(_TRAILING_SEPARATORS)
    if cleaned in ACTION_CODES:
        return cleaned, ({"note": rest} if rest else {})
    return "MANUAL_REVIEW", {}


# ---------------------------------------------------------------------------
# Row normalization
# ---------------------------------------------------------------------------

def normalize_rows(path: str) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Read a workbook and return ``(normalized_rows, file_notes)``.

    Each row follows the standard normalized object of the workflow doc. Fully
    blank rows are skipped; rows with data but no Contact name become file-level
    notes for manual review."""
    rows_out: List[Dict[str, Any]] = []
    file_notes: List[str] = []
    source_file = path.rsplit("/", 1)[-1]

    for sheet_name, rows in read_contact_sheets(path).items():
        for row_number, cells in enumerate(rows[1:], start=2):
            cells = [str(c) for c in cells] + [""] * (len(EXPORT_COLUMNS) - len(cells))
            (accion, name, mobile, mobile2, company, title,
             email, email2, linkedin, industry, sub_industry, employees) = cells[:12]
            if not any(c.strip() for c in cells[:12]):
                continue  # fully blank row
            if not name.strip():
                file_notes.append(
                    f"{sheet_name} row {row_number}: data but no Contact name — manual review"
                )
                continue

            notes: List[str] = []
            norm_mobile, n1 = normalize_phone(mobile)
            norm_mobile2, n2 = normalize_phone(mobile2)
            norm_email, n3 = normalize_email(email)
            norm_email2, n4 = normalize_email(email2)
            notes.extend(n for n in (n1, n2, n3, n4) if n)

            code, payload = parse_accion(accion)
            rows_out.append({
                "source_file": source_file,
                "sheet_name": sheet_name,
                "row_number": row_number,
                "original_action": accion,
                "normalized_action": code,
                "action_payload": payload,
                "contact": {
                    "name": name.strip(),
                    "mobile": norm_mobile,
                    "mobile_2": norm_mobile2,
                    "work_email": norm_email,
                    "work_email_2": norm_email2,
                    "linkedin_profile": linkedin.strip(),
                },
                "company": {
                    "name": company.strip(),
                    "industry": industry.strip(),
                    "sub_industry": sub_industry.strip(),
                    "employees_linkedin": employees.strip(),
                },
                "job_title": title.strip(),
                "notes": notes,
            })
    return rows_out, file_notes


# ---------------------------------------------------------------------------
# NTD — extract the real decision-maker target (never invent)
# ---------------------------------------------------------------------------

def extract_ntd_targets(note: str) -> Tuple[List[Dict[str, Any]], str]:
    """Return ``(names, department_hint)`` from an NTD payload note.

    Names come only from parentheses — ``(Alberto)``, ``(Narciso y Vicente
    García)`` — split on `` y ``/`` e ``/commas. A single-word name is partial
    with LOW confidence. ``> Financiero`` (no parentheses) is a department
    hint. Nothing is ever completed into a fuller name."""
    names: List[Dict[str, Any]] = []
    department = ""
    m = re.search(r"\(([^)]*)\)", note)
    if m:
        for part in re.split(r"\s+y\s+|\s+e\s+|,|;", m.group(1)):
            part = part.strip()
            if not part:
                continue
            partial = len(part.split()) == 1
            names.append({
                "name": part,
                "partial": partial,
                "confidence": "LOW" if partial else "MEDIUM",
            })
    else:
        dm = re.search(r">\s*(.+)$", note)
        if dm:
            department = dm.group(1).strip()
    return names, department


# ---------------------------------------------------------------------------
# RLL — recall date/time parsing (ambiguous → manual review, never guessed)
# ---------------------------------------------------------------------------

def parse_rll_when(note: str, reference_date: datetime.date) -> Dict[str, Any]:
    """Parse a recall note into ``{date, time, unparsed, ambiguous}``.

    Supported: ``D/M`` (or ``D/M/YY[YY]``), Spanish weekday names, ``HH:MM``,
    ``NNh``. Dates without a year resolve to the next occurrence on/after
    ``reference_date`` (weekdays: strictly after). Anything unrecognized lands
    in ``unparsed``; no resolvable date, or leftovers, means ``ambiguous`` —
    the row must route to manual review, not be scheduled on a guess."""
    date: Optional[datetime.date] = None
    time: Optional[str] = None
    unparsed: List[str] = []

    for tok in note.split():
        t = tok.lower().strip(",.")
        m = re.fullmatch(r"(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?", t)
        if m and date is None:
            day, month, year_s = int(m.group(1)), int(m.group(2)), m.group(3)
            year = (2000 + int(year_s)) if year_s and len(year_s) == 2 else (
                int(year_s) if year_s else reference_date.year)
            try:
                cand = datetime.date(year, month, day)
            except ValueError:
                unparsed.append(tok)
                continue
            if not year_s and cand < reference_date:
                cand = datetime.date(year + 1, month, day)
            date = cand
            continue
        m = re.fullmatch(r"(\d{1,2}):(\d{2})", t)
        if m and time is None:
            time = f"{int(m.group(1)):02d}:{m.group(2)}"
            continue
        m = re.fullmatch(r"(\d{1,2})h", t)
        if m and time is None:
            time = f"{int(m.group(1)):02d}:00"
            continue
        if t in _WEEKDAYS_ES and date is None:
            delta = (_WEEKDAYS_ES[t] - reference_date.weekday()) % 7 or 7
            date = reference_date + datetime.timedelta(days=delta)
            continue
        unparsed.append(tok)

    return {
        "date": date.isoformat() if date else None,
        "time": time,
        "unparsed": unparsed,
        "ambiguous": date is None or bool(unparsed),
    }


def _employees_upper_bound(value: str) -> int:
    """``201-500`` → 500, ``10.001+`` → 10001, plain digits → int, else 0."""
    s = value.strip().replace(".", "").replace(",", "").rstrip("+")
    m = re.fullmatch(r"(\d+)\s*-\s*(\d+)", s)
    if m:
        return int(m.group(2))
    return int(s) if s.isdigit() else 0


def rll_priority_key(row: Dict[str, Any], when: Dict[str, Any]) -> Tuple:
    """Sort key for the prioritized call list (ascending sort = call order).

    Rules, in order: decision-maker title → larger company (Employees in
    LinkedIn) → has a direct email → clearer time (explicit HH:MM) → urgent
    note."""
    title = row.get("job_title", "").lower()
    is_dm = any(t in title for t in DECISION_MAKER_TITLES)
    employees = _employees_upper_bound(row["company"].get("employees_linkedin", ""))
    has_email = bool(row["contact"].get("work_email") or row["contact"].get("work_email_2"))
    clarity = 1 if when.get("time") else 0
    urgent = 1 if _URGENT_RE.search(row.get("action_payload", {}).get("note", "")) else 0
    return (-int(is_dm), -employees, -int(has_email), -clarity, -urgent,
            row["sheet_name"], row["row_number"])


# ---------------------------------------------------------------------------
# NC — intro email draft (never sent from here)
# ---------------------------------------------------------------------------

def _usable_email(value: str) -> bool:
    return "@" in value and " " not in value and "." in value.rpartition("@")[2]

# TODO: Define final NC intro/presentation email template with Manuel.
NC_EMAIL_TEMPLATE = {
    "subject": "[PLACEHOLDER] Presentación — {company}",
    "body": (
        "[PLACEHOLDER — pending final template]\n"
        "Hola {first_name},\n\n"
        "Le llamé recientemente sin éxito. Le escribo para presentarle ...\n\n"
        "TODO: Define final NC intro/presentation email template with Manuel."
    ),
}


def nc_email_draft(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Draft (not send) an intro email for an NC row.

    Prefers Work email; falls back to Work email 2; returns None when no
    usable address exists (caller routes the row to manual review)."""
    contact = row["contact"]
    recipient = next(
        (e for e in (contact["work_email"], contact["work_email_2"]) if _usable_email(e)),
        None,
    )
    if recipient is None:
        return None
    first_name = contact["name"].split()[0] if contact["name"] else ""
    company = row["company"]["name"]
    return {
        "recipient": recipient,
        "subject": NC_EMAIL_TEMPLATE["subject"].format(company=company),
        "body": NC_EMAIL_TEMPLATE["body"].format(first_name=first_name, company=company),
        "company": company,
        "source": {"sheet_name": row["sheet_name"], "row_number": row["row_number"]},
        "approval_status": "approval_required",
    }


# ---------------------------------------------------------------------------
# Router — dry-run only; side effects become approval_required actions
# ---------------------------------------------------------------------------

def route_rows(rows: List[Dict[str, Any]],
               reference_date: datetime.date) -> Dict[str, Any]:
    """Route normalized rows to their next workflow — as a dry-run.

    Returns the execution summary. Every side effect (email, reminder, memory
    write) appears only as a structured ``approval_required`` action under
    ``proposed_actions`` (Cerberus pattern); nothing is executed."""
    proposed: List[Dict[str, Any]] = []
    manual: List[Dict[str, Any]] = []
    groups: Dict[str, List[Dict[str, Any]]] = {
        "NC": [], "NTD": [], "RLL": [], "MANUAL_REVIEW": [], "NONE": [],
    }
    for row in rows:
        groups[row["normalized_action"]].append(row)

    def _src(row: Dict[str, Any]) -> Dict[str, Any]:
        return {"sheet_name": row["sheet_name"], "row_number": row["row_number"],
                "original_action": row["original_action"]}

    for row in groups["MANUAL_REVIEW"]:
        manual.append({"reason": "unknown action", **_src(row)})

    # NC → nc-email-introduction (himalaya, approval-gated)
    nc_emails = 0
    for row in groups["NC"]:
        draft = nc_email_draft(row)
        if draft is None:
            manual.append({"reason": "NC without usable email", **_src(row)})
            continue
        nc_emails += 1
        proposed.append({
            "action_id": f"action_email_{nc_emails:02d}",
            "type": "send_email",
            "workflow": "nc-email-introduction",
            "status": "approval_required",
            "summary": f"Send NC intro email to {draft['recipient']}",
            "payload": draft,
            "dry_run": True,
        })

    # NTD → ntd-vault-capture (memory tool, approval-gated)
    ntd_captures = 0
    seen_targets: set = set()
    for row in groups["NTD"]:
        note = row["action_payload"].get("note", "")
        names, department = extract_ntd_targets(note)
        if not names and not department:
            manual.append({"reason": "NTD without extractable target", **_src(row)})
            continue
        company = row["company"]["name"]
        targets = [n["name"] for n in names] or [f"dept:{department}"]
        keys = {(company.lower(), t.lower()) for t in targets}
        if keys & seen_targets:
            continue  # duplicate capture for the same company target
        seen_targets |= keys
        ntd_captures += 1
        proposed.append({
            "action_id": f"action_memory_{ntd_captures:02d}",
            "type": "memory_write",
            "workflow": "ntd-vault-capture",
            "status": "approval_required",
            "summary": f"Capture decision-maker target for {company}",
            "payload": {
                "company": company,
                "names": names,
                "department_hint": department,
                "source_contact": row["contact"]["name"],
                **_src(row),
            },
            "dry_run": True,
        })

    # RLL → rll-recall-reminder (cronjob tool, approval-gated)
    schedulable: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
    for row in groups["RLL"]:
        when = parse_rll_when(row["action_payload"].get("note", ""), reference_date)
        if when["ambiguous"]:
            manual.append({"reason": "RLL with ambiguous date/time", **_src(row)})
            continue
        schedulable.append((row, when))

    rll_reminders = 0
    for row, when in sorted(schedulable, key=lambda rw: rll_priority_key(*rw)):
        rll_reminders += 1
        proposed.append({
            "action_id": f"action_reminder_{rll_reminders:02d}",
            "type": "create_reminder",
            "workflow": "rll-recall-reminder",
            "status": "approval_required",
            "summary": (f"Recall {row['contact']['name']} ({row['company']['name']}) "
                        f"on {when['date']}" + (f" {when['time']}" if when["time"] else "")),
            "payload": {"date": when["date"], "time": when["time"],
                        "contact": row["contact"]["name"], **_src(row)},
            "dry_run": True,
        })

    # Prioritized call list per (date, time) slot with more than one contact.
    slots: Dict[Tuple, List[Tuple[Dict[str, Any], Dict[str, Any]]]] = {}
    for row, when in schedulable:
        slots.setdefault((when["date"], when["time"]), []).append((row, when))
    call_lists = [
        {
            "date": date, "time": time,
            "call_order": [r["contact"]["name"] for r, _ in
                           sorted(entries, key=lambda rw: rll_priority_key(*rw))],
        }
        for (date, time), entries in sorted(slots.items(),
                                            key=lambda kv: (kv[0][0], kv[0][1] or ""))
        if len(entries) > 1
    ]

    return {
        "object": "hermes.prospecting.route_dry_run",
        "dry_run": True,
        "reference_date": reference_date.isoformat(),
        "total_rows": len(rows),
        "nc_email_count": nc_emails,
        "ntd_capture_count": ntd_captures,
        "rll_reminder_count": rll_reminders,
        "manual_review_count": len(manual),
        "no_action_count": len(groups["NONE"]),
        "proposed_actions": proposed,
        "manual_review": manual,
        "call_lists": call_lists,
    }


# ---------------------------------------------------------------------------
# Writer — same format back out (reuses export_leads_xlsx), with validation
# ---------------------------------------------------------------------------

def rows_to_result(rows: List[Dict[str, Any]], company: str = "prospecting") -> Dict[str, Any]:
    """Shape normalized rows into the dict ``export_leads_xlsx`` expects.

    ``rank`` preserves input order; ``accion`` carries the ORIGINAL action cell
    (unknown/manual-review values included) back into column A."""
    prospects = []
    for i, row in enumerate(rows, start=1):
        contact, comp = row["contact"], row["company"]
        prospects.append({
            "id": f"prospect_{i:02d}",
            "rank": i,
            "accion": row["original_action"],
            "full_name": contact["name"],
            "mobile": contact["mobile"],
            "mobile_2": contact["mobile_2"],
            "company": comp["name"],
            "title": row["job_title"],
            "email": contact["work_email"],
            "work_email_2": contact["work_email_2"],
            "linkedin_profile": contact["linkedin_profile"],
            "industry": comp["industry"],
            "sub_industry": comp["sub_industry"],
            "employees_in_linkedin": comp["employees_linkedin"],
            **({"owner": row["owner"]} if row.get("owner") else {}),
        })
    return {"query": {"company": company}, "prospects": prospects}


def write_rows_xlsx(rows: List[Dict[str, Any]],
                    export_dir: Optional[str] = None,
                    date_tag: Optional[str] = None,
                    company: str = "prospecting") -> Dict[str, Any]:
    """Write normalized rows back to the standard format and validate the
    round-trip: the output must be re-readable by ``read_contact_sheets`` with
    the same master-sheet row count. Raises ``ValueError`` if not."""
    from tools.sales_prospector import export_leads_xlsx

    meta = export_leads_xlsx(rows_to_result(rows, company),
                             export_dir=export_dir, date_tag=date_tag)
    sheets = read_contact_sheets(meta["file_path"])
    master = sheets.get(meta["main_sheet"])
    if master is None:
        raise ValueError(f"round-trip failed: main sheet {meta['main_sheet']!r} "
                         "not readable as a contact sheet")
    if len(master) - 1 != len(rows):
        raise ValueError(f"round-trip failed: wrote {len(rows)} rows, "
                         f"read back {len(master) - 1}")
    meta["round_trip_validated"] = True
    return meta
