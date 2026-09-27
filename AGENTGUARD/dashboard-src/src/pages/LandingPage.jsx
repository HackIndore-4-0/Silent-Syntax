import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  Target, Bot, Wrench, GitBranch, ClipboardCheck, RotateCcw,
  Eye, CircleAlert, TrendingUp, ShieldCheck, LayoutDashboard,
  ArrowRight, FileCheck, Sparkles, CheckCircle2, TriangleAlert,
} from 'lucide-react'
import badgeLight from '../layout/assets/brand-badge-light.png'
import '../landing.css'

// The landing page is always dark, regardless of the app's stored
// theme preference, so it always uses the light (white-tile) badge —
// the one with contrast against a dark surface.
function LandingBrand({ size = 32 }) {
  return <img src={badgeLight} width={size} height={size} alt="AgentGuard" draggable={false} />
}

// Adds an 'in-view' class the first time an element crosses the
// viewport threshold — IntersectionObserver, not a JS animation
// ticker, so the reveal fires reliably regardless of tab focus/rAF
// throttling. CSS (.lp-reveal / .in-view) owns the actual transition.
function useReveal() {
  const ref = useRef(null)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const items = el.querySelectorAll('.lp-reveal')
    const io = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          entry.target.classList.add('in-view')
          io.unobserve(entry.target)
        }
      })
    }, { threshold: 0.15 })
    items.forEach((item) => io.observe(item))
    return () => io.disconnect()
  }, [])
  return ref
}

const NAV_LINKS = [
  ['product', 'Product'],
  ['how-it-works', 'How It Works'],
  ['capabilities', 'Capabilities'],
  ['architecture', 'Architecture'],
  ['use-cases', 'Use Cases'],
]

const PILLARS = [
  [Eye, 'Observe', 'Every agent action, tool call, and model response is captured as it happens — no black box.'],
  [ClipboardCheck, 'Evaluate', 'Constraint checks and LLM-as-judge scoring turn each step into a pass/fail with a confidence score.'],
  [CircleAlert, 'Detect', 'A deterministic risk score and root-cause diff over state history catch drift before it compounds.'],
  [RotateCcw, 'Recover', 'Checkpointed rollback and counterfactual replay let a run resume from a known-good state.'],
  [TrendingUp, 'Improve', 'Root causes become structured, human-approved recommendations — nothing auto-deploys.'],
]

const CAPABILITIES = [
  [GitBranch, 'Observability', 'Full trace of runs, latency, tokens, cost, tools and errors across every agent.'],
  [ShieldCheck, 'Reliability', 'Evaluations, risk scoring, and behavioral fingerprinting against a live baseline.'],
  [RotateCcw, 'Recovery', 'Hash-verified checkpoints, rollback, and actual-vs-counterfactual comparison.'],
  [FileCheck, 'Governance', 'Declarative policies and a SHA-256 hash-chained, independently verifiable audit trail.'],
  [Sparkles, 'Improvement', 'Failure patterns become reviewed, approved-or-rejected improvement candidates.'],
  [LayoutDashboard, 'Evaluation Platform', 'Golden-dataset validation, model benchmarking, and prompt-failure diagnosis.'],
]

const USE_CASES = [
  ['Customer-facing agents', 'Keep support and sales agents inside policy, with human escalation on genuine uncertainty.'],
  ['Ops & workflow automation', 'Catch a flaky tool or a silently dropped constraint before it becomes an incident.'],
  ['Multi-agent systems', 'One audit trail and one risk model across every agent in the pipeline.'],
  ['Regulated environments', 'A tamper-evident record of every decision, checkable independently of AgentGuard itself.'],
]

const LIFECYCLE = ['Goal', 'Plan', 'Tool', 'Decision', 'Action', 'Outcome']

const EXECUTION_LOG = [
  ['Goal received', 'ok'],
  ['Planning completed', 'ok'],
  ['Tool call: search_web', 'ok'],
  ['Policy check', 'ok'],
  ['Risk detected: drift', 'warn'],
  ['Recovery initiated', 'ok'],
]

function ScrollNav() {
  const [scrolled, setScrolled] = useState(false)
  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 12)
    onScroll()
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => window.removeEventListener('scroll', onScroll)
  }, [])
  return (
    <header className={`lp-nav${scrolled ? ' lp-nav-scrolled' : ''}`}>
      <div className="lp-nav-inner">
        <div className="lp-brand">
          <LandingBrand size={32} />
          <span>AGENTGUARD</span>
        </div>
        <nav className="lp-nav-links">
          {NAV_LINKS.map(([id, label]) => (
            <a key={id} href={`#${id}`}>{label}</a>
          ))}
        </nav>
        <div className="lp-nav-actions">
          <Link to="/login" className="lp-link-btn">Log in</Link>
          <Link to="/signup" className="lp-btn lp-btn-primary">
            Get Started <ArrowRight size={14} strokeWidth={2.25} />
          </Link>
        </div>
      </div>
    </header>
  )
}

