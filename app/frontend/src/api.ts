export type CiSummary = {
  id: string
  kind?: string
  name?: string
  status?: string
  env?: string
  path?: string
  owner?: string
  updated?: string | number
  endpoints?: string[]
  addresses?: Record<string, string>
  missing?: boolean
  runtime?: string
  placement_summary?: string
  /** Present on probeable servers (list + detail). */
  ssh?: string
}

export type K8sPlacement = {
  namespace?: string
  deployments?: string[]
  statefulsets?: string[]
  services?: string[]
  ingresses?: string[]
  notes?: string
  [key: string]: unknown
}

export type Placement = {
  runtime?: string
  servers?: CiSummary[]
  services?: CiSummary[]
  k8s?: K8sPlacement | null
}

export type CiDetail = CiSummary & {
  notes?: string
  roles?: string[]
  sources?: string[]
  depends_on?: string[]
  depends_on_resolved?: CiSummary[]
  dependents?: CiSummary[]
  endpoints?: string[]
  addresses?: Record<string, unknown>
  os?: Record<string, unknown>
  hardware?: Record<string, unknown>
  network?: Record<string, unknown>
  ports?: unknown
  ports_observed?: unknown
  unit?: string
  units?: unknown
  observed_ips?: unknown
  runs_on?: string[]
  placement?: Placement
  k8s?: K8sPlacement
  [key: string]: unknown
}

export type LiveProbe = {
  ok: boolean
  probed_at: string
  ssh: string
  error: string | null
  live: {
    os?: Record<string, string | null>
    hostname?: string | null
    cpu?: { model?: string | null; cores?: number | null; threads?: number | null }
    memory?: {
      total_bytes?: number | null
      used_bytes?: number | null
      available_bytes?: number | null
      total_human?: string | null
      used_human?: string | null
      available_human?: string | null
    }
    loadavg?: { '1m': number; '5m': number; '15m': number } | null
    uptime_seconds?: number | null
    temperature_c?: number | null
    temperatures?: { name: string; celsius: number }[]
    disks?: {
      source: string
      mount: string
      size_human?: string
      used_human?: string
      available_human?: string
      use_percent?: number | null
      drive_type?: string | null
      device?: string | null
      transport?: string | null
      model?: string | null
      vendor?: string | null
      serial?: string | null
      rotational?: boolean | null
      removable?: boolean | null
      device_size_human?: string | null
      device_size_bytes?: number | null
    }[]
    physical_disks?: {
      device?: string | null
      name?: string | null
      model?: string | null
      serial?: string | null
      vendor?: string | null
      capacity_bytes?: number | null
      capacity_human?: string | null
      interface?: string | null
      firmware?: string | null
      rotational?: boolean | null
      removable?: boolean | null
    }[]
    nics?: {
      name: string
      mac?: string | null
      mtu?: number | null
      operstate?: string | null
      speed_mbps?: number | null
      duplex?: string | null
      ipv4?: string | null
    }[]
    product?: string | null
    vendor?: string | null
    board?: string | null
  } | null
}

export type Meta = {
  root: string
  total: number
  counts: Record<string, number>
  statuses: Record<string, number>
  envs: string[]
  index_updated?: string | null
  load_errors: { path: string; error: string }[]
  error_count: number
}

export type AuthStatus = {
  token_required: boolean
}

const TOKEN_STORAGE_KEY = 'cmdb_api_token'

export function getStoredApiToken(): string {
  try {
    return sessionStorage.getItem(TOKEN_STORAGE_KEY) || ''
  } catch {
    return ''
  }
}

export function setStoredApiToken(token: string): void {
  try {
    const trimmed = token.trim()
    if (trimmed) sessionStorage.setItem(TOKEN_STORAGE_KEY, trimmed)
    else sessionStorage.removeItem(TOKEN_STORAGE_KEY)
  } catch {
    // private mode / blocked storage — ignore
  }
}

export class ApiAuthError extends Error {
  status: number
  constructor(message: string, status = 401) {
    super(message)
    this.name = 'ApiAuthError'
    this.status = status
  }
}

