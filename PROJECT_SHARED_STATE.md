# CMDB frontend — project shared state

## Last updated

2026-08-10 (Online/Offline reachability pills from probe cache)

## Architecture

- FastAPI + Vite React browse UI; inventory YAML under `inventory/` (image seed at `/data/cmdb-seed`, runtime at `/data/cmdb`)
- Helm chart: `chart/cmdb-frontend` — **StatefulSet** + Service + Ingress HTTP+HTTPS, Traefik redirect Middleware, TLS
- Writable inventory: PVC `inventory-data` (`persistence.enabled`, storageClass `local-path`); initContainer seeds from `/data/cmdb-seed` once
- Live metrics: `app/cmdb_api/probe.py` via SSH; ephemeral UI cache with **5 min TTL** — auto-probe on server select when stale/missing; **Refresh** forces; no interval polling
- Reachability pills: **Online** / **Offline** / **Unknown** on probeable servers (list + detail) from that client probe cache (not inventory status)
- Optional persist: `POST /api/items/{id}/live?persist=true` writes static hardware/os/network (+ `updated`) into server YAML; never status; never live gauges
- LAN discovery: `app/cmdb_api/scan.py` — IPv4 sweep (capped ≤1024 hosts/prefix) + IPv6 NDP/AAAA (no /64 brute-force); Rescan → `POST /api/network/scan` + `POST /api/network/devices`
- Chart can use `hostNetwork: true` + `dnsPolicy: ClusterFirstWithHostNet` so NDP sees node LAN neighbors
- Probe/loader/agent_api/main/scan shipped via Helm ConfigMap `cmdb-frontend-probe` (mount over `/app/cmdb_api/*.py`)
- Browse list: Active / Deprecated tabs; URL `?tab=deprecated`
- Applications: `placement` API field — servers from `runs_on`/`depends_on`, plus optional `runtime: k3s` + `k8s:` workloads
- Agent API: `/api/agent/*` (search, context bundle, by-address, by-role); MCP wrapper in `mcp/`
- Repo ships a **sample** inventory only; replace before real deployments

## Decisions

- Live metrics: TTL-gated probe on select (5 min client cache) + manual Refresh — never continuous interval polling
- Online/Offline pills reflect last live probe in-browser (Unknown until probed / after TTL); distinct from inventory lifecycle status
- Optional `persist=true` on live probe may write static observed specs (hardware/os/network) into YAML; still never interval-polled
- LAN rescan is manual Rescan only — never continuous polling; not an MCP/agent tool
- Network Add writes `servers/<id>.yaml` into writable `CMDB_ROOT` (PVC or local bind) and reloads store; commit/export from PVC separately if needed for git
- Cluster SSH: mount Secret with the keys your hosts accept; `CMDB_SSH_DIR=/ssh` tries all keys
- Status filter dropdown replaced by Active/Deprecated tabs
- Application placement is inventory-declared (`runtime` / `runs_on` / `k8s`); do not store ephemeral pod names
- Agent API is public read-only; live SSH probe and LAN scan/add are not MCP tools
- Separate desired vs observed for k3s membership; probes do not change inventory `status`
- Public sample inventory uses `example.com` / RFC1918 demo ranges only — never commit real infra

## References

- Dev API: `cd app && CMDB_ROOT=../inventory pipenv run uvicorn cmdb_api.main:app --reload --port 8000`
- Agent discovery: `GET /api/agent` · OpenAPI `/openapi.json`
- Live probe: `POST /api/items/{id}/live` · persist: `POST /api/items/{id}/live?persist=true`
- LAN scan: `POST /api/network/scan` · Add: `POST /api/network/devices`
- MCP: `cd mcp && pipenv run python server.py` (`CMDB_BASE_URL`)
- Helm: `helm upgrade cmdb-frontend ./chart/cmdb-frontend -n cmdb -f chart/cmdb-frontend/values-ncdlabs.yaml`
- Style guide: `/style-guide`
- Conventions: `inventory/docs/conventions.md`

## Known gotchas

- Bare `helm upgrade` / lone `--set` can wipe prior user values (lost `ssh.existingSecret`, reset ingress host) — always use `-f values-ncdlabs.yaml`
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
- Ask before adding broader CI edit/write features beyond LAN rescan Add + optional live-probe persist
- Do not add continuous (interval) live metrics or network scan polling without approval
- Ask before exposing live SSH probe or LAN scan/add via MCP / agent API
- Do not commit real hostnames, public IPs, Tailscale IPs, personal usernames, or customer domains (except intentional deploy values like `values-ncdlabs.yaml`)

## Environment notes

- Public repo sample inventory under `inventory/` (demo-lab / example.com)
- Mutating routes respect optional `CMDB_API_TOKEN` (`X-CMDB-Token` / Bearer)
- Cluster Ingress host: `cmdb.ncdlabs.com`; SSH secret: `cmdb-ssh`
