import { EmptyState } from './ui'

export function BarChart({ data, dataKey = 'count', labelKey = 'key', width = 480, height = 120 }) {
  if (!data || !data.length) return <EmptyState />
  const max = Math.max(...data.map((d) => d[dataKey]), 1)
  const barW = width / data.length
  return (
    <svg className="chart-svg" viewBox={`0 0 ${width} ${height}`}>
      {data.map((d, i) => {
        const h = (d[dataKey] / max) * (height - 20)
        const x = i * barW + 2
        const y = height - h - 16
        return (
          <g key={i}>
            <rect className="hist-bar" x={x} y={y} width={barW - 4} height={h}>
              <title>{d[labelKey]}: {d[dataKey]}</title>
            </rect>
            <text x={x + (barW - 4) / 2} y={height - 4} fontSize="9" textAnchor="middle" fill="var(--ink-faint)">
              {String(d[labelKey]).slice(0, 8)}
            </text>
          </g>
        )
      })}
    </svg>
  )
}

export function LineChart({ points, dataKey = 'value', width = 480, height = 120 }) {
  if (!points || !points.length) return <EmptyState />
  if (points.length < 2) return <EmptyState>Not enough data points to draw a trend yet.</EmptyState>
  const values = points.map((p) => p[dataKey])
  const max = Math.max(...values)
  const min = Math.min(...values)
  const range = max - min || 1
  const stepX = width / (points.length - 1)
  const coords = values.map((v, i) => {
    const x = i * stepX
    const y = height - 10 - ((v - min) / range) * (height - 20)
    return `${x},${y}`
  }).join(' ')
  return (
    <svg className="chart-svg" viewBox={`0 0 ${width} ${height}`}>
      <polyline className="line-path" points={coords} />
    </svg>
  )
}