function authHeaders(): HeadersInit {
  const token = getStoredApiToken()
  if (!token) return {}
  return { 'X-CMDB-Token': token }
}

async function getJson<T>(url: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers)
  for (const [k, v] of Object.entries(authHeaders())) {
    if (!headers.has(k)) headers.set(k, v)
  }
  const res = await fetch(url, { ...init, headers })
  const text = await res.text()
  if (res.status === 401) {
    throw new ApiAuthError(formatHttpError(res.status, text), 401)
  }
  if (!res.ok) {
    throw new Error(formatHttpError(res.status, text))
  }
  if (looksLikeHtml(text)) {
    throw new Error(formatHttpError(res.status, text))
  }
  try {
    return JSON.parse(text) as T
  } catch {
    throw new Error(`Invalid JSON response (HTTP ${res.status})`)
  }
}

function looksLikeHtml(text: string): boolean {
  const head = text.trim().slice(0, 200).toLowerCase()
  return head.startsWith('<!doctype') || head.startsWith('<html')
}

function formatHttpError(status: number, body: string): string {
  const trimmed = (body || '').trim()
  if (!trimmed) return `Request failed (${status})`
  if (looksLikeHtml(trimmed)) {
    const title = trimmed.match(/<title[^>]*>([^<]+)<\/title>/i)?.[1]?.replace(/\s+/g, ' ').trim()
    if (title?.toLowerCase().includes('cloudflare tunnel')) {
      return `Cloudflare Tunnel is down or unreachable (HTTP ${status}). Try again once cloudflared is healthy.`
    }
    if (title) return `${title} (HTTP ${status})`
    return `Upstream returned an HTML error page (HTTP ${status}) instead of API JSON.`
  }
  try {
    const parsed = JSON.parse(trimmed) as { detail?: unknown }
    if (typeof parsed.detail === 'string') return parsed.detail
  } catch {
    // not JSON
  }
  if (trimmed.length > 280) return `${trimmed.slice(0, 280)}…`
  return trimmed
}

export function fetchAuthStatus(): Promise<AuthStatus> {
  return getJson<AuthStatus>('/api/auth/status')
}

export function fetchMeta(): Promise<Meta> {
  return getJson<Meta>('/api/meta')
}

export function fetchItems(params: {
  q?: string
  kind?: string
  status?: string
  env?: string
}): Promise<{ items: CiSummary[]; count: number }> {
  const qs = new URLSearchParams()
  if (params.q) qs.set('q', params.q)
  if (params.kind) qs.set('kind', params.kind)
  if (params.status) qs.set('status', params.status)
  if (params.env) qs.set('env', params.env)
  const suffix = qs.toString() ? `?${qs}` : ''
  return getJson(`/api/items${suffix}`)
}

export function fetchItem(id: string): Promise<CiDetail> {
  return getJson<CiDetail>(`/api/items/${encodeURIComponent(id)}`)
}

/** One-shot SSH probe. UI may call on server select when client TTL elapsed; never on an interval. */
export function fetchLiveProbe(id: string): Promise<LiveProbe> {
  return getJson<LiveProbe>(`/api/items/${encodeURIComponent(id)}/live`, { method: 'POST' })
}

export type ConfirmIdentityResult = {
  ok: boolean
  id: string
  path: string
  status: string
  updated?: string
  notes?: string | null
  item: CiDetail
}

/** Promote status unknown → active after LAN rescan. Manual UI only. */
export function confirmServerIdentity(id: string): Promise<ConfirmIdentityResult> {
  return getJson<ConfirmIdentityResult>(`/api/items/${encodeURIComponent(id)}/confirm`, {
    method: 'POST',
  })
}

export type DiscoveredHost = {
  ip: string
  family?: 'ipv4' | 'ipv6' | string
  hostname: string | null
  ping: boolean
  ports: number[]
  ssh_open: boolean
  source?: string
  mac?: string
  in_unidentified?: boolean
  in_inventory?: boolean
}

export type ScanDiffHost = {
  ip: string
  family?: string | null
  hostname?: string | null
  ports?: number[]
  ssh_open?: boolean
  ping?: boolean
  mac?: string | null
  source?: string | null
  in_inventory?: boolean
}

