import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import {
  ApiAuthError,
  fetchAuthStatus,
  fetchItem,
  fetchItems,
  fetchLiveProbe,
  fetchMeta,
  getStoredApiToken,
  type CiDetail,
  type CiSummary,
  type LiveProbe,
  type Meta,
  type Placement,
} from './api'
import {
  AddressBlock,
  CiLinkChip,
  Copyable,
  DetailRow,
  DriveInfoTip,
  HumanValue,
  KindBadge,
  PillList,
  ReachabilityBadge,
  StatusBadge,
} from './components'
import { ApiTokenDialog } from './components/ApiTokenDialog'
import { NetworkRescanDialog } from './components/NetworkRescanDialog'
import {
  clearCachedLiveProbe,
  getCachedLiveProbe,
  isProbeableServer,
  setCachedLiveProbe,
  useReachability,
} from './liveProbeCache'

const KINDS = ['server', 'service', 'application', 'environment']

function useDebounced<T>(value: T, ms: number): T {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const t = window.setTimeout(() => setDebounced(value), ms)
    return () => window.clearTimeout(t)
  }, [value, ms])
  return debounced
}

export function BrowsePage() {
  const { itemId } = useParams()
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const q = searchParams.get('q') || ''
  const kind = searchParams.get('kind') || ''
  const env = searchParams.get('env') || ''
  // Legacy ?status=deprecated maps to Deprecated tab
  const tabParam = searchParams.get('tab')
  const legacyStatus = searchParams.get('status')
  const tab: 'active' | 'deprecated' =
    tabParam === 'deprecated' || (!tabParam && legacyStatus === 'deprecated') ? 'deprecated' : 'active'
  const debouncedQ = useDebounced(q, 150)

  const [meta, setMeta] = useState<Meta | null>(null)
  const [items, setItems] = useState<CiSummary[]>([])
  const [detail, setDetail] = useState<CiDetail | null>(null)
  const [listError, setListError] = useState<string | null>(null)
  const [detailError, setDetailError] = useState<string | null>(null)
  const [loadingList, setLoadingList] = useState(true)
  const [loadingDetail, setLoadingDetail] = useState(false)
  const [rescanOpen, setRescanOpen] = useState(false)
  const [tokenDialogOpen, setTokenDialogOpen] = useState(false)
  const [tokenRequired, setTokenRequired] = useState(false)
  const [hasSessionToken, setHasSessionToken] = useState(() => Boolean(getStoredApiToken()))
  const [listVersion, setListVersion] = useState(0)

  useEffect(() => {
    fetchMeta()
      .then(setMeta)
      .catch((err: Error) => setListError(err.message))
    fetchAuthStatus()
      .then((status) => setTokenRequired(status.token_required))
      .catch(() => undefined)
  }, [])

  useEffect(() => {
    let cancelled = false
    setLoadingList(true)
    // Fetch without status; tabs split active vs deprecated client-side
    fetchItems({
      q: debouncedQ || undefined,
      kind: kind || undefined,
      env: env || undefined,
    })
      .then((data) => {
        if (!cancelled) {
          setItems(data.items)
          setListError(null)
        }
      })
      .catch((err: Error) => {
        if (!cancelled) setListError(err.message)
      })
      .finally(() => {
        if (!cancelled) setLoadingList(false)
      })
    return () => {
      cancelled = true
    }
  }, [debouncedQ, kind, env, listVersion])

  useEffect(() => {
    if (!itemId) {
      setDetail(null)
      setDetailError(null)
      return
    }
    let cancelled = false
    setLoadingDetail(true)
    fetchItem(itemId)
      .then((data) => {
        if (!cancelled) {
          setDetail(data)
          setDetailError(null)
        }
      })
      .catch((err: Error) => {
        if (!cancelled) {
          setDetail(null)
          setDetailError(err.message)
        }
      })
      .finally(() => {
        if (!cancelled) setLoadingDetail(false)
      })
    return () => {
      cancelled = true
    }
  }, [itemId])

  const envs = useMemo(() => meta?.envs || [], [meta])

  const activeItems = useMemo(
    () => items.filter((item) => item.status !== 'deprecated'),
    [items],
  )
  const deprecatedItems = useMemo(
    () => items.filter((item) => item.status === 'deprecated'),
    [items],
  )
  const visibleItems = tab === 'deprecated' ? deprecatedItems : activeItems

  function updateParam(key: string, value: string) {
    const next = new URLSearchParams(searchParams)
    if (value) next.set(key, value)
    else next.delete(key)
    // Drop legacy status when using tabs
    if (key === 'tab') next.delete('status')
    setSearchParams(next, { replace: true })
  }

  function setTab(nextTab: 'active' | 'deprecated') {
    updateParam('tab', nextTab === 'active' ? '' : 'deprecated')
  }

  function selectItem(id: string) {
    navigate({ pathname: `/ci/${encodeURIComponent(id)}`, search: searchParams.toString() })
  }

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <header className="app-header">
        <div>
          <p className="app-brand">CMDB</p>
          <p className="app-tagline">Searchable inventory of servers, services, applications, and environments.</p>
        </div>
        <div className="meta-strip" aria-live="polite">
          {meta ? (
            <>
              <span>{meta.total} CIs</span>
              {meta.index_updated ? <span>index {meta.index_updated}</span> : null}
              {Object.entries(meta.counts).map(([k, n]) => (
                <span key={k}>
                  {k}: {n}
                </span>
              ))}
            </>
          ) : (
            <span>Loading meta…</span>
          )}
        </div>
      </header>

      <main id="main">
        {meta && meta.error_count > 0 ? (
          <div className="alert alert-warn" role="status">
            {meta.error_count} YAML file(s) failed to load. Check API meta.load_errors.
          </div>
        ) : null}

        <section className="toolbar" aria-label="Search and filters">
          <div className="toolbar-row">
            <label className="visually-hidden" htmlFor="cmdb-search" style={{ position: 'absolute', left: '-9999px' }}>
              Search
            </label>
            <input
              id="cmdb-search"
              className="search-input"
              type="search"
              placeholder="Search id, name, IP, endpoint, notes…"
              value={q}
              onChange={(e) => updateParam('q', e.target.value)}
              autoFocus
            />
          </div>
          <div className="toolbar-row">
            <select
              className="select-input"
              aria-label="Kind"
              value={kind}
              onChange={(e) => updateParam('kind', e.target.value)}
            >
              <option value="">All kinds</option>
              {KINDS.map((k) => (
                <option key={k} value={k}>
                  {k}
                </option>
              ))}
            </select>
            <select
              className="select-input"
              aria-label="Environment"
              value={env}
              onChange={(e) => updateParam('env', e.target.value)}
            >
              <option value="">All envs</option>
              {envs.map((e) => (
                <option key={e} value={e}>
                  {e}
                </option>
              ))}
            </select>
            <button type="button" className="btn btn-secondary" onClick={() => setRescanOpen(true)}>
              Rescan network
            </button>
            {tokenRequired ? (
              <button type="button" className="btn btn-secondary" onClick={() => setTokenDialogOpen(true)}>
                {hasSessionToken ? 'API token ✓' : 'Set API token'}
              </button>
            ) : null}
          </div>
          {tokenRequired && !hasSessionToken ? (
            <div className="alert alert-warn" role="status">
              Mutating actions (Refresh, Rescan, Add) need an API token for this deployment.{' '}
              <button type="button" className="btn btn-secondary" onClick={() => setTokenDialogOpen(true)}>
                Set token
              </button>
            </div>
          ) : null}
        </section>

        <ApiTokenDialog
          open={tokenDialogOpen}
          tokenRequired={tokenRequired}
          onClose={() => setTokenDialogOpen(false)}
          onSaved={() => setHasSessionToken(Boolean(getStoredApiToken()))}
        />

        <NetworkRescanDialog
          open={rescanOpen}
          envs={envs}
          onClose={() => setRescanOpen(false)}
          onAdded={(ids) => {
            setListVersion((v) => v + 1)
            fetchMeta().then(setMeta).catch(() => undefined)
            if (ids[0]) selectItem(ids[0])
          }}
          onAuthRequired={() => setTokenDialogOpen(true)}
        />

        <div className="layout-split">
          <section className="panel" aria-label="Results">
            <div className="panel-header">
              <h2>Results</h2>
              <span className="mono">{loadingList ? '…' : visibleItems.length}</span>
            </div>
            <div className="result-tabs" role="tablist" aria-label="CI lifecycle">
              <button
                type="button"
                role="tab"
                id="tab-active"
                aria-selected={tab === 'active'}
                className={`result-tab${tab === 'active' ? ' is-active' : ''}`}
                onClick={() => setTab('active')}
              >
                Active
                <span className="result-tab-count mono">{loadingList ? '…' : activeItems.length}</span>
              </button>
              <button
                type="button"
                role="tab"
                id="tab-deprecated"
                aria-selected={tab === 'deprecated'}
                className={`result-tab${tab === 'deprecated' ? ' is-active' : ''}`}
                onClick={() => setTab('deprecated')}
              >
                Deprecated
                <span className="result-tab-count mono">{loadingList ? '…' : deprecatedItems.length}</span>
              </button>
            </div>
            {listError ? <div className="error-state">{listError}</div> : null}
            {!listError && loadingList ? <div className="loading-state">Loading…</div> : null}
            {!listError && !loadingList && visibleItems.length === 0 ? (
              <div className="empty-state">
                {tab === 'deprecated' ? 'No deprecated configuration items match.' : 'No active configuration items match.'}
              </div>
            ) : null}
            {!listError && !loadingList && visibleItems.length > 0 ? (
              <ul className="ci-list" role="tabpanel" aria-labelledby={tab === 'deprecated' ? 'tab-deprecated' : 'tab-active'}>
                {visibleItems.map((item) => (
                  <li key={item.id}>
                    <button
                      type="button"
                      className={`ci-row${itemId === item.id ? ' is-active' : ''}`}
                      onClick={() => selectItem(item.id)}
                    >
                      <div className="ci-row-title">
                        <span>{item.name || item.id}</span>
                        <KindBadge kind={item.kind} />
                        <StatusBadge status={item.status} />
                        <CiReachabilityPill item={item} />
                      </div>
                      <div className="ci-row-meta">
                        <span className="mono">{item.id}</span>
                        {item.env ? <span>{item.env}</span> : null}
                        {item.placement_summary ? (
                          <span className={item.runtime === 'k3s' || item.placement_summary.startsWith('k3s') ? 'placement-hint is-k3s' : 'placement-hint'}>
                            {item.placement_summary}
                          </span>
                        ) : null}
                        {item.addresses?.ipv4 ? <span className="mono">{item.addresses.ipv4}</span> : null}
                      </div>
                    </button>
                  </li>
                ))}
              </ul>
            ) : null}
          </section>

          <section className="panel" aria-label="Detail">
            <div className="panel-header">
              <h2>Detail</h2>
              {itemId ? (
                <button type="button" className="btn btn-secondary" onClick={() => navigate({ pathname: '/', search: searchParams.toString() })}>
                  Clear
                </button>
              ) : null}
            </div>
            {!itemId ? <div className="empty-state">Select a configuration item.</div> : null}
            {itemId && loadingDetail ? <div className="loading-state">Loading…</div> : null}
            {itemId && detailError ? <div className="error-state">{detailError}</div> : null}
            {itemId && detail && !loadingDetail ? (
              <CiDetailView
                item={detail}
                onSelect={selectItem}
                onAuthRequired={() => setTokenDialogOpen(true)}
              />
            ) : null}
          </section>
        </div>
      </main>
    </div>
  )
}

