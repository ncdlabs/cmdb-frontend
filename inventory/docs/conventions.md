# CMDB conventions

## Kinds

| kind | prefix | directory |
|------|--------|-----------|
| server | `srv-` | `servers/` |
| service | `svc-` | `services/` |
| application | `app-` | `applications/` |
| environment | `env-` | `environments/` |

## Required fields

- `id`, `kind`, `name`, `status`, `updated`
- `status`: `active` | `deprecated` | `planned` | `unknown`

## OS field

For servers, prefer:

```yaml
os:
  name: Debian GNU/Linux
  version: "12"
  arch: amd64
  kernel: 6.1.x
```

## Hardware field

Static machine specs (filled by SSH probe or hand-authored). Keep **physical disks** separate from **filesystems/mounts**:

```yaml
hardware:
  vendor: Example Vendor
  product: EXAMPLE-MODEL
  cpu:
    model: Example CPU
    cores: 4
    threads: 8
  memory:
    total_bytes: 17179869184
    total_human: 16 GiB
  disks:
    - device: /dev/nvme0n1
      model: EXAMPLE-SSD-1TB
      serial: EXSERIAL0001
      capacity_bytes: 1000204886016
      capacity_human: 931.5 GiB
      interface: nvme          # nvme | sata | usb | sas | mmc
      rotational: false
      purpose: os              # os | data | backup | scratch
      source: ssh-lsblk
      observed_at: "2026-08-09T00:00:00Z"
      confidence: high
  filesystems:
    - device: /dev/nvme0n1p2
      mount: /
      size_bytes: 100000000000
      size_human: 93.1 GiB
      disk_ref: /dev/nvme0n1
```

Legacy `hardware.storage` (mount rows) is deprecated — migrate to `filesystems`. UI still reads `storage` as a fallback.

Live metrics (load, RAM used, temperature, uptime, fresh SMART/NIC gauges) are **not** auto-written into YAML.
The browse UI loads them via `POST /api/items/{id}/live` only when you click **Refresh** (forces a new probe).
Select hydrates from the **5-minute** client-side live cache when present; it never auto-probes. Never poll on an interval.

Optional one-shot enrichment: `POST /api/items/{id}/live?persist=true` writes static **hardware / os / network** (plus `updated`) from a successful probe into the server YAML and reloads the store. It does **not** change `status`, and it never stores live gauges. Never poll this on an interval; not an agent/MCP tool.

## LAN rescan

Toolbar **Rescan network** runs `POST /api/network/scan`:

- **IPv4:** sweep the **union** of (1) `CMDB_LAN_CIDR` if set, (2) `/24` from `CMDB_NODE_IP` (pod `status.hostIP`, skipping k3s/CNI overlays), (3) private non-overlay addresses on host interfaces, (4) `env.network.lan_cidr`, (5) `/24`s from server IPv4s — with TCP fingerprint (+ ICMP when permitted).
- **IPv6:** never sweep a /64. Discover via NDP (`ip -6 neigh`, pod uses `hostNetwork`) and AAAA lookups for inventory / PTR hostnames; optional sweep only if a prefix has ≤256 hosts (e.g. `/120`).
- **MAC:** from IPv6 NDP and IPv4 ARP (`ip -4 neigh`) neighbor tables when available under hostNetwork.

Unknown hosts are selected by default in the UI and written as new `servers/<id>.yaml` via `POST /api/network/devices` when the operator confirms Add and `CMDB_ROOT` is writable (cluster: StatefulSet PVC). Store `addresses.ipv4` and/or `addresses.ipv6`, plus `addresses.mac` when NDP/ARP reported one, and `sources` including `scan:<ndp|arp|aaaa|…>`. Complementary IPv4+IPv6 rows that share a hostname are merged into one CI. Optional env keys: `lan_cidr_v6`, `ula_cidr`. Live inventory stays on the PVC — do not commit real host YAML to git. Do not expose scan/add on the agent/MCP API. Do not poll rescan on an interval.

New servers are created with `status: unknown` and a short confirmation note. Detail **Confirm identity** (`POST /api/items/{id}/confirm`) promotes them to `active` and clears that default note. Manual UI only. Optional ops settings may set `ssh:` from the default SSH user and run probe+persist on confirm.

## Operational settings

PVC file `.cmdb/settings.yaml` (UI `/settings`, API `GET/PUT /api/settings`) overrides empty Helm/env defaults for:

