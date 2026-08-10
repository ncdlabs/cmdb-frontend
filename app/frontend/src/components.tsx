import { useState, type ReactNode } from 'react'
import type { CiSummary } from './api'

const STATUS_CLASS: Record<string, string> = {
  active: 'badge-active',
  deprecated: 'badge-deprecated',
  planned: 'badge-planned',
  unknown: 'badge-unknown',
}

export function StatusBadge({ status }: { status?: string }) {
  const value = status || 'unknown'
  const cls = STATUS_CLASS[value] || 'badge-unknown'
  return <span className={`badge ${cls}`}>{value}</span>
}

export function KindBadge({ kind }: { kind?: string }) {
  return <span className="badge badge-kind">{kind || 'unknown'}</span>
}

export function CiLinkChip({ item, onSelect }: { item: CiSummary; onSelect: (id: string) => void }) {
  if (item.missing) {
    return (
      <span className="chip mono" title="Missing from CMDB">
        {item.id} (missing)
      </span>
    )
  }
  return (
    <button type="button" className="dep-link" onClick={() => onSelect(item.id)}>
      <KindBadge kind={item.kind} />
      <span className="mono">{item.id}</span>
      {item.name ? <span>{item.name}</span> : null}
    </button>
  )
}

export function isHttpUrl(value: string): boolean {
  return /^https?:\/\//i.test(value)
}

function CopyIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true" focusable="false">
      <rect x="5.5" y="5.5" width="8" height="8" rx="1.5" fill="none" stroke="currentColor" strokeWidth="1.4" />
      <path
        d="M3.5 10.5h-1A1.5 1.5 0 0 1 1 9V3.5A1.5 1.5 0 0 1 2.5 2H8A1.5 1.5 0 0 1 9.5 3.5v1"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.4"
      />
    </svg>
  )
}

export function CopyButton({ text, label }: { text: string; label?: string }) {
  const [copied, setCopied] = useState(false)

  async function onCopy() {
    const value = text.trim()
    if (!value) return
    try {
      await navigator.clipboard.writeText(value)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1400)
    } catch {
      // ignore clipboard failures (non-secure context, permissions)
    }
  }

  return (
    <button
      type="button"
      className={`btn-copy${copied ? ' is-copied' : ''}`}
      onClick={onCopy}
      aria-label={copied ? 'Copied' : label || `Copy ${text}`}
      title={copied ? 'Copied' : 'Copy'}
    >
      {copied ? <span className="btn-copy-ok">✓</span> : <CopyIcon />}
    </button>
  )
}

export function Copyable({
  text,
  children,
  mono = true,
  label,
}: {
  text: string
  children?: ReactNode
  mono?: boolean
  label?: string
}) {
  if (!text) return <span className="mono">—</span>
  return (
    <span className="copyable">
      <span className={mono ? 'mono' : undefined}>{children ?? text}</span>
      <CopyButton text={text} label={label} />
    </span>
  )
}

export function Pill({
  children,
  href,
  copyText,
}: {
  children: ReactNode
  href?: string
  copyText?: string
}) {
  const body = href ? (
    <a href={href} target="_blank" rel="noreferrer">
      {children}
    </a>
  ) : (
    children
  )
  return (
    <span className="chip">
      <span className={typeof children === 'string' || href ? 'mono' : undefined}>{body}</span>
      {copyText ? <CopyButton text={copyText} /> : null}
    </span>
  )
}

export function PillList({ items }: { items: string[] }) {
  if (items.length === 0) return <span className="mono">—</span>
  return (
    <ul className="chip-list">
      {items.map((item) => (
        <li key={item}>
          <Pill href={isHttpUrl(item) ? item : undefined} copyText={item}>
            {item}
          </Pill>
        </li>
      ))}
    </ul>
  )
}

export function DetailRow({
  label,
  children,
  copyText,
}: {
  label: string
  children: ReactNode
  copyText?: string
}) {
  return (
    <div className="kv-row">
      <div className="kv-label">{label}</div>
      <div className="kv-value">
        {copyText ? <Copyable text={copyText}>{children}</Copyable> : children}
      </div>
    </div>
  )
}

function humanizeKey(key: string): string {
  return key
    .replace(/_/g, ' ')
    .replace(/([a-z])([A-Z])/g, '$1 $2')
    .replace(/\b\w/g, (c) => c.toUpperCase())
}

function stringifyScalar(value: unknown): string | null {
  if (value == null || value === '') return null
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
    return String(value)
  }
  return null
}

