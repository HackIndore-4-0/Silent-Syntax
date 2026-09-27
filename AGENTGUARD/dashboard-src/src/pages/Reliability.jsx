import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api'
import { Badge, EmptyState, ErrorState, KvRow, LoadingState, Panel, useApiData } from '../components/ui'
import { BarChart, LineChart } from '../components/charts'
import { fmtNum, fmtPct, shortId } from '../format'

const EVAL_LABELS = {
  goal_completion: 'Goal Completion', constraint_adherence: 'Constraint Adherence', tool_usage: 'Tool Reliability',
  decision_consistency: 'Decision Consistency', correctness: 'Policy / Safety', behavioral_reliability: 'Progress Evaluation',
}

export function Evaluations() {
  const navigate = useNavigate()
  const { loading, error, data } = useApiData('/api/v2/evaluations')
  if (loading) return <LoadingState />
  if (error) return <ErrorState error={error} />
  return (
    <>
      <h1 className="page-title">Evaluations</h1>
      <div className="grid-2">
        {Object.entries(EVAL_LABELS).map(([key, label]) => {
          const d = data[key]
          return (
            <Panel header={label} key={key}>
              <KvRow label="Current score">{d.current_score !== null ? fmtPct(d.current_score) : 'N/A'}</KvRow>
              <KvRow label="Pass / Fail">{d.pass_count} / {d.fail_count}</KvRow>
              <LineChart points={d.trend} dataKey="value" />
              <h4 style={{ margin: '10px 0 4px', fontSize: 11.5, color: 'var(--ink-soft)' }}>Recent failures</h4>
              {d.recent_failures.length ? d.recent_failures.map((f, i) => (
                <div className="kv-row" style={{ cursor: 'pointer' }} key={i} onClick={() => navigate(`/runs/${f.run_id}`)}>
                  <span className="k mono">{shortId(f.run_id)}</span><span>{fmtPct(f.value)}</span>
                </div>
              )) : <EmptyState>No failures recorded.</EmptyState>}
            </Panel>
          )
        })}
      </div>
    </>
  )
}

export function Risk() {
  const navigate = useNavigate()
  const { loading, error, data } = useApiData('/api/v2/risk')
  if (loading) return <LoadingState />
  if (error) return <ErrorState error={error} />
  return (
    <>
      <h1 className="page-title">Risk & Confidence</h1>
      <div className="grid-2">
        <Panel header="Risk Distribution"><BarChart data={data.risk_distribution} dataKey="count" labelKey="range" /></Panel>
        <Panel header="Confidence Distribution"><BarChart data={data.confidence_distribution} dataKey="count" labelKey="range" /></Panel>
      </div>
      <Panel header="Risk Over Time" style={{ margin: '14px 0' }}><LineChart points={data.risk_over_time} dataKey="risk_score" /></Panel>
      <div className="grid-2">
        <Panel header="High-Risk Runs">
          {data.high_risk_runs.length ? data.high_risk_runs.map((r, i) => (
            <div className="kv-row" style={{ cursor: 'pointer' }} key={i} onClick={() => navigate(`/runs/${r.run_id}`)}>
              <span className="k mono">{shortId(r.run_id)}</span><span>{fmtNum(r.risk_score, 2)} · <Badge status={r.status} /></span>
            </div>
          )) : <EmptyState>No high-risk runs in this window.</EmptyState>}
        </Panel>
        <Panel header="Low-Confidence Decisions">
          {data.low_confidence_decisions.length ? data.low_confidence_decisions.map((d, i) => (
            <div className="kv-row" style={{ cursor: 'pointer' }} key={i} onClick={() => navigate(`/runs/${d.run_id}`)}>
              <span className="k mono">{shortId(d.run_id)}</span><span><Badge status={d.outcome} /> · {fmtPct(d.confidence)}</span>
            </div>
          )) : <EmptyState>No low-confidence decisions recorded.</EmptyState>}
        </Panel>
      </div>
    </>
  )
}

export function Behavior() {
  const { loading, error, data: agents } = useApiData('/api/v2/agents')
  const [name, setName] = useState('')
  const [fp, setFp] = useState(null)

  async function load(agentName) {
    setName(agentName)
    if (!agentName) { setFp(null); return }
    setFp(await api(`/api/v2/agents/${encodeURIComponent(agentName)}/fingerprint`))
  }

  if (loading) return <LoadingState />
  if (error) return <ErrorState error={error} />

  return (
    <>
      <h1 className="page-title">Agent Behavior</h1>
      <div className="filters-row">
        <select value={name} onChange={(e) => load(e.target.value)}>
          <option value="">{'— select an agent —'}</option>
          {agents.map((a) => <option value={a.name} key={a.name}>{a.name}</option>)}
        </select>
      </div>
      {!name && <EmptyState>Select an agent to view its behavior fingerprint.</EmptyState>}
      {name && !fp && <LoadingState />}
      {fp && !fp.baseline && (
        <EmptyState>sample_count={fp.sample_count} (needs ≥ {fp.min_sample_size} prior runs for a meaningful baseline).</EmptyState>
      )}
      {fp && fp.baseline && (
        <Panel>
          <KvRow label="Sample count">{fp.sample_count}</KvRow>
          {Object.keys(fp.baseline).map((k) => {
            const cur = fp.current ? fp.current[k] : undefined
            const dev = fp.deviation ? fp.deviation[k] : undefined
            return (
              <KvRow label={k.replace(/_/g, ' ')} key={k}>
                baseline {fmtNum(fp.baseline[k], 2)}
                {cur !== undefined && ` · current ${fmtNum(cur, 2)}`}
                {dev !== undefined && dev !== null && ` · deviation ${(dev * 100).toFixed(0)}%`}
              </KvRow>
            )
          })}
          <KvRow label="Anomaly status">
            {fp.anomaly ? <span style={{ color: 'var(--danger)', fontWeight: 700 }}>ANOMALY DETECTED</span> : 'Normal'}
          </KvRow>
        </Panel>
      )}
    </>
  )
}
