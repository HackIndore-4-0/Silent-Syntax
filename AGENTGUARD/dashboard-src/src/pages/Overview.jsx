import { useEffect, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import gsap from 'gsap'
import { Badge, EmptyState, ErrorState, KpiRow, KvRow, LoadingState, Panel, useApiData } from '../components/ui'
import { BarChart } from '../components/charts'
import { fmtDate, fmtMs, fmtPct, shortId } from '../format'
import { fadeInUp, staggerInUp } from '../animations'

export default function Overview() {
  const navigate = useNavigate()
  const { loading, error, data } = useApiData('/api/v2/overview')
  const rootRef = useRef(null)

  useEffect(() => {
    if (loading || error || !rootRef.current) return
    const ctx = gsap.context(() => {
      fadeInUp(rootRef.current, { duration: 0.45 })
      staggerInUp('.obs-grid, .panel', { delay: 0.08 })
    }, rootRef)
    return () => ctx.revert()
  }, [loading, error])

  if (loading) return <LoadingState />
  if (error) return <ErrorState error={error} />

  const k = data.kpis
  return (
    <div ref={rootRef}>
      <h1 className="page-title">Overview</h1>
      <div className="page-subtitle">Workspace reliability summary — last 7 days</div>
      <KpiRow items={[
        { label: 'Total Runs', value: k.total_runs },
        { label: 'Success Rate', value: fmtPct(k.success_rate), na: k.success_rate === null },
        { label: 'Avg Latency', value: fmtMs(k.avg_latency_ms), na: k.avg_latency_ms === null },
        { label: 'Total Tokens', value: k.total_tokens === null ? 'N/A' : k.total_tokens, na: k.total_tokens === null },
        { label: 'Estimated Cost', value: k.estimated_cost_usd === null ? 'N/A' : `$${k.estimated_cost_usd.toFixed(4)}`, na: k.estimated_cost_usd === null },
        { label: 'Reliability Score', value: fmtPct(k.reliability_score), na: k.reliability_score === null, highlight: true },
      ]} />
      <div className="obs-grid">
        <div className="obs-cell">
          <div className="obs-cell-header">Run Volume</div>
          <BarChart
            data={data.charts.run_volume}
            dataKey="count"
            labelKey="date"
            height={70}
            emptyTitle="No runs recorded for this period"
            emptyMessage="No run data is available for the selected time range."
          />
        </div>
        <div className="obs-cell">
          <div className="obs-cell-header">Latency Percentiles</div>
          <KvRow label="P50">{fmtMs(data.charts.latency_percentiles.p50)}</KvRow>
          <KvRow label="P95">{fmtMs(data.charts.latency_percentiles.p95)}</KvRow>
          <KvRow label="P99">{fmtMs(data.charts.latency_percentiles.p99)}</KvRow>
        </div>
        <div className="obs-cell">
          <div className="obs-cell-header">Success / Failure</div>
          <BarChart data={[
            { key: 'Success', count: data.charts.success_failure.success },
            { key: 'Failure', count: data.charts.success_failure.failure },
          ]} dataKey="count" labelKey="key" height={70} />
        </div>
        <div className="obs-cell">
          <div className="obs-cell-header">Evaluation Summary</div>
          {Object.entries(data.evaluation_summary).map(([key, v]) => (
            <KvRow label={key.replace(/_/g, ' ')} key={key}>{fmtPct(v)}</KvRow>
          ))}
        </div>
      </div>
      <Panel header="Recent Runs">
        <table>
          <thead><tr><th>Run ID</th><th>Agent</th><th>Project</th><th>Status</th><th>Duration</th><th>Tokens</th><th>Started</th></tr></thead>
          <tbody>
            {data.recent_runs.length ? data.recent_runs.map((r) => (
              <tr className="clickable" key={r.id} onClick={() => navigate(`/runs/${r.id}`)}>
                <td className="mono">{shortId(r.id)}</td><td>{r.agent_name}</td><td>{shortId(r.project_id)}</td>
                <td><Badge status={r.status} /></td><td>{fmtMs(r.duration_ms)}</td><td>{r.tokens ?? 'N/A'}</td><td>{fmtDate(r.started_at)}</td>
              </tr>
            )) : <tr><td colSpan={7}><EmptyState title="No runs yet">Runs will appear here once agents report to this workspace.</EmptyState></td></tr>}
          </tbody>
        </table>
      </Panel>
    </div>
  )
}
