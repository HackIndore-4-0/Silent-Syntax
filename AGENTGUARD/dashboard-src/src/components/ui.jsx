import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'

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

export function EmptyState({ children = 'No data available for this time range.' }) {
  return <div className="empty-state">{children}</div>
}

export function KpiRow({ items }) {
  return (
    <div className="kpi-row">
      {items.map(({ label, value, na }) => (
        <div className="kpi-card" key={label}>
          <div className="kpi-label">{label}</div>
          <div className={`kpi-value ${na ? 'na' : ''}`}>{value}</div>
        </div>
      ))}
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
