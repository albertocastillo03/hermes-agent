# Running Hermes locally (dry-run operator setup)

Minimal, verified startup path for exercising Hermes locally against the
sales-prospecting dry-run workflow — no messaging platforms, no external
services, nothing sent anywhere. This does not introduce a new framework; it
documents the existing `hermes gateway run` path scoped to one platform.

## What exists (surveyed 2026-07-05)

| Mechanism | Command | Notes |
| --- | --- | --- |
| CLI chat | `hermes` / `hermes chat` | Interactive agent REPL. Needs a working model provider. |
| Gateway (hosts the API server) | `hermes gateway run` | Starts only the platforms with `enabled: true` in config.yaml / env. Nothing else auto-connects. |
| Health check | `hermes doctor` | Environment/config/connectivity report. Run this first. |
| Status | `hermes status` | Runtime status of a running gateway. |
| Web UI | `hermes dashboard` / `hermes_cli.main web` + `web/` (Vite) | A **config/monitoring** dashboard (API keys, sessions) — separate concern from the sales-prospector endpoint. Not needed for this workflow; not used in this phase. |
| Runtime health tests | `tests/gateway/test_api_server_bind_guard.py`, `tests/gateway/test_api_server*.py` | Exercise the API server adapter in-process (no real network). |

`hermes gateway run` only starts adapters whose `platform_config.enabled` is
true (`gateway/run.py`, the loop around `for platform, platform_config in
self.config.platforms.items(): if not platform_config.enabled: continue`).
A fresh `~/.hermes/config.yaml` has no platform enabled by default — so
starting the gateway with only the API server's env vars set connects
**exactly one** adapter and touches no messaging platform.

## Shortest reliable command sequence

```bash
cd ~/Projects/hermes-agent

# 1. Health check (optional but recommended first run)
.venv/bin/hermes doctor

# 2. Generate a local-only key (never reuse this in a real deployment)
export API_SERVER_KEY=$(openssl rand -hex 32)
export API_SERVER_ENABLED=true

# 3. Start the gateway — connects ONLY the api_server adapter
.venv/bin/hermes gateway run -v
```

The API server refuses to start without `API_SERVER_KEY` (>=16 chars) —
this is enforced in `gateway/platforms/api_server.py::_api_key_passes_startup_guard`,
not optional. Default bind is `127.0.0.1:8642` (loopback only).

### Health check endpoint

```bash
curl -s http://127.0.0.1:8642/health
# {"status": "ok", "platform": "hermes-agent", "version": "..."}

curl -s http://127.0.0.1:8642/health/detailed \
  -H "Authorization: Bearer $API_SERVER_KEY"
# includes gateway_state, connected platforms, active_agents, pid
```

### Run a dry-run task (no LLM required — the sales-prospector pipeline is
pure deterministic Python)

```bash
curl -s -X POST http://127.0.0.1:8642/v1/sales/prospect/dry-run \
  -H "Authorization: Bearer $API_SERVER_KEY" \
  -H "Content-Type: application/json" \
  -d '{"company": "Indra", "sector": "IT consulting", "geography": "Spain",
       "campaign_goal": "book a meeting with IT decision makers",
       "count": 3, "export_xlsx": true}'
```

### Logs

- `~/.hermes/logs/agent.log` — main activity log (rotating, `logging.level` in config.yaml).
- `~/.hermes/logs/gateway.log` — gateway-only records (created because `mode="gateway"`).
- Gateway stdout/stderr (whatever you redirect `hermes gateway run` to) — startup banner, per-platform connect lines, `aiohttp.access` request lines.
- `exports/sales_prospector/*.xlsx` (repo-relative, git-ignored) — dry-run Excel exports.

### Stopping

`Ctrl+C` (or `kill <pid>`) — the gateway does a bounded teardown
(`Gateway stopped (total teardown ...)` in the log) and exits.

## Opting out of the tirith security-scanner download

`hermes gateway run` triggers `tools.tirith_security` to **download a binary
release** the first time it's missing (`tirith not found — downloading
latest release for ...`), before any platform connects. This is by design —
`security.tirith_enabled` defaults to `true` (pre-exec command scanning) —
**not a bug**, but it is a real external call, so for a strict
zero-outbound-calls session, disable it explicitly:

```bash
export TIRITH_ENABLED=false   # or: security.tirith_enabled: false in config.yaml
```

Verified: with `TIRITH_ENABLED=false`, the gateway log has zero mentions of
`tirith` end to end, and `api_server` still connects normally. Add this to
the startup sequence above whenever the loop must guarantee no network
egress at all.

## Verified (2026-07-05)

Ran exactly the sequence above end-to-end: `hermes doctor` completed;
`hermes gateway run` connected only `api_server` ("Gateway running with 1
platform(s)"); `/health` and `/health/detailed` returned 200; a dry-run POST
with `export_xlsx: true` returned 200 with 6 `approval_required` actions and
a real `.xlsx` written to `exports/sales_prospector/`; the gateway log showed
no non-loopback URLs during the request; the process stopped cleanly on
`kill -TERM`.
