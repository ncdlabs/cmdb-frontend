import { useEffect, useMemo, useState } from 'react'
import {
  addNetworkDevices,
  ApiAuthError,
  fetchNetworkScan,
  suggestServerId,
  type DiscoveredHost,
  type NetworkDeviceAdd,
  type NetworkScanDiff,
  type NetworkScanResult,
  type ScanDiffHost,
  type ScanDiffModified,
} from '../api'

const ID_RE = /^srv-[a-z0-9]+(?:-[a-z0-9]+)*$/

type Draft = {
  selected: boolean
  id: string
  name: string
  idError: string | null
  nameError: string | null
}

type Props = {
  open: boolean
  envs: string[]
  onClose: () => void
  onAdded: (ids: string[]) => void
  onAuthRequired?: () => void
}

function validateId(id: string): string | null {
  if (!id.trim()) return 'Id is required'
  if (!ID_RE.test(id.trim())) return 'Must match srv-<lowercase-slug>'
  return null
}

function validateName(name: string): string | null {
  if (!name.trim()) return 'Name is required'
  return null
}

function hostLabel(host: { ip: string; hostname?: string | null }) {
  const short = host.hostname?.split('.')[0]
  return short || host.ip
}

function hostnameKey(hostname?: string | null): string | null {
  if (!hostname?.trim()) return null
  return hostname.trim().toLowerCase().replace(/\.$/, '')
}

function hostFamily(host: DiscoveredHost): 'ipv4' | 'ipv6' {
  if (host.family === 'ipv6' || host.family === 'ipv4') return host.family
  return host.ip.includes(':') ? 'ipv6' : 'ipv4'
}

const SOURCE_PRIORITY: Record<string, number> = {
  ndp: 4,
  arp: 4,
  aaaa: 3,
  'ipv6-sweep': 2,
  'ipv4-sweep': 1,
}

/** Merge selected v4+v6 rows that share a hostname into one Add payload. */
function buildAddDevices(
  selectedHosts: DiscoveredHost[],
  drafts: Record<string, Draft>,
  env: string,
): NetworkDeviceAdd[] {
  const used = new Set<string>()
  const devices: NetworkDeviceAdd[] = []

  for (const host of selectedHosts) {
    if (used.has(host.ip)) continue
    const key = hostnameKey(host.hostname)
    const fam = hostFamily(host)
    const partner =
      key != null
        ? selectedHosts.find(
            (other) =>
              other.ip !== host.ip &&
              !used.has(other.ip) &&
              hostnameKey(other.hostname) === key &&
              hostFamily(other) !== fam,
          )
        : undefined

    const group = partner ? [host, partner] : [host]
    for (const g of group) used.add(g.ip)

    const v4 = group.find((h) => hostFamily(h) === 'ipv4')
    const v6 = group.find((h) => hostFamily(h) === 'ipv6')
    const prefer = v4 || host
    const draft = drafts[prefer.ip] || drafts[host.ip]
    const ports = [...new Set(group.flatMap((h) => h.ports || []))].sort((a, b) => a - b)
    const mac = group.map((h) => h.mac).find((m) => Boolean(m)) || null
    const source = group
      .map((h) => h.source)
      .filter((s): s is string => Boolean(s))
      .sort((a, b) => (SOURCE_PRIORITY[b] || 0) - (SOURCE_PRIORITY[a] || 0))[0] || null
    const ipv4 = v4?.ip || null
    const ipv6 = v6?.ip || null
    const primary = ipv4 || ipv6 || host.ip

    devices.push({
      ip: primary,
      ipv4,
      ipv6,
      id: draft.id.trim(),
      name: draft.name.trim(),
      hostname: prefer.hostname || host.hostname || partner?.hostname || null,
      env: env || null,
      status: 'unknown',
      ports,
      ssh_user: null,
      mac,
      source,
    })
  }
  return devices
}

