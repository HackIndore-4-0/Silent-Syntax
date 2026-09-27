import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { api } from '../api'
import { Badge, EmptyState, ErrorState, KvRow, LoadingState, Panel, StatusBadge, useApiData } from '../components/ui'
import { fmtDate, fmtMs, fmtNum, fmtPct, shortId } from '../format'

const TABS = ['overview', 'trace', 'steps', 'evaluations', 'latency', 'tokens', 'reliability', 'recovery', 'audit', 'metadata']

const EVENT_KIND_LABEL = {
  RUN_START: 'Agent Run', AGENT_STEP: 'Planning', TOOL_CALL: 'Tool Call', TOOL_SUBSTITUTED: 'Tool Call',
  EVALUATION: 'Evaluation', DECISION: 'Decision', ROLLBACK: 'Recovery', HUMAN: 'Recovery', CHECKPOINT: 'Planning',
}

export default function RunDetail() {
  const { runId } = useParams()
  const [tab, setTab] = useState('overview')
  const [run, setRun] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    setRun(null)
    setError(null)
    api(`/api/v2/runs/${runId}`).then(setRun, setError)
  }, [runId])

  if (error) return <ErrorState error={error} />
  if (!run) return <LoadingState />

  return (
    <>
      <h1 className="page-title">Run {shortId(runId)}</h1>
      <div className="page-subtitle">{run.agent_name} · <Badge status={run.status} /></div>
      <div className="tabs">
        {TABS.map((t) => (
          <div className={`tab ${t === tab ? 'active' : ''}`} key={t} onClick={() => setTab(t)}>
            {t[0].toUpperCase() + t.slice(1)}
          </div>
        ))}
      </div>
      <TabContent tab={tab} runId={runId} run={run} onRunUpdate={setRun} />
    </>
  )
}

function TabContent({ tab, runId, run, onRunUpdate }) {
  switch (tab) {
    case 'overview': return <OverviewTab run={run} />
    case 'trace': return <TraceTab runId={runId} />
    case 'steps': return <StepsTab runId={runId} />
    case 'evaluations': return <EvaluationsTab run={run} />
    case 'latency': return <LatencyTab run={run} />
    case 'tokens': return <TokensTab run={run} />
    case 'reliability': return <ReliabilityTab runId={runId} />
    case 'recovery': return <RecoveryTab runId={runId} run={run} onRunUpdate={onRunUpdate} />
    case 'audit': return <AuditTab runId={runId} />
    case 'metadata': return <MetadataTab run={run} />
    default: return null
  }
}

function OverviewTab({ run }) {
  const decisions = run.decisions || []
  return (
    <div className="grid-2">
      <Panel header="Summary">
        <KvRow label="Run ID"><span className="mono">{run.id}</span></KvRow>
        <KvRow label="Agent">{run.agent_name}</KvRow>
        <KvRow label="Task">{run.task || '—'}</KvRow>
        <KvRow label="Status"><Badge status={run.status} /></KvRow>
        <KvRow label="Policy version">{(run.policy || {}).version ?? '—'}</KvRow>
        <KvRow label="Started">{fmtDate(run.started_at)}</KvRow>
        <KvRow label="Duration">{fmtMs(run.duration_ms)}</KvRow>
        <KvRow label="Retry / Replan">{run.retry_count} / {run.replan_count}</KvRow>
      </Panel>
      <Panel header="Decision History">
        {decisions.length ? decisions.map((d, i) => (
          <KvRow label={<Badge status={d.outcome} />} key={i}>{d.reason}</KvRow>
        )) : <EmptyState>No decisions recorded.</EmptyState>}
      </Panel>
    </div>
  )
}