function ExecutionMonitor() {
  return (
    <div className="lp-monitor-wrap">
      <div className="lp-monitor-node lp-monitor-node-top">
        <Bot size={16} strokeWidth={1.75} />
        <span>AI AGENT</span>
        <em><span className="lp-dot lp-dot-live" /> RUNNING</em>
      </div>
      <div className="lp-monitor-link lp-monitor-link-top"><span className="lp-flow-v" /></div>

      <div className="lp-monitor">
        <div className="lp-monitor-bar">
          <span>AGENTGUARD / LIVE EXECUTION</span>
          <span className="lp-live-badge"><span className="lp-dot lp-dot-live" /> LIVE</span>
        </div>
        <div className="lp-monitor-body">
          <div className="lp-monitor-run">
            <span className="lp-mono">RUN #AG-28491</span>
            <span className="lp-monitor-agent">ResearchAgent</span>
          </div>
          <ul className="lp-monitor-log">
            {EXECUTION_LOG.map(([label, kind], i) => (
              <li key={label} className="reveal" style={{ '--reveal-delay': `${0.4 + i * 0.12}s` }}>
                {kind === 'ok'
                  ? <CheckCircle2 size={14} strokeWidth={2} className="lp-log-ok" />
                  : <TriangleAlert size={14} strokeWidth={2} className="lp-log-warn" />}
                {label}
              </li>
            ))}
          </ul>
          <div className="lp-monitor-stats">
            <div><span>Reliability</span><b className="lp-stat-ok">94%</b></div>
            <div><span>Risk</span><b className="lp-stat-ok">LOW</b></div>
            <div><span>Status</span><b className="lp-stat-accent">RECOVERED</b></div>
          </div>
        </div>
      </div>

      <div className="lp-monitor-link lp-monitor-link-bottom"><span className="lp-flow-v" /></div>
      <div className="lp-monitor-node lp-monitor-node-bottom">
        <RotateCcw size={16} strokeWidth={1.75} />
        <span>RECOVERY</span>
        <em><CheckCircle2 size={12} strokeWidth={2.25} /> SAFE STATE</em>
      </div>
    </div>
  )
}

