import { useNavigate } from 'react-router-dom'
import { StatusBadge, Badge, EmptyState, ErrorState, KpiRow, KvRow, LoadingState, Panel, useApiData } from '../components/ui'
import { LineChart } from '../components/charts'
import { fmtDate, fmtMs, fmtPct, shortId } from '../format'

function BreakdownTable({ rows }) {
  if (!rows || !rows.length) return <EmptyState />
  return (
    <table>
      <thead><tr><th>Key</th><th>P50</th><th>P95</th><th>Avg</th><th>N</th></tr></thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.key}><td>{shortId(r.key)}</td><td>{fmtMs(r.p50)}</td><td>{fmtMs(r.p95)}</td><td>{fmtMs(r.avg)}</td><td>{r.count}</td></tr>
        ))}
      </tbody>
    </table>
  )
}

export function Latency() {
  const { loading, error, data } = useApiData('/api/v2/latency')
  if (loading) return <LoadingState />
  if (error) return <ErrorState error={error} />
  return (
    <>
      <h1 className="page-title">Latency</h1>
      <div className="page-subtitle">Based on {data.overall.sample_count} run(s) with recorded duration</div>
      <KpiRow items={[
        { label: 'P50', value: fmtMs(data.overall.p50) },
        { label: 'P95', value: fmtMs(data.overall.p95) },
        { label: 'P99', value: fmtMs(data.overall.p99) },
        { label: 'Average', value: fmtMs(data.overall.avg) },
      ]} />
      <div className="grid-3">
        <Panel header="By Agent"><BreakdownTable rows={data.by_agent} /></Panel>
        <Panel header="By Project"><BreakdownTable rows={data.by_project} /></Panel>
        <Panel header="By Model"><BreakdownTable rows={data.by_model} /></Panel>
      </div>
    </>
  )
}

function TokenGroupTable({ rows }) {
  if (!rows || !rows.length) return <EmptyState />
  return (
    <table>
      <thead><tr><th>Key</th><th>Tokens</th></tr></thead>
      <tbody>{rows.map((r) => <tr key={r.key}><td>{shortId(r.key)}</td><td>{r.tokens}</td></tr>)}</tbody>
    </table>
  )
}

export function Tokens() {
  const { loading, error, data } = useApiData('/api/v2/tokens')
  if (loading) return <LoadingState />
  if (error) return <ErrorState error={error} />
  if (!data.available) {
    return (<><h1 className="page-title">Tokens & Cost</h1><EmptyState>{data.detail}<br />No data available for this time range.</EmptyState></>)
  }
  return (
    <>
      <h1 className="page-title">Tokens & Cost</h1>
      <KpiRow items={[
        { label: 'Input Tokens', value: data.input_tokens },
        { label: 'Output Tokens', value: data.output_tokens },
        { label: 'Total Tokens', value: data.total_tokens },
        { label: 'Estimated Cost', value: data.estimated_cost_usd !== null ? `$${data.estimated_cost_usd.toFixed(4)}` : 'N/A', na: data.estimated_cost_usd === null },
      ]} />
      <Panel header="Tokens Over Time" style={{ marginBottom: 14 }}><LineChart points={data.over_time} dataKey="tokens" /></Panel>
      <div className="grid-3">
        <Panel header="By Agent"><TokenGroupTable rows={data.by_agent} /></Panel>
        <Panel header="By Model"><TokenGroupTable rows={data.by_model} /></Panel>
        <Panel header="By Project"><TokenGroupTable rows={data.by_project} /></Panel>
      </div>
    </>
  )
}

