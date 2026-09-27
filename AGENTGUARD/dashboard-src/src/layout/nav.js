export const NAV = [
  { group: null, items: [['overview', 'Overview', 'LayoutDashboard']] },
  { group: 'Observability', items: [
    ['runs', 'Runs / Traces', 'GitBranch'], ['latency', 'Latency', 'Timer'], ['tokens', 'Tokens & Cost', 'Coins'],
    ['errors', 'Errors', 'CircleAlert'], ['tools', 'Tools', 'Wrench'], ['models', 'Models', 'Cpu'],
  ] },
  { group: 'Reliability', items: [
    ['evaluations', 'Evaluations', 'ClipboardCheck'], ['risk', 'Risk & Confidence', 'ShieldCheck'], ['behavior', 'Agent Behavior', 'Bot'],
  ] },
  { group: 'Recovery', items: [
    ['interventions', 'Interventions', 'Zap'], ['checkpoints', 'Checkpoints / Rollback', 'RotateCcw'], ['replay-compare', 'Replay / Compare', 'GitCompare'],
  ] },
  { group: 'Governance', items: [['policies', 'Policy Management', 'FileCheck'], ['audit', 'Audit', 'FileText']] },
  { group: 'Improvement', items: [
    ['problems', 'Problems', 'Bug'], ['failure-patterns', 'Failure Patterns', 'TrendingDown'], ['recommendations', 'Recommendations', 'Sparkles'],
  ] },
  { group: 'Evaluation Platform', items: [
    ['eval-runs', 'Eval Runs', 'PlayCircle'], ['datasets', 'Datasets', 'Database'], ['suites', 'Suites', 'FolderKanban'],
    ['benchmarks', 'Model Benchmarks', 'BarChart2'], ['eval-recommendations', 'Eval Recommendations', 'Lightbulb'],
  ] },
  { group: 'Settings', items: [
    ['settings/api-keys', 'API Keys', 'KeyRound'], ['settings/profile', 'Profile', 'UserRound'], ['settings/team', 'Team / Users', 'Users'],
  ] },
  { group: 'Skills', items: [['skills', 'Skills', 'Terminal']] },
]

// Which NAV group (if any) owns the current route — drives which
// sidebar drill-down section should be showing for a given pathname
// (covers child routes like /runs/:id, /datasets/:id/versions/:v, …).
export function findGroupForPath(pathname) {
  const clean = pathname.replace(/^\//, '')
  for (const section of NAV) {
    if (!section.group) continue
    for (const [path] of section.items) {
      if (clean === path || clean.startsWith(`${path}/`)) return section.group
    }
  }
  return null
}
