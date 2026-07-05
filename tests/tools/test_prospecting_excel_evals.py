"""Evals for the prospecting Excel workflow pack (tools/prospecting_excel.py).

Realistic ``Accion`` scenarios confirmed with the workflow owner:
NC = no contesta, NTD = not the (target) decision maker, RLL = recall later.
Anything not confidently one of those must land in MANUAL_REVIEW.

Also covers RLL date parsing against a fixed reference date, the prioritized
call list, the router dry-run summary (side effects only as approval_required
actions), and the writer→reader round trip.
"""

import datetime

from tools.prospecting_excel import (
    extract_ntd_targets,
    nc_email_draft,
    normalize_rows,
    parse_accion,
    parse_rll_when,
    rll_priority_key,
    route_rows,
    sheet_reference_date,
    write_rows_xlsx,
)

REF = datetime.date(2026, 1, 5)  # a Monday; matches the exporter's date tag


def _row(accion="", name="Ana Pérez", company="Acme", title="Gerente",
         email="ana@acme.example", email2="", mobile="", employees="",
         note_action_payload=None, sheet="Todos los contactos 20260105", rownum=2):
    code, payload = parse_accion(accion)
    return {
        "source_file": "test.xlsx",
        "sheet_name": sheet,
        "row_number": rownum,
        "original_action": accion,
        "normalized_action": code,
        "action_payload": payload if note_action_payload is None else note_action_payload,
        "contact": {"name": name, "mobile": mobile, "mobile_2": "",
                    "work_email": email, "work_email_2": email2,
                    "linkedin_profile": ""},
        "company": {"name": company, "industry": "", "sub_industry": "",
                    "employees_linkedin": employees},
        "job_title": title,
        "notes": [],
    }


class TestAccionParsing:
    def test_clean_nc(self):
        assert parse_accion("NC") == ("NC", {})

    def test_nc_with_trailing_slash_variants(self):
        assert parse_accion("NC/") == ("NC", {})
        assert parse_accion("NC /") == ("NC", {"note": "/"})

    def test_lowercase_and_spacing(self):
        assert parse_accion("  nc  ")[0] == "NC"
        assert parse_accion("ntd")[0] == "NTD"

    def test_unknown_actions_to_manual_review(self):
        for raw in ("llamar lunes", "RLL?", "ntd/nc", "ok", "enviado", "X"):
            assert parse_accion(raw)[0] == "MANUAL_REVIEW", raw

    def test_blank_is_none(self):
        assert parse_accion("") == ("NONE", {})
        assert parse_accion("   ") == ("NONE", {})
        assert parse_accion(None) == ("NONE", {})


class TestNtdTargets:
    def test_single_first_name_low_confidence(self):
        code, payload = parse_accion("NTD (Alberto)")
        assert code == "NTD"
        names, dept = extract_ntd_targets(payload["note"])
        assert names == [{"name": "Alberto", "partial": True, "confidence": "LOW"}]
        assert dept == ""

    def test_multiple_names_split_carefully(self):
        _, payload = parse_accion("NTD (Narciso y Vicente García)")
        names, _ = extract_ntd_targets(payload["note"])
        assert [n["name"] for n in names] == ["Narciso", "Vicente García"]
        assert names[0]["partial"] is True and names[0]["confidence"] == "LOW"
        assert names[1]["partial"] is False and names[1]["confidence"] == "MEDIUM"

    def test_department_hint_without_person(self):
        _, payload = parse_accion("NTD > Financiero")
        names, dept = extract_ntd_targets(payload["note"])
        assert names == []
        assert dept == "Financiero"


class TestRllWhen:
    def test_numeric_date_and_hour(self):
        when = parse_rll_when("8/7 12h", REF)
        assert when == {"date": "2026-07-08", "time": "12:00",
                        "unparsed": [], "ambiguous": False}

    def test_weekday_and_time(self):
        when = parse_rll_when("viernes 10:00", REF)  # REF is a Monday
        assert when["date"] == "2026-01-09"
        assert when["time"] == "10:00"
        assert when["ambiguous"] is False

    def test_vague_is_ambiguous(self):
        for note in ("", "en unos días", "próxima semana", "cuando pueda"):
            assert parse_rll_when(note, REF)["ambiguous"] is True, note

    def test_no_invented_dates(self):
        # A time alone is not a schedulable date.
        assert parse_rll_when("12h", REF)["ambiguous"] is True

    def test_sheet_reference_date(self):
        assert sheet_reference_date("Todos los contactos 20260105") == REF
        assert sheet_reference_date("David - 20260105") == REF
        assert sheet_reference_date("Notas") is None


