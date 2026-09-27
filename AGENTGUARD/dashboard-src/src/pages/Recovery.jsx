import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api'
import { Badge, EmptyState, ErrorState, KpiRow, LoadingState, Panel, useApiData } from '../components/ui'
import { fmtDate, fmtNum, shortId } from '../format'

export function Interventions() {
  const navigate = useNavigate()
  const { loading, error, data } = useApiData('/api/v2/recovery')
  if (loading) return <LoadingState />
  if (error) return <ErrorState error={error} />
  const human = data.attempts.filter((a) => a.outcomes.includes('human'))
  return (
    <>
      <h1 className="page-title">Interventions</h1>
      <div className="page-subtitle">{data.human_interventions} human intervention(s) across {data.recovery_attempts} recovery attempt(s)</div>
      <Panel>
        <table>
          <thead><tr><th>Run ID</th><th>Agent</th><th>Outcomes</th><th>Status</th><th>Started</th></tr></thead>
          <tbody>
            {human.length ? human.map((a) => (
              <tr className="clickable" key={a.run_id} onClick={() => navigate(`/runs/${a.run_id}`)}>
                <td className="mono">{shortId(a.run_id)}</td><td>{a.agent_name}</td><td>{a.outcomes.join(', ')}</td>
                <td><Badge status={a.status} /></td><td>{fmtDate(a.started_at)}</td>
              </tr>
            )) : <tr><td colSpan={5}><EmptyState>No human interventions recorded.</EmptyState></td></tr>}
          </tbody>
        </table>
      </Panel>
    </>
  )
}

export function Checkpoints() {
  const navigate = useNavigate()
  const { loading, error, data } = useApiData('/api/v2/recovery')
  if (loading) return <LoadingState />
  if (error) return <ErrorState error={error} />
  return (
    <>
      <h1 className="page-title">Checkpoints / Rollback</h1>
      <KpiRow items={[
        { label: 'Rollbacks', value: data.rollbacks },
        { label: 'Successful Recoveries', value: data.successful_recoveries },
        { label: 'Retries', value: data.retries },
        { label: 'Replans', value: data.replans },
      ]} />
      <Panel>
        <table>
          <thead><tr><th>Run ID</th><th>Agent</th><th>Root Cause</th><th>Recovered</th><th>Status</th></tr></thead>
          <tbody>
            {data.attempts.length ? data.attempts.map((a) => (
              <tr className="clickable" key={a.run_id} onClick={() => navigate(`/runs/${a.run_id}`)}>
                <td className="mono">{shortId(a.run_id)}</td><td>{a.agent_name}</td>
                <td className="wrap">{a.root_cause || '—'}</td><td>{a.recovered ? 'Yes' : 'No'}</td><td><Badge status={a.status} /></td>
              </tr>
            )) : <tr><td colSpan={5}><EmptyState>No recovery attempts recorded.</EmptyState></td></tr>}
          </tbody>
        </table>
      </Panel>
    </>
  )
}

export function ReplayCompare() {
  const [replayId, setReplayId] = useState('')
  const [replayResult, setReplayResult] = useState(null)
  const [replayError, setReplayError] = useState(null)
  const [a, setA] = useState('')
  const [b, setB] = useState('')
  const [compareResult, setCompareResult] = useState(null)
  const [compareError, setCompareError] = useState(null)

  async function doReplay() {
    if (!replayId.trim()) return
    setReplayResult(null); setReplayError(null)
    try {
      const session = await api(`/api/v2/runs/${replayId.trim()}/replay`)
      setReplayResult(session.steps)
    } catch (e) { setReplayError(e.message) }
  }

  async function doCompare() {
    if (!a.trim() || !b.trim()) return
    setCompareResult(null); setCompareError(null)
    try {
      const result = await api(`/api/v2/runs/compare?run_a=${encodeURIComponent(a.trim())}&run_b=${encodeURIComponent(b.trim())}`)
      setCompareResult(result.metrics)
    } catch (e) { setCompareError(e.message) }
  }

  return (
    <>
      <h1 className="page-title">Replay / Compare</h1>
      <div className="grid-2">
        <Panel header="Replay a run">
          <div className="filters-row">
            <input type="text" placeholder="Run ID" value={replayId} onChange={(e) => setReplayId(e.target.value)} />
            <button className="primary" onClick={doReplay}>Replay</button>
          </div>
          {replayError && <ErrorState error={{ message: replayError }} />}
          {replayResult && (
            <>
              <div className="kv-row"><span className="k">Mode</span><span>REPLAY — SAFE / DRY-RUN, no external side effects</span></div>
              {replayResult.map((s, i) => (
                <div className="kv-row" key={i}><span className="k">Step {s.step} [{s.kind}]</span><span>{s.label}</span></div>
              ))}
            </>
          )}
        </Panel>
        <Panel header="Compare two runs">
          <div className="filters-row">
            <input type="text" placeholder="Run A ID" value={a} onChange={(e) => setA(e.target.value)} />
            <input type="text" placeholder="Run B ID" value={b} onChange={(e) => setB(e.target.value)} />
            <button className="primary" onClick={doCompare}>Compare</button>
          </div>
          {compareError && <ErrorState error={{ message: compareError }} />}
          {compareResult && (
            <table>
              <thead><tr><th>Metric</th><th>Run A</th><th>Run B</th><th>Delta</th></tr></thead>
              <tbody>
                {compareResult.map((m, i) => (
                  <tr key={i}><td>{m.metric}</td><td>{m.value_a ?? 'N/A'}</td><td>{m.value_b ?? 'N/A'}</td><td>{m.available ? fmtNum(m.delta, 3) : 'n/a'}</td></tr>
                ))}
              </tbody>
            </table>
          )}
        </Panel>
      </div>
    </>
  )
}