function formatUptime(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds)) return '—'
  const s = Math.max(0, Math.floor(seconds))
  const d = Math.floor(s / 86400)
  const h = Math.floor((s % 86400) / 3600)
  const m = Math.floor((s % 3600) / 60)
  const parts: string[] = []
  if (d) parts.push(`${d}d`)
  if (h || d) parts.push(`${h}h`)
  parts.push(`${m}m`)
  return parts.join(' ')
}

function driveDetailRows(d: {
  source?: string
  mount?: string
  drive_type?: string | null
  device?: string | null
  transport?: string | null
  model?: string | null
  vendor?: string | null
  serial?: string | null
  rotational?: boolean | null
  removable?: boolean | null
  device_size_human?: string | null
  size_human?: string
  used_human?: string
  available_human?: string
  use_percent?: number | null
}): { label: string; value: string }[] {
  const rows: { label: string; value: string }[] = []
  const push = (label: string, value: unknown) => {
    if (value == null || value === '') return
    rows.push({ label, value: String(value) })
  }
  push('Type', d.drive_type)
  push('Device', d.device)
  push('Source', d.source)
  push('Mount', d.mount)
  push('Transport', d.transport)
  push('Model', d.model)
  push('Vendor', d.vendor)
  push('Serial', d.serial)
  push('Drive size', d.device_size_human)
  push('Volume size', d.size_human)
  push('Used', d.used_human)
  push('Available', d.available_human)
  if (d.use_percent != null) push('Use', `${d.use_percent}%`)
  if (d.rotational != null) push('Rotational', d.rotational ? 'yes (HDD)' : 'no (SSD/flash)')
  if (d.removable != null) push('Removable', d.removable ? 'yes' : 'no')
  return rows
}

