export const NAV = [
  { group: null, items: [['overview', 'Overview', 'o']] },
  { group: 'Observability', items: [
    ['runs', 'Runs / Traces', 'r'], ['latency', 'Latency', 'l'], ['tokens', 'Tokens & Cost', 't'],
    ['errors', 'Errors', 'e'], ['tools', 'Tools', 'T'], ['models', 'Models', 'M'],
  ] },
  { group: 'Reliability', items: [
    ['evaluations', 'Evaluations', 'E'], ['risk', 'Risk & Confidence', 'R'], ['behavior', 'Agent Behavior', 'b'],
  ] },
  { group: 'Recovery', items: [
    ['interventions', 'Interventions', 'i'], ['checkpoints', 'Checkpoints / Rollback', 'c'], ['replay-compare', 'Replay / Compare', 'p'],
  ] },
  { group: 'Governance', items: [['policies', 'Policy Management', 'P'], ['audit', 'Audit', 'A']] },
  { group: 'Improvement', items: [
    ['problems', 'Problems', 'P'], ['failure-patterns', 'Failure Patterns', 'f'], ['recommendations', 'Recommendations', 'S'],
  ] },
  { group: 'Evaluation Platform', items: [
    ['eval-runs', 'Eval Runs', 'V'], ['datasets', 'Datasets', 'D'], ['suites', 'Suites', 'U'],
    ['benchmarks', 'Model Benchmarks', 'B'], ['eval-recommendations', 'Eval Recommendations', 'N'],
  ] },
  { group: 'Settings', items: [
    ['settings/api-keys', 'API Keys', 'k'], ['settings/profile', 'Profile', 'u'], ['settings/team', 'Team / Users', 'g'],
  ] },
]
