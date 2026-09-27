export function fmtPct(x) {
  return x === null || x === undefined ? 'N/A' : `${Math.round(x * 100)}%`
}
export function fmtNum(x, digits = 0) {
  return x === null || x === undefined ? 'N/A' : Number(x).toFixed(digits)
}
export function fmtMs(x) {
  return x === null || x === undefined ? 'N/A' : `${Number(x).toFixed(1)} ms`
}
export function fmtDate(x) {
  return x ? new Date(x).toLocaleString() : '—'
}
export function fmtCost(x) {
  return x === null || x === undefined ? 'N/A' : `$${Number(x).toFixed(4)}`
}
export function shortId(id) {
  return id ? String(id).slice(0, 8) : '—'
}
