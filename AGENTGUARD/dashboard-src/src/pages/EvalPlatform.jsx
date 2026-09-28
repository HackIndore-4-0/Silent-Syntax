import { Fragment, useEffect, useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { api } from '../api'
import { useAuth } from '../auth/AuthContext'
import { Badge, EmptyState, ErrorState, KvRow, LoadingState, Panel, StatusBadge, useApiData } from '../components/ui'
import { fmtDate, shortId } from '../format'

const STATUS_BADGE_CLASS = {
  VERIFIED: 'reliable', INCORRECT: 'unreliable', UNSUPPORTED: 'degraded', NEEDS_REVIEW: 'degraded',
  AMBIGUOUS: 'degraded', OUTDATED: 'degraded', unvalidated: 'insufficient_data',
}

// Model recommendations reuse the same reliable/degraded/insufficient_data
// badge palette ModelProfile.reliability already uses on the Models page,
// rather than adding new CSS for what's the same "how much can I trust
// this number" idea.
const CONFIDENCE_BADGE_CLASS = { HIGH: 'reliable', MODERATE: 'degraded', INSUFFICIENT_DATA: 'insufficient_data' }
const RECOMMENDATION_STATUS_BADGE_CLASS = { pending: 'pending', accepted: 'approved', rejected: 'rejected' }

const OBJECTIVES = [
  'cheapest_above_quality_threshold',
  'fastest_above_quality_threshold',
  'highest_quality_within_budget',
  'best_tool_calling_reliability',
  'best_long_context',
]

// A stored Recommendation row (from GET /model-recommendations) has
// `evidence_ids`; the raw POST /benchmarks/{id}/recommend response
// instead carries `evidence_count` directly — this card renders either
// shape without the caller needing to normalize first.
export function ModelRecommendationCard({ rec, onDecided }) {
  const { user } = useAuth()
  const [busy, setBusy] = useState(false)
  const recommended = rec.recommendation || { model: rec.model, objective: rec.objective, alternatives: rec.alternatives }
  const evidenceCount = rec.evidence_ids?.length ?? rec.evidence_count ?? 0

  async function decide(action) {
    setBusy(true)
    try {
      const updated = await api(`/api/v2/model-recommendations/${rec.id}/${action}`, {
        method: 'POST',
        body: JSON.stringify({ decided_by: user.email }),
      })
      onDecided?.(updated)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Panel
      header={
        <>
          {recommended.model || 'no model met the objective'}{' '}
          <StatusBadge label={rec.status} cssClass={RECOMMENDATION_STATUS_BADGE_CLASS[rec.status] || rec.status} />{' '}
          {rec.confidence && (
            <StatusBadge label={rec.confidence} cssClass={CONFIDENCE_BADGE_CLASS[rec.confidence] || 'insufficient_data'} />
          )}
        </>
      }
      style={{ marginBottom: 12 }}
    >
      <KvRow label="Objective">{recommended.objective || '—'}</KvRow>
      <KvRow label="Reasoning">{rec.reasoning}</KvRow>
      <KvRow label="Alternatives">{(recommended.alternatives || []).join(', ') || '—'}</KvRow>
      <KvRow label="Evidence">{evidenceCount} sample(s) — observed historical performance</KvRow>
      {rec.created_at && <KvRow label="Created">{fmtDate(rec.created_at)}</KvRow>}
      {rec.status === 'pending' && (
        <div className="filters-row" style={{ marginTop: 8 }}>
          <button className="primary" onClick={() => decide('accept')} disabled={busy}>Accept</button>
          <button className="danger" onClick={() => decide('reject')} disabled={busy}>Reject</button>
        </div>
      )}
    </Panel>
  )
}

export function EvalRuns() {
  const navigate = useNavigate()
  const { loading, error, data: runs } = useApiData('/api/v2/eval-runs')
  if (loading) return <LoadingState />
  if (error) return <ErrorState error={error} />
  return (
    <>
      <h1 className="page-title">Eval Runs</h1>
      <Panel>
        <table>
          <thead><tr><th>ID</th><th>Suite</th><th>Status</th><th>Source Runs</th><th>Started</th><th>Finished</th></tr></thead>
          <tbody>
            {runs.length ? runs.map((r) => (
              <tr className="clickable" key={r.id} onClick={() => navigate(`/eval-runs/${r.id}`)}>
                <td className="mono">{shortId(r.id)}</td><td className="mono">{shortId(r.suite_id)}</td>
                <td><Badge status={r.status} /></td><td>{r.source_run_ids.length}</td>
                <td>{fmtDate(r.started_at)}</td><td>{r.finished_at ? fmtDate(r.finished_at) : '—'}</td>
              </tr>
            )) : <tr><td colSpan={6}><EmptyState>No evaluation runs yet.</EmptyState></td></tr>}
          </tbody>
        </table>
      </Panel>
    </>
  )
}

function DiagnosisPanel({ resultId }) {
  const [state, setState] = useState({ loading: true, diagnosis: null, error: null })

  useEffect(() => {
    api(`/api/v2/eval-results/${resultId}/diagnose`, { method: 'POST' }).then(
      (diagnosis) => setState({ loading: false, diagnosis, error: null }),
      (error) => setState({ loading: false, diagnosis: null, error }),
    )
  }, [resultId])

  if (state.loading) return <LoadingState />
  if (state.error) return <ErrorState error={state.error} />
  const d = state.diagnosis
  return (
    <div className="panel-body" style={{ background: 'var(--surface-alt)' }}>
      <div className="kv-row"><span className="k">Why</span><span className="wrap">{d.why}</span></div>
      <div className="kv-row"><span className="k">Where in code</span><span className="mono">{d.code_file ? `${d.code_file}:${d.code_lineno} (${d.code_function})` : 'unknown'}</span></div>
      {d.available ? (
        <>
          <div className="kv-row"><span className="k">Suggested prompt</span><span className="wrap">{d.suggested_prompt}</span></div>
          <div className="kv-row"><span className="k">Why this change</span><span className="wrap">{d.suggestion_explanation}</span></div>
          <div className="kv-row"><span className="k">Judge model</span><span>{d.judge_model_used}</span></div>
        </>
      ) : (
        <div className="empty-state">No prompt suggestion available: {d.unavailable_reason}</div>
      )}
    </div>
  )
}

export function EvalRunDetail() {
  const { evaluationRunId } = useParams()
  const { loading, error, data } = useApiData(`/api/v2/eval-runs/${evaluationRunId}`, [evaluationRunId])
  const [expanded, setExpanded] = useState(null)
  if (loading) return <LoadingState />
  if (error) return <ErrorState error={error} />
  const { evaluation_run: run, results } = data
  return (
    <>
      <h1 className="page-title">Eval Run {shortId(run.id)}</h1>
      <Panel>
        <div className="kv-row"><span className="k">Status</span><span><Badge status={run.status} /></span></div>
        <div className="kv-row"><span className="k">Suite</span><span className="mono">{shortId(run.suite_id)}</span></div>
        <div className="kv-row"><span className="k">Started</span><span>{fmtDate(run.started_at)}</span></div>
      </Panel>
      <Panel style={{ marginTop: 14 }}>
        <table>
          <thead><tr><th>Metric</th><th>Source Run</th><th>Score</th><th>Passed</th><th>Available</th><th>Reason</th><th></th></tr></thead>
          <tbody>
            {results.length ? results.map((r) => (
              <Fragment key={r.id}>
                <tr>
                  <td>{r.metric}</td>
                  <td>{r.source_run_id ? <Link to={`/runs/${r.source_run_id}`} className="mono">{shortId(r.source_run_id)}</Link> : '—'}</td>
                  <td>{r.score != null ? r.score.toFixed(3) : 'N/A'}</td>
                  <td>{r.passed === null ? '—' : <StatusBadge label={r.passed ? 'Passed' : 'Failed'} cssClass={r.passed ? 'reliable' : 'unreliable'} />}</td>
                  <td>{r.available ? 'yes' : 'no'}</td>
                  <td className="wrap">{r.reason || ''}</td>
                  <td>
                    {r.passed === false && (
                      <button onClick={() => setExpanded(expanded === r.id ? null : r.id)}>
                        {expanded === r.id ? 'Hide' : 'Diagnose'}
                      </button>
                    )}
                  </td>
                </tr>
                {expanded === r.id && (
                  <tr><td colSpan={7} style={{ padding: 0 }}><DiagnosisPanel resultId={r.id} /></td></tr>
                )}
              </Fragment>
            )) : <tr><td colSpan={7}><EmptyState>No results recorded.</EmptyState></td></tr>}
          </tbody>
        </table>
      </Panel>
    </>
  )
}

export function Datasets() {
  const { loading, error, data: datasets } = useApiData('/api/v2/datasets')
  if (loading) return <LoadingState />
  if (error) return <ErrorState error={error} />
  return (
    <>
      <h1 className="page-title">Datasets</h1>
      <Panel>
        <table>
          <thead><tr><th>Name</th><th>Current Version</th><th>Created</th><th></th></tr></thead>
          <tbody>
            {datasets.length ? datasets.map((d) => (
              <tr key={d.id}>
                <td>{d.name}</td><td>{d.current_version}</td><td>{fmtDate(d.created_at)}</td>
                <td>{d.current_version ? <Link to={`/datasets/${d.id}/versions/${d.current_version}`}>View quality</Link> : '—'}</td>
              </tr>
            )) : <tr><td colSpan={4}><EmptyState>No datasets registered yet.</EmptyState></td></tr>}
          </tbody>
        </table>
      </Panel>
    </>
  )
}

export function DatasetQuality() {
  const { datasetId, version } = useParams()
  const { loading, error, data } = useApiData(`/api/v2/datasets/${datasetId}/versions/${version}`, [datasetId, version])
  if (loading) return <LoadingState />
  if (error) return <ErrorState error={error} />
  return (
    <>
      <h1 className="page-title">Dataset Quality — v{version}</h1>
      <Panel>
        {Object.entries(data.status_counts).length ? Object.entries(data.status_counts).map(([status, count]) => (
          <div className="kv-row" key={status}>
            <span className="k"><StatusBadge label={status} cssClass={STATUS_BADGE_CLASS[status] || 'insufficient_data'} /> {status}</span>
            <span>{count}</span>
          </div>
        )) : <EmptyState>No examples in this version.</EmptyState>}
      </Panel>
      <Panel style={{ marginTop: 14 }}>
        <table>
          <thead><tr><th>Question</th><th>Expected Answer</th><th>Status</th><th>Confidence</th><th>Evidence</th></tr></thead>
          <tbody>
            {data.examples.length ? data.examples.map((e) => (
              <tr key={e.id}>
                <td className="wrap">{e.question}</td><td className="wrap">{e.expected_answer}</td>
                <td><StatusBadge label={e.golden_status} cssClass={STATUS_BADGE_CLASS[e.golden_status] || 'insufficient_data'} /></td>
                <td>{e.validation_confidence != null ? e.validation_confidence.toFixed(2) : 'N/A'}</td>
                <td>
                  {e.evidence.length} passage(s)
                  {e.validation_reason && <div style={{ color: 'var(--ink-soft)', fontSize: 11.5 }}>{e.validation_reason}</div>}
                </td>
              </tr>
            )) : <tr><td colSpan={5}><EmptyState>No examples in this version.</EmptyState></td></tr>}
          </tbody>
        </table>
      </Panel>
    </>
  )
}

export function Suites() {
  const { loading, error, data: suites } = useApiData('/api/v2/suites')
  if (loading) return <LoadingState />
  if (error) return <ErrorState error={error} />
  return (
    <>
      <h1 className="page-title">Evaluation Suites</h1>
      {suites.length ? suites.map((s) => (
        <Panel header={<>{s.name} {s.app_type && <Badge status={s.app_type} />}</>} key={s.id} style={{ marginBottom: 12 }}>
          <div className="kv-row"><span className="k">Version</span><span>v{s.version}</span></div>
          <table>
            <thead><tr><th>Metric</th><th>Threshold</th><th>Recommended by</th><th>Reason</th></tr></thead>
            <tbody>
              {s.metrics.map((m, i) => (
                <tr key={i}>
                  <td>{m.evaluator}</td><td>{m.threshold ?? '—'}</td><td><Badge status={m.recommended_by} /></td>
                  <td className="wrap">{m.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
      )) : <EmptyState>No evaluation suites created yet.</EmptyState>}
    </>
  )
}

function RunExperimentForm({ onCreated }) {
  const [searchParams] = useSearchParams()
  const { data: suites } = useApiData('/api/v2/suites')
  const { data: catalog } = useApiData('/api/v2/model-catalog')
  const [suiteId, setSuiteId] = useState('')
  const [sourceRunIds, setSourceRunIds] = useState(searchParams.get('seed_run_id') || '')
  const [selectedModels, setSelectedModels] = useState([])
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState(null)

  function toggleModel(model) {
    setSelectedModels((prev) => (prev.includes(model) ? prev.filter((m) => m !== model) : [...prev, model]))
  }

  async function submit(e) {
    e.preventDefault()
    setSubmitting(true)
    setError(null)
    try {
      const body = {
        suite_id: suiteId,
        source_run_ids: sourceRunIds.split(',').map((s) => s.trim()).filter(Boolean),
        models: selectedModels,
      }
      const res = await api('/api/v2/benchmarks', { method: 'POST', body: JSON.stringify(body) })
      onCreated(res.benchmark_id)
    } catch (err) {
      setError(err)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <form onSubmit={submit}>
      <div className="filters-row" style={{ flexWrap: 'wrap', gap: 8 }}>
        <select value={suiteId} onChange={(e) => setSuiteId(e.target.value)} required>
          <option value="">Select a suite…</option>
          {(suites || []).map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
        </select>
        <input
          placeholder="source run ids, comma-separated"
          value={sourceRunIds}
          onChange={(e) => setSourceRunIds(e.target.value)}
          style={{ minWidth: 260 }}
          required
        />
        <button className="primary" type="submit" disabled={submitting || !selectedModels.length}>
          {submitting ? 'Starting…' : 'Run Experiment'}
        </button>
      </div>
      <div className="filters-row" style={{ flexWrap: 'wrap', gap: 6, marginTop: 8 }}>
        {(catalog || []).map((m) => (
          <label key={m.model} style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
            <input type="checkbox" checked={selectedModels.includes(m.model)} onChange={() => toggleModel(m.model)} />
            {m.model}
          </label>
        ))}
      </div>
      {error && <ErrorState error={error} />}
    </form>
  )
}

export function Benchmarks() {
  const navigate = useNavigate()
  const [expanded, setExpanded] = useState(false)
  const { loading, error, data: benchmarks } = useApiData('/api/v2/benchmarks')
  if (loading) return <LoadingState />
  if (error) return <ErrorState error={error} />
  return (
    <>
      <h1 className="page-title">Model Benchmarks</h1>
      <Panel header={
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <span>Run Experiment</span>
          <button onClick={() => setExpanded((e) => !e)}>{expanded ? 'Hide' : 'New experiment'}</button>
        </div>
      }>
        {expanded && <RunExperimentForm onCreated={(id) => navigate(`/benchmarks/${id}`)} />}
      </Panel>
      <Panel style={{ marginTop: 14 }}>
        <table>
          <thead><tr><th>ID</th><th>Suite</th><th>Models</th><th>Created</th></tr></thead>
          <tbody>
            {benchmarks.length ? benchmarks.map((b) => (
              <tr className="clickable" key={b.id} onClick={() => navigate(`/benchmarks/${b.id}`)}>
                <td className="mono">{shortId(b.id)}</td><td className="mono">{shortId(b.suite_id)}</td>
                <td>{b.models.join(', ')}</td><td>{fmtDate(b.created_at)}</td>
              </tr>
            )) : <tr><td colSpan={4}><EmptyState>No model benchmarks run yet.</EmptyState></td></tr>}
          </tbody>
        </table>
      </Panel>
    </>
  )
}

function RecommendForm({ benchmarkId, onRecommended }) {
  const [objective, setObjective] = useState(OBJECTIVES[0])
  const [qualityMetric, setQualityMetric] = useState('')
  const [qualityThreshold, setQualityThreshold] = useState('0.7')
  const [costBudget, setCostBudget] = useState('')
  const [minTokens, setMinTokens] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState(null)

  async function submit(e) {
    e.preventDefault()
    setSubmitting(true)
    setError(null)
    try {
      const body = { objective }
      if (objective !== 'best_tool_calling_reliability') body.quality_metric = qualityMetric
      if (['cheapest_above_quality_threshold', 'fastest_above_quality_threshold'].includes(objective)) {
        body.quality_threshold = Number(qualityThreshold)
      }
      if (objective === 'highest_quality_within_budget' && costBudget) body.cost_budget_usd = Number(costBudget)
      if (objective === 'best_long_context' && minTokens) body.min_tokens_input = Number(minTokens)
      const rec = await api(`/api/v2/benchmarks/${benchmarkId}/recommend`, { method: 'POST', body: JSON.stringify(body) })
      onRecommended(rec)
    } catch (err) {
      setError(err)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <form onSubmit={submit}>
      <div className="filters-row" style={{ flexWrap: 'wrap', gap: 8 }}>
        <select value={objective} onChange={(e) => setObjective(e.target.value)}>
          {OBJECTIVES.map((o) => <option key={o} value={o}>{o}</option>)}
        </select>
        {objective !== 'best_tool_calling_reliability' && (
          <input
            placeholder="quality metric (e.g. faithfulness)"
            value={qualityMetric}
            onChange={(e) => setQualityMetric(e.target.value)}
            required
          />
        )}
        {['cheapest_above_quality_threshold', 'fastest_above_quality_threshold'].includes(objective) && (
          <input type="number" step="0.01" placeholder="quality threshold" value={qualityThreshold} onChange={(e) => setQualityThreshold(e.target.value)} />
        )}
        {objective === 'highest_quality_within_budget' && (
          <input type="number" step="0.001" placeholder="cost budget USD" value={costBudget} onChange={(e) => setCostBudget(e.target.value)} />
        )}
        {objective === 'best_long_context' && (
          <input type="number" placeholder="min tokens input" value={minTokens} onChange={(e) => setMinTokens(e.target.value)} />
        )}
        <button className="primary" type="submit" disabled={submitting}>{submitting ? 'Recommending…' : 'Recommend'}</button>
      </div>
      {error && <ErrorState error={error} />}
    </form>
  )
}

export function BenchmarkDetail() {
  const { benchmarkId } = useParams()
  const { loading, error, data } = useApiData(`/api/v2/benchmarks/${benchmarkId}`, [benchmarkId])
  const [recommendation, setRecommendation] = useState(null)
  if (loading) return <LoadingState />
  if (error) return <ErrorState error={error} />
  const { benchmark, results } = data

  const recAsRow = recommendation && {
    id: recommendation.recommendation_id,
    status: recommendation.status || 'pending',
    confidence: recommendation.confidence,
    reasoning: recommendation.reasoning,
    recommendation: { model: recommendation.model, objective: recommendation.objective, alternatives: recommendation.alternatives },
    evidence_count: recommendation.evidence_count,
  }

  return (
    <>
      <h1 className="page-title">Benchmark {shortId(benchmark.id)}</h1>
      <div className="page-subtitle">Models: {benchmark.models.join(', ')}</div>
      <Panel>
        <table>
          <thead><tr><th>Model</th><th>Example</th><th>Metric</th><th>Score</th><th>Cost</th><th>Latency</th><th>Tokens In</th></tr></thead>
          <tbody>
            {results.length ? results.map((r) => (
              <tr key={r.id}>
                <td>{r.model}</td><td>{r.example_id ? shortId(r.example_id) : '—'}</td><td>{r.metric || '—'}</td>
                <td>{r.score != null ? r.score.toFixed(3) : 'N/A'}</td>
                <td>{r.cost_usd != null ? `$${r.cost_usd.toFixed(4)}` : 'N/A'}</td>
                <td>{r.latency_ms.toFixed(1)} ms</td><td>{r.tokens_input ?? 'N/A'}</td>
              </tr>
            )) : <tr><td colSpan={7}><EmptyState>No results recorded.</EmptyState></td></tr>}
          </tbody>
        </table>
      </Panel>
      <Panel header="Recommend a Model" style={{ marginTop: 14 }}>
        <RecommendForm benchmarkId={benchmarkId} onRecommended={setRecommendation} />
      </Panel>
      {recAsRow && recAsRow.id && (
        <ModelRecommendationCard
          rec={recAsRow}
          onDecided={(updated) => setRecommendation((r) => ({ ...r, status: updated.status }))}
        />
      )}
      {recommendation && !recommendation.recommendation_id && (
        <Panel style={{ marginTop: 14 }}><EmptyState>No model met this objective's constraints.</EmptyState></Panel>
      )}
    </>
  )
}

export function EvalRecommendations() {
  const { loading, error, data: recs } = useApiData('/api/v2/eval-recommendations')
  if (loading) return <LoadingState />
  if (error) return <ErrorState error={error} />
  return (
    <>
      <h1 className="page-title">Evaluation Recommendations</h1>
      {recs.length ? recs.map((r) => (
        <Panel header={<><Badge status={r.kind} /> {shortId(r.subject_id)}</>} key={r.id} style={{ marginBottom: 12 }}>
          <div className="kv-row"><span className="k">Reasoning</span><span className="wrap">{r.reasoning}</span></div>
          <div className="kv-row"><span className="k">Recommendation</span><span className="mono">{JSON.stringify(r.recommendation)}</span></div>
          <div className="kv-row"><span className="k">Created</span><span>{fmtDate(r.created_at)}</span></div>
        </Panel>
      )) : <EmptyState>No recommendations computed yet.</EmptyState>}
    </>
  )
}
