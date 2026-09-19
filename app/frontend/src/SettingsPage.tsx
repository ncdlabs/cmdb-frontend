import { useEffect, useMemo, useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import {
  ApiAuthError,
  fetchAuthStatus,
  fetchSettings,
  getStoredApiToken,
  saveSettings,
  type OpsSettingsFields,
  type OpsSettingsResponse,
} from './api'
import { ApiTokenDialog } from './components/ApiTokenDialog'

type FormState = {
  default_ssh_user: string
  lan_cidrs: string
  default_env: string
  discover_select_all: boolean
  confirm_sets_ssh: boolean
  confirm_probes_persist: boolean
}

function toForm(stored: OpsSettingsFields): FormState {
  return {
    default_ssh_user: stored.default_ssh_user || '',
    lan_cidrs: (stored.lan_cidrs || []).join(', '),
    default_env: stored.default_env || '',
    discover_select_all: Boolean(stored.discover_select_all),
    confirm_sets_ssh: Boolean(stored.confirm_sets_ssh),
    confirm_probes_persist: Boolean(stored.confirm_probes_persist),
  }
}

export function SettingsPage() {
  const [data, setData] = useState<OpsSettingsResponse | null>(null)
  const [form, setForm] = useState<FormState | null>(null)
  const [baseline, setBaseline] = useState<FormState | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [saveError, setSaveError] = useState<string | null>(null)
  const [tokenRequired, setTokenRequired] = useState(false)
  const [tokenDialogOpen, setTokenDialogOpen] = useState(false)
  const [hasSessionToken, setHasSessionToken] = useState(() => Boolean(getStoredApiToken()))

  async function load() {
    setLoading(true)
    setError(null)
    try {
      const next = await fetchSettings()
      setData(next)
      const f = toForm(next.stored)
      setForm(f)
      setBaseline(f)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void load()
    fetchAuthStatus()
      .then((s) => setTokenRequired(s.token_required))
      .catch(() => undefined)
  }, [])

  const dirty = useMemo(() => {
    if (!form || !baseline) return false
    return JSON.stringify(form) !== JSON.stringify(baseline)
  }, [form, baseline])

  function patchForm(partial: Partial<FormState>) {
    setForm((prev) => (prev ? { ...prev, ...partial } : prev))
    setSaveError(null)
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    if (!form || !dirty || saving) return
    setSaving(true)
    setSaveError(null)
    try {
      const next = await saveSettings({
        default_ssh_user: form.default_ssh_user.trim(),
        lan_cidrs: form.lan_cidrs,
        default_env: form.default_env.trim(),
        discover_select_all: form.discover_select_all,
        confirm_sets_ssh: form.confirm_sets_ssh,
        confirm_probes_persist: form.confirm_probes_persist,
      })
      setData(next)
      const f = toForm(next.stored)
      setForm(f)
      setBaseline(f)
    } catch (err) {
      if (err instanceof ApiAuthError) {
        setSaveError(err.message)
        setTokenDialogOpen(true)
      } else {
        setSaveError(err instanceof Error ? err.message : String(err))
      }
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="app-shell">
      <header className="app-header">
        <div>
          <p className="app-brand">CMDB · Settings</p>
          <p className="app-tagline">Operational configuration stored on the inventory PVC</p>
        </div>
        <div className="toolbar-row">
          {tokenRequired ? (
            <button
              type="button"
              className="btn btn-secondary"
              onClick={() => setTokenDialogOpen(true)}
            >
              {hasSessionToken ? 'API token ✓' : 'Set API token'}
            </button>
          ) : null}
          <Link className="btn btn-secondary" to="/">
            Back to browse
          </Link>
        </div>
      </header>

      <main id="main">
        {loading ? <div className="loading-state">Loading settings…</div> : null}
        {error ? <div className="error-state">{error}</div> : null}

        {data && form ? (
          <form className="panel settings-panel" onSubmit={onSubmit}>
            {!data.writable ? (
              <div className="alert alert-warn" role="status">
                Inventory is read-only — settings cannot be saved until CMDB_ROOT is writable.
              </div>
            ) : null}

            <section className="detail-section" aria-label="SSH and probing">
              <h2 className="detail-section-title">SSH &amp; probing</h2>
              <p className="machine-hint">
                Default user applied when LAN Add finds port 22 open. Confirm identity can also set{' '}
                <span className="mono">ssh:</span> and optionally probe+persist machine specs.
              </p>
              <div className="form-field">
                <label htmlFor="settings-ssh-user">Default SSH user</label>
                <input
                  id="settings-ssh-user"
                  className="search-input"
                  value={form.default_ssh_user}
                  onChange={(e) => patchForm({ default_ssh_user: e.target.value })}
                  placeholder="e.g. lou"
                  autoComplete="off"
                  spellCheck={false}
                />
                <p className="muted-line mono">
                  effective: {data.effective.default_ssh_user || '(none)'} · source:{' '}
                  {data.sources.default_ssh_user}
                </p>
              </div>
              <label className="form-check">
                <input
                  type="checkbox"
                  checked={form.confirm_sets_ssh}
                  onChange={(e) => patchForm({ confirm_sets_ssh: e.target.checked })}
                />
                <span>On Confirm identity, set ssh: when port 22 was observed</span>
              </label>
              <label className="form-check">
                <input
                  type="checkbox"
                  checked={form.confirm_probes_persist}
                  onChange={(e) => patchForm({ confirm_probes_persist: e.target.checked })}
                />
                <span>On Confirm identity, run live probe and persist os/hardware/network</span>
              </label>
            </section>

            <section className="detail-section" aria-label="Network discovery">
              <h2 className="detail-section-title">Network discovery</h2>
              <div className="form-field">
                <label htmlFor="settings-lan-cidrs">Extra LAN CIDRs</label>
                <input
                  id="settings-lan-cidrs"
                  className="search-input"
                  value={form.lan_cidrs}
                  onChange={(e) => patchForm({ lan_cidrs: e.target.value })}
                  placeholder="192.168.1.0/24"
                  autoComplete="off"
                  spellCheck={false}
                />
                <p className="muted-line">
                  Comma-separated. Unioned with Helm <span className="mono">CMDB_LAN_CIDR</span>, node
                  IP /24, and inventory prefixes.
                </p>
                <p className="muted-line mono">
                  effective: {(data.effective.lan_cidrs || []).join(', ') || '(none)'} · source:{' '}
                  {data.sources.lan_cidrs}
                </p>
              </div>
              <label className="form-check">
                <input
                  type="checkbox"
                  checked={form.discover_select_all}
                  onChange={(e) => patchForm({ discover_select_all: e.target.checked })}
                />
                <span>Select discovered hosts by default in Rescan</span>
              </label>
            </section>

            <section className="detail-section" aria-label="Defaults">
              <h2 className="detail-section-title">Defaults</h2>
              <div className="form-field">
                <label htmlFor="settings-default-env">Default env for new servers</label>
                <input
                  id="settings-default-env"
                  className="search-input"
                  list="settings-env-list"
                  value={form.default_env}
                  onChange={(e) => patchForm({ default_env: e.target.value })}
                  placeholder="e.g. demo-lab or leave blank"
                  autoComplete="off"
                />
                <datalist id="settings-env-list">
                  {(data.envs || []).map((envId) => (
                    <option key={envId} value={envId} />
                  ))}
                </datalist>
                <p className="muted-line">Used when Rescan Add leaves env blank.</p>
              </div>
            </section>

            <section className="detail-section" aria-label="Deployment facts">
              <h2 className="detail-section-title">Deployment (read-only)</h2>
              <div className="kv">
                <div className="kv-row">
                  <div className="kv-label">CMDB_ROOT</div>
                  <div className="kv-value mono">{data.deployment.cmdb_root || '—'}</div>
                </div>
                <div className="kv-row">
                  <div className="kv-label">Node IP</div>
                  <div className="kv-value mono">{data.deployment.cmdb_node_ip || '—'}</div>
                </div>
                <div className="kv-row">
                  <div className="kv-label">Helm CMDB_LAN_CIDR</div>
                  <div className="kv-value mono">{data.deployment.cmdb_lan_cidr_env || '—'}</div>
                </div>
                <div className="kv-row">
                  <div className="kv-label">Helm default SSH user</div>
                  <div className="kv-value mono">
                    {data.deployment.cmdb_default_ssh_user_env || '—'}
                  </div>
                </div>
                <div className="kv-row">
                  <div className="kv-label">SSH keys mounted</div>
                  <div className="kv-value mono">
                    {data.deployment.cmdb_ssh_dir_configured ? 'yes' : 'no'}
                  </div>
                </div>
                <div className="kv-row">
                  <div className="kv-label">API token required</div>
                  <div className="kv-value mono">
                    {data.deployment.api_token_required ? 'yes' : 'no'}
                  </div>
                </div>
                <div className="kv-row">
                  <div className="kv-label">Settings path</div>
                  <div className="kv-value mono">{data.settings_path}</div>
                </div>
              </div>
            </section>

            {saveError ? <div className="error-state">{saveError}</div> : null}

            <div className="action-row">
              <Link className="btn btn-secondary" to="/">
                Cancel
              </Link>
              <button
                type="submit"
                className="btn btn-primary"
                disabled={!dirty || saving || !data.writable}
              >
                {saving ? 'Saving…' : 'Save changes'}
              </button>
            </div>
          </form>
        ) : null}
      </main>

      <ApiTokenDialog
        open={tokenDialogOpen}
        tokenRequired={tokenRequired}
        onClose={() => setTokenDialogOpen(false)}
        onSaved={() => setHasSessionToken(Boolean(getStoredApiToken()))}
      />
    </div>
  )
}
