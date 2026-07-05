"""Tests for POST /v1/sales/prospect/dry-run.

The endpoint is a thin transport wrapper over tools.sales_prospector.run_dry_run;
these tests cover auth, request validation, and the dry-run contract at the HTTP
boundary. The pipeline's own guarantees live in tests/tools/test_sales_prospector.py.
"""

import json
import os

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gateway.config import PlatformConfig
from gateway.platforms.api_server import (
    APIServerAdapter,
    cors_middleware,
    security_headers_middleware,
)
from tools.sales_prospector import EXPORT_COLUMNS

ROUTE = "/v1/sales/prospect/dry-run"


def _make_adapter(api_key: str = "") -> APIServerAdapter:
    extra = {"key": api_key} if api_key else {}
    return APIServerAdapter(PlatformConfig(enabled=True, extra=extra))


def _make_app(adapter: APIServerAdapter) -> web.Application:
    mws = [mw for mw in (cors_middleware, security_headers_middleware) if mw is not None]
    app = web.Application(middlewares=mws)
    app["api_server_adapter"] = adapter
    app.router.add_post(ROUTE, adapter._handle_sales_prospect_dry_run)
    return app


@pytest.fixture
def adapter():
    return _make_adapter()


@pytest.fixture
def auth_adapter():
    return _make_adapter(api_key="sk-secret")


class TestDryRunEndpoint:
    @pytest.mark.asyncio
    async def test_happy_path(self, adapter):
        app = _make_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            resp = await cli.post(ROUTE, json={
                "company": "Indra",
                "sector": "IT consulting",
                "geography": "Spain",
                "campaign_goal": "book a meeting with IT decision makers",
                "count": 3,
            })
            assert resp.status == 200
            data = await resp.json()
            assert data["object"] == "hermes.sales_prospector.dry_run"
            assert data["dry_run"] is True
            assert data["mock_only"] is True
            assert len(data["prospects"]) == 3
            assert data["approvals_required"]
            assert all(a["status"] == "approval_required" for a in data["approvals_required"])

    @pytest.mark.asyncio
    async def test_campaign_context_is_reflected(self, adapter):
        app = _make_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            payload = {
                "company": "Indra",
                "sector": "IT consulting",
                "geography": "Spain",
                "campaign_goal": "book a meeting with IT decision makers",
            }
            data = await (await cli.post(ROUTE, json=payload)).json()
            for key in ("company", "sector", "geography", "campaign_goal"):
                assert data["query"][key] == payload[key]
                assert data["request_context"][key] == payload[key]
            assert data["request_context"]["target_role"] == "IT Director"
            # Regression: the title must not be mis-pluralized anywhere.
            assert "Saless" not in json.dumps(data)

    @pytest.mark.asyncio
    async def test_empty_body_uses_defaults(self, adapter):
        app = _make_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            resp = await cli.post(ROUTE, json={})
            assert resp.status == 200
            data = await resp.json()
            assert data["query"]["count"] == 3

    @pytest.mark.asyncio
    async def test_deterministic_across_requests(self, adapter):
        app = _make_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            payload = {"industry": "saas", "role": "CRO", "location": "NYC", "count": 4}
            a = await (await cli.post(ROUTE, json=payload)).json()
            b = await (await cli.post(ROUTE, json=payload)).json()
            assert a == b

    @pytest.mark.asyncio
    async def test_invalid_json_returns_400(self, adapter):
        app = _make_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            resp = await cli.post(ROUTE, data="not json",
                                  headers={"Content-Type": "application/json"})
            assert resp.status == 400

    @pytest.mark.asyncio
    async def test_requires_auth_when_key_set(self, auth_adapter):
        app = _make_app(auth_adapter)
        async with TestClient(TestServer(app)) as cli:
            resp = await cli.post(ROUTE, json={})
            assert resp.status == 401

    @pytest.mark.asyncio
    async def test_valid_auth_succeeds(self, auth_adapter):
        app = _make_app(auth_adapter)
        async with TestClient(TestServer(app)) as cli:
            resp = await cli.post(ROUTE, json={},
                                  headers={"Authorization": "Bearer sk-secret"})
            assert resp.status == 200


