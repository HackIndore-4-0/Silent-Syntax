import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api'
import { Badge, EmptyState, ErrorState, LoadingState, Panel } from '../components/ui'
import { fmtDate, fmtMs, shortId } from '../format'

const STATUSES = ['continue', 'stop', 'failed', 'human', 'rolled_back', 'retry', 'replan']

export default function RunsList() {
  const navigate = useNavigate()
  const [filters, setFilters] = useState({ q: '', agent_name: '', status: '' })
  const [state, setState] = useState({ loading: true, error: null, data: null })

  useEffect(() => { load(filters) }, []) // eslint-disable-line react-hooks/exhaustive-deps

  async function load(f) {
    setState({ loading: true, error: null, data: null })
    const params = new URLSearchParams()
    if (f.q) params.set('q', f.q)
    if (f.agent_name) params.set('agent_name', f.agent_name)
    if (f.status) params.set('status', f.status)
    try {
      const data = await api(`/api/v2/runs?${params.toString()}`)
      setState({ loading: false, error: null, data })
    } catch (error) {
      setState({ loading: false, error, data: null })
    }
  }

  function apply() {
    load(filters)
  }

  if (state.loading && !state.data) return (<><h1 className="page-title">Runs / Traces</h1><LoadingState /></>)
  if (state.error) return <ErrorState error={state.error} />
  const data = state.data

  return (
    <>
      <h1 className="page-title">Runs / Traces</h1>
      <div className="page-subtitle">{data.total} run(s)</div>
      <div className="filters-row">
        <input type="search" placeholder="Search task or run ID" value={filters.q} onChange={(e) => setFilters({ ...filters, q: e.target.value })} />
        <input type="text" placeholder="Agent" value={filters.agent_name} onChange={(e) => setFilters({ ...filters, agent_name: e.target.value })} />
        <select value={filters.status} onChange={(e) => setFilters({ ...filters, status: e.target.value })}>
          <option value="">All statuses</option>
          {STATUSES.map((s) => <option value={s} key={s}>{s}</option>)}
        </select>
        <button onClick={apply}>Apply</button>
      </div>
      <Panel>
        <table>
          <thead><tr><th>Run ID</th><th>Agent</th><th>Task</th><th>Started</th><th>Duration</th><th>Tokens</th><th>Status</th><th>Risk</th><th>Reliability</th></tr></thead>
          <tbody>
            {data.runs.length ? data.runs.map((r) => (
              <tr className="clickable" key={r.id} onClick={() => navigate(`/runs/${r.id}`)}>
                <td className="mono">{shortId(r.id)}</td><td>{r.agent_name}</td><td className="wrap">{r.task || '—'}</td>
                <td>{fmtDate(r.started_at)}</td><td>{fmtMs(r.duration_ms)}</td>
                <td>{(r.tokens_input || r.tokens_output) ? (r.tokens_input || 0) + (r.tokens_output || 0) : 'N/A'}</td>
                <td><Badge status={r.status} /></td><td>—</td><td>—</td>
              </tr>
            )) : <tr><td colSpan={9}><EmptyState /></td></tr>}
          </tbody>
        </table>
      </Panel>
    </>
  )
}
