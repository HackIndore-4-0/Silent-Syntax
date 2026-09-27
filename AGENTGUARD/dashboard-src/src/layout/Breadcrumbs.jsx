import { Link, useMatches } from 'react-router-dom'

export function Breadcrumbs() {
  const matches = useMatches()
  const match = [...matches].reverse().find((m) => m.handle?.crumbs)
  const crumbs = match ? match.handle.crumbs(match.params) : []
  return (
    <div id="breadcrumbs">
      {crumbs.map((c, i) => (
        <span key={i}>
          {i > 0 && <span className="crumb-sep">/</span>}
          {c.to ? (
            <Link to={c.to} style={{ color: 'inherit', textDecoration: 'none' }}>{c.label}</Link>
          ) : (
            <span className="crumb-current">{c.label}</span>
          )}
        </span>
      ))}
    </div>
  )
}
