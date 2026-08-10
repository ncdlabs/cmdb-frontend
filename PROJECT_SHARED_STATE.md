# CMDB frontend — project shared state

## Last updated

2026-08-09 (production hardening: probe SSH path + scan/loader safety)

## Architecture

- FastAPI + Vite React browse UI; inventory YAML under `inventory/` (image seed at `/data/cmdb-seed`, runtime at `/data/cmdb`)
- Helm chart: `chart/cmdb-frontend` — **StatefulSet** + Service + Ingress HTTP+HTTPS, Traefik redirect Middleware, TLS
- Writable inventory: PVC `inventory-data` (`persistence.enabled`, storageClass `local-path`); initContainer seeds from `/data/cmdb-seed` once
- Live metrics: `app/cmdb_api/probe.py` via SSH; UI triggers only on Refresh (includes physical_disks + NIC negotiated speed)
- LAN discovery: `app/cmdb_api/scan.py` — IPv4 sweep (capped ≤1024 hosts/prefix) + IPv6 NDP/AAAA (no /64 brute-force); Rescan → `POST /api/network/scan` + `POST /api/network/devices`
- Chart can use `hostNetwork: true` + `dnsPolicy: ClusterFirstWithHostNet` so NDP sees node LAN neighbors
- Probe/loader/agent_api/main/scan shipped via Helm ConfigMap `cmdb-frontend-probe` (mount over `/app/cmdb_api/*.py`)
- Browse list: Active / Deprecated tabs; URL `?tab=deprecated`
- Applications: `placement` API field — servers from `runs_on`/`depends_on`, plus optional `runtime: k3s` + `k8s:` workloads
- Agent API: `/api/agent/*` (search, context bundle, by-address, by-role); MCP wrapper in `mcp/`
- Repo ships a **sample** inventory only; replace before real deployments

## Decisions

- Live host metrics are manual Refresh only — never continuous polling
- LAN rescan is manual Rescan only — never continuous polling; not an MCP/agent tool
- Network Add writes `servers/<id>.yaml` into writable `CMDB_ROOT` (PVC or local bind) and reloads store; commit/export from PVC separately if needed for git
- Cluster SSH: mount Secret with the keys your hosts accept; `CMDB_SSH_DIR=/ssh` tries all keys
- Status filter dropdown replaced by Active/Deprecated tabs
- Application placement is inventory-declared (`runtime` / `runs_on` / `k8s`); do not store ephemeral pod names
- Agent API is public read-only; live SSH probe and LAN scan/add are not MCP tools
- Separate desired vs observed for k3s membership; probes do not auto-write inventory YAML
- Public sample inventory uses `example.com` / RFC1918 demo ranges only — never commit real infra

## References

- Dev API: `cd app && CMDB_ROOT=../inventory pipenv run uvicorn cmdb_api.main:app --reload --port 8000`
- Agent discovery: `GET /api/agent` · OpenAPI `/openapi.json`
- Live probe: `POST /api/items/{id}/live`
- LAN scan: `POST /api/network/scan` · Add: `POST /api/network/devices`
- MCP: `cd mcp && pipenv run python server.py` (`CMDB_BASE_URL`)
- Helm: `helm upgrade cmdb-frontend ./chart/cmdb-frontend -n cmdb --set ssh.existingSecret=cmdb-ssh`
- Style guide: `/style-guide`
- Conventions: `inventory/docs/conventions.md`

## Known gotchas

- Pod may be pinned via `nodeSelector` — set explicitly in values if you need hostNetwork NDP on a specific node
- `latest` tag needs `rollout restart` after image import (StatefulSet: `kubectl rollout restart sts/cmdb-frontend -n cmdb`)
- Helm SSA can conflict with prior `kubectl replace` on podAnnotations — prefer `rollout restart`
- After agent/probe/API changes: sync `chart/cmdb-frontend/files/{loader,agent_api,main,probe,scan,addresses,security}.py` then `helm upgrade` + rollout
- LAN Add leaves `ssh_user` unset from the UI; set `CMDB_DEFAULT_SSH_USER` in Helm for auto `user@host` when port 22 is open
- UI changes require image rebuild (ConfigMap does not ship frontend)
- PVC inventory diverges from git `inventory/` until you copy YAML back; image rebuild alone does not wipe PVC (seed only if empty)
- Deployment→StatefulSet upgrade: remove old Deployment first then `helm upgrade`
- Pod drops `NET_RAW` — ICMP ping in LAN rescan may fail in-cluster; TCP port fingerprint still discovers hosts

## Policies

- Do not remove style-guide sections without approval
- Ask before adding broader CI edit/write features beyond LAN rescan Add
- Do not add continuous live metrics or network scan polling without approval
- Ask before exposing live SSH probe or LAN scan/add via MCP / agent API
- Do not commit real hostnames, public IPs, Tailscale IPs, personal usernames, or customer domains

## Environment notes

- Public repo sample inventory under `inventory/` (demo-lab / example.com)
- Mutating routes respect optional `CMDB_API_TOKEN` (`X-CMDB-Token` / Bearer)
