import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import { useAuth } from '../auth/AuthContext'
import { Badge, EmptyState, ErrorState, LoadingState, Panel, useApiData } from '../components/ui'
import { shortId } from '../format'

export function FailurePatterns() {
  const { loading, error, data } = useApiData('/api/v2/failure-patterns')
  if (loading) return <LoadingState />
  if (error) return <ErrorState error={error} />
  return (
    <>
      <h1 className="page-title">Failure Patterns</h1>
      <Panel>
        <table>
          <thead><tr><th>Pattern</th><th>Earliest Deviation</th><th>Occurrences</th><th>Runs</th></tr></thead>
          <tbody>
            {data.length ? data.map((p, i) => (
              <tr key={i}>
                <td className="wrap">{p.explanation}</td><td>{p.earliest_deviation}</td><td>{p.count}</td>
                <td>
                  {p.run_ids.slice(0, 3).map((id, j) => (
                    <span key={id}>{j > 0 && ', '}<Link to={`/runs/${id}`} className="mono">{shortId(id)}</Link></span>
                  ))}
                  {p.run_ids.length > 3 && ` +${p.run_ids.length - 3}`}
                </td>
              </tr>
            )) : <tr><td colSpan={4}><EmptyState>No failure patterns recorded.</EmptyState></td></tr>}
          </tbody>
        </table>
      </Panel>
    </>
  )
}

export function Recommendations() {
  const { user } = useAuth()
  const [refreshKey, setRefreshKey] = useState(0)
  const { loading, error, data: candidates } = useApiData('/api/v2/improvements', [refreshKey])

  async function decide(id, action) {
    await api(`/api/improvements/${id}/${action}`, { method: 'POST', body: JSON.stringify({ approved_by: user.email }) })
    setRefreshKey((k) => k + 1)
  }

  if (loading) return (<><h1 className="page-title">Recommendations</h1><LoadingState /></>)
  if (error) return <ErrorState error={error} />

  return (
    <>
      <h1 className="page-title">Recommendations</h1>
      {candidates.length ? candidates.map((c) => (
        <Panel header={<>{c.recommendation} <Badge status={c.status} /></>} key={c.id} style={{ marginBottom: 12 }}>
          <div className="kv-row"><span className="k">Problem</span><span>{c.problem}</span></div>
          <div className="kv-row"><span className="k">Root cause</span><span>{c.root_cause_summary}</span></div>
          <div className="kv-row"><span className="k">Before</span><span>{c.candidate_change.before || '—'}</span></div>
          <div className="kv-row"><span className="k">After</span><span>{c.candidate_change.after || '—'}</span></div>
          <div className="kv-row"><span className="k">Optimizer</span><span>{c.optimizer}{c.real_dspy_optimizer ? ' (real DSPy)' : ' (deterministic stand-in)'}</span></div>
          {c.status === 'proposed' && (
            <div className="filters-row" style={{ marginTop: 8 }}>
              <button className="primary" onClick={() => decide(c.id, 'approve')}>Approve</button>
              <button className="danger" onClick={() => decide(c.id, 'reject')}>Reject</button>
            </div>
          )}
        </Panel>
      )) : <EmptyState>No recommendations proposed yet.</EmptyState>}
    </>
  )
}

export function Problems() {
  const { user } = useAuth()
  const [refreshKey, setRefreshKey] = useState(0)
  const { loading, error, data: problems } = useApiData('/api/v2/problems', [refreshKey])

  async function setStatus(clusterKey, status) {
    if (!status) return
    await api(`/api/v2/problems/${clusterKey}/status`, { method: 'POST', body: JSON.stringify({ status, triaged_by: user.email }) })
    setRefreshKey((k) => k + 1)
  }

  async function proposeFix(clusterKey) {
    await api(`/api/v2/problems/${clusterKey}/propose-fix`, { method: 'POST', body: JSON.stringify({ triaged_by: user.email }) })
    setRefreshKey((k) => k + 1)
  }

  if (loading) return (<><h1 className="page-title">Problems</h1><LoadingState /></>)
  if (error) return <ErrorState error={error} />

  return (
    <>
      <h1 className="page-title">Problems</h1>
      <Panel>
        <table>
          <thead><tr><th>Problem</th><th>Signal</th><th>Occurrences</th><th>Confidence</th><th>Priority</th><th>Status</th><th>Runs</th><th></th></tr></thead>
          <tbody>
            {problems.length ? problems.map((p) => (
              <tr key={p.cluster_key}>
                <td className="wrap">{p.explanation}</td><td>{p.signal}</td><td>{p.count}</td>
                <td>{p.confidence.toFixed(2)}</td><td>{p.priority_score.toFixed(2)}</td><td><Badge status={p.status} /></td>
                <td>
                  {p.run_ids.slice(0, 3).map((id, j) => (
                    <span key={id}>{j > 0 && ', '}<Link to={`/runs/${id}`} className="mono">{shortId(id)}</Link></span>
                  ))}
                  {p.run_ids.length > 3 && ` +${p.run_ids.length - 3}`}
                </td>
                <td>
                  {p.status !== 'fix_proposed'
                    ? <button className="primary" onClick={() => proposeFix(p.cluster_key)}>Propose Fix</button>
                    : <Link to="/recommendations">View fix</Link>}
                  {' '}
                  <select onChange={(e) => setStatus(p.cluster_key, e.target.value)} defaultValue="">
                    <option value="">Set status…</option>
                    <option value="acknowledged">Acknowledge</option>
                    <option value="resolved">Resolve</option>
                    <option value="ignored">Ignore</option>
                  </select>
                </td>
              </tr>
            )) : <tr><td colSpan={8}><EmptyState>No recurring problems detected.</EmptyState></td></tr>}
          </tbody>
        </table>
      </Panel>
    </>
  )
}