function asRecord(value: unknown): Record<string, unknown> | null {
  if (value && typeof value === 'object' && !Array.isArray(value)) {
    return value as Record<string, unknown>
  }
  return null
}

function CiReachabilityPill({ item }: { item: { id: string; kind?: string; ssh?: string } }) {
  const reach = useReachability(item.id)
  if (!isProbeableServer(item)) return null
  return <ReachabilityBadge state={reach} />
}

function MachineSection({
  item,
  onAuthRequired,
}: {
  item: CiDetail
  onAuthRequired?: () => void
}) {
  const [live, setLive] = useState<LiveProbe | null>(null)
  const [probing, setProbing] = useState(false)
  const [probeError, setProbeError] = useState<string | null>(null)
  const onAuthRequiredRef = useRef(onAuthRequired)
  onAuthRequiredRef.current = onAuthRequired
  const probingRef = useRef(false)

  const os = asRecord(item.os)
  const hardware = asRecord(item.hardware)
  const network = asRecord(item.network)
  const k3s = asRecord(item.k3s)
  const k3sDesired = asRecord(k3s?.desired)
  const k3sObserved = asRecord(k3s?.observed)
  const cpu = asRecord(hardware?.cpu)
  const memory = asRecord(hardware?.memory)
  const physicalDisks = Array.isArray(hardware?.disks) ? hardware.disks : null
  const filesystems = Array.isArray(hardware?.filesystems)
    ? hardware.filesystems
    : Array.isArray(hardware?.storage)
      ? hardware.storage
      : null
  const interfaces = Array.isArray(network?.interfaces) ? network.interfaces : null
  const canProbe = isProbeableServer(item)

  async function runProbe(force: boolean) {
    if (!canProbe || probingRef.current) return
    if (!force) {
      const cached = getCachedLiveProbe(item.id)
      if (cached) {
        setLive(cached.result)
        setProbeError(cached.result.ok ? null : cached.result.error || 'Probe failed')
        return
      }
    }
    probingRef.current = true
    setProbing(true)
    setProbeError(null)
    try {
      const result = await fetchLiveProbe(item.id)
      setCachedLiveProbe(item.id, result)
      setLive(result)
      if (!result.ok) setProbeError(result.error || 'Probe failed')
    } catch (err) {
      setLive(null)
      clearCachedLiveProbe(item.id)
      if (err instanceof ApiAuthError) {
        setProbeError(err.message)
        onAuthRequiredRef.current?.()
      } else {
        setProbeError(err instanceof Error ? err.message : String(err))
      }
    } finally {
      probingRef.current = false
      setProbing(false)
    }
  }

  useEffect(() => {
    let cancelled = false

    const cached = getCachedLiveProbe(item.id)
    if (cached) {
      setLive(cached.result)
      setProbeError(cached.result.ok ? null : cached.result.error || 'Probe failed')
      setProbing(false)
      probingRef.current = false
      return () => {
        cancelled = true
      }
    }

    setLive(null)
    setProbeError(null)
    setProbing(false)
    probingRef.current = false
    if (!canProbe) {
      return () => {
        cancelled = true
      }
    }

    probingRef.current = true
    setProbing(true)
    fetchLiveProbe(item.id)
      .then((result) => {
        if (cancelled) return
        setCachedLiveProbe(item.id, result)
        setLive(result)
        if (!result.ok) setProbeError(result.error || 'Probe failed')
      })
      .catch((err: unknown) => {
        if (cancelled) return
        setLive(null)
        clearCachedLiveProbe(item.id)
        if (err instanceof ApiAuthError) {
          setProbeError(err.message)
          onAuthRequiredRef.current?.()
        } else {
          setProbeError(err instanceof Error ? err.message : String(err))
        }
      })
      .finally(() => {
        if (cancelled) return
        probingRef.current = false
        setProbing(false)
      })

    return () => {
      cancelled = true
    }
  }, [item.id, canProbe])

  const showMachine =
    item.kind === 'server' ||
    Boolean(os) ||
    Boolean(hardware) ||
    Boolean(item.ssh) ||
    Boolean(k3s) ||
    Boolean(network)

  if (!showMachine) return null

  const liveData = live?.ok ? live.live : null

  return (
    <section className="machine-section" aria-label="Machine">
      <div className="machine-header">
        <h4 className="machine-title">Machine</h4>
        {canProbe ? (
          <button
            type="button"
            className="btn btn-secondary"
            onClick={() => void runProbe(true)}
            disabled={probing}
          >
            {probing ? 'Probing…' : 'Refresh'}
          </button>
        ) : null}
      </div>
      <p className="machine-hint">
        Static specs come from inventory YAML (desired vs last observed).
        {canProbe
          ? ' Selecting a server SSH-probes when live data is older than 5 minutes (or missing). Refresh forces a new probe. Never polled on an interval.'
          : null}
      </p>

      {k3s ? (
        <div className="kv" aria-label="k3s membership">
          <DetailRow label="k3s cluster" copyText={String(k3s.cluster || '')}>
            <span className="mono">{String(k3s.cluster || '—')}</span>
          </DetailRow>
          {k3sDesired ? (
            <DetailRow
              label="k3s desired"
              copyText={[k3sDesired.node_name, k3sDesired.role, k3sDesired.etcd != null ? `etcd=${k3sDesired.etcd}` : null]
                .filter(Boolean)
                .join(' · ')}
            >
              <div className="mono">
                {[
                  k3sDesired.node_name != null ? String(k3sDesired.node_name) : null,
                  k3sDesired.role != null ? `role=${k3sDesired.role}` : null,
                  k3sDesired.etcd != null ? `etcd=${String(k3sDesired.etcd)}` : null,
                  k3sDesired.schedulable != null ? `schedulable=${String(k3sDesired.schedulable)}` : null,
                ]
                  .filter(Boolean)
                  .join(' · ') || '—'}
              </div>
            </DetailRow>
          ) : null}
          {k3sObserved ? (
            <DetailRow
              label="k3s observed"
              copyText={[k3sObserved.role, k3sObserved.k3s_version, k3sObserved.ready != null ? `ready=${k3sObserved.ready}` : null]
                .filter(Boolean)
                .join(' · ')}
            >
              <div className="mono">
                {[
                  k3sObserved.ready != null ? `ready=${String(k3sObserved.ready)}` : null,
                  k3sObserved.role != null ? `role=${k3sObserved.role}` : null,
                  k3sObserved.etcd != null ? `etcd=${String(k3sObserved.etcd)}` : null,
                  k3sObserved.k3s_version != null ? String(k3sObserved.k3s_version) : null,
                ]
                  .filter(Boolean)
                  .join(' · ') || '—'}
              </div>
              <div className="mono muted-line">
                {[
                  k3sObserved.joined_at != null ? `joined ${String(k3sObserved.joined_at)}` : null,
                  k3sObserved.observed_at != null ? `obs ${String(k3sObserved.observed_at)}` : null,
                  k3sObserved.source != null ? String(k3sObserved.source) : null,
                ]
                  .filter(Boolean)
                  .join(' · ')}
              </div>
            </DetailRow>
          ) : null}
        </div>
      ) : null}

      <div className="kv">
        {os ? (
          <DetailRow
            label="OS"
            copyText={[os.name, os.version, os.codename, os.arch, os.kernel].filter(Boolean).join(' ')}
          >
            <div>
              {[os.name, os.version, os.codename].filter(Boolean).join(' · ') || '—'}
            </div>
            <div className="mono muted-line">
              {[os.arch, os.kernel, os.platform].filter(Boolean).join(' · ')}
            </div>
          </DetailRow>
        ) : null}
        {cpu || hardware?.vendor || hardware?.product || hardware?.platform || hardware?.model_friendly ? (
          <DetailRow
            label="CPU / board"
            copyText={String(cpu?.model || hardware?.model_friendly || hardware?.product || '')}
          >
            <div>{String(cpu?.model || hardware?.model_friendly || hardware?.product || '—')}</div>
            <div className="mono muted-line">
              {[
                cpu?.cores != null ? `${cpu.cores} cores` : null,
                cpu?.threads != null ? `${cpu.threads} threads` : null,
                hardware?.vendor,
                hardware?.product,
                hardware?.platform,
              ]
                .filter(Boolean)
                .join(' · ')}
            </div>
          </DetailRow>
        ) : null}
        {memory ? (
          <DetailRow label="Memory" copyText={String(memory.total_human || memory.total_bytes || '')}>
            <span className="mono">{String(memory.total_human || memory.total_bytes || '—')}</span>
          </DetailRow>
        ) : null}
        {physicalDisks && physicalDisks.length > 0 ? (
          <DetailRow label="Physical disks">
            <ul className="machine-list disk-list">
              {physicalDisks.map((disk, idx) => {
                const d = asRecord(disk) || {}
                const device = String(d.device || d.name || `disk-${idx}`)
                const cap = String(d.capacity_human || d.capacity_bytes || '—')
                const iface = d.interface != null ? String(d.interface) : null
                const model = d.model != null ? String(d.model) : null
                const purpose = d.purpose != null ? String(d.purpose) : null
                const line = [device, cap, iface, model, purpose].filter(Boolean).join(' · ')
                return (
                  <li key={device} className="disk-row">
                    <Copyable text={line}>
                      <span className="mono">{line}</span>
                    </Copyable>
                  </li>
                )
              })}
            </ul>
          </DetailRow>
        ) : null}
        {filesystems && filesystems.length > 0 ? (
          <DetailRow label="Filesystems">
            <ul className="machine-list disk-list">
              {filesystems.map((disk, idx) => {
                const d = asRecord(disk) || {}
                const mount = String(d.mount || d.source || 'fs')
                const size = String(d.size_human || d.size_bytes || '—')
                const device = d.device != null ? String(d.device) : d.source != null ? String(d.source) : null
                const driveType = d.drive_type != null ? String(d.drive_type) : null
                const line = `${mount}: ${size}${device ? ` · ${device}` : ''}${driveType ? ` · ${driveType}` : ''}`
                return (
                  <li key={`${mount}-${idx}`} className="disk-row">
                    <Copyable text={line}>
                      <span className="mono">
                        {mount}: {size}
                        {device ? ` · ${device}` : ''}
                      </span>
                    </Copyable>
                    {driveType ? (
                      <DriveInfoTip
                        driveType={driveType}
                        details={driveDetailRows({
                          source: device || undefined,
                          mount,
                          drive_type: driveType,
                          device: d.disk_ref != null ? String(d.disk_ref) : null,
                          transport: d.interface != null ? String(d.interface) : d.transport != null ? String(d.transport) : null,
                          model: d.model != null ? String(d.model) : null,
                          size_human: size,
                        })}
                      />
                    ) : null}
                  </li>
                )
              })}
            </ul>
          </DetailRow>
        ) : hardware && !cpu && !memory && !physicalDisks ? (
          <DetailRow label="Hardware">
            <HumanValue value={item.hardware} />
          </DetailRow>
        ) : null}
        {interfaces && interfaces.length > 0 ? (
          <DetailRow label="Network">
            <ul className="machine-list disk-list">
              {interfaces.map((iface, idx) => {
                const n = asRecord(iface) || {}
                const name = String(n.name || `nic-${idx}`)
                const speed = n.speed_mbps != null ? `${n.speed_mbps} Mb/s` : null
                const duplex = n.duplex != null ? String(n.duplex) : null
                const ipv4 = n.ipv4 != null ? String(n.ipv4) : null
                const mac = n.mac != null ? String(n.mac) : null
                const line = [name, speed, duplex, ipv4, mac].filter(Boolean).join(' · ')
                return (
                  <li key={name} className="disk-row">
                    <Copyable text={line}>
                      <span className="mono">{line}</span>
                    </Copyable>
                  </li>
                )
              })}
            </ul>
          </DetailRow>
        ) : null}
      </div>

      {probeError ? (
        <div className="alert alert-warn" role="status">
          Live probe failed: {probeError}
        </div>
      ) : null}

      {liveData ? (
        <div className="live-panel" aria-live="polite">
          <div className="live-panel-header">
            <strong>Live</strong>
            <span className="mono muted-line">as of {live?.probed_at}</span>
          </div>
          <div className="kv">
            {liveData.loadavg ? (
              <DetailRow label="Load">
                <span className="mono">
                  {liveData.loadavg['1m']} / {liveData.loadavg['5m']} / {liveData.loadavg['15m']}
                </span>
              </DetailRow>
            ) : null}
            {liveData.memory ? (
              <DetailRow
                label="RAM used"
                copyText={[liveData.memory.used_human, liveData.memory.available_human, liveData.memory.total_human]
                  .filter(Boolean)
                  .join(' / ')}
              >
                <span className="mono">
                  {liveData.memory.used_human || '—'} used · {liveData.memory.available_human || '—'} avail ·{' '}
                  {liveData.memory.total_human || '—'} total
                </span>
              </DetailRow>
            ) : null}
            {liveData.temperature_c != null ? (
              <DetailRow label="Temp" copyText={`${liveData.temperature_c.toFixed(1)} °C`}>
                <span className="mono">{liveData.temperature_c.toFixed(1)} °C</span>
              </DetailRow>
            ) : null}
            {liveData.uptime_seconds != null ? (
              <DetailRow label="Uptime">
                <span className="mono">{formatUptime(liveData.uptime_seconds)}</span>
              </DetailRow>
            ) : null}
            {liveData.hostname ? (
              <DetailRow label="Hostname" copyText={liveData.hostname}>
                <span className="mono">{liveData.hostname}</span>
              </DetailRow>
            ) : null}
            {liveData.physical_disks && liveData.physical_disks.length > 0 ? (
              <DetailRow label="Physical disks">
                <ul className="machine-list disk-list">
                  {liveData.physical_disks.map((d) => {
                    const line = [
                      d.device || d.name,
                      d.capacity_human,
                      d.interface,
                      d.model,
                      d.serial,
                    ]
                      .filter(Boolean)
                      .join(' · ')
                    return (
                      <li key={String(d.device || d.name)} className="disk-row">
                        <Copyable text={line}>
                          <span className="mono">{line}</span>
                        </Copyable>
                      </li>
                    )
                  })}
                </ul>
              </DetailRow>
            ) : null}
            {liveData.nics && liveData.nics.length > 0 ? (
              <DetailRow label="NICs">
                <ul className="machine-list disk-list">
                  {liveData.nics.map((n) => {
                    const line = [
                      n.name,
                      n.speed_mbps != null ? `${n.speed_mbps} Mb/s` : null,
                      n.duplex,
                      n.ipv4,
                      n.mac,
                      n.operstate,
                    ]
                      .filter(Boolean)
                      .join(' · ')
                    return (
                      <li key={n.name} className="disk-row">
                        <Copyable text={line}>
                          <span className="mono">{line}</span>
                        </Copyable>
                      </li>
                    )
                  })}
                </ul>
              </DetailRow>
            ) : null}
            {liveData.disks && liveData.disks.length > 0 ? (
              <DetailRow label="Filesystems">
                <ul className="machine-list disk-list">
                  {liveData.disks.map((d) => {
                    const usage = `${d.mount}: ${d.used_human}/${d.size_human}${
                      d.use_percent != null ? ` (${d.use_percent}%)` : ''
                    }`
                    return (
                      <li key={`${d.mount}-${d.source}`} className="disk-row">
                        <Copyable text={usage}>
                          <span className="mono">{usage}</span>
                        </Copyable>
                        <DriveInfoTip driveType={d.drive_type} details={driveDetailRows(d)} />
                      </li>
                    )
                  })}
                </ul>
              </DetailRow>
            ) : null}
            {liveData.temperatures && liveData.temperatures.length > 1 ? (
              <DetailRow label="Sensors">
                <ul className="machine-list">
                  {liveData.temperatures.map((t) => (
                    <li key={`${t.name}-${t.celsius}`} className="mono">
                      {t.name}: {t.celsius.toFixed(1)} °C
                    </li>
                  ))}
                </ul>
              </DetailRow>
            ) : null}
          </div>
        </div>
      ) : null}
    </section>
  )
}

