import { useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { Copyable, DriveInfoTip, KindBadge, PillList, StatusBadge } from './components'

const SWATCHES = [
  { name: 'Canvas', varName: '--cmdb-canvas', hex: '#dfe6e2', usage: 'Page background' },
  { name: 'Surface', varName: '--cmdb-surface', hex: '#f3f6f4', usage: 'Panels / inputs' },
  { name: 'Ink', varName: '--cmdb-ink', hex: '#14201b', usage: 'Primary text' },
  { name: 'Muted', varName: '--cmdb-ink-muted', hex: '#4a5c54', usage: 'Secondary text' },
  { name: 'Border', varName: '--cmdb-border', hex: '#b7c4bc', usage: 'Dividers / controls' },
  { name: 'Accent', varName: '--cmdb-accent', hex: '#0c6b52', usage: 'Primary actions / links' },
  { name: 'Danger', varName: '--cmdb-danger', hex: '#9b2c2c', usage: 'Errors / destructive' },
  { name: 'Warn', varName: '--cmdb-warn', hex: '#8a5a12', usage: 'Warnings / planned' },
]

export function StyleGuidePage() {
  const [name, setName] = useState('')
  const [dirty, setDirty] = useState(false)
  const [pending, setPending] = useState(false)
  const nameError = name.trim().length === 0 ? 'Name is required' : ''

  function onNameChange(value: string) {
    setName(value)
    setDirty(true)
  }

  function onSave(e: FormEvent) {
    e.preventDefault()
    if (nameError) return
    setPending(true)
    window.setTimeout(() => {
      setPending(false)
      setDirty(false)
    }, 400)
  }

  return (
    <div className="sg-page" data-testid="style-guide-root">
      <a className="skip-link" href="#sg-main">
        Skip to content
      </a>
      <header className="sg-header">
        <p className="sg-kicker">CMDB · Internal</p>
        <h1 className="app-brand" style={{ fontSize: '2rem' }}>
          Style guide
        </h1>
        <p className="app-tagline">
          Living reference for CMDB browse UI tokens and shared patterns. Prefer these primitives over ad-hoc styles.
        </p>
        <nav className="sg-nav" aria-label="Style guide sections">
          <a href="#sg-colors">Colors</a>
          <a href="#sg-typography">Typography</a>
          <a href="#sg-buttons">Buttons</a>
          <a href="#sg-action-layout">Actions</a>
          <a href="#sg-forms">Forms</a>
          <a href="#sg-alerts">Alerts</a>
          <a href="#sg-badges">Badges</a>
          <a href="#sg-tabs">Tabs</a>
          <a href="#sg-placement">Placement</a>
          <a href="#sg-surfaces">Surfaces</a>
          <a href="#sg-detail">Detail</a>
          <a href="#sg-machine">Machine</a>
          <a href="#sg-network-rescan">Network rescan</a>
          <a href="#sg-tables">Tables</a>
          <a href="#sg-empty-loading">Empty / loading</a>
          <a href="#sg-a11y">Accessibility</a>
          <Link to="/">Back to CMDB</Link>
        </nav>
      </header>

      <main id="sg-main">
        <section className="sg-section" id="sg-colors" data-testid="sg-colors">
          <h2>Colors</h2>
          <p>CSS variables defined in <code>src/index.css</code>.</p>
          <div className="swatch-grid">
            {SWATCHES.map((s) => (
              <div className="swatch" key={s.varName}>
                <div className="swatch-color" style={{ background: `var(${s.varName})` }} />
                <div className="swatch-meta">
                  <strong>{s.name}</strong>
                  <div className="mono">{s.hex}</div>
                  <div>{s.usage}</div>
                </div>
              </div>
            ))}
          </div>
        </section>

        <section className="sg-section" id="sg-typography" data-testid="sg-typography">
          <h2>Typography</h2>
          <p>IBM Plex Sans for UI; IBM Plex Mono for ids, paths, and addresses.</p>
          <p className="app-brand" style={{ fontSize: '2rem' }}>
            CMDB
          </p>
          <p>Body copy uses the sans stack at comfortable reading size.</p>
          <p className="mono">srv-app-01 · 10.0.0.20</p>
        </section>

        <section className="sg-section" id="sg-buttons" data-testid="sg-buttons">
          <h2>Buttons</h2>
          <p>Primary for confirm; secondary for cancel/neutral; danger for destructive.</p>
          <div className="demo-row">
            <button type="button" className="btn btn-primary">
              Primary
            </button>
            <button type="button" className="btn btn-secondary">
              Secondary
            </button>
            <button type="button" className="btn btn-danger">
              Danger
            </button>
            <button type="button" className="btn btn-primary" disabled>
              Disabled
            </button>
          </div>
        </section>

        <section className="sg-section" id="sg-action-layout" data-testid="sg-action-layout">
          <h2>Action placement</h2>
          <p>Destructive left; Cancel left of Confirm; Confirm rightmost. Cancel returns to the previous screen.</p>
          <div className="action-row">
            <button type="button" className="btn btn-danger">
              Delete
            </button>
            <button type="button" className="btn btn-secondary">
              Cancel
            </button>
            <button type="button" className="btn btn-primary">
              Save
            </button>
          </div>
        </section>

        <section className="sg-section" id="sg-forms" data-testid="sg-forms">
          <h2>Forms</h2>
          <p>
            Frontend validation before submit with field-level errors. Save stays disabled until dirty versus loaded
            baseline (and while pending). Demo form below. Network rescan Add uses the same field-error pattern;
            Confirm stays disabled until selection is valid.
          </p>
          <form onSubmit={onSave} noValidate>
            <div className="form-field">
              <label htmlFor="sg-name">Name</label>
              <input
                id="sg-name"
                className="search-input"
                value={name}
                onChange={(e) => onNameChange(e.target.value)}
                aria-invalid={Boolean(nameError && dirty)}
              />
              {dirty && nameError ? <div className="field-error">{nameError}</div> : null}
            </div>
            <div className="action-row">
              <button type="button" className="btn btn-secondary" onClick={() => history.back()}>
                Cancel
              </button>
              <button type="submit" className="btn btn-primary" disabled={!dirty || pending || Boolean(nameError)}>
                {pending ? 'Saving…' : 'Save'}
              </button>
            </div>
          </form>
        </section>

        <section className="sg-section" id="sg-alerts" data-testid="sg-alerts">
          <h2>Alerts</h2>
          <div className="alert alert-warn" role="status">
            Example warning: YAML parse errors were skipped during load.
          </div>
        </section>

        <section className="sg-section" id="sg-badges" data-testid="sg-badges">
          <h2>Badges</h2>
          <div className="demo-row">
            <KindBadge kind="server" />
            <StatusBadge status="active" />
            <StatusBadge status="planned" />
            <StatusBadge status="deprecated" />
            <StatusBadge status="unknown" />
            <span className="badge badge-k3s">k3s</span>
          </div>
        </section>

        <section className="sg-section" id="sg-tabs" data-testid="sg-tabs">
          <h2>Tabs</h2>
          <p>
            Browse results use <code>result-tabs</code> for Active vs Deprecated. Active includes non-deprecated statuses
            (active, planned, unknown). Deprecated is <code>status=deprecated</code> only. URL: default Active;{' '}
            <code>?tab=deprecated</code> for Deprecated.
          </p>
          <div className="panel">
            <div className="panel-header">
              <h2>Results</h2>
              <span className="mono">12</span>
            </div>
            <div className="result-tabs" role="tablist" aria-label="CI lifecycle demo">
              <button type="button" role="tab" aria-selected className="result-tab is-active">
                Active
                <span className="result-tab-count mono">10</span>
              </button>
              <button type="button" role="tab" aria-selected={false} className="result-tab">
                Deprecated
                <span className="result-tab-count mono">2</span>
              </button>
            </div>
            <div className="detail">Tab panel content (CI list) goes here.</div>
          </div>
        </section>

        <section className="sg-section" id="sg-placement" data-testid="sg-placement">
          <h2>Application placement</h2>
          <p>
            Application detail shows a <strong>Placement</strong> section: host servers (from <code>runs_on</code> or{' '}
            <code>depends_on</code>), and when <code>runtime: k3s</code> / <code>k8s:</code> is set, namespace plus
            Deployments, StatefulSets, Services, and Ingresses. Pod names are not inventoried (ephemeral).
          </p>
          <div className="detail" style={{ padding: 0 }}>
            <div className="kv">
              <div className="kv-row">
                <div className="kv-label">Runtime</div>
                <div className="kv-value">
                  <span className="runtime-badge is-k3s">k3s</span>
                </div>
              </div>
              <div className="kv-row">
                <div className="kv-label">Namespace</div>
                <div className="kv-value">
                  <span className="mono">demo</span>
                </div>
              </div>
            </div>
          </div>
        </section>

        <section className="sg-section" id="sg-surfaces" data-testid="sg-surfaces">
          <h2>Surfaces</h2>
          <p>Panels wrap interactive lists and detail content. Avoid card chrome in heroes; use panels for interaction.</p>
          <div className="panel">
            <div className="panel-header">
              <h2>Panel</h2>
              <span className="mono">example</span>
            </div>
            <div className="detail">Panel body content.</div>
          </div>
        </section>

        <section className="sg-section" id="sg-detail" data-testid="sg-detail">
          <h2>Detail fields</h2>
          <p>
            CI detail uses labeled rows, pills for lists, and copy buttons for pasteable values (id, SSH, IPs,
            endpoints, paths). Never dump raw JSON in the detail panel — flatten maps into rows and arrays into pills.
          </p>
          <div className="detail" style={{ padding: 0 }}>
            <div className="kv">
              <div className="kv-row">
                <div className="kv-label">SSH</div>
                <div className="kv-value">
                  <Copyable text="ops@10.0.0.10">ops@10.0.0.10</Copyable>
                </div>
              </div>
              <div className="kv-row">
                <div className="kv-label">Roles</div>
                <div className="kv-value">
                  <PillList items={['dns', 'edge', 'app']} />
                </div>
              </div>
            </div>
          </div>
        </section>

        <section className="sg-section" id="sg-machine" data-testid="sg-machine">
          <h2>Machine detail</h2>
          <p>
            Server detail shows a Machine block: <code>k3s</code> desired/observed membership, physical{' '}
            <code>hardware.disks</code> (separate from <code>filesystems</code>), negotiated NIC speed under{' '}
            <code>network.interfaces</code>, plus static <code>os</code>/<code>hardware</code> from YAML. Optional{' '}
            <strong>Refresh</strong> runs a one-shot SSH probe (load, RAM, temp, physical disks, NICs, filesystem usage).
            Never poll live metrics on an interval. Do not auto-write probe results into inventory YAML.
          </p>
          <div className="demo-row" style={{ marginTop: '1rem' }}>
            <span className="mono">eth0 · 1000 Mb/s · full · 10.0.0.20/24</span>
          </div>
          <div className="demo-row" style={{ marginTop: '0.5rem' }}>
            <span className="mono">/dev/nvme0n1 · 238.5 GiB · nvme · EXAMPLE-SSD-1TB · os</span>
          </div>
          <div className="demo-row" style={{ marginTop: '0.5rem' }}>
            <span className="mono">/: 45.2 GiB / 238.5 GiB (19%)</span>
            <DriveInfoTip
              driveType="NVMe"
              details={[
                { label: 'Type', value: 'NVMe' },
                { label: 'Device', value: 'nvme0n1' },
                { label: 'Model', value: 'EXAMPLE-SSD-1TB' },
                { label: 'Serial', value: 'EXSERIAL0001' },
                { label: 'Transport', value: 'nvme' },
                { label: 'Drive size', value: '238.5 GiB' },
              ]}
            />
          </div>
          <div className="machine-section" style={{ borderTop: 'none', paddingTop: 0, marginTop: 0 }}>
            <div className="machine-header">
              <h4 className="machine-title">Machine</h4>
              <button type="button" className="btn btn-secondary">
                Refresh
              </button>
            </div>
            <p className="machine-hint">
              Static specs come from inventory YAML (desired vs last observed). Refresh runs a one-shot SSH probe.
            </p>
            <div className="kv">
              <div className="kv-row">
                <div className="kv-label">k3s observed</div>
                <div className="kv-value mono">ready=true · role=agent · etcd=false · v1.36.2+k3s1</div>
              </div>
            </div>
            <div className="live-panel">
              <div className="live-panel-header">
                <strong>Live</strong>
                <span className="mono muted-line">as of …</span>
              </div>
              <div className="kv">
                <div className="kv-row">
                  <div className="kv-label">Load</div>
                  <div className="kv-value mono">0.12 / 0.08 / 0.05</div>
                </div>
                <div className="kv-row">
                  <div className="kv-label">NICs</div>
                  <div className="kv-value mono">eno1 · 1000 Mb/s · full</div>
                </div>
              </div>
            </div>
          </div>
        </section>

        <section className="sg-section" id="sg-network-rescan" data-testid="sg-network-rescan">
          <h2>Network rescan</h2>
          <p>
            Toolbar <strong>Rescan network</strong> opens a dialog and runs a one-shot LAN discovery (
            <code>POST /api/network/scan</code>): IPv4 sweeps of inventory subnets (
            <code>env.network.lan_cidr</code> or /24s from server IPv4s), plus IPv6 via NDP neighbors (
            <code>ip -6 neigh</code>, requires <code>hostNetwork</code>) and AAAA lookups — never brute-force a
            /64. Results show family (ipv4/ipv6), IP, PTR, source, ports/MAC. Multi-select rows expose editable
            Id/Name fields. <strong>Add selected</strong> writes <code>servers/&lt;id&gt;.yaml</code> with{' '}
            <code>addresses.ipv4</code> and/or <code>addresses.ipv6</code>. Cancel closes the dialog. Action row:
            Cancel left, Confirm rightmost.
          </p>
          <div className="demo-row" style={{ marginTop: '1rem' }}>
            <button type="button" className="btn btn-secondary">
              Rescan network
            </button>
            <button type="button" className="btn btn-secondary">
              Set API token
            </button>
          </div>
          <p style={{ marginTop: '0.75rem' }}>
            When the server sets <code>CMDB_API_TOKEN</code>, the toolbar shows <strong>Set API token</strong>.
            Token is saved in sessionStorage and sent as <code>X-CMDB-Token</code> on Refresh / Rescan / Add.
            Save is disabled until dirty vs the loaded baseline; Cancel closes the dialog.
          </p>
          <div className="discover-row is-selected" style={{ marginTop: '1rem' }}>
            <div className="discover-check">
              <input type="checkbox" checked readOnly aria-label="Demo selected host" />
            </div>
            <div className="discover-body">
              <div className="discover-title">
                <span className="discover-hostname">pi</span>
                <span className="badge badge-kind">ipv4</span>
              </div>
              <div className="discover-meta">
                <span className="mono">10.0.0.55</span>
                <span className="muted-line">dns.example.com</span>
                <span className="mono">ping · ports 22 · ssh</span>
              </div>
              <div className="discover-fields">
                <div className="form-field">
                  <label htmlFor="sg-discover-id">Id</label>
                  <input id="sg-discover-id" className="search-input" defaultValue="srv-pi" readOnly />
                </div>
                <div className="form-field">
                  <label htmlFor="sg-discover-name">Name</label>
                  <input id="sg-discover-name" className="search-input" defaultValue="pi" readOnly />
                </div>
              </div>
            </div>
          </div>
          <div className="action-row">
            <button type="button" className="btn btn-secondary">
              Cancel
            </button>
            <button type="button" className="btn btn-primary">
              Add selected (1)
            </button>
          </div>
        </section>

        <section className="sg-section" id="sg-tables" data-testid="sg-tables">
          <h2>Tables</h2>
          <p>Used sparingly; browse prefers list + detail. Table pattern for dense comparisons:</p>
          <table className="table-demo">
            <thead>
              <tr>
                <th>ID</th>
                <th>Kind</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td className="mono">srv-app-01</td>
                <td>server</td>
                <td>active</td>
              </tr>
            </tbody>
          </table>
        </section>

        <section className="sg-section" id="sg-empty-loading" data-testid="sg-empty-loading">
          <h2>Empty + loading</h2>
          <div className="panel">
            <div className="loading-state">Loading…</div>
            <div className="empty-state">No configuration items match.</div>
            <div className="error-state">Request failed</div>
          </div>
        </section>

        <section className="sg-section" id="sg-a11y" data-testid="sg-a11y">
          <h2>Accessibility</h2>
          <ul>
            <li>Skip link to main content</li>
            <li>One page <code>h1</code>; section <code>h2</code>s with stable <code>sg-*</code> ids</li>
            <li>Labeled search and filter controls</li>
            <li>Focus outlines on inputs and buttons</li>
          </ul>
        </section>
      </main>
    </div>
  )
}
