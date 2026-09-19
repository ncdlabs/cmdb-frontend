import { useEffect, useId, useRef, useState } from 'react'
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
  const inputRef = useRef<HTMLInputElement>(null)
  const titleId = useId()
  const errorId = useId()

  useEffect(() => {
    if (!open) return
    const stored = getStoredApiToken()
    setBaseline(stored)
    setValue(stored)
    setError(null)
    const focusTimer = window.setTimeout(() => inputRef.current?.focus(), 0)
    return () => window.clearTimeout(focusTimer)
  }, [open])

  useEffect(() => {
    if (!open) return
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') {
        e.preventDefault()
        onClose()
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [open, onClose])

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
        aria-labelledby={titleId}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="panel-header">
          <h2 id={titleId}>API token</h2>
          <button type="button" className="btn btn-secondary" onClick={onClose}>
            Close
          </button>
        </div>
        <form onSubmit={onSubmit}>
          <p className="machine-hint">
            {tokenRequired
              ? 'This deployment requires a shared secret for live probe, confirm identity, LAN rescan, and add-device. Stored in sessionStorage only for this browser tab.'
              : 'Optional. When the server sets CMDB_API_TOKEN, paste it here for mutating actions. Stored in sessionStorage only.'}
          </p>
          <div className="form-field">
            <label htmlFor="api-token-input">Token</label>
            <input
              ref={inputRef}
              id="api-token-input"
              type="password"
              className="search-input"
              autoComplete="off"
              spellCheck={false}
              value={value}
              onChange={(e) => {
                setValue(e.target.value)
                setError(null)
              }}
              aria-invalid={Boolean(error)}
              aria-describedby={error ? errorId : undefined}
            />
            {error ? (
              <div id={errorId} className="field-error" role="alert">
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