function TraceTab({ runId }) {
  const [events, setEvents] = useState(null)
  const [expanded, setExpanded] = useState(new Set())
  useEffect(() => { api(`/api/v2/runs/${runId}/timeline`).then((t) => setEvents(t.events)) }, [runId])
  if (!events) return <LoadingState />
  if (!events.length) return <EmptyState>No trace events recorded.</EmptyState>
  return (
    <div className="span-tree">
      {events.map((e, i) => (
        <div className={`span-node ${expanded.has(i) ? 'expanded' : ''}`} key={i} onClick={() => {
          const next = new Set(expanded)
          if (next.has(i)) next.delete(i)
          else next.add(i)
          setExpanded(next)
        }}>
          <div className="span-head">
            <span className="span-type">{EVENT_KIND_LABEL[e.event_type] || e.event_type}</span>
            <span>— {e.event_type}</span>
            <span className="span-time">seq {e.seq} · {fmtDate(e.created_at)}{e.policy_version ? ` · policy v${e.policy_version}` : ''}</span>
          </div>
          <div className="span-detail"><pre style={{ margin: 0, whiteSpace: 'pre-wrap' }}>{JSON.stringify(e.payload, null, 2)}</pre></div>
        </div>
      ))}
    </div>
  )
}

// Fine-grained @traceable / wrap_llm_client call tree — every retrieval
// call, tool call, or LLM call an integration (e.g. a RAG pipeline) wraps
// inside an already-@monitor-wrapped run. Distinct from TraceTab above,
// which shows the coarser audit-event timeline (run-start/decision/etc.),
// not individual traced calls.
function StepsTab({ runId }) {
  const { loading, error, data } = useApiData(`/api/v2/runs/${runId}/trace-steps`)
  const [expanded, setExpanded] = useState(new Set())

  if (loading) return <LoadingState />
  if (error) return <ErrorState error={error} />

  const steps = data.steps || []
  if (!steps.length) {
    return (
      <EmptyState>
        No traced calls recorded. Wrap the functions this run calls internally
        (e.g. a retrieval step or an LLM call) with @traceable or wrap_llm_client
        to see them here.
      </EmptyState>
    )
  }

  const childrenOf = new Map()
  for (const step of steps) {
    const key = step.parent_step_id || 'root'
    if (!childrenOf.has(key)) childrenOf.set(key, [])
    childrenOf.get(key).push(step)
  }

  const toggle = (id, e) => {
    e.stopPropagation()
    const next = new Set(expanded)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    setExpanded(next)
  }

  const renderStep = (step) => (
    <div className={`span-node ${expanded.has(step.id) ? 'expanded' : ''}`} key={step.id} onClick={(e) => toggle(step.id, e)}>
      <div className="span-head">
        <span className="span-type">{step.kind === 'llm_call' ? 'LLM Call' : 'Function'}</span>
        <span className="mono">{step.name}</span>
        <StatusBadge label={step.outcome} cssClass={step.outcome === 'success' ? 'complete' : 'stop'} />
        <span className="span-time">
          {fmtMs(step.latency_ms)}
          {step.model_name ? ` · ${step.model_name}` : ''}
          {step.tokens_input != null ? ` · ${step.tokens_input}+${step.tokens_output ?? 0} tok` : ''}
          {step.cost_usd != null ? ` · $${step.cost_usd.toFixed(4)}` : ''}
        </span>
      </div>
      <div className="span-detail">
        <pre style={{ margin: 0, whiteSpace: 'pre-wrap' }}>
          {JSON.stringify(
            {
              input: step.input,
              output: step.output,
              ...(step.exception_message ? { exception: `${step.exception_type}: ${step.exception_message}` } : {}),
              ...(step.code_file ? { code: `${step.code_file}:${step.code_lineno} (${step.code_function})` } : {}),
            },
            null,
            2
          )}
        </pre>
      </div>
      {(childrenOf.get(step.id) || []).map(renderStep)}
    </div>
  )

  return <div className="span-tree">{(childrenOf.get('root') || []).map(renderStep)}</div>
}

