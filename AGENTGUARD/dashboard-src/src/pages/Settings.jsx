import { useState } from 'react'
import { api } from '../api'
import { useAuth } from '../auth/AuthContext'
import { Badge, EmptyState, ErrorState, KvRow, LoadingState, Panel, useApiData } from '../components/ui'
import { fmtDate } from '../format'

export function ApiKeys() {
  const [refreshKey, setRefreshKey] = useState(0)
  const { loading, error, data: keys } = useApiData('/api/settings/api-keys', [refreshKey])
  const [name, setName] = useState('')
  const [message, setMessage] = useState(null)

  async function create() {
    if (!name.trim()) return
    const result = await api('/api/settings/api-keys', { method: 'POST', body: JSON.stringify({ name: name.trim() }) })
    setName('')
    setRefreshKey((k) => k + 1)
    setMessage({ tone: 'ok', text: <>Copy this key now — it will not be shown again:<br /><code>{result.raw_key}</code></> })
  }

  async function revoke(id) {
    await api(`/api/settings/api-keys/${id}/revoke`, { method: 'POST' })
    setRefreshKey((k) => k + 1)
  }

  async function rotate(id) {
    const result = await api(`/api/settings/api-keys/${id}/rotate`, { method: 'POST' })
    setRefreshKey((k) => k + 1)
    setMessage({ tone: 'ok', text: <>Rotated. New key (copy now): <code>{result.raw_key}</code></> })
  }

  if (loading) return (<><h1 className="page-title">API Keys</h1><LoadingState /></>)
  if (error) return <ErrorState error={error} />

  return (
    <>
      <h1 className="page-title">API Keys</h1>
      <div className="page-subtitle">Server-side agent code authenticates with <code>Authorization: Bearer &lt;API_KEY&gt;</code>. Never embed a key in frontend JavaScript.</div>
      <Panel>
        <table>
          <thead><tr><th>Name</th><th>Key</th><th>Created</th><th>Last used</th><th>Status</th><th></th></tr></thead>
          <tbody>
            {keys.length ? keys.map((k) => (
              <tr key={k.id}>
                <td>{k.name}</td><td className="mono">{k.key_prefix}…</td><td>{fmtDate(k.created_at)}</td>
                <td>{fmtDate(k.last_used_at)}</td><td><Badge status={k.status} /></td>
                <td>{k.status === 'active' && <><button onClick={() => revoke(k.id)}>Revoke</button> <button onClick={() => rotate(k.id)}>Rotate</button></>}</td>
              </tr>
            )) : <tr><td colSpan={6}><EmptyState>No API keys yet.</EmptyState></td></tr>}
          </tbody>
        </table>
      </Panel>
      <Panel header="Create API Key" style={{ marginTop: 14 }}>
        <div className="filters-row">
          <input type="text" placeholder="Key name (e.g. production-server)" value={name} onChange={(e) => setName(e.target.value)} />
          <button className="primary" onClick={create}>Create API Key</button>
        </div>
        {message && <div className={`auth-${message.tone === 'ok' ? 'success' : 'error'}`}>{message.text}</div>}
      </Panel>
    </>
  )
}

export function Profile() {
  const { user, workspaces } = useAuth()
  return (
    <>
      <h1 className="page-title">Profile</h1>
      <Panel>
        <KvRow label="Name">{user.name}</KvRow>
        <KvRow label="Email">{user.email}</KvRow>
        <KvRow label="Account created">{fmtDate(user.created_at)}</KvRow>
      </Panel>
      <Panel header="Workspaces" style={{ marginTop: 14 }}>
        {workspaces.map((w) => <KvRow label={w.name} key={w.id}>{w.role}</KvRow>)}
      </Panel>
    </>
  )
}

export function Team() {
  const [refreshKey, setRefreshKey] = useState(0)
  const { loading, error, data: members } = useApiData('/api/workspace/members', [refreshKey])
  const [email, setEmail] = useState('')
  const [inviteError, setInviteError] = useState(null)

  async function invite() {
    setInviteError(null)
    try {
      await api('/api/workspace/members', { method: 'POST', body: JSON.stringify({ email: email.trim() }) })
      setEmail('')
      setRefreshKey((k) => k + 1)
    } catch (e) { setInviteError(e.message) }
  }

  if (loading) return (<><h1 className="page-title">Team / Users</h1><LoadingState /></>)
  if (error) return <ErrorState error={error} />

  return (
    <>
      <h1 className="page-title">Team / Users</h1>
      <Panel>
        <table>
          <thead><tr><th>Name</th><th>Email</th><th>Role</th></tr></thead>
          <tbody>{members.map((m, i) => <tr key={i}><td>{m.name}</td><td>{m.email}</td><td>{m.role}</td></tr>)}</tbody>
        </table>
      </Panel>
      <Panel header="Invite a member" style={{ marginTop: 14 }}>
        <div className="page-subtitle">This environment has no outbound email — inviting only works for an email that already has an AgentGuard account.</div>
        <div className="filters-row">
          <input type="text" placeholder="Email" value={email} onChange={(e) => setEmail(e.target.value)} />
          <button className="primary" onClick={invite}>Invite</button>
        </div>
        {inviteError && <ErrorState error={{ message: inviteError }} />}
      </Panel>
    </>
  )
}