function DiffSection({ diff }: { diff: NetworkScanDiff }) {
  const { counts } = diff
  const total = counts.added + counts.removed + counts.modified

  if (!diff.baseline) {
    return (
      <div className="scan-diff" role="status">
        <h3 className="scan-diff-title">Changes since last scan</h3>
        <p className="muted-line">{diff.note || 'First scan — no previous baseline to compare.'}</p>
      </div>
    )
  }

  return (
    <div className="scan-diff" role="region" aria-label="Changes since last scan">
      <h3 className="scan-diff-title">Changes since last scan</h3>
      <p className="mono muted-line">
        vs {diff.previous_scanned_at || 'previous'} · {counts.added} added · {counts.removed} removed ·{' '}
        {counts.modified} modified
      </p>
      {total === 0 ? <p className="muted-line">No changes since the previous scan.</p> : null}
      {counts.added > 0 ? (
        <DiffGroup title="Added" tone="added" hosts={diff.added} />
      ) : null}
      {counts.removed > 0 ? (
        <DiffGroup title="Removed" tone="removed" hosts={diff.removed} />
      ) : null}
      {counts.modified > 0 ? <DiffModifiedList rows={diff.modified} /> : null}
    </div>
  )
}

function DiffGroup({
  title,
  tone,
  hosts,
}: {
  title: string
  tone: 'added' | 'removed'
  hosts: ScanDiffHost[]
}) {
  return (
    <div className={`scan-diff-group scan-diff-${tone}`}>
      <h4>
        {title} ({hosts.length})
      </h4>
      <ul className="scan-diff-list">
        {hosts.map((host) => (
          <li key={host.ip}>
            <span className="discover-hostname">{hostLabel(host)}</span>
            <span className="mono">{host.ip}</span>
            {host.hostname && host.hostname.includes('.') ? (
              <span className="muted-line">{host.hostname}</span>
            ) : null}
          </li>
        ))}
      </ul>
    </div>
  )
}