function stringList(value: unknown): string[] {
  if (!Array.isArray(value)) return []
  return value.filter((v): v is string => typeof v === 'string' && v.trim() !== '')
}

function PlacementSection({
  placement,
  onSelect,
}: {
  placement: Placement
  onSelect: (id: string) => void
}) {
  const runtime = (placement.runtime || 'host').toLowerCase()
  const isK3s = runtime === 'k3s' || runtime === 'kubernetes'
  const servers = placement.servers || []
  const services = placement.services || []
  const k8s = placement.k8s || null
  const deployments = stringList(k8s?.deployments)
  const statefulsets = stringList(k8s?.statefulsets)
  const k8sServices = stringList(k8s?.services)
  const ingresses = stringList(k8s?.ingresses)
  const namespace = typeof k8s?.namespace === 'string' ? k8s.namespace : ''
  const k8sNotes = typeof k8s?.notes === 'string' ? k8s.notes : ''
  const hasK8sResources =
    Boolean(namespace) ||
    deployments.length > 0 ||
    statefulsets.length > 0 ||
    k8sServices.length > 0 ||
    ingresses.length > 0 ||
    Boolean(k8sNotes)

  if (!isK3s && servers.length === 0 && services.length === 0 && !hasK8sResources) {
    return null
  }

  return (
    <section className="detail-section" aria-label="Placement">
      <h4 className="detail-section-title">Placement</h4>
      <div className="kv">
        <DetailRow label="Runtime">
          <span className={`runtime-badge${isK3s ? ' is-k3s' : ''}`}>{isK3s ? 'k3s' : runtime}</span>
        </DetailRow>
        {servers.length > 0 ? (
          <DetailRow label={isK3s ? 'Cluster nodes' : 'Runs on'}>
            <div className="rel-list">
              {servers.map((srv) => (
                <CiLinkChip key={srv.id} item={srv} onSelect={onSelect} />
              ))}
            </div>
          </DetailRow>
        ) : null}
        {services.length > 0 ? (
          <DetailRow label="Services">
            <div className="rel-list">
              {services.map((svc) => (
                <CiLinkChip key={svc.id} item={svc} onSelect={onSelect} />
              ))}
            </div>
          </DetailRow>
        ) : null}
        {isK3s || hasK8sResources ? (
          <>
            {namespace ? (
              <DetailRow label="Namespace" copyText={namespace}>
                <span className="mono">{namespace}</span>
              </DetailRow>
            ) : null}
            {deployments.length > 0 ? (
              <DetailRow label="Deployments">
                <PillList items={deployments} />
              </DetailRow>
            ) : null}
            {statefulsets.length > 0 ? (
              <DetailRow label="StatefulSets">
                <PillList items={statefulsets} />
              </DetailRow>
            ) : null}
            {k8sServices.length > 0 ? (
              <DetailRow label="K8s services">
                <PillList items={k8sServices} />
              </DetailRow>
            ) : null}
            {ingresses.length > 0 ? (
              <DetailRow label="Ingresses">
                <PillList items={ingresses} />
              </DetailRow>
            ) : null}
            {k8sNotes ? (
              <DetailRow label="K8s notes">
                <span>{k8sNotes}</span>
              </DetailRow>
            ) : null}
            <DetailRow label="Pods">
              <span className="muted">
                Pod names are ephemeral — see Deployments / StatefulSets above for stable controllers.
              </span>
            </DetailRow>
          </>
        ) : null}
      </div>
    </section>
  )
}

