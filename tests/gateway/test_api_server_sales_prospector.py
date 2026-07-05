"""Tests for POST /v1/sales/prospect/dry-run.

The endpoint is a thin transport wrapper over tools.sales_prospector.run_dry_run;
these tests cover auth, request validation, and the dry-run contract at the HTTP
boundary. The pipeline's own guarantees live in tests/tools/test_sales_prospector.py.
"""

import json

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gateway.config import PlatformConfig
from gateway.platforms.api_server import (
    APIServerAdapter,
    cors_middleware,
    security_headers_middleware,
)

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
