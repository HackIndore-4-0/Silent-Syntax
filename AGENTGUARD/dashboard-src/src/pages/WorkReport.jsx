import { useLayoutEffect, useRef } from 'react'
import { gsap } from 'gsap'

// Standalone report page — deliberately outside ProtectedLayout/the app
// shell (own nav, own auth state) since it's a point-in-time write-up,
// not a live dashboard view. Reuses the app's existing color/type tokens
// from index.css (--bg, --surface, --ink, --accent, --mono, --font, …).

const ENTRIES = [
  {
    cat: 'ENVIRONMENT',
    title: 'Local stack provisioned from a clean machine',
    body: 'PostgreSQL 16 installed and configured (role, database, schema migrations), a Python virtual environment created, and the SDK installed in editable mode with the dev and server extras.',
    detail: 'psql: agentguard / agentguard @ localhost:5432',
  },
  {
    cat: 'APPLICATION',
    title: 'App run end to end against real data',
    body: 'Three example agents were executed against the live database — a basic CONTINUE/STOP run, a retry & root-cause run, and a full rollback-and-recovery run with an intact audit chain. The dashboard was built and the API server started to serve it.',
    detail: 'uvicorn server.api:app --port 8000',
  },
  {
    cat: 'AUTHENTICATION',
    title: 'Demo login added',
    body: 'A one-click "Demo login" control was added below Sign in, backed by a real, fully-provisioned workspace so reviewers can get in without typing credentials.',
    detail: 'demo@agentguard.dev',
  },
  {
    cat: 'THEME',
    title: 'Dark mode',
    body: "A persisted dark/light toggle was added across the dashboard and the auth screens, defaulting to the operating system's preference and remembering the viewer's choice.",
  },
  {
    cat: 'UI/UX',
    title: 'Overview dashboard redesign',
    body: 'A full visual pass on the Overview page — refined sidebar and top bar, a unified metric strip with Reliability Score emphasized, quieter panels and tables, and deliberate empty states — with no change to navigation, metrics, or data.',
  },
  {
    cat: 'MOTION',
    title: 'Restrained motion pass',
    body: "A light animation layer was added on load — the page and its panels fade and settle in, metric values transition on change, and the sidebar's active state and button presses respond with subtle, fast feedback.",
  },
]

export default function WorkReport() {
  const rootRef = useRef(null)

  useLayoutEffect(() => {
    const ctx = gsap.context(() => {
      const tl = gsap.timeline({ defaults: { ease: 'power2.out' } })
      tl.from('.wr-title', { opacity: 0, y: 8, duration: 0.45 })
        .from('.wr-meta', { opacity: 0, y: 6, duration: 0.35 }, '-=0.25')
        .from('.wr-summary', { opacity: 0, y: 8, duration: 0.4 }, '-=0.15')
        .from('.wr-entry', { opacity: 0, y: 8, duration: 0.4, stagger: 0.05 }, '-=0.15')
        .from('.wr-footline', { opacity: 0, duration: 0.3 }, '-=0.1')
    }, rootRef)

    return () => ctx.revert()
  }, [])

  return (
    <div className="work-report" ref={rootRef}>
      <div className="wr-shell">
        <div className="wr-brand">
          <span className="wr-mark">AG</span>
          <span className="wr-brand-name">AgentGuard</span>
        </div>

        <h1 className="wr-title">Work Report</h1>
        <div className="wr-meta">
          <span>Sep 27, 2026</span>
          <span className="wr-sep">·</span>
          <span className="wr-who">@Dhruv</span>
        </div>

        <section className="wr-summary">
          <div className="wr-label">Summary</div>
          <p>
            Starting from an unrun repo, this session got the full AgentGuard stack working locally end to
            end &mdash; PostgreSQL installed and configured, the SDK and dashboard built, example agent runs
            executed against a real database, and the FastAPI server serving the React dashboard in Chrome.
            On top of that, three feature and design passes were added on top: a one-click demo login, a
            dark/light theme toggle, and two rounds of visual polish &mdash; a full design-system rework,
            then a restrained motion layer &mdash; on the Overview dashboard.
          </p>
        </section>

        <section className="wr-worklog">
          <div className="wr-label" style={{ marginBottom: 16 }}>Work completed</div>

          {ENTRIES.map((e) => (
            <div className="wr-entry" key={e.cat}>
              <div className="wr-cat">{e.cat}</div>
              <div className="wr-body">
                <h3>{e.title}</h3>
                <p>{e.body}</p>
                {e.detail && <div className="wr-detail">{e.detail}</div>}
              </div>
            </div>
          ))}

          <div className="wr-entry">
            <div className="wr-cat">ACCESS</div>
            <div className="wr-body">
              <h3>Current state</h3>
              <table className="wr-state-table">
                <tbody>
                  <tr><td>PostgreSQL 16</td><td><span className="wr-dot" />Running, schema migrated</td></tr>
                  <tr><td>API server</td><td><span className="wr-dot" />Running &mdash; <code>127.0.0.1:8000</code></td></tr>
                  <tr><td>Dashboard</td><td>Built, served at <code>127.0.0.1:8000</code></td></tr>
                  <tr><td>Demo login</td><td><code>demo@agentguard.dev</code> / <code>demopass123</code></td></tr>
                </tbody>
              </table>
            </div>
          </div>
        </section>

        <div className="wr-footline">Internal engineering report &middot; AgentGuard</div>
      </div>
    </div>
  )
}
