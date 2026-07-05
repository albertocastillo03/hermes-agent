"""Tests for the Excel Analyst local commercial calling-list .xlsx export.

Covers the export contract: the exact 12 commercial columns, the main sheet
name, that internal analytics columns are NOT visible, correct prospect→column
mapping, header styling (blue/white/bold + frozen row), owner-split behavior,
and that the export stays deterministic and local-only (mock, no external I/O).
"""

import os
import zipfile

from tools.read_extract import _extract_xlsx
from tools.sales_prospector import (
    EXPORT_COLUMNS,
    export_leads_xlsx,
    run_dry_run,
)

# The exact commercial columns, in order, per the reference calling sheet.
COMMERCIAL_HEADERS = [
    "Accion", "Contact name", "Mobile", "Mobile 2", "Company name", "Job title",
    "Work email", "Work email 2", "LinkedIn profile", "Industry", "Sub industry",
    "Employees in LinkedIn",
]
# Internal analytics fields that must NEVER appear in the commercial Excel.
INTERNAL_FIELDS = ["rank", "fit_score", "dry_run", "approval_status"]


def _query():
    return {
        "company": "Indra",
        "sector": "IT consulting",
        "geography": "Spain",
        "campaign_goal": "book a meeting with IT decision makers",
        "count": 3,
    }


def _extract_lines(file_path):
    text = _extract_xlsx(file_path)
    return [ln for ln in text.splitlines() if ln and not ln.startswith("#")]


class TestHeadersAndSheet:
    def test_exact_12_headers_in_order(self, tmp_path):
        meta = export_leads_xlsx(run_dry_run(_query()), export_dir=str(tmp_path))
        header = _extract_lines(meta["file_path"])[0].split("\t")
        assert header == COMMERCIAL_HEADERS
        assert meta["columns"] == COMMERCIAL_HEADERS == EXPORT_COLUMNS

    def test_main_sheet_name(self, tmp_path):
        meta = export_leads_xlsx(run_dry_run(_query()), export_dir=str(tmp_path))
        assert meta["main_sheet"] == "Todos los contactos 20260105"
        assert meta["sheets"] == ["Todos los contactos 20260105"]

    def test_sheet_name_preserves_spaces_and_is_consistent(self, tmp_path):
        """Regression: the sheet name must keep every space (no 'Todoslos'), and
        the workbook name, metadata['main_sheet'] and metadata['sheets'] must all
        be the exact same string."""
        import re

        expected = "Todos los contactos 20260105"
        meta = export_leads_xlsx(run_dry_run(_query()), export_dir=str(tmp_path))

        # Metadata is internally consistent and preserves the spaces.
        assert meta["main_sheet"] == expected
        assert meta["sheets"] == [expected]
        assert meta["main_sheet"] in meta["sheets"]
        assert "Todoslos" not in meta["main_sheet"]
        assert meta["main_sheet"].count(" ") == 3

        # The actual workbook sheet name matches the metadata exactly.
        with zipfile.ZipFile(meta["file_path"]) as zf:
            wb = zf.read("xl/workbook.xml").decode("utf-8")
        names = re.findall(r'<sheet name="([^"]+)"', wb)
        assert names == meta["sheets"]
        assert names[0] == meta["main_sheet"] == expected

    def test_internal_columns_not_visible(self, tmp_path):
        result = run_dry_run(_query())
        meta = export_leads_xlsx(result, export_dir=str(tmp_path))
        header = _extract_lines(meta["file_path"])[0].split("\t")
        for field in INTERNAL_FIELDS:
            assert field not in header
        # The internal fields still exist in the JSON result, though.
        assert "fit_score" in result["prospects"][0]
        assert result["dry_run"] is True


class TestDataMapping:
    def test_rows_map_prospect_to_commercial_columns(self, tmp_path):
        result = run_dry_run(_query())
        meta = export_leads_xlsx(result, export_dir=str(tmp_path))
        lines = _extract_lines(meta["file_path"])
        header, rows = lines[0].split("\t"), lines[1:]
        assert len(rows) == len(result["prospects"])

        by_name = {p["full_name"]: p for p in result["prospects"]}
        for row in rows:
            cells = row.split("\t")
            rec = dict(zip(header, cells))
            p = by_name[rec["Contact name"]]
            assert rec["Company name"] == p["company"]
            assert rec["Job title"] == p["title"]
            assert rec["Work email"] == p["email"]
            assert rec["Sub industry"] == p["sector"]
            assert rec["Industry"] == p["industry"]
            # Accion is blank by default (manual call notes).
            assert rec["Accion"] == ""

    def test_unknown_contact_fields_are_blank_not_invented(self, tmp_path):
        """No invented phone numbers / LinkedIn URLs in dry-run."""
        result = run_dry_run(_query())
        meta = export_leads_xlsx(result, export_dir=str(tmp_path))
        lines = _extract_lines(meta["file_path"])
        header, rows = lines[0].split("\t"), lines[1:]
        for row in rows:
            # Split with a fixed width so trailing blanks are preserved.
            cells = row.split("\t")
            cells += [""] * (len(header) - len(cells))
            rec = dict(zip(header, cells))
            assert rec["Mobile"] == ""
            assert rec["Mobile 2"] == ""
            assert rec["Work email 2"] == ""
            assert rec["LinkedIn profile"] == ""

    def test_linkedin_kept_as_plain_string_when_present(self, tmp_path):
        result = run_dry_run(_query())
        url = "https://www.linkedin.com/in/mock-lead"
        result["prospects"][0]["linkedin_profile"] = url
        meta = export_leads_xlsx(result, export_dir=str(tmp_path))
        assert url in _extract_xlsx(meta["file_path"])


