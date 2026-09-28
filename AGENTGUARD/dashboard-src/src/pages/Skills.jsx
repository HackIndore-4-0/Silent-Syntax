import { useEffect, useState } from 'react'
import { api } from '../api'
import { ErrorState, LoadingState, Panel, useApiData } from '../components/ui'

export default function Skills() {
  const { loading, error, data: options } = useApiData('/api/v2/skills/options')
  const [projects, setProjects] = useState(null)
  const [projectsError, setProjectsError] = useState(null)
  const [projectId, setProjectId] = useState('')
  const [framework, setFramework] = useState('plain_python')
  const [judgeModel, setJudgeModel] = useState('gpt-4o-mini')
  const [selectedCategories, setSelectedCategories] = useState({})
  const [selectedMetrics, setSelectedMetrics] = useState({})
  const [expanded, setExpanded] = useState({})
  const [markdown, setMarkdown] = useState('')
  const [genError, setGenError] = useState(null)
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    api('/api/projects').then(
      (list) => {
        setProjects(list)
        if (list.length && !projectId) setProjectId(list[0].id)
      },
      (e) => setProjectsError(e)
    )
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  function toggleCategory(key) {
    setSelectedCategories((prev) => ({ ...prev, [key]: !prev[key] }))
    // Checking the category box means "include this whole feature" --
    // default to every metric under it so it isn't silently dropped
    // from the generated Skill just because nothing was expanded and
    // hand-picked underneath. Unchecking leaves selectedMetrics alone
    // since an unselected category is never rendered anyway.
    setSelectedMetrics((prev) => {
      if (prev[key] && prev[key].length > 0) return prev
      const category = options.categories.find((c) => c.key === key)
      if (!category || category.metrics.length === 0) return prev
      return { ...prev, [key]: category.metrics.map((m) => m.key) }
    })
  }

  function toggleMetric(categoryKey, metricKey) {
    setSelectedMetrics((prev) => {
      const current = prev[categoryKey] || []
      const next = current.includes(metricKey)
        ? current.filter((m) => m !== metricKey)
        : [...current, metricKey]
      return { ...prev, [categoryKey]: next }
    })
  }

  async function generate() {
    setGenError(null)
    setCopied(false)
    try {
      const categories = Object.keys(selectedCategories).filter((k) => selectedCategories[k])
      const result = await api('/api/v2/skills/generate', {
        method: 'POST',
        body: JSON.stringify({
          project_id: projectId,
          framework,
          categories,
          metrics: selectedMetrics,
          judge_model: judgeModel,
        }),
      })
      setMarkdown(result.markdown)
    } catch (e) {
      setGenError(e.message)
    }
  }

  async function copy() {
    await navigator.clipboard.writeText(markdown)
    setCopied(true)
  }

  if (error) return <ErrorState error={error} />
  if (projectsError) return <ErrorState error={projectsError} />
  if (loading || !projects) return <LoadingState />

  return (
    <>
      <h1 className="page-title">Skills</h1>
      <div className="page-subtitle">Generate a copyable integration guide for an AI coding agent.</div>

      <Panel header="1. Project">
        <select value={projectId} onChange={(e) => setProjectId(e.target.value)}>
          {projects.map((p) => <option value={p.id} key={p.id}>{p.name}</option>)}
        </select>
      </Panel>

      <Panel header="2. Framework" style={{ marginTop: 12 }}>
        {options.frameworks.map((f) => (
          <div className="kv-row" key={f.key}>
            <label>
              <input type="radio" name="framework" checked={framework === f.key} onChange={() => setFramework(f.key)} />
              {' '}{f.label} — {f.description}
            </label>
          </div>
        ))}
      </Panel>

      <Panel header="3. Features" style={{ marginTop: 12 }}>
        {options.categories.map((c) => (
          <div key={c.key} style={{ marginBottom: 8 }}>
            <label>
              <input type="checkbox" checked={!!selectedCategories[c.key]} onChange={() => toggleCategory(c.key)} />
              {' '}{c.label} — {c.description}
              {c.metrics.length > 0 && (
                <button
                  type="button"
                  onClick={() => setExpanded((prev) => ({ ...prev, [c.key]: !prev[c.key] }))}
                  style={{ marginLeft: 8 }}
                >
                  {expanded[c.key] ? 'hide metrics' : 'expand'}
                </button>
              )}
            </label>
            {expanded[c.key] && c.metrics.map((m) => (
              <div className="kv-row" key={m.key} style={{ marginLeft: 24 }}>
                <label>
                  <input
                    type="checkbox"
                    checked={(selectedMetrics[c.key] || []).includes(m.key)}
                    onChange={() => toggleMetric(c.key, m.key)}
                  />
                  {' '}{m.key} — {m.description}
                </label>
              </div>
            ))}
          </div>
        ))}
      </Panel>

      <Panel header="4. Judge model" style={{ marginTop: 12 }}>
        {options.judge_models.map((m) => (
          <div className="kv-row" key={m.key}>
            <label>
              <input type="radio" name="judge_model" checked={judgeModel === m.key} onChange={() => setJudgeModel(m.key)} />
              {' '}{m.label}
            </label>
            <div style={{ marginLeft: 24, color: 'var(--ink-faint)', fontSize: 12 }}>{m.note}</div>
          </div>
        ))}
      </Panel>

      <div className="filters-row" style={{ marginTop: 12 }}>
        <button className="primary" onClick={generate}>Generate</button>
      </div>
      {genError && <ErrorState error={{ message: genError }} />}

      {markdown && (
        <Panel header="Generated Skill" style={{ marginTop: 12 }}>
          <textarea readOnly value={markdown} rows={20} style={{ width: '100%', fontFamily: 'var(--mono)' }} />
          <button className="primary" onClick={copy} style={{ marginTop: 8 }}>
            {copied ? 'Copied!' : 'Copy'}
          </button>
        </Panel>
      )}
    </>
  )
}