class TestPrioritizedCallList:
    def test_same_slot_ordering(self):
        # Same recall slot; expected order: DM+larger company, DM+smaller, non-DM.
        owner_big = _row("RLL 8/7 12h", name="Carla Owner", title="Owner",
                         employees="201-500", email="", rownum=2)
        ceo_small = _row("RLL 8/7 12h", name="Ana CEO", title="CEO",
                         employees="11-50", email="ana@x.example", rownum=3)
        rep_huge = _row("RLL 8/7 12h", name="Bea Rep", title="Sales Rep",
                        employees="10.001+", email="bea@y.example", rownum=4)
        summary = route_rows([owner_big, ceo_small, rep_huge], REF)
        assert summary["rll_reminder_count"] == 3
        assert len(summary["call_lists"]) == 1
        assert summary["call_lists"][0]["call_order"] == [
            "Carla Owner", "Ana CEO", "Bea Rep",
        ]

    def test_email_and_clarity_break_ties(self):
        with_email = _row("RLL 8/7 12h", name="Con Email", email="a@b.example", rownum=2)
        without_email = _row("RLL 8/7 12h", name="Sin Email", email="", rownum=3)
        key_a = rll_priority_key(with_email, {"time": "12:00"})
        key_b = rll_priority_key(without_email, {"time": "12:00"})
        assert key_a < key_b

    def test_urgent_note_increases_priority(self):
        urgent = _row("RLL 8/7 12h urgente", name="U", rownum=2)
        normal = _row("RLL 8/7 12h", name="N", rownum=3)
        assert (rll_priority_key(urgent, {"time": "12:00"})
                < rll_priority_key(normal, {"time": "12:00"}))


class TestRouterDryRun:
    def test_summary_counts_and_no_side_effects(self):
        rows = [
            _row("NC", rownum=2),                              # → email draft
            _row("NC", email="", email2="", rownum=3),         # → manual (no email)
            _row("NTD (Alberto)", rownum=4),                   # → memory capture
            _row("NTD (Alberto)", rownum=5),                   # duplicate → skipped
            _row("NTD", rownum=6),                             # → manual (no target)
            _row("RLL 8/7 12h", rownum=7),                     # → reminder
            _row("RLL en unos días", rownum=8),                # → manual (vague)
            _row("llamar lunes", rownum=9),                    # → manual (unknown)
            _row("", rownum=10),                               # → no action
        ]
        s = route_rows(rows, REF)
        assert s["dry_run"] is True
        assert s["total_rows"] == 9
        assert s["nc_email_count"] == 1
        assert s["ntd_capture_count"] == 1
        assert s["rll_reminder_count"] == 1
        assert s["manual_review_count"] == 4
        assert s["no_action_count"] == 1
        # Every proposed side effect requires approval; nothing executes here.
        assert s["proposed_actions"]
        for action in s["proposed_actions"]:
            assert action["status"] == "approval_required"
            assert action["dry_run"] is True
            assert action["type"] in {"send_email", "memory_write", "create_reminder"}

    def test_nc_prefers_work_email_then_fallback(self):
        primary = _row("NC", email="p@x.example", email2="s@x.example")
        fallback = _row("NC", email="", email2="s@x.example")
        bad_primary = _row("NC", email="not an email", email2="s@x.example")
        assert nc_email_draft(primary)["recipient"] == "p@x.example"
        assert nc_email_draft(fallback)["recipient"] == "s@x.example"
        assert nc_email_draft(bad_primary)["recipient"] == "s@x.example"

    def test_nc_draft_is_placeholder_with_todo(self):
        draft = nc_email_draft(_row("NC"))
        assert draft["approval_status"] == "approval_required"
        assert "PLACEHOLDER" in draft["subject"] or "PLACEHOLDER" in draft["body"]
        assert "TODO: Define final NC intro/presentation email template with Manuel" in draft["body"]


class TestWriterRoundTrip:
    def test_writer_output_read_back_by_reader(self, tmp_path):
        rows = [
            _row("NC", name="Ana Pérez", rownum=2),
            _row("NTD (Alberto)", name="Luis Gómez", rownum=3),
            _row("RLL 8/7 12h", name="Marta Ruiz", rownum=4),
            _row("llamar lunes", name="Pepe Sanz", rownum=5),  # manual-review value
            _row("", name="Eva Blanco", rownum=6),
        ]
        meta = write_rows_xlsx(rows, export_dir=str(tmp_path), date_tag="20260105")
        assert meta["round_trip_validated"] is True

        read_back, notes = normalize_rows(meta["file_path"])
        assert notes == []
        assert len(read_back) == len(rows)
        # Original Accion values preserved — including the unknown one.
        assert [r["original_action"] for r in read_back] == [
            "NC", "NTD (Alberto)", "RLL 8/7 12h", "llamar lunes", "",
        ]
        assert [r["normalized_action"] for r in read_back] == [
            "NC", "NTD", "RLL", "MANUAL_REVIEW", "NONE",
        ]
        # Contact/company/email values preserved.
        first = read_back[0]
        assert first["contact"]["name"] == "Ana Pérez"
        assert first["contact"]["work_email"] == "ana@acme.example"
        assert first["company"]["name"] == "Acme"

    def test_full_pipeline_file_to_dry_run(self, tmp_path):
        """End-to-end: write file → read → route; deterministic dry-run."""
        rows = [
            _row("NC", rownum=2),
            _row("RLL viernes 10:00", name="Marta Ruiz", rownum=3),
            _row("NTD > Financiero", name="Luis Gómez", company="Globex", rownum=4),
        ]
        meta = write_rows_xlsx(rows, export_dir=str(tmp_path), date_tag="20260105")
        read_back, _ = normalize_rows(meta["file_path"])
        ref = sheet_reference_date(read_back[0]["sheet_name"])
        assert ref == REF
        s1 = route_rows(read_back, ref)
        s2 = route_rows(read_back, ref)
        assert s1 == s2  # deterministic
        assert s1["nc_email_count"] == 1
        assert s1["rll_reminder_count"] == 1
        assert s1["ntd_capture_count"] == 1
        assert s1["manual_review_count"] == 0