function DiffModifiedList({ rows }: { rows: ScanDiffModified[] }) {
  return (
    <div className="scan-diff-group scan-diff-modified">
      <h4>Modified ({rows.length})</h4>
      <ul className="scan-diff-list">
        {rows.map((row) => (
          <li key={row.ip}>
            <span className="discover-hostname">{hostLabel(row)}</span>
            <span className="mono">{row.ip}</span>
            <span className="muted-line">{row.changes.join(', ')}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}

export function NetworkRescanDialog({ open, envs, onClose, onAdded, onAuthRequired }: Props) {
  const [scanning, setScanning] = useState(false)
  const [adding, setAdding] = useState(false)
  const [scanError, setScanError] = useState<string | null>(null)
  const [addError, setAddError] = useState<string | null>(null)
  const [result, setResult] = useState<NetworkScanResult | null>(null)
  const [drafts, setDrafts] = useState<Record<string, Draft>>({})
  const [env, setEnv] = useState('')

  const defaultEnv = useMemo(() => {
    return envs[0] || ''
  }, [envs])

  useEffect(() => {
    if (!open) return
    setScanError(null)
    setAddError(null)
    setResult(null)
    setDrafts({})
    setEnv(defaultEnv)
    void runScan()
    // eslint-disable-next-line react-hooks/exhaustive-deps -- scan once when opened
  }, [open, defaultEnv])

  async function runScan() {
    setScanning(true)
    setScanError(null)
    setAddError(null)
    try {
      const data = await fetchNetworkScan()
      setResult(data)
      if (data.default_env) {
        setEnv(data.default_env)
      }
      const selectAll = data.discover_select_all !== false
      const next: Record<string, Draft> = {}
      for (const host of data.discovered) {
        const id = suggestServerId(host.ip, host.hostname)
        const name = host.hostname?.split('.')[0] || host.ip
        next[host.ip] = {
          selected: selectAll,
          id,
          name,
          idError: validateId(id),
          nameError: validateName(name),
        }
      }
      setDrafts(next)
    } catch (err) {
      setResult(null)
      if (err instanceof ApiAuthError) {
        setScanError(err.message)
        onAuthRequired?.()
      } else {
        setScanError(err instanceof Error ? err.message : String(err))
      }
    } finally {
      setScanning(false)
    }
  }

  const selectedHosts = useMemo(() => {
    if (!result) return [] as DiscoveredHost[]
    return result.discovered.filter((h) => drafts[h.ip]?.selected)
  }, [result, drafts])

  const selectedValid =
    selectedHosts.length > 0 &&
    selectedHosts.every((h) => {
      const d = drafts[h.ip]
      return d && !d.idError && !d.nameError
    })

  const idDupes = useMemo(() => {
    const counts = new Map<string, DiscoveredHost[]>()
    for (const h of selectedHosts) {
      const id = drafts[h.ip]?.id.trim()
      if (!id) continue
      const list = counts.get(id) || []
      list.push(h)
      counts.set(id, list)
    }
    const dupes = new Set<string>()
    for (const [id, hosts] of counts.entries()) {
      if (hosts.length < 2) continue
      // Same id on complementary v4+v6 with matching hostname is a merge, not a conflict.
      if (
        hosts.length === 2 &&
        hostnameKey(hosts[0].hostname) &&
        hostnameKey(hosts[0].hostname) === hostnameKey(hosts[1].hostname) &&
        hostFamily(hosts[0]) !== hostFamily(hosts[1])
      ) {
        continue
      }
      dupes.add(id)
    }
    return dupes
  }, [selectedHosts, drafts])

  const canAdd = selectedValid && idDupes.size === 0 && !adding && Boolean(result?.writable)

  function updateDraft(ip: string, patch: Partial<Draft>) {
    setDrafts((prev) => {
      const cur = prev[ip]
      if (!cur) return prev
      const next = { ...cur, ...patch }
      if (patch.id !== undefined) next.idError = validateId(patch.id)
      if (patch.name !== undefined) next.nameError = validateName(patch.name)
      return { ...prev, [ip]: next }
    })
  }

  function toggleAll(selected: boolean) {
    setDrafts((prev) => {
      const next: Record<string, Draft> = {}
      for (const [ip, draft] of Object.entries(prev)) {
        next[ip] = { ...draft, selected }
      }
      return next
    })
  }

  async function onAdd() {
    if (!result || !canAdd) return
    setAdding(true)
    setAddError(null)
    try {
      const devices = buildAddDevices(selectedHosts, drafts, env)
      const addResult = await addNetworkDevices(devices)
      if (addResult.error_count > 0 && addResult.created_count === 0) {
        setAddError(addResult.errors.map((e) => e.error).join('; '))
        return
      }
      if (addResult.error_count > 0) {
        setAddError(
          `Added ${addResult.created_count}; ${addResult.error_count} failed: ` +
            addResult.errors.map((e) => e.error).join('; '),
        )
      }
      onAdded(addResult.created.map((c) => c.id))
      if (addResult.error_count === 0) onClose()
    } catch (err) {
      if (err instanceof ApiAuthError) {
        setAddError(err.message)
        onAuthRequired?.()
      } else {
        setAddError(err instanceof Error ? err.message : String(err))
      }
    } finally {
      setAdding(false)
    }
  }

  if (!open) return null

  return (
    <div className="dialog-backdrop" role="presentation" onClick={onClose}>
      <div
        className="dialog-panel"
        role="dialog"
        aria-modal="true"
        aria-labelledby="network-rescan-title"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="panel-header">
          <h2 id="network-rescan-title">Rescan network</h2>
          <button type="button" className="btn btn-secondary" onClick={onClose}>
            Close
          </button>
        </div>
        <p className="machine-hint">
          One-shot LAN discovery: IPv4 subnet sweep (node LAN + inventory prefixes) plus IPv6 via NDP
          neighbors and AAAA lookups (no /64 brute-force). Each scan reports added / removed / modified
          vs the previous snapshot. Unknown hosts are selected by default — review, then Add. Never
          polled automatically.
        </p>

        <div className="toolbar-row" style={{ marginBottom: '0.75rem' }}>
          <button type="button" className="btn btn-secondary" onClick={() => void runScan()} disabled={scanning}>
            {scanning ? 'Scanning…' : 'Scan again'}
          </button>
          {result ? (
            <span className="mono muted-line">
              {result.subnets.join(', ') || 'no prefixes'} · {result.observed_count ?? '—'} observed ·{' '}
              {result.count} new
              {result.ipv4_count != null || result.ipv6_count != null
                ? ` (v4 ${result.ipv4_count ?? 0} / v6 ${result.ipv6_count ?? 0})`
                : ''}{' '}
              · {result.known_skipped} in inventory
            </span>
          ) : null}
        </div>

        {result?.notes?.length ? (
          <div className="alert alert-warn" role="status">
            {result.notes.join(' ')}
          </div>
        ) : null}

        {scanError ? <div className="error-state">{scanError}</div> : null}
        {scanning && !result ? <div className="loading-state">Scanning LAN…</div> : null}

        {result && !scanning && result.diff ? <DiffSection diff={result.diff} /> : null}

        {result && !scanning && result.count === 0 ? (
          <div className="empty-state">No unknown responders found on scanned subnets.</div>
        ) : null}

        {result && result.count > 0 ? (
          <>
            <div className="toolbar-row" style={{ marginBottom: '0.5rem' }}>
              <button type="button" className="btn btn-secondary" onClick={() => toggleAll(true)}>
                Select all
              </button>
              <button type="button" className="btn btn-secondary" onClick={() => toggleAll(false)}>
                Select none
              </button>
              <label className="form-inline">
                <span>Env</span>
                <select
                  className="select-input"
                  aria-label="Environment for new servers"
                  value={env}
                  onChange={(e) => setEnv(e.target.value)}
                >
                  <option value="">(none)</option>
                  {envs.map((e) => (
                    <option key={e} value={e}>
                      {e}
                    </option>
                  ))}
                </select>
              </label>
            </div>

            {!result.writable ? (
              <div className="alert alert-warn" role="status">
                Inventory is read-only at <span className="mono">{result.root}</span>. Mount a writable PVC (or local{' '}
                <span className="mono">CMDB_ROOT</span>) before adding devices.
              </div>
            ) : null}

            <ul className="discover-list">
              {result.discovered.map((host) => {
                const draft = drafts[host.ip]
                if (!draft) return null
                const dupe = draft.selected && idDupes.has(draft.id.trim())
                const fieldKey = host.ip.replace(/[^a-zA-Z0-9]/g, '-')
                const family = host.family || (host.ip.includes(':') ? 'ipv6' : 'ipv4')
                return (
                  <li key={host.ip} className={`discover-row${draft.selected ? ' is-selected' : ''}`}>
                    <label className="discover-check">
                      <input
                        type="checkbox"
                        checked={draft.selected}
                        onChange={(e) => updateDraft(host.ip, { selected: e.target.checked })}
                      />
                      <span className="visually-hidden">Select {host.ip}</span>
                    </label>
                    <div className="discover-body">
                      <div className="discover-title">
                        <span className="discover-hostname">
                          {host.hostname ? host.hostname.split('.')[0] : host.ip}
                        </span>
                        <span className="badge badge-kind">{family}</span>
                      </div>
                      <div className="discover-meta">
                        {host.hostname ? <span className="mono">{host.ip}</span> : null}
                        {!host.hostname ? <span className="muted-line">hostname unknown</span> : null}
                        {host.hostname && host.hostname.includes('.') ? (
                          <span className="muted-line">{host.hostname}</span>
                        ) : null}
                        <span className="mono">
                          {host.source ? `${host.source} · ` : ''}
                          {host.ping ? 'ping' : 'no-ping'}
                          {host.ports.length ? ` · ports ${host.ports.join(',')}` : ''}
                          {host.ssh_open ? ' · ssh' : ''}
                          {host.mac ? ` · ${host.mac}` : ''}
                        </span>
                        {host.in_unidentified ? <span className="muted-line">in unidentified pool</span> : null}
                      </div>
                      {draft.selected ? (
                        <div className="discover-fields">
                          <div className="form-field">
                            <label htmlFor={`discover-id-${fieldKey}`}>Id</label>
                            <input
                              id={`discover-id-${fieldKey}`}
                              className="search-input"
                              value={draft.id}
                              onChange={(e) => updateDraft(host.ip, { id: e.target.value })}
                              aria-invalid={Boolean(draft.idError || dupe)}
                            />
                            {draft.idError ? <div className="field-error">{draft.idError}</div> : null}
                            {dupe ? <div className="field-error">Duplicate id in selection</div> : null}
                          </div>
                          <div className="form-field">
                            <label htmlFor={`discover-name-${fieldKey}`}>Name</label>
                            <input
                              id={`discover-name-${fieldKey}`}
                              className="search-input"
                              value={draft.name}
                              onChange={(e) => updateDraft(host.ip, { name: e.target.value })}
                              aria-invalid={Boolean(draft.nameError)}
                            />
                            {draft.nameError ? <div className="field-error">{draft.nameError}</div> : null}
                          </div>
                        </div>
                      ) : null}
                    </div>
                  </li>
                )
              })}
            </ul>
          </>
        ) : null}

        {addError ? <div className="error-state">{addError}</div> : null}

        <div className="action-row">
          <button type="button" className="btn btn-secondary" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="btn btn-primary" disabled={!canAdd} onClick={() => void onAdd()}>
            {adding ? 'Adding…' : `Add selected (${selectedHosts.length})`}
          </button>
        </div>
      </div>
    </div>
  )
}