/** Flatten nested maps/arrays into readable labeled rows and pills — never JSON. */
export function HumanValue({ value, depth = 0 }: { value: unknown; depth?: number }) {
  if (value == null || value === '') return <span className="mono">—</span>
  if (typeof value === 'string') {
    if (isHttpUrl(value)) {
      return (
        <Copyable text={value}>
          <a href={value} target="_blank" rel="noreferrer">
            {value}
          </a>
        </Copyable>
      )
    }
    return <Copyable text={value}>{value}</Copyable>
  }
  if (typeof value === 'number' || typeof value === 'boolean') {
    const text = String(value)
    return <Copyable text={text}>{text}</Copyable>
  }
  if (Array.isArray(value)) {
    if (value.length === 0) return <span className="mono">—</span>
    if (value.every((v) => typeof v === 'string' || typeof v === 'number' || typeof v === 'boolean')) {
      return <PillList items={value.map((v) => String(v))} />
    }
    return (
      <ul className="detail-stack">
        {value.map((entry, idx) => (
          <li key={idx}>
            <HumanValue value={entry} depth={depth + 1} />
          </li>
        ))}
      </ul>
    )
  }
  if (typeof value === 'object') {
    const entries = Object.entries(value as Record<string, unknown>).filter(([, v]) => v != null && v !== '')
    if (entries.length === 0) return <span className="mono">—</span>
    if (depth >= 2) {
      // Avoid deep nesting; show compact key=value pills
      return (
        <ul className="chip-list">
          {entries.map(([k, v]) => {
            const scalar = stringifyScalar(v)
            return (
              <li key={k}>
                <Pill copyText={scalar ?? undefined}>
                  <span className="pill-key">{humanizeKey(k)}</span>
                  {scalar ? <span className="mono"> {scalar}</span> : null}
                </Pill>
              </li>
            )
          })}
        </ul>
      )
    }
    return (
      <div className="kv kv-nested">
        {entries.map(([k, v]) => {
          const scalar = stringifyScalar(v)
          return (
            <DetailRow key={k} label={humanizeKey(k)} copyText={scalar ?? undefined}>
              {scalar ? <span className="mono">{scalar}</span> : <HumanValue value={v} depth={depth + 1} />}
            </DetailRow>
          )
        })}
      </div>
    )
  }
  return <span>{String(value)}</span>
}

export function AddressBlock({ addresses }: { addresses: Record<string, unknown> }) {
  const order = ['ipv4', 'ipv6', 'tailscale', 'hostname', 'mac', 'dns']
  const keys = [
    ...order.filter((k) => k in addresses),
    ...Object.keys(addresses)
      .filter((k) => !order.includes(k))
      .sort(),
  ]

  return (
    <div className="kv kv-nested">
      {keys.map((key) => {
        const value = addresses[key]
        if (Array.isArray(value) && value.every((v) => typeof v === 'string')) {
          return (
            <DetailRow key={key} label={humanizeKey(key)}>
              <PillList items={value} />
            </DetailRow>
          )
        }
        const scalar = stringifyScalar(value)
        if (scalar) {
          return (
            <DetailRow key={key} label={humanizeKey(key)} copyText={scalar}>
              <span className="mono">{scalar}</span>
            </DetailRow>
          )
        }
        return (
          <DetailRow key={key} label={humanizeKey(key)}>
            <HumanValue value={value} />
          </DetailRow>
        )
      })}
    </div>
  )
}

export function DriveInfoTip({
  driveType,
  details,
}: {
  driveType?: string | null
  details: { label: string; value: string }[]
}) {
  const type = driveType || 'Unknown'
  return (
    <span className={`drive-tip drive-tip-${type.toLowerCase().replace(/[^a-z0-9]+/g, '-')}`}>
      <span className="drive-type-badge">{type}</span>
      <button type="button" className="drive-info-btn" aria-label={`${type} drive details`}>
        <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true" focusable="false">
          <circle cx="8" cy="8" r="6.25" fill="none" stroke="currentColor" strokeWidth="1.4" />
          <circle cx="8" cy="5.2" r="0.9" fill="currentColor" />
          <path d="M8 7.2v4.2" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
        </svg>
        <span className="drive-tooltip" role="tooltip">
          <span className="drive-tooltip-inner">
            {details.length === 0 ? (
              <span className="muted-line">No extra drive details</span>
            ) : (
              details.map((row) => (
                <span className="drive-tooltip-row" key={row.label}>
                  <span className="drive-tooltip-label">{row.label}</span>
                  <span className="drive-tooltip-value mono">{row.value}</span>
                </span>
              ))
            )}
          </span>
        </span>
      </button>
    </span>
  )
}
