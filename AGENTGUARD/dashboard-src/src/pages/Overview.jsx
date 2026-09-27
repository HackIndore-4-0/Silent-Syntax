import { useNavigate } from 'react-router-dom'
import { Badge, EmptyState, ErrorState, KpiRow, KvRow, LoadingState, Panel, useApiData } from '../components/ui'
import { BarChart } from '../components/charts'
import { fmtDate, fmtMs, fmtPct, shortId } from '../format'

export default function Overview() {
  const navigate = useNavigate()
  const { loading, error, data } = useApiData('/api/v2/overview')
  if (loading) return <LoadingState />
  if (error) return <ErrorState error={error} />

  const k = data.kpis
  return (
    <>
      <h1 className="page-title">Overview</h1>
      <div className="page-subtitle">Workspace reliability summary — last 7 days</div>
      <KpiRow items={[
        { label: 'Total Runs', value: k.total_runs },
        { label: 'Success Rate', value: fmtPct(k.success_rate), na: k.success_rate === null },
        { label: 'Avg Latency', value: fmtMs(k.avg_latency_ms), na: k.avg_latency_ms === null },
        { label: 'Total Tokens', value: k.total_tokens === null ? 'N/A' : k.total_tokens, na: k.total_tokens === null },
        { label: 'Estimated Cost', value: k.estimated_cost_usd === null ? 'N/A' : `$${k.estimated_cost_usd.toFixed(4)}`, na: k.estimated_cost_usd === null },
        { label: 'Reliability Score', value: fmtPct(k.reliability_score), na: k.reliability_score === null },
      ]} />
      <div className="grid-2">
        <Panel header="Run Volume"><BarChart data={data.charts.run_volume} dataKey="count" labelKey="date" /></Panel>
        <Panel header="Latency Percentiles">
          <KvRow label="P50">{fmtMs(data.charts.latency_percentiles.p50)}</KvRow>
          <KvRow label="P95">{fmtMs(data.charts.latency_percentiles.p95)}</KvRow>
          <KvRow label="P99">{fmtMs(data.charts.latency_percentiles.p99)}</KvRow>
        </Panel>
      </div>
      <div className="grid-2" style={{ marginTop: 14 }}>
        <Panel header="Success / Failure">
          <BarChart data={[
            { key: 'Success', count: data.charts.success_failure.success },
            { key: 'Failure', count: data.charts.success_failure.failure },
          ]} dataKey="count" labelKey="key" />
        </Panel>
        <Panel header="Evaluation Summary">
          {Object.entries(data.evaluation_summary).map(([key, v]) => (
            <KvRow label={key.replace(/_/g, ' ')} key={key}>{fmtPct(v)}</KvRow>
          ))}
        </Panel>
      </div>
      <Panel header="Recent Runs" style={{ marginTop: 14 }}>
        <table>
          <thead><tr><th>Run ID</th><th>Agent</th><th>Project</th><th>Status</th><th>Duration</th><th>Tokens</th><th>Started</th></tr></thead>
          <tbody>
            {data.recent_runs.length ? data.recent_runs.map((r) => (
              <tr className="clickable" key={r.id} onClick={() => navigate(`/runs/${r.id}`)}>
                <td className="mono">{shortId(r.id)}</td><td>{r.agent_name}</td><td>{shortId(r.project_id)}</td>
                <td><Badge status={r.status} /></td><td>{fmtMs(r.duration_ms)}</td><td>{r.tokens ?? 'N/A'}</td><td>{fmtDate(r.started_at)}</td>
              </tr>
            )) : <tr><td colSpan={7}><EmptyState /></td></tr>}
          </tbody>
        </table>
      </Panel>
    </>
  )
}