def _export_payload(**extra):
    return {
        "company": "Indra",
        "sector": "IT consulting",
        "geography": "Spain",
        "campaign_goal": "book a meeting with IT decision makers",
        "count": 3,
        **extra,
    }


class TestDryRunExportEndpoint:
    """POST /v1/sales/prospect/dry-run with the optional export_xlsx flag."""

    @pytest.mark.asyncio
    async def test_export_omitted_keeps_old_shape(self, adapter):
        app = _make_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            data = await (await cli.post(ROUTE, json=_export_payload())).json()
            assert "export" not in data          # unchanged default response
            assert data["dry_run"] is True
            assert data["mock_only"] is True

    @pytest.mark.asyncio
    async def test_export_false_keeps_old_shape(self, adapter):
        app = _make_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            data = await (
                await cli.post(ROUTE, json=_export_payload(export_xlsx=False))
            ).json()
            assert "export" not in data
            assert data["dry_run"] is True
            assert data["mock_only"] is True

    @pytest.mark.asyncio
    async def test_export_true_includes_metadata_and_file(
        self, adapter, tmp_path, monkeypatch
    ):
        # Redirect the export folder to a temp dir (order-independent, no cwd
        # mutation) so nothing lands in the repo tree regardless of test order.
        monkeypatch.setattr(
            "tools.sales_prospector.DEFAULT_EXPORT_DIR", str(tmp_path)
        )
        app = _make_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            resp = await cli.post(ROUTE, json=_export_payload(export_xlsx=True))
            assert resp.status == 200
            data = await resp.json()

            # Core dry-run guarantees intact.
            assert data["dry_run"] is True
            assert data["mock_only"] is True

            export = data["export"]
            for key in (
                "file_path", "row_count", "dry_run", "created_by",
                "columns", "sheets", "main_sheet",
            ):
                assert key in export
            assert export["dry_run"] is True
            assert export["created_by"] == "excel_analyst"
            assert export["row_count"] == 3
            assert export["columns"] == EXPORT_COLUMNS
            assert export["main_sheet"] in export["sheets"]

            # The generated file actually exists locally.
            assert os.path.isfile(export["file_path"])
            assert export["file_path"].endswith(".xlsx")

    @pytest.mark.asyncio
    async def test_export_true_requires_auth(self, auth_adapter):
        app = _make_app(auth_adapter)
        async with TestClient(TestServer(app)) as cli:
            resp = await cli.post(ROUTE, json=_export_payload(export_xlsx=True))
            assert resp.status == 401

    @pytest.mark.asyncio
    async def test_export_true_with_valid_auth(self, auth_adapter, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "tools.sales_prospector.DEFAULT_EXPORT_DIR", str(tmp_path)
        )
        app = _make_app(auth_adapter)
        async with TestClient(TestServer(app)) as cli:
            resp = await cli.post(
                ROUTE,
                json=_export_payload(export_xlsx=True),
                headers={"Authorization": "Bearer sk-secret"},
            )
            assert resp.status == 200
            data = await resp.json()
            assert os.path.isfile(data["export"]["file_path"])

    @pytest.mark.asyncio
    async def test_export_invalid_json_returns_400(self, adapter):
        app = _make_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            resp = await cli.post(ROUTE, data="not json",
                                  headers={"Content-Type": "application/json"})
            assert resp.status == 400

    @pytest.mark.asyncio
    async def test_no_raw_bytes_in_response(self, adapter, tmp_path, monkeypatch):
        """Only metadata is returned — never the workbook bytes."""
        monkeypatch.setattr(
            "tools.sales_prospector.DEFAULT_EXPORT_DIR", str(tmp_path)
        )
        app = _make_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            resp = await cli.post(ROUTE, json=_export_payload(export_xlsx=True))
            # Response is JSON, not an octet-stream file download.
            assert resp.content_type == "application/json"
            data = await resp.json()
            assert set(data["export"].keys()) == {
                "file_path", "row_count", "dry_run", "created_by",
                "columns", "sheets", "main_sheet",
            }