function CiDetailView({
  item,
  onSelect,
  onAuthRequired,
}: {
  item: CiDetail
  onSelect: (id: string) => void
  onAuthRequired?: () => void
}) {
  const reserved = new Set([
    'id',
    'name',
    'kind',
    'status',
    'env',
    'path',
    'owner',
    'updated',
    'notes',
    'depends_on',
    'depends_on_resolved',
    'dependents',
    'endpoints',
    'addresses',
    'roles',
    'sources',
    'ssh',
    'os',
    'hardware',
    'network',
    'k3s',
    'runtime',
    'runs_on',
    'k8s',
    'placement',
    'placement_summary',
  ])

  const extras = Object.keys(item)
    .filter((k) => !reserved.has(k) && !k.startsWith('_'))
    .sort()

  const addressMap = asRecord(item.addresses)
  const roles = Array.isArray(item.roles) ? item.roles.filter((r): r is string => typeof r === 'string') : []
  const sources = Array.isArray(item.sources)
    ? item.sources.filter((r): r is string => typeof r === 'string')
    : []
  const endpoints = Array.isArray(item.endpoints)
    ? item.endpoints.filter((r): r is string => typeof r === 'string')
    : []
  const placement = item.placement

  return (
    <div className="detail">
      <header className="detail-header">
        <h3 className="detail-title">{item.name || item.id}</h3>
        <div className="detail-id-row">
          <Copyable text={item.id} label="Copy CI id">
            {item.id}
          </Copyable>
          <div className="detail-badges">
            <KindBadge kind={item.kind} />
            <StatusBadge status={item.status} />
            <CiReachabilityPill item={item} />
            {item.runtime === 'k3s' || placement?.runtime === 'k3s' ? (
              <span className="badge badge-k3s">k3s</span>
            ) : null}
          </div>
        </div>
      </header>

      <section className="detail-section" aria-label="Overview">
        <h4 className="detail-section-title">Overview</h4>
        <div className="kv">
          {item.env ? (
            <DetailRow label="Environment" copyText={String(item.env)}>
              <span>{String(item.env)}</span>
            </DetailRow>
          ) : null}
          {item.owner ? (
            <DetailRow label="Owner" copyText={String(item.owner)}>
              <span>{String(item.owner)}</span>
            </DetailRow>
          ) : null}
          {item.updated != null && item.updated !== '' ? (
            <DetailRow label="Updated" copyText={String(item.updated)}>
              <span className="mono">{String(item.updated)}</span>
            </DetailRow>
          ) : null}
          {item.path ? (
            <DetailRow label="Inventory path" copyText={item.path}>
              <span className="mono">{item.path}</span>
            </DetailRow>
          ) : null}
        </div>
      </section>

      {placement ? <PlacementSection placement={placement} onSelect={onSelect} /> : null}

      {item.ssh || addressMap || endpoints.length > 0 ? (
        <section className="detail-section" aria-label="Connectivity">
          <h4 className="detail-section-title">Connectivity</h4>
          <div className="kv">
            {item.ssh ? (
              <DetailRow label="SSH" copyText={item.ssh}>
                <span className="mono">{item.ssh}</span>
              </DetailRow>
            ) : null}
            {addressMap ? (
              <DetailRow label="Addresses">
                <AddressBlock addresses={addressMap} />
              </DetailRow>
            ) : null}
            {endpoints.length > 0 ? (
              <DetailRow label="Endpoints">
                <PillList items={endpoints} />
              </DetailRow>
            ) : null}
          </div>
        </section>
      ) : null}

      {roles.length > 0 || sources.length > 0 || item.network ? (
        <section className="detail-section" aria-label="Classification">
          <h4 className="detail-section-title">Classification</h4>
          <div className="kv">
            {roles.length > 0 ? (
              <DetailRow label="Roles">
                <PillList items={roles} />
              </DetailRow>
            ) : null}
            {sources.length > 0 ? (
              <DetailRow label="Sources">
                <PillList items={sources} />
              </DetailRow>
            ) : null}
            {item.network ? (
              <DetailRow label="Network">
                <HumanValue value={item.network} />
              </DetailRow>
            ) : null}
          </div>
        </section>
      ) : null}

      {item.notes ? (
        <section className="detail-section" aria-label="Notes">
          <h4 className="detail-section-title">Notes</h4>
          <pre className="notes">{item.notes}</pre>
        </section>
      ) : null}

      {(item.depends_on_resolved && item.depends_on_resolved.length > 0) ||
      (item.dependents && item.dependents.length > 0) ? (
        <section className="detail-section" aria-label="Relationships">
          <h4 className="detail-section-title">Relationships</h4>
          <div className="kv">
            {item.depends_on_resolved && item.depends_on_resolved.length > 0 ? (
              <DetailRow label="Depends on">
                <div className="rel-list">
                  {item.depends_on_resolved.map((dep) => (
                    <CiLinkChip key={dep.id} item={dep} onSelect={onSelect} />
                  ))}
                </div>
              </DetailRow>
            ) : null}
            {item.dependents && item.dependents.length > 0 ? (
              <DetailRow label="Dependents">
                <div className="rel-list">
                  {item.dependents.map((dep) => (
                    <CiLinkChip key={dep.id} item={dep} onSelect={onSelect} />
                  ))}
                </div>
              </DetailRow>
            ) : null}
          </div>
        </section>
      ) : null}

      {extras.length > 0 ? (
        <section className="detail-section" aria-label="More fields">
          <h4 className="detail-section-title">More</h4>
          <div className="kv">
            {extras.map((key) => (
              <DetailRow key={key} label={key.replace(/_/g, ' ')}>
                <HumanValue value={item[key]} />
              </DetailRow>
            ))}
          </div>
        </section>
      ) : null}

      <MachineSection item={item} onAuthRequired={onAuthRequired} />
    </div>
  )
}
