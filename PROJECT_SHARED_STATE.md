# CMDB frontend — project shared state

## Last updated

2026-09-19 (Live probe: select uses 5 min client cache only — no auto-SSH; Refresh forces)

2026-09-19 (LAN Add persists MAC + scan source; dual-stack merge by hostname; IPv4 ARP MACs via `ip -4 neigh`)

2026-09-18 (Ops settings UI `/settings` + PVC `.cmdb/settings.yaml`; SSH/LAN/confirm-probe defaults)

2026-09-18 (Confirm identity: UI + `POST /api/items/{id}/confirm` promotes LAN-added `unknown` servers to `active`)

2026-09-18 (LAN rescan returns added/removed/modified vs PVC `.cmdb/last-network-scan.json`; probes inventory + unknown responders)

2026-09-18 (LAN rescan: union `CMDB_NODE_IP`/`CMDB_LAN_CIDR` with inventory prefixes so sample `lan_cidr` cannot hide the real LAN; UI defaults Select all; live PVC populated via scan+add — not git)

2026-09-14 (`hostNetwork: false` — Traefik on other nodes could not reach hostNetwork endpoint; HTTPS 504 fixed)

2026-09-14 (README screenshots in `docs/screenshots/` — live UI with inventory data blurred)

2026-08-17 (TLS: cert-manager `letsencrypt-cloudflare-production` in `values-ncdlabs.yaml`; ingress-shim annotation disabled when explicit Certificate is used)

2026-08-13 (unpinned `kubernetes.io/hostname` in `values-ncdlabs.yaml` + live STS; PV affinity keeps inventory on disk node)

2026-08-10 (Online/Offline reachability pills from probe cache)

## Architecture

- FastAPI + Vite React browse UI; inventory YAML under `inventory/` (image seed at `/data/cmdb-seed`, runtime at `/data/cmdb`)
- Helm chart: `chart/cmdb-frontend` — **StatefulSet** + Service + Ingress HTTP+HTTPS, Traefik redirect Middleware, TLS
- Writable inventory: PVC `inventory-data` (`persistence.enabled`, storageClass `local-path`); initContainer seeds from `/data/cmdb-seed` once
- Live metrics: `app/cmdb_api/probe.py` via SSH; ephemeral UI cache with **5 min TTL** — select hydrates cache only (no auto-SSH); **Refresh** forces; no interval polling
- Reachability pills: **Online** / **Offline** / **Unknown** on probeable servers (list + detail) from that client probe cache (not inventory status)
- Optional persist: `POST /api/items/{id}/live?persist=true` writes static hardware/os/network (+ `updated`) into server YAML; never status; never live gauges
- LAN discovery: `app/cmdb_api/scan.py` — IPv4 sweep (capped ≤1024 hosts/prefix) + ARP MACs + IPv6 NDP/AAAA (no /64 brute-force); Rescan → `POST /api/network/scan` + `POST /api/network/devices`
- Network Add persists `addresses.mac` (NDP/ARP), `sources` incl. `scan:<source>`, and merges complementary v4+v6 by shared hostname into one CI
- Scan subnet sources (union): `CMDB_LAN_CIDR`, `/24` from `CMDB_NODE_IP` (pod `status.hostIP`, skips k3s/CNI overlays), host iface private prefixes, `env.network.lan_cidr`, server address `/24`s
- Scan diff: each UI rescan compares all observed responders to `.cmdb/last-network-scan.json` on the PVC and returns added / removed / modified; snapshot rewritten after each successful writable scan
- Default `hostNetwork: false` so Traefik/Service routing works cross-node; optional `hostNetwork: true` + `dnsPolicy: ClusterFirstWithHostNet` only for LAN IPv6 NDP (and only if host:8080 is reachable from Traefik nodes)
- Probe/loader/agent_api/main/scan shipped via Helm ConfigMap `cmdb-frontend-probe` (mount over `/app/cmdb_api/*.py`)
- Browse list: Active / Deprecated tabs; URL `?tab=deprecated`
- Applications: `placement` API field — servers from `runs_on`/`depends_on`, plus optional `runtime: k3s` + `k8s:` workloads
- Agent API: `/api/agent/*` (search, context bundle, by-address, by-role); MCP wrapper in `mcp/`
- Repo ships a **sample** inventory only; live network data belongs on the PVC — never commit real host YAML to git

## Decisions

