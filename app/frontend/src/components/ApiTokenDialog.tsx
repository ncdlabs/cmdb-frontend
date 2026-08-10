import { useEffect, useState } from 'react'
import { getStoredApiToken, setStoredApiToken } from '../api'

type Props = {
  open: boolean
  tokenRequired: boolean
  onClose: () => void
  onSaved: () => void
}

/** Session API token for mutating routes when CMDB_API_TOKEN is configured. */
export function ApiTokenDialog({ open, tokenRequired, onClose, onSaved }: Props) {
  const [baseline, setBaseline] = useState('')
  const [value, setValue] = useState('')
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!open) return
    const stored = getStoredApiToken()
    setBaseline(stored)
    setValue(stored)
    setError(null)
  }, [open])

  if (!open) return null

  const dirty = value !== baseline
  const canSave = dirty

  function onSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!canSave) return
    if (tokenRequired && !value.trim()) {
      setError('Token is required for Refresh and Rescan on this deployment')
      return
    }
    setStoredApiToken(value)
    setBaseline(value.trim())
    setError(null)
    onSaved()
    onClose()
  }

  return (
    <div className="dialog-backdrop" role="presentation" onClick={onClose}>
      <div
        className="dialog-panel dialog-panel-narrow"
        role="dialog"
        aria-modal="true"
        aria-labelledby="api-token-title"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="panel-header">
          <h2 id="api-token-title">API token</h2>
          <button type="button" className="btn btn-secondary" onClick={onClose}>
            Close
          </button>
        </div>
        <form onSubmit={onSubmit}>
          <p className="machine-hint">
            {tokenRequired
              ? 'This deployment requires a shared secret for live probe, LAN rescan, and add-device. Stored in sessionStorage only for this browser tab.'
              : 'Optional. When the server sets CMDB_API_TOKEN, paste it here for mutating actions. Stored in sessionStorage only.'}
          </p>
          <div className="form-field">
            <label htmlFor="api-token-input">Token</label>
            <input
              id="api-token-input"
              type="password"
              className="search-input"
              autoComplete="off"
              value={value}
              onChange={(e) => {
                setValue(e.target.value)
                setError(null)
              }}
              aria-invalid={Boolean(error)}
              aria-describedby={error ? 'api-token-error' : undefined}
            />
            {error ? (
              <div id="api-token-error" className="field-error" role="alert">
                {error}
              </div>
            ) : null}
          </div>
          <div className="action-row">
            <button
              type="button"
              className="btn btn-danger"
              onClick={() => {
                setValue('')
                setError(null)
              }}
              disabled={!value}
            >
              Clear
            </button>
            <button type="button" className="btn btn-secondary" onClick={onClose}>
              Cancel
            </button>
            <button type="submit" className="btn btn-primary" disabled={!canSave}>
              Save
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
