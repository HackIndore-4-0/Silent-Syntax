import { useState } from 'react'
import { api } from '../api'
import { Badge, EmptyState, ErrorState, KvRow, LoadingState, Panel, useApiData } from '../components/ui'
import { fmtDate, shortId } from '../format'

export function Policies() {
  const [refreshKey, setRefreshKey] = useState(0)
  const { loading, error, data: list } = useApiData('/api/v2/policies', [refreshKey])
  const [name, setName] = useState('')
  const [maxCost, setMaxCost] = useState('')
  const [createError, setCreateError] = useState(null)
  const [detailName, setDetailName] = useState(null)
  const [versions, setVersions] = useState(null)

  async function create() {
    setCreateError(null)
    try {
      await api('/api/v2/policies', { method: 'POST', body: JSON.stringify({ name, policy: { max_cost: parseFloat(maxCost || '0') } }) })
      setName(''); setMaxCost('')
      setRefreshKey((k) => k + 1)
    } catch (e) { setCreateError(e.message) }
  }

  async function loadDetail(policyName) {
    setDetailName(policyName)
    setVersions(await api(`/api/v2/policies/${encodeURIComponent(policyName)}/versions`))
  }

  if (loading) return (<><h1 className="page-title">Policy Management</h1><LoadingState /></>)
  if (error) return <ErrorState error={error} />

  return (
    <>
      <h1 className="page-title">Policy Management</h1>
      <Panel>
        <table>
          <thead><tr><th>Name</th><th>Version</th><th>Status</th><th>Updated</th></tr></thead>
          <tbody>
            {list.length ? list.map((p) => (
              <tr className="clickable" key={p.name} onClick={() => loadDetail(p.name)}>
                <td>{p.name}</td><td>v{p.current_version}</td><td><Badge status="active" /></td><td>{fmtDate(p.updated_at)}</td>
              </tr>
            )) : <tr><td colSpan={4}><EmptyState>No policies created yet.</EmptyState></td></tr>}
          </tbody>
        </table>
      </Panel>
      <Panel header="Create Policy" style={{ marginTop: 14 }}>
        <div className="filters-row">
          <input type="text" placeholder="Policy name" value={name} onChange={(e) => setName(e.target.value)} />
          <input type="number" placeholder="max_cost" value={maxCost} onChange={(e) => setMaxCost(e.target.value)} />
          <button className="primary" onClick={create}>Create</button>
        </div>
        {createError && <ErrorState error={{ message: createError }} />}
      </Panel>
      {detailName && (
        <Panel header={`${detailName} — version history`} style={{ marginTop: 14 }}>
          {versions === null ? <LoadingState /> : versions.map((v) => (
            <KvRow label={`v${v.version}`} key={v.version}><span className="mono">{JSON.stringify(v.policy)}</span></KvRow>
          ))}
        </Panel>
      )}
    </>
  )
}

export function Audit() {
  const { loading, error, data } = useApiData('/api/v2/runs?limit=200')
  const [verifying, setVerifying] = useState(null)

  async function verify(runId) {
    setVerifying({ runId, loading: true })
    const [audit, verification] = await Promise.all([
      api(`/api/v2/runs/${runId}/audit`), api(`/api/v2/runs/${runId}/audit/verify`, { method: 'POST' }),
    ])
    setVerifying({ runId, loading: false, audit, verification })
  }

  if (loading) return (<><h1 className="page-title">Audit</h1><LoadingState /></>)
  if (error) return <ErrorState error={error} />

  return (
    <>
      <h1 className="page-title">Audit</h1>
      <div className="page-subtitle">Select a run to verify its SHA-256 hash chain</div>
      <Panel>
        <table>
          <thead><tr><th>Run ID</th><th>Agent</th><th>Status</th><th>Started</th><th></th></tr></thead>
          <tbody>
            {data.runs.length ? data.runs.map((r) => (
              <tr key={r.id}>
                <td className="mono">{shortId(r.id)}</td><td>{r.agent_name}</td><td><Badge status={r.status} /></td><td>{fmtDate(r.started_at)}</td>
                <td><button onClick={() => verify(r.id)}>Verify Integrity</button></td>
              </tr>
            )) : <tr><td colSpan={5}><EmptyState /></td></tr>}
          </tbody>
        </table>
      </Panel>
      {verifying && (
        <Panel style={{ marginTop: 12 }}>
          {verifying.loading ? <LoadingState /> : (
            <>
              <KvRow label="Run"><span className="mono">{verifying.runId}</span></KvRow>
              <KvRow label="Events verified">{verifying.audit.event_count}</KvRow>
              <KvRow label="Chain status">
                {verifying.verification.intact
                  ? <span style={{ color: 'var(--ok)' }}>CHAIN VALID</span>
                  : <span style={{ color: 'var(--danger)' }}>CHAIN INVALID</span>}
              </KvRow>
              <KvRow label="Last hash">
                <span className="mono">
                  {verifying.audit.events.length ? `${verifying.audit.events[verifying.audit.events.length - 1].event_hash.slice(0, 16)}…` : '—'}
                </span>
              </KvRow>
            </>
          )}
        </Panel>
      )}
    </>
  )
}
