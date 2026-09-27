import { createHashRouter, Navigate, RouterProvider } from 'react-router-dom'
import { AuthProvider } from './auth/AuthContext'
import ProtectedLayout from './layout/ProtectedLayout'
import { shortId } from './format'

import { ForgotPassword, Login, ResetPassword, Signup } from './pages/auth/AuthPages'
import WorkReport from './pages/WorkReport'
import LandingPage from './pages/LandingPage'
import Overview from './pages/Overview'
import RunsList from './pages/RunsList'
import RunDetail from './pages/RunDetail'
import { Errors, Latency, Models, Tokens, Tools } from './pages/Observability'
import { Behavior, Evaluations, Risk } from './pages/Reliability'
import { Checkpoints, Interventions, ReplayCompare } from './pages/Recovery'
import { Audit, Policies } from './pages/Governance'
import { FailurePatterns, Problems, Recommendations } from './pages/Improvement'
import {
  Benchmarks, BenchmarkDetail, Datasets, DatasetQuality, EvalRecommendations,
  EvalRunDetail, EvalRuns, Suites,
} from './pages/EvalPlatform'
import { ApiKeys, Profile, Team } from './pages/Settings'
import Skills from './pages/Skills'

const crumb = (label, to) => ({ label, to })

const router = createHashRouter([
  { path: '/login', element: <Login /> },
  { path: '/signup', element: <Signup /> },
  { path: '/forgot-password', element: <ForgotPassword /> },
  { path: '/reset-password/:token', element: <ResetPassword /> },
  { path: '/work-report', element: <WorkReport /> },
  { path: '/welcome', element: <LandingPage /> },
  {
    path: '/',
    element: <ProtectedLayout />,
    children: [
      { index: true, element: <Navigate to="/overview" replace /> },
      { path: 'overview', element: <Overview />, handle: { crumbs: () => [crumb('Overview')] } },
      { path: 'runs', element: <RunsList />, handle: { crumbs: () => [crumb('Runs / Traces')] } },
      { path: 'runs/:runId', element: <RunDetail />, handle: { crumbs: (p) => [crumb('Runs / Traces', '/runs'), crumb(shortId(p.runId))] } },
      { path: 'latency', element: <Latency />, handle: { crumbs: () => [crumb('Latency')] } },
      { path: 'tokens', element: <Tokens />, handle: { crumbs: () => [crumb('Tokens & Cost')] } },
      { path: 'errors', element: <Errors />, handle: { crumbs: () => [crumb('Errors')] } },
      { path: 'tools', element: <Tools />, handle: { crumbs: () => [crumb('Tools')] } },
      { path: 'models', element: <Models />, handle: { crumbs: () => [crumb('Models')] } },
      { path: 'evaluations', element: <Evaluations />, handle: { crumbs: () => [crumb('Evaluations')] } },
      { path: 'risk', element: <Risk />, handle: { crumbs: () => [crumb('Risk & Confidence')] } },
      { path: 'behavior', element: <Behavior />, handle: { crumbs: () => [crumb('Agent Behavior')] } },
      { path: 'interventions', element: <Interventions />, handle: { crumbs: () => [crumb('Recovery', '/interventions'), crumb('Interventions')] } },
      { path: 'checkpoints', element: <Checkpoints />, handle: { crumbs: () => [crumb('Recovery', '/checkpoints'), crumb('Checkpoints / Rollback')] } },
      { path: 'replay-compare', element: <ReplayCompare />, handle: { crumbs: () => [crumb('Recovery', '/replay-compare'), crumb('Replay / Compare')] } },
      { path: 'policies', element: <Policies />, handle: { crumbs: () => [crumb('Policy Management')] } },
      { path: 'audit', element: <Audit />, handle: { crumbs: () => [crumb('Audit')] } },
      { path: 'failure-patterns', element: <FailurePatterns />, handle: { crumbs: () => [crumb('Improvement', '/failure-patterns'), crumb('Failure Patterns')] } },
      { path: 'recommendations', element: <Recommendations />, handle: { crumbs: () => [crumb('Improvement', '/recommendations'), crumb('Recommendations')] } },
      { path: 'problems', element: <Problems />, handle: { crumbs: () => [crumb('Improvement', '/problems'), crumb('Problems')] } },
      { path: 'eval-runs', element: <EvalRuns />, handle: { crumbs: () => [crumb('Eval Runs')] } },
      { path: 'eval-runs/:evaluationRunId', element: <EvalRunDetail />, handle: { crumbs: (p) => [crumb('Eval Runs', '/eval-runs'), crumb(shortId(p.evaluationRunId))] } },
      { path: 'datasets', element: <Datasets />, handle: { crumbs: () => [crumb('Datasets')] } },
      { path: 'datasets/:datasetId/versions/:version', element: <DatasetQuality />, handle: { crumbs: (p) => [crumb('Datasets', '/datasets'), crumb(`v${p.version}`)] } },
      { path: 'suites', element: <Suites />, handle: { crumbs: () => [crumb('Evaluation Suites')] } },
      { path: 'benchmarks', element: <Benchmarks />, handle: { crumbs: () => [crumb('Model Benchmarks')] } },
      { path: 'benchmarks/:benchmarkId', element: <BenchmarkDetail />, handle: { crumbs: (p) => [crumb('Model Benchmarks', '/benchmarks'), crumb(shortId(p.benchmarkId))] } },
      { path: 'eval-recommendations', element: <EvalRecommendations />, handle: { crumbs: () => [crumb('Evaluation Recommendations')] } },
      { path: 'settings/api-keys', element: <ApiKeys />, handle: { crumbs: () => [crumb('Settings', '/settings/api-keys'), crumb('API Keys')] } },
      { path: 'settings/profile', element: <Profile />, handle: { crumbs: () => [crumb('Settings', '/settings/profile'), crumb('Profile')] } },
      { path: 'settings/team', element: <Team />, handle: { crumbs: () => [crumb('Settings', '/settings/team'), crumb('Team / Users')] } },
      { path: 'skills', element: <Skills />, handle: { crumbs: () => [crumb('Skills')] } },
    ],
  },
  { path: '*', element: <Navigate to="/overview" replace /> },
])

export default function App() {
  return (
    <AuthProvider>
      <RouterProvider router={router} />
    </AuthProvider>
  )
}
