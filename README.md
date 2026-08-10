# CMDB frontend

Read-only searchable CMDB browser (FastAPI + React). Inventory YAML is baked into the image from `inventory/` (replace the sample data with your own source of truth before deploying).

## Layout

| Path | Purpose |
|------|---------|
| `app/cmdb_api/` | FastAPI API |
| `app/frontend/` | Vite React UI |
| `inventory/` | YAML CIs copied into the image (sample included) |
| `chart/cmdb-frontend/` | Helm chart (k3s / Traefik + TLS) |
| `Containerfile` | Podman/OCI image build |
| `mcp/` | Optional MCP stdio wrapper for the agent API |

## Local development

```bash
# API
cd app
pipenv install
CMDB_ROOT=../inventory pipenv run uvicorn cmdb_api.main:app --reload --port 8000

# UI
cd app/frontend
npm install
npm run dev
```

Style guide: http://127.0.0.1:5173/style-guide

### API tests

```bash
cd app
pipenv install --dev
pipenv run pytest tests/ -q
```

## Build image (amd64 for k3s)

```bash
# Optional: sync your private CMDB into inventory/ before building
# rsync -a --delete /path/to/your-cmdb/{servers,services,applications,environments,docs} \
#   /path/to/your-cmdb/index.yaml inventory/

podman build --platform linux/amd64 -t docker.io/library/cmdb:latest -f Containerfile .
```

Import the image into your cluster (e.g. `k3s ctr images import`), then install/upgrade the chart.

## Helm

Chart deploys a **StatefulSet** with a writable inventory PVC (`local-path` by default; seeded once from `/data/cmdb-seed`).

```bash
helm upgrade --install cmdb-frontend ./chart/cmdb-frontend \
  --namespace cmdb --create-namespace \
  --set image.repository=docker.io/library/cmdb \
  --set image.tag=latest \
  --set ingress.host=cmdb.example.com
```

If upgrading from an older Deployment-based release, delete the Deployment once before `helm upgrade` (kind change):

```bash
kubectl -n cmdb delete deploy cmdb-frontend --ignore-not-found
```

### TLS modes (`ingress.tls.source`)

| Value | Behavior |
|-------|----------|
| `selfSigned` (default) | Helm generates a TLS Secret (stable across upgrades via `lookup`) |
| `certManager` | Creates a cert-manager `Certificate` (ClusterIssuer required) |
| `existingSecret` | Uses `ingress.tls.secretName` |

HTTP→HTTPS redirect Middleware is created when `ingress.tls.enabled` and `ingress.redirectHTTP` are true.

### API token (mutating routes)

When `CMDB_API_TOKEN` is set, `POST /api/items/{id}/live`, `POST /api/network/scan`, and `POST /api/network/devices` require header `X-CMDB-Token` (or `Authorization: Bearer`). Agent read routes stay open. The browse UI stores the token in sessionStorage.

```bash
kubectl -n cmdb create secret generic cmdb-api-token --from-literal=token='your-long-random-secret'

helm upgrade --install cmdb-frontend ./chart/cmdb-frontend -n cmdb \
  --set apiToken.existingSecret=cmdb-api-token \
  --set env.CMDB_DEFAULT_SSH_USER=ops
```

Leave `apiToken` unset for open local/dev. Empty `CMDB_DEFAULT_SSH_USER` omits auto `ssh:` on LAN add (port 22); set it to prefer a default user.

## Live machine metrics (manual Refresh)

Server detail shows a **Machine** block:

- Static `os` / `hardware` from inventory YAML
- **Refresh** button → one-shot `POST /api/items/{id}/live` over SSH (load, RAM used, temp, disks, uptime)
- Never polled on an interval

## LAN rescan (manual)

Toolbar **Rescan network** → one-shot `POST /api/network/scan` (TCP fingerprint + ping when permitted) across inventory-derived subnets. Multi-select unknown hosts and **Add selected** → `POST /api/network/devices` writes `servers/<id>.yaml` into the PVC and reloads. Never polled on an interval. Not part of the agent/MCP API.

ICMP ping may be blocked by the default dropped capabilities (`NET_RAW`); discovery still works via TCP connect probes.

## Agent API (read-only)

LLM/tool agents should use `/api/agent/*` (not the browse `/api/items*` routes):

| Endpoint | Purpose |
|----------|---------|
| `GET /api/agent` | Discovery + examples |
| `GET /api/agent/search` | Search (`q`, `kind`, `status`, `env`, `role`, `runtime`) |
| `GET /api/agent/ci/{id}` | Full CI detail |
| `GET /api/agent/context/{id}` | One-shot bundle; `?format=md` for markdown |
| `GET /api/agent/by-address?q=` | IP / hostname / Tailscale / SSH host → servers |
| `GET /api/agent/by-role?role=` | Servers with that role |

OpenAPI: `/openapi.json` · Swagger: `/docs`. No auth on agent routes. Live SSH probe is **not** part of the agent API.

MCP stdio wrapper: [`mcp/`](mcp/) (`CMDB_BASE_URL`, tools `cmdb_search` / `cmdb_get` / `cmdb_context` / `cmdb_by_address` / `cmdb_by_role` / `cmdb_meta`).

For live probes from the cluster, mount an SSH identity Secret with the keys your hosts accept:

```bash
kubectl -n cmdb create secret generic cmdb-ssh \
  --from-file=id_ed25519=$HOME/.ssh/id_ed25519 \
  --from-file=id_rsa=$HOME/.ssh/id_rsa

helm upgrade --install cmdb-frontend ./chart/cmdb-frontend -n cmdb \
  --set ssh.existingSecret=cmdb-ssh \
  --set ssh.identityFile=id_ed25519
```

`CMDB_SSH_DIR=/ssh` makes the probe try every `id_*` key in the mount. Optional single-key env: `CMDB_SSH_IDENTITY`. Image includes `openssh-client`. The chart also mounts probe/API modules from ConfigMap `cmdb-frontend-probe` so dual-key support survives until the next full image bake.

Mutating routes (live probe, network scan/add) can require `CMDB_API_TOKEN` when set; clients send `X-CMDB-Token` or `Authorization: Bearer …`.

cert-manager example:

```bash
helm upgrade --install cmdb-frontend ./chart/cmdb-frontend \
  --namespace cmdb \
  --set ingress.tls.source=certManager \
  --set ingress.tls.certManager.enabled=true \
  --set ingress.tls.certManager.clusterIssuer=letsencrypt-prod
```