export type ScanDiffModified = {
  ip: string
  changes: string[]
  before: Record<string, unknown>
  after: Record<string, unknown>
  hostname?: string | null
  family?: string | null
}

export type NetworkScanDiff = {
  previous_scanned_at: string | null
  baseline: boolean
  added: ScanDiffHost[]
  removed: ScanDiffHost[]
  modified: ScanDiffModified[]
  counts: { added: number; removed: number; modified: number }
  note?: string
}

export type NetworkScanResult = {
  ok: boolean
  scanned_at: string
  subnets: string[]
  targets: number
  known_skipped: number
  discovered: DiscoveredHost[]
  count: number
  observed_count?: number
  ipv4_count?: number
  ipv6_count?: number
  notes?: string[]
  writable: boolean
  root: string
  diff?: NetworkScanDiff
  snapshot_saved?: boolean
  snapshot_path?: string | null
  discover_select_all?: boolean
  default_env?: string
}

export type OpsSettingsFields = {
  default_ssh_user: string
  lan_cidrs: string[]
  default_env: string
  discover_select_all: boolean
  confirm_sets_ssh: boolean
  confirm_probes_persist: boolean
  updated?: string | null
}

export type OpsSettingsResponse = {
  ok: boolean
  writable: boolean
  settings_path: string
  effective: OpsSettingsFields
  stored: OpsSettingsFields
  sources: Record<string, string>
  deployment: {
    cmdb_root?: string | null
    cmdb_node_ip?: string | null
    cmdb_lan_cidr_env?: string | null
    cmdb_default_ssh_user_env?: string | null
    cmdb_ssh_dir_configured?: boolean
    cmdb_ssh_identity_configured?: boolean
    api_token_required?: boolean
  }
  envs: string[]
}

export type OpsSettingsUpdate = {
  default_ssh_user?: string
  lan_cidrs?: string[] | string
  default_env?: string
  discover_select_all?: boolean
  confirm_sets_ssh?: boolean
  confirm_probes_persist?: boolean
}

export function fetchSettings(): Promise<OpsSettingsResponse> {
  return getJson<OpsSettingsResponse>('/api/settings')
}

export function saveSettings(body: OpsSettingsUpdate): Promise<OpsSettingsResponse> {
  return getJson<OpsSettingsResponse>('/api/settings', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

export type NetworkDeviceAdd = {
  ip: string
  ipv4?: string | null
  ipv6?: string | null
  id?: string
  name?: string
  hostname?: string | null
  env?: string | null
  status?: string
  ports?: number[]
  ssh_user?: string | null
  notes?: string | null
  mac?: string | null
  source?: string | null
}

export type NetworkAddResult = {
  ok: boolean
  created: { id: string; ip: string; path: string }[]
  created_count: number
  errors: { ip?: string; id?: string; error: string }[]
  error_count: number
}

/** Manual one-shot LAN sweep — never call on an interval. */
export function fetchNetworkScan(): Promise<NetworkScanResult> {
  return getJson<NetworkScanResult>('/api/network/scan', { method: 'POST' })
}

export function addNetworkDevices(devices: NetworkDeviceAdd[]): Promise<NetworkAddResult> {
  return getJson<NetworkAddResult>('/api/network/devices', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ devices }),
  })
}

export function suggestServerId(ip: string, hostname?: string | null): string {
  if (hostname) {
    const short = hostname.split('.')[0]?.toLowerCase() || ''
    const slug = short.replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '')
    if (slug && !/^\d/.test(slug)) return `srv-${slug}`
  }
  if (ip.includes(':')) {
    // Expand-ish slug from compressed IPv6 without needing full explode in UI
    const slug = ip
      .toLowerCase()
      .replace(/^\[|\]$/g, '')
      .replace(/%.*$/, '')
      .replace(/:/g, '-')
      .replace(/[^a-z0-9-]+/g, '-')
      .replace(/-+/g, '-')
      .replace(/^-+|-+$/g, '')
    return `srv-host-${slug || 'ipv6'}`
  }
  return `srv-host-${ip.replace(/\./g, '-')}`
}