function EvaluationsTab({ run }) {
  const evals = run.evaluations || []
  return (
    <Panel>
      <table>
        <thead><tr><th>Evaluator</th><th>Label</th><th>Passed</th><th>Score</th><th>Confidence</th><th>Reason</th></tr></thead>
        <tbody>
          {evals.length ? evals.map((e, i) => (
            <tr key={i}>
              <td>{e.evaluator}</td><td>{e.label}</td><td>{e.passed ? 'PASS' : 'FAIL'}</td>
              <td>{fmtNum(e.score, 2)}</td><td>{fmtPct(e.confidence)}</td><td className="wrap">{e.reason || '—'}</td>
            </tr>
          )) : <tr><td colSpan={6}><EmptyState>No evaluations recorded.</EmptyState></td></tr>}
        </tbody>
      </table>
    </Panel>
  )
}

function LatencyTab({ run }) {
  return (
    <Panel>
      <KvRow label="Total Duration">{fmtMs(run.duration_ms)}</KvRow>
      <KvRow label="Started">{fmtDate(run.started_at)}</KvRow>
      <KvRow label="Finished">{fmtDate(run.finished_at)}</KvRow>
    </Panel>
  )
}

function TokensTab({ run }) {
  if (run.tokens_input === null && run.tokens_output === null) return <EmptyState />
  return (
    <Panel>
      <KvRow label="Model">{run.model_name || 'N/A'}</KvRow>
      <KvRow label="Input tokens">{run.tokens_input ?? 'N/A'}</KvRow>
      <KvRow label="Output tokens">{run.tokens_output ?? 'N/A'}</KvRow>
      <KvRow label="Estimated cost">{run.estimated_cost_usd !== null ? `$${run.estimated_cost_usd.toFixed(4)}` : 'N/A'}</KvRow>
    </Panel>
  )
}

function ReliabilityTab({ runId }) {
  const [report, setReport] = useState(null)
  useEffect(() => { api(`/api/v2/runs/${runId}/reliability-report`).then(setReport) }, [runId])
  if (!report) return <LoadingState />
  return (
    <Panel>
      {Object.entries(report.dimensions).map(([name, dim]) => (
        <div key={name}>
          <KvRow label={name.replace(/_/g, ' ')}>
            {dim.available ? fmtPct(dim.value) : <span style={{ color: 'var(--ink-faint)' }}>unavailable</span>}
          </KvRow>
          <div className="bar-track" style={{ marginBottom: 6 }}>
            <div className="bar-fill" style={{ width: `${dim.available ? dim.value * 100 : 0}%` }} />
          </div>
        </div>
      ))}
      <KvRow label="Risk">{fmtNum(report.risk, 3)}</KvRow>
      <KvRow label="Confidence">{fmtPct(report.confidence)}</KvRow>
      <KvRow label="Recovery">{report.recovery.status}</KvRow>
      <KvRow label="Root cause">{report.root_cause_summary || 'none'}</KvRow>
    </Panel>
  )
}

