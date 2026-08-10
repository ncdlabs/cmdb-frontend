/** Client-side TTL cache for ephemeral live SSH probes (not interval polling). */

import { useEffect, useState } from 'react'
import type { LiveProbe } from './api'

export const LIVE_PROBE_TTL_MS = 5 * 60 * 1000

export type Reachability = 'online' | 'offline' | 'unknown'

type ProbeCacheEntry = {
  probedAt: number
  result: LiveProbe
}

const liveProbeCache = new Map<string, ProbeCacheEntry>()
const listeners = new Set<() => void>()

function emit(): void {
  for (const listener of listeners) listener()
}

export function subscribeLiveProbeCache(listener: () => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

export function getCachedLiveProbe(id: string): ProbeCacheEntry | null {
  const entry = liveProbeCache.get(id)
  if (!entry) return null
  if (Date.now() - entry.probedAt >= LIVE_PROBE_TTL_MS) {
    liveProbeCache.delete(id)
    emit()
    return null
  }
  return entry
}

export function setCachedLiveProbe(id: string, result: LiveProbe): void {
  liveProbeCache.set(id, { probedAt: Date.now(), result })
  emit()
}

export function clearCachedLiveProbe(id: string): void {
  if (!liveProbeCache.delete(id)) return
  emit()
}

export function getReachability(id: string): Reachability {
  const entry = getCachedLiveProbe(id)
  if (!entry) return 'unknown'
  return entry.result.ok ? 'online' : 'offline'
}

/** Subscribe to probe-cache updates and TTL expiry for a CI id. */
export function useReachability(id: string | undefined): Reachability {
  const [tick, setTick] = useState(0)

  useEffect(() => subscribeLiveProbeCache(() => setTick((n) => n + 1)), [])

  useEffect(() => {
    if (!id) return
    const entry = liveProbeCache.get(id)
    if (!entry) return
    const remaining = LIVE_PROBE_TTL_MS - (Date.now() - entry.probedAt)
    if (remaining <= 0) {
      getCachedLiveProbe(id)
      setTick((n) => n + 1)
      return
    }
    const timer = window.setTimeout(() => setTick((n) => n + 1), remaining + 25)
    return () => window.clearTimeout(timer)
  }, [id, tick])

  if (!id) return 'unknown'
  return getReachability(id)
}

export function isProbeableServer(item: { kind?: string; ssh?: string | null }): boolean {
  return item.kind === 'server' && Boolean(typeof item.ssh === 'string' && item.ssh.trim())
}
