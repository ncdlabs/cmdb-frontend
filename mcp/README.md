# CMDB MCP server

Thin stdio MCP wrapper around the read-only CMDB agent HTTP API (`/api/agent/*`).

## Setup

```bash
cd mcp
pipenv install
```

Env:

| Variable | Default | Purpose |
|----------|---------|---------|
| `CMDB_BASE_URL` | `https://cmdb.example.com` | CMDB origin (no trailing slash) |

## Tools

| Tool | Maps to |
|------|---------|
| `cmdb_meta` | `GET /api/meta` |
| `cmdb_search` | `GET /api/agent/search` |
| `cmdb_get` | `GET /api/agent/ci/{id}` |
| `cmdb_context` | `GET /api/agent/context/{id}` |
| `cmdb_by_address` | `GET /api/agent/by-address` |
| `cmdb_by_role` | `GET /api/agent/by-role` |

Live SSH probe is **not** exposed.

## Codex

Example registration (adjust paths to your clone and Pipenv venv):

```bash
codex mcp add cmdb --env CMDB_BASE_URL=https://cmdb.example.com -- \
  $(cd mcp && pipenv --venv)/bin/python \
  /path/to/cmdb-frontend/mcp/server.py
```

Config lands in `~/.codex/config.toml` under `[mcp_servers.cmdb]`.
Reload / start a new Codex task after adding — existing sessions do not pick up new MCP servers automatically.

Verify: `codex mcp get cmdb` · `codex mcp list`

## Cursor

Add to MCP settings (path adjusted to your clone):

```json
{
  "mcpServers": {
    "cmdb": {
      "command": "pipenv",
      "args": ["run", "python", "server.py"],
      "cwd": "/path/to/cmdb-frontend/mcp",
      "env": {
        "CMDB_BASE_URL": "https://cmdb.example.com"
      }
    }
  }
}
```

Local API during development:

```json
"env": { "CMDB_BASE_URL": "http://127.0.0.1:8000" }
```

## Other agents

Prefer the HTTP agent API directly (`GET /api/agent`). Use this MCP server when the host only speaks MCP over stdio.

## Run manually

```bash
cd mcp
CMDB_BASE_URL=https://cmdb.example.com pipenv run python server.py
```