function RecoveryTab({ runId, run }) {
  const [checkpoints, setCheckpoints] = useState(null)
  const [counterfactual, setCounterfactual] = useState(null)
  const [target, setTarget] = useState('')
  const [result, setResult] = useState(null)
  const [rollbackError, setRollbackError] = useState(null)

  useEffect(() => {
    Promise.all([api(`/api/v2/runs/${runId}/checkpoints`), api(`/api/v2/runs/${runId}/counterfactual`)])
      .then(([cp, cf]) => { setCheckpoints(cp); setCounterfactual(cf); if (cp.length) setTarget(cp[0].label) })
  }, [runId])

  if (!checkpoints) return <LoadingState />
  const canRollback = ['stop', 'failed', 'human', 'rolled_back'].includes(run.status)

  async function doRollback() {
    setResult(null); setRollbackError(null)
    try {
      const r = await api(`/api/v2/runs/${runId}/rollback`, { method: 'POST', body: JSON.stringify({ to_checkpoint: target }) })
      setResult(r.restored_state)
    } catch (e) { setRollbackError(e.message) }
  }

  return (
    <>
      <Panel header="Checkpoints">
        {checkpoints.length ? checkpoints.map((c, i) => (
          <KvRow label={c.label} key={i}><span className="mono">{JSON.stringify(c.state)}</span></KvRow>
        )) : <EmptyState>No checkpoints recorded.</EmptyState>}
      </Panel>
      {canRollback && (
        <Panel header="Rollback" style={{ marginTop: 12 }}>
          <div className="filters-row">
            <select value={target} onChange={(e) => setTarget(e.target.value)}>
              {checkpoints.map((c) => <option value={c.label} key={c.label}>{c.label}</option>)}
            </select>
            <button className="primary" onClick={doRollback}>Rollback</button>
          </div>
          {rollbackError && <ErrorState error={{ message: rollbackError }} />}
          {result && <KvRow label="Restored state"><span className="mono">{JSON.stringify(result)}</span></KvRow>}
        </Panel>
      )}
      <Panel header="Counterfactual" style={{ marginTop: 12 }}>
        {counterfactual && counterfactual.result
          ? <KvRow label="Result">{counterfactual.result}</KvRow>
          : <EmptyState>No counterfactual analysis recorded for this run.</EmptyState>}
      </Panel>
    </>
  )
}

function AuditTab({ runId }) {
  const [audit, setAudit] = useState(null)
  const [verification, setVerification] = useState(null)
  useEffect(() => {
    Promise.all([api(`/api/v2/runs/${runId}/audit`), api(`/api/v2/runs/${runId}/audit/verify`, { method: 'POST' })])
      .then(([a, v]) => { setAudit(a); setVerification(v) })
  }, [runId])
  if (!audit) return <LoadingState />
  return (
    <>
      <Panel>
        <KvRow label="Events">{audit.event_count}</KvRow>
        <KvRow label="Chain status">
          {verification.intact
            ? <span style={{ color: 'var(--ok)' }}>CHAIN VALID</span>
            : <span style={{ color: 'var(--danger)' }}>CHAIN INVALID</span>}
        </KvRow>
        {!verification.intact && <KvRow label="First broken event"><span className="mono">{verification.first_invalid_event}</span></KvRow>}
      </Panel>
      <Panel style={{ marginTop: 12 }}>
        <table>
          <thead><tr><th>Seq</th><th>Event</th><th>Hash</th><th>Previous Hash</th></tr></thead>
          <tbody>
            {audit.events.map((e) => (
              <tr key={e.seq}>
                <td>{e.seq}</td><td>{e.event_type}</td>
                <td className="mono">{e.event_hash.slice(0, 10)}…</td>
                <td className="mono">{e.previous_hash.slice(0, 10)}…</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Panel>
    </>
  )
}

function MetadataTab({ run }) {
  return (
    <Panel>
      <KvRow label="Trace ID"><span className="mono">{run.trace_id || '—'}</span></KvRow>
      <KvRow label="Span ID"><span className="mono">{run.span_id || '—'}</span></KvRow>
      <KvRow label="Workspace ID"><span className="mono">{run.workspace_id || '—'}</span></KvRow>
      <KvRow label="Project ID"><span className="mono">{run.project_id || '—'}</span></KvRow>
      <KvRow label="Agent ID"><span className="mono">{run.agent_id || '—'}</span></KvRow>
      <KvRow label="Agent version">{run.agent_version || '—'}</KvRow>
      <h3>Initial state</h3><pre>{JSON.stringify(run.initial_state, null, 2)}</pre>
      <h3>Final state</h3><pre>{JSON.stringify(run.final_state, null, 2)}</pre>
      {run.exception_type && <><h3>Exception</h3><pre>{run.exception_type}: {run.exception_message}</pre></>}
    </Panel>
  )
}