- Live metrics: 5 min client cache on select + manual Refresh only (no auto-SSH on select) — never continuous interval polling
- Online/Offline pills reflect last live probe in-browser (Unknown until Refresh / after TTL); distinct from inventory lifecycle status
- Optional `persist=true` on live probe may write static observed specs (hardware/os/network) into YAML; still never interval-polled
- LAN rescan is manual Rescan only — never continuous polling; not an MCP/agent tool
- Network Add writes `servers/<id>.yaml` into writable `CMDB_ROOT` (PVC or local bind) and reloads store; do not commit live inventory to git
- Confirm identity (`POST /api/items/{id}/confirm`) promotes LAN-added `status: unknown` servers to `active` and clears the default rescan note; UI only
- Ops settings: `/settings` UI + `GET/PUT /api/settings` → PVC `.cmdb/settings.yaml` (default SSH user, LAN CIDRs, default env, discover select-all, confirm sets ssh / probe+persist); merges over Helm env; not MCP
- Cluster SSH: mount Secret with the keys your hosts accept; `CMDB_SSH_DIR=/ssh` tries all keys
- Status filter dropdown replaced by Active/Deprecated tabs
- Application placement is inventory-declared (`runtime` / `runs_on` / `k8s`); do not store ephemeral pod names
- Agent API is public read-only; live SSH probe and LAN scan/add are not MCP tools
- Separate desired vs observed for k3s membership; probes do not change inventory `status`
- Public sample inventory uses `example.com` / RFC1918 demo ranges only — never commit real infra
- StatefulSet injects `CMDB_NODE_IP` from `status.hostIP` so UI rescan finds the site LAN even when PVC still has sample `lan_cidr`

## References

- Dev API: `cd app && CMDB_ROOT=../inventory pipenv run uvicorn cmdb_api.main:app --reload --port 8000`
- Agent discovery: `GET /api/agent` · OpenAPI `/openapi.json`
- Live probe: `POST /api/items/{id}/live` · persist: `POST /api/items/{id}/live?persist=true`
- LAN scan: `POST /api/network/scan` · Add: `POST /api/network/devices`
- MCP: `cd mcp && pipenv run python server.py` (`CMDB_BASE_URL`)
- Helm: `helm upgrade cmdb-frontend ./chart/cmdb-frontend -n cmdb -f chart/cmdb-frontend/values-ncdlabs.yaml`
- Style guide: `/style-guide`
- Conventions: `inventory/docs/conventions.md`
- README screenshots: `docs/screenshots/` (blurred inventory)

## Known gotchas

- Bare `helm upgrade` / lone `--set` can wipe prior user values (lost `ssh.existingSecret`, reset ingress host) — always use `-f values-ncdlabs.yaml`
- Do not hostname-pin the STS; local-path PV affinity keeps inventory on the disk node. Optional `nodeSelector` only if you need hostNetwork NDP on a specific node
- `hostNetwork: true` makes the Service endpoint the node LAN IP; if that port is firewalled from other nodes, Traefik returns 504/timeout while port-forward still works — keep `hostNetwork: false` unless NDP is required and the host port is opened
- `latest` tag needs `rollout restart` after image import (StatefulSet: `kubectl rollout restart sts/cmdb-frontend -n cmdb`)
- Helm SSA can conflict with prior `kubectl replace` on podAnnotations — prefer `rollout restart`
- After agent/probe/API changes: sync `chart/cmdb-frontend/files/{loader,agent_api,main,probe,scan,addresses,security}.py` then `helm upgrade` + rollout
- LAN Add leaves `ssh_user` unset from the UI; set `CMDB_DEFAULT_SSH_USER` in Helm for auto `user@host` when port 22 is open
- UI changes require image rebuild (ConfigMap does not ship frontend)
- PVC inventory diverges from git `inventory/` until you copy YAML back; image rebuild alone does not wipe PVC (seed only if empty)
- Deployment→StatefulSet upgrade: remove old Deployment first then `helm upgrade`
- Pod drops `NET_RAW` — ICMP ping in LAN rescan may fail in-cluster; TCP port fingerprint still discovers hosts
- Sample seed `10.0.0.0/24` may still appear in scan subnet list (union) until sample server/env YAML is removed from the PVC; real LAN still sweeps via `CMDB_NODE_IP`

## Policies

- Do not remove style-guide sections without approval
- Ask before adding broader CI edit/write features beyond LAN rescan Add, confirm-identity, ops settings, + optional live-probe persist
- Do not add continuous (interval) live metrics or network scan polling without approval
- Ask before exposing live SSH probe or LAN scan/add via MCP / agent API
- Do not commit real hostnames, public IPs, Tailscale IPs, personal usernames, or customer domains (except intentional deploy values like `values-ncdlabs.yaml`)

## Environment notes

- Public repo sample inventory under `inventory/` (demo-lab / example.com)
- Mutating routes respect optional `CMDB_API_TOKEN` (`X-CMDB-Token` / Bearer)
- Cluster Ingress host: `cmdb.ncdlabs.com`; TLS via cert-manager ClusterIssuer `letsencrypt-cloudflare-production` (DNS-01); SSH secret: `cmdb-ssh`
- MCP `CMDB_BASE_URL=https://cmdb.ncdlabs.com` — inventory is whatever is on the live PVC