- `default_ssh_user` — LAN Add + optional Confirm identity SSH fill
- `lan_cidrs` — extra scan prefixes (unioned with `CMDB_LAN_CIDR` / node IP)
- `default_env` — env applied when Add leaves env blank
- `discover_select_all`, `confirm_sets_ssh`, `confirm_probes_persist`

Do not put secrets in settings YAML. API token and SSH keys remain Helm/K8s-only.

Each successful scan also:

- Probes **all** responders on the swept prefixes (inventory members and unknowns) so presence/absence is comparable.
- Compares against the previous snapshot at `.cmdb/last-network-scan.json` and returns `diff` with **added**, **removed**, and **modified** (hostname / ports / mac / ssh_open / ping / family).
- Overwrites that snapshot when the inventory root is writable (first scan establishes the baseline).

## Network interfaces

```yaml
network:
  interfaces:
    - name: eth0
      mac: "02:00:00:00:00:01"
      speed_mbps: 1000          # negotiated link speed
      duplex: full
      mtu: 1500
      ipv4: 10.0.0.20/24
      storage_network: false
      source: ethtool
      observed_at: "2026-08-09T00:00:00Z"
      confidence: high
```

## k3s membership (servers)

Separate **desired** intent from **observed** reality:

```yaml
k3s:
  cluster: svc-k3s-cluster
  desired:
    node_name: app-01
    role: agent                 # server | agent | server+agent
    etcd: false
    schedulable: true
  observed:
    ready: true
    role: agent
    etcd: false
    schedulable: true
    k3s_version: v1.x.x+k3s1
    kubernetes_version: v1.x.x+k3s1
    labels: {}
    taints: []
    observed_at: "2026-08-09T00:00:00Z"
    source: kubernetes-api
    confidence: high
```

Cluster-wide facts live on a cluster service CI (e.g. `svc-k3s-cluster`). Storage provider / StorageClasses on a storage service CI.

## Observation / data-quality fields

On observed blocks and probe-derived inventory fields, record:

- `source` — `manual` | `ssh-lsblk` | `ethtool` | `kubernetes-api` | …
- `observed_at` — ISO-8601 UTC
- `confidence` — `high` | `medium` | `low`

Never store passwords, API tokens, private keys, kubeconfig contents, or recovery codes. Use `secret_ref` pointers only.

## SSH field

```yaml
ssh: ops@10.0.0.10
# optional port / note:
# ssh: ops@10.0.0.30 -p 2222
# ssh: root@10.0.0.40 (key name)
```

## Rules

1. Never invent CIs — use `status: unknown` when identity is unconfirmed.
2. Keep `index.yaml` in sync on add/remove/rename.
3. Record DNS drift in `notes` rather than silently picking one address.
4. No secrets in CMDB files (no tokens, keys, kubeconfigs).
5. Prefer static `os` / `hardware` / `k3s.desired` in YAML; do not auto-write live gauges into inventory.
6. Separate physical `hardware.disks` from `hardware.filesystems`; do not overload mount lists as drive identity.
7. Keep `k3s.desired` authoritative for intent; probes/API refresh may update `k3s.observed` only via deliberate inventory edits.

## Application placement

Applications should declare where they run:

```yaml
runtime: host   # or k3s
runs_on:        # optional explicit servers; else derived from depends_on
  - srv-edge-01
depends_on:
  - svc-web
  - srv-edge-01
```

For workloads on a Kubernetes / k3s cluster:

```yaml
runtime: k3s
runs_on:
  - srv-app-01
k8s:
  namespace: demo
  deployments: [demo-web]
  statefulsets: []
  services: []
  ingresses: []
  notes: "Optional scheduling / networking notes"
```

Do **not** inventory ephemeral pod names — record controllers (Deployments / StatefulSets) and Services / Ingresses instead.

## Agent API contract

Agents (MCP clients, scripts) should call the read-only agent endpoints:

- Discovery: `GET /api/agent`
- Search: `GET /api/agent/search?kind=server&role=k3s`
- Context: `GET /api/agent/context/{id}` or `?format=md`
- Address: `GET /api/agent/by-address?q=10.0.0.10`
- Role: `GET /api/agent/by-role?role=dns`

Browse UI continues to use `/api/items*`. Do not expose live SSH probe to automated agents. Inventory YAML must not contain secrets.