class TestHeaderStyle:
    def _read_parts(self, file_path):
        with zipfile.ZipFile(file_path) as zf:
            return (
                zf.read("xl/styles.xml").decode("utf-8"),
                zf.read("xl/worksheets/sheet1.xml").decode("utf-8"),
            )

    def test_blue_fill_and_white_bold_font(self, tmp_path):
        meta = export_leads_xlsx(run_dry_run(_query()), export_dir=str(tmp_path))
        styles, _ = self._read_parts(meta["file_path"])
        assert "FF0070C0" in styles          # blue header fill #0070C0
        assert "FFFFFFFF" in styles          # white header text
        assert "<b/>" in styles              # bold
        assert 'horizontal="center"' in styles and 'wrapText="1"' in styles

    def test_header_row_is_styled_and_frozen(self, tmp_path):
        meta = export_leads_xlsx(run_dry_run(_query()), export_dir=str(tmp_path))
        _, sheet1 = self._read_parts(meta["file_path"])
        assert 's="1"' in sheet1             # header cells reference the styled xf
        assert 'state="frozen"' in sheet1    # first row frozen
        assert "customHeight" in sheet1      # tall header row
        assert "<col " in sheet1             # column widths set


class TestOwnerSplit:
    def test_single_master_sheet_without_owner(self, tmp_path):
        meta = export_leads_xlsx(run_dry_run(_query()), export_dir=str(tmp_path))
        assert meta["sheets"] == ["Todos los contactos 20260105"]

    def test_owner_split_keeps_master_and_adds_owner_sheets(self, tmp_path):
        result = run_dry_run({**_query(), "count": 4})
        prospects = sorted(result["prospects"], key=lambda p: p["rank"])
        for p, owner in zip(prospects, ["David", "Alberto", "David", "Alberto"]):
            p["owner"] = owner
        meta = export_leads_xlsx(result, export_dir=str(tmp_path))
        assert meta["sheets"] == [
            "Todos los contactos 20260105",
            "David - 20260105",
            "Alberto - 20260105",
        ]
        assert meta["row_count"] == 4  # master keeps every contact

        with zipfile.ZipFile(meta["file_path"]) as zf:
            # sheet2 == David: 2 data rows + header.
            david = zf.read("xl/worksheets/sheet2.xml").decode("utf-8")
        assert david.count("<row ") == 3


class TestValidXlsx:
    def test_is_a_valid_zip_with_ooxml_parts(self, tmp_path):
        meta = export_leads_xlsx(run_dry_run(_query()), export_dir=str(tmp_path))
        with zipfile.ZipFile(meta["file_path"]) as zf:
            names = set(zf.namelist())
        for required in (
            "[Content_Types].xml",
            "_rels/.rels",
            "xl/workbook.xml",
            "xl/_rels/workbook.xml.rels",
            "xl/styles.xml",
            "xl/worksheets/sheet1.xml",
        ):
            assert required in names

    def test_metadata_shape(self, tmp_path):
        meta = export_leads_xlsx(run_dry_run(_query()), export_dir=str(tmp_path))
        assert meta["dry_run"] is True
        assert meta["created_by"] == "excel_analyst"
        assert meta["row_count"] == 3
        assert meta["file_path"].endswith(".xlsx")

    def test_default_export_dir(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        meta = export_leads_xlsx(run_dry_run(_query()))
        assert os.path.isfile(meta["file_path"])
        assert os.path.join("exports", "sales_prospector") in meta["file_path"]


class TestDeterminism:
    def test_same_input_is_byte_identical(self, tmp_path):
        result = run_dry_run(_query())
        m1 = export_leads_xlsx(result, export_dir=str(tmp_path / "a"))
        m2 = export_leads_xlsx(result, export_dir=str(tmp_path / "b"))
        assert os.path.basename(m1["file_path"]) == os.path.basename(m2["file_path"])
        with open(m1["file_path"], "rb") as f1, open(m2["file_path"], "rb") as f2:
            assert f1.read() == f2.read()

    def test_run_dry_run_stays_deterministic(self):
        import json
        import tempfile

        a = run_dry_run(_query())
        snapshot = json.dumps(a, sort_keys=True)
        with tempfile.TemporaryDirectory() as d:
            export_leads_xlsx(a, export_dir=d)  # must not mutate the result
        assert json.dumps(a, sort_keys=True) == snapshot
        assert json.dumps(run_dry_run(_query()), sort_keys=True) == snapshot