export function Errors() {
  const navigate = useNavigate()
  const { loading, error, data } = useApiData('/api/v2/runs?limit=500')
  if (loading) return <LoadingState />
  if (error) return <ErrorState error={error} />
  const errors = data.runs.filter((r) => r.status === 'failed' || r.status === 'stop')
  return (
    <>
      <h1 className="page-title">Errors</h1>
      <div className="page-subtitle">{errors.length} run(s) that stopped or failed</div>
      <Panel>
        <table>
          <thead><tr><th>Run ID</th><th>Agent</th><th>Status</th><th>Exception</th><th>Started</th></tr></thead>
          <tbody>
            {errors.length ? errors.map((r) => (
              <tr className="clickable" key={r.id} onClick={() => navigate(`/runs/${r.id}`)}>
                <td className="mono">{shortId(r.id)}</td><td>{r.agent_name}</td><td><Badge status={r.status} /></td>
                <td className="wrap">{r.exception_type || '—'}</td><td>{fmtDate(r.started_at)}</td>
              </tr>
            )) : <tr><td colSpan={5}><EmptyState /></td></tr>}
          </tbody>
        </table>
      </Panel>
    </>
  )
}

export function Tools() {
  const { loading, error, data } = useApiData('/api/v2/tools')
  if (loading) return <LoadingState />
  if (error) return <ErrorState error={error} />
  return (
    <>
      <h1 className="page-title">Tools</h1>
      <Panel>
        <table>
          <thead><tr><th>Tool</th><th>Samples</th><th>Success Rate</th><th>Failures</th><th>Avg Latency</th><th>P95 Latency</th><th>Reliability</th></tr></thead>
          <tbody>
            {data.tools.length ? data.tools.map((t) => (
              <tr key={t.tool}>
                <td>{t.tool}</td><td>{t.sample_count}</td><td>{fmtPct(t.success_rate)}</td><td>{fmtPct(t.failure_rate)}</td>
                <td>{fmtMs(t.latency_mean_ms)}</td><td>{fmtMs(t.latency_p95_ms)}</td><td><Badge status={t.reliability.toLowerCase()} /></td>
              </tr>
            )) : <tr><td colSpan={7}><EmptyState /></td></tr>}
          </tbody>
        </table>
      </Panel>
      <Panel header="Registered Alternatives" style={{ marginTop: 14 }}>
        {data.alternatives.length ? data.alternatives.map((a, i) => (
          <KvRow label={`${a.primary} → ${a.fallback}`} key={i}>threshold {a.reliability_threshold}</KvRow>
        )) : <EmptyState>No fallback tools registered.</EmptyState>}
      </Panel>
    </>
  )
}

export function Models() {
  const { loading, error, data } = useApiData('/api/v2/models')
  if (loading) return <LoadingState />
  if (error) return <ErrorState error={error} />
  return (
    <>
      <h1 className="page-title">Models</h1>
      <Panel>
        <table>
          <thead><tr><th>Model</th><th>Samples</th><th>Success Rate</th><th>Avg Latency</th><th>P95 Latency</th><th>Avg Tokens In/Out</th><th>Avg Cost</th><th>Reliability</th></tr></thead>
          <tbody>
            {data.models.length ? data.models.map((m) => (
              <tr key={m.model}>
                <td>{m.model}</td><td>{m.sample_count}</td><td>{fmtPct(m.success_rate)}</td>
                <td>{fmtMs(m.latency_mean_ms)}</td><td>{fmtMs(m.latency_p95_ms)}</td>
                <td>{m.avg_tokens_input != null ? m.avg_tokens_input.toFixed(0) : 'N/A'} / {m.avg_tokens_output != null ? m.avg_tokens_output.toFixed(0) : 'N/A'}</td>
                <td>{m.avg_cost_usd != null ? `$${m.avg_cost_usd.toFixed(4)}` : 'N/A'}</td>
                <td><StatusBadge label={m.reliability} cssClass={m.reliability.toLowerCase()} /></td>
              </tr>
            )) : <tr><td colSpan={8}><EmptyState>No LLM calls recorded yet.</EmptyState></td></tr>}
          </tbody>
        </table>
      </Panel>
      <Panel header="Registered Fallbacks" style={{ marginTop: 14 }}>
        {data.alternatives.length ? data.alternatives.map((a, i) => (
          <KvRow label={`${a.primary_model} → ${a.fallback_model}`} key={i}>threshold {a.reliability_threshold}</KvRow>
        )) : <EmptyState>No fallback models registered.</EmptyState>}
      </Panel>
    </>
  )
}