export default function LandingPage() {
  const productRef = useReveal()
  const capsRef = useReveal()
  const archRef = useReveal()
  const casesRef = useReveal()
  const transitionRef = useReveal()

  return (
    <div className="landing">
      <ScrollNav />

      <section className="lp-hero">
        <div className="lp-hero-bg" aria-hidden="true" />
        <div className="lp-hero-inner">
          <div className="lp-hero-copy">
            <div className="lp-eyebrow reveal" style={{ '--reveal-delay': '0s' }}>RELIABILITY INFRASTRUCTURE FOR AUTONOMOUS AI</div>
            <h1 className="lp-headline reveal" style={{ '--reveal-delay': '.08s' }}>
              Make Autonomous AI<br />
              <span className="lp-accent-word">Reliable</span>, <span className="lp-accent-word">Observable</span>,<br />
              and <span className="lp-accent-word">Auditable</span>.
            </h1>
            <p className="lp-sub reveal" style={{ '--reveal-delay': '.16s' }}>
              AgentGuard continuously observes agent decisions, evaluates behavior, detects failures,
              and enables recovery — turning autonomous AI from a black box into a system you can trust.
            </p>
            <div className="lp-cta-row">
              <a href="#product" className="lp-btn lp-btn-primary lp-cta reveal" style={{ '--reveal-delay': '.24s' }}>
                Explore AgentGuard <ArrowRight size={15} strokeWidth={2} />
              </a>
              <a href="#architecture" className="lp-btn lp-btn-ghost lp-cta reveal" style={{ '--reveal-delay': '.29s' }}>View Architecture</a>
            </div>
            <div className="lp-pipeline-strip reveal" style={{ '--reveal-delay': '.36s' }}>
              Observe → Evaluate → Detect → Intervene → Recover → Improve
            </div>
          </div>

          <div className="lp-hero-visual reveal" style={{ '--reveal-delay': '.3s' }} id="how-it-works">
            <div className="lp-system-status"><span className="lp-dot lp-dot-live" /> AGENTGUARD ACTIVE</div>
            <ExecutionMonitor />
          </div>
        </div>
      </section>

      <section className="lp-transition" ref={transitionRef}>
        <div className="lp-section-inner">
          <p className="lp-transition-lead lp-reveal">
            Autonomous AI is powerful. But autonomy without observability is a blind spot.
          </p>
          <div className="lp-lifecycle lp-reveal">
            {LIFECYCLE.map((step, i) => (
              <div className="lp-lifecycle-item" key={step}>
                <div className="lp-lifecycle-node">{step}</div>
                {i < LIFECYCLE.length - 1 && <div className="lp-lifecycle-link"><span className="lp-flow" /></div>}
              </div>
            ))}
          </div>
          <div className="lp-lifecycle-wrap">AgentGuard observes and governs every stage of this loop</div>
        </div>
      </section>

      <section className="lp-section" id="product" ref={productRef}>
        <div className="lp-section-inner">
          <div className="lp-section-head lp-reveal">
            <div className="lp-kicker">THE FIVE-STAGE LOOP</div>
            <h2>A runtime layer between your agent and the outside world</h2>
          </div>
          <div className="lp-pillars">
            {PILLARS.map(([Icon, title, body], i) => (
              <div className="lp-pillar lp-reveal" style={{ '--reveal-delay': `${i * 0.06}s` }} key={title}>
                <Icon size={20} strokeWidth={1.6} />
                <h3>{title}</h3>
                <p>{body}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section className="lp-section lp-section-alt" id="capabilities" ref={capsRef}>
        <div className="lp-section-inner">
          <div className="lp-section-head lp-reveal">
            <div className="lp-kicker">CAPABILITIES</div>
            <h2>Everything a governance layer for agents needs</h2>
          </div>
          <div className="lp-cap-grid">
            {CAPABILITIES.map(([Icon, title, body], i) => (
              <div className="lp-cap-card lp-reveal" style={{ '--reveal-delay': `${i * 0.05}s` }} key={title}>
                <Icon size={19} strokeWidth={1.6} />
                <h3>{title}</h3>
                <p>{body}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section className="lp-section" id="architecture" ref={archRef}>
        <div className="lp-section-inner lp-arch">
          <div className="lp-section-head lp-reveal">
            <div className="lp-kicker">ARCHITECTURE</div>
            <h2>Documented mechanics, not a black box</h2>
          </div>
          <div className="lp-arch-grid">
            <div className="lp-arch-card lp-reveal">
              <div className="lp-arch-label">Decision engine</div>
              <code>RUNNING → EVALUATING → CONTINUE | RETRY | REPLAN | HUMAN | STOP</code>
            </div>
            <div className="lp-arch-card lp-reveal" style={{ '--reveal-delay': '.06s' }}>
              <div className="lp-arch-label">Risk score</div>
              <code>0.45·impact + 0.30·policy + 0.15·uncertainty + 0.05·drift + 0.05·tool</code>
            </div>
            <div className="lp-arch-card lp-reveal" style={{ '--reveal-delay': '.12s' }}>
              <div className="lp-arch-label">Audit chain</div>
              <code>event_hash = SHA256(canonical_json(payload) + previous_hash)</code>
            </div>
          </div>
        </div>
      </section>

      <section className="lp-section lp-section-alt" id="use-cases" ref={casesRef}>
        <div className="lp-section-inner">
          <div className="lp-section-head lp-reveal">
            <div className="lp-kicker">USE CASES</div>
            <h2>Wherever an agent acts without a human in the loop</h2>
          </div>
          <div className="lp-usecase-grid">
            {USE_CASES.map(([title, body], i) => (
              <div className="lp-usecase-card lp-reveal" style={{ '--reveal-delay': `${i * 0.05}s` }} key={title}>
                <h3>{title}</h3>
                <p>{body}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section className="lp-final-cta">
        <div className="lp-section-inner lp-final-cta-inner">
          <h2>Give your agents a runtime you can audit.</h2>
          <div className="lp-cta-row">
            <Link to="/signup" className="lp-btn lp-btn-primary">Get Started</Link>
            <Link to="/login" className="lp-btn lp-btn-ghost">Log in</Link>
          </div>
        </div>
      </section>

      <footer className="lp-footer">
        <div className="lp-section-inner lp-footer-inner">
          <div className="lp-brand"><LandingBrand size={22} /><span>AGENTGUARD</span></div>
          <nav className="lp-nav-links">
            {NAV_LINKS.map(([id, label]) => (
              <a key={id} href={`#${id}`}>{label}</a>
            ))}
          </nav>
          <div className="lp-footer-meta">© {new Date().getFullYear()} AgentGuard</div>
        </div>
      </footer>
    </div>
  )
}
