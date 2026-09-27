import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import gsap from 'gsap'
import { api } from '../api'
import { staggerInUp } from '../animations'

export function Badge({ status }) {
  return <span className={`badge ${status}`}>{status}</span>
}

// Unlike Badge, the CSS class and the visible label are independent —
// use this whenever a status's display text differs from the class
// that colors it (e.g. golden_status "VERIFIED" -> class "reliable").
export function StatusBadge({ label, cssClass }) {
  return <span className={`badge ${cssClass}`}>{label}</span>
}

export function Panel({ header, children, style }) {
  return (
    <div className="panel" style={style}>
      {header && <div className="panel-header">{header}</div>}
      <div className="panel-body">{children}</div>
    </div>
  )
}

export function KvRow({ label, children }) {
  return (
    <div className="kv-row">
      <span className="k">{label}</span>
      <span>{children}</span>
    </div>
  )
}

export function PageTitle({ children, subtitle }) {
  return (
    <>
      <h1 className="page-title">{children}</h1>
      {subtitle && <div className="page-subtitle">{subtitle}</div>}
    </>
  )
}

export function LoadingState() {
  return <div className="loading-state">Loading…</div>
}

export function ErrorState({ error }) {
  return <div className="error-state">{error?.message || String(error)}</div>
}

export function EmptyState({ title, children = 'No data available for this time range.' }) {
  if (title) {
    return (
      <div className="empty-state">
        <div className="empty-state-title">{title}</div>
        <div className="empty-state-sub">{children}</div>
      </div>
    )
  }
  return <div className="empty-state">{children}</div>
}

function KpiCard({ label, value, na, highlight }) {
  const valueRef = useRef(null)
  const prevValue = useRef(value)
  const isFirst = useRef(true)

  useEffect(() => {
    if (isFirst.current) {
      isFirst.current = false
      prevValue.current = value
      return
    }
    if (prevValue.current !== value && valueRef.current) {
      gsap.fromTo(
        valueRef.current,
        { opacity: 0.35, y: 3 },
        { opacity: 1, y: 0, duration: 0.3, ease: 'power2.out' }
      )
    }
    prevValue.current = value
  }, [value])

  return (
    <div className={`kpi-card${highlight ? ' highlight' : ''}`}>
      <div className="kpi-label">{label}</div>
      <div ref={valueRef} className={`kpi-value ${na ? 'na' : ''}`}>{value}</div>
    </div>
  )
}

export function KpiRow({ items }) {
  const rowRef = useRef(null)

  useEffect(() => {
    if (!rowRef.current) return
    const cards = rowRef.current.querySelectorAll('.kpi-card')
    const tween = staggerInUp(cards)
    return () => tween.kill()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return (
    <div className="kpi-row" ref={rowRef}>
      {items.map((item) => <KpiCard key={item.label} {...item} />)}
    </div>
  )
}

export function RunLink({ id, children }) {
  return <Link to={`/runs/${id}`} className="mono">{children}</Link>
}

// Fetches `path` on mount (and whenever `deps` change) and renders
// loading/error/children(data) states — the shared shape every page's
// `try { data = await api(...) } catch` block used to hand-roll.
export function useApiData(path, deps = []) {
  const [state, setState] = useState({ loading: true, error: null, data: null })

  useEffect(() => {
    let cancelled = false
    setState({ loading: true, error: null, data: null })
    api(path).then(
      (data) => { if (!cancelled) setState({ loading: false, error: null, data }) },
      (error) => { if (!cancelled) setState({ loading: false, error, data: null }) }
    )
    return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)

  return state
}
