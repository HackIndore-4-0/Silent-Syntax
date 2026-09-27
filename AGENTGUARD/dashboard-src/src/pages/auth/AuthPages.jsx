import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api } from '../../api'
import { useAuth } from '../../auth/AuthContext'

function AuthCard({ title, subtitle, children }) {
  return (
    <div className="auth-shell">
      <div className="auth-card">
        <h1>{title}</h1>
        {subtitle && <div className="sub">{subtitle}</div>}
        {children}
      </div>
    </div>
  )
}

export function Login() {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState(null)
  const { refresh } = useAuth()
  const navigate = useNavigate()

  async function submit(e) {
    e.preventDefault()
    try {
      await api('/api/auth/login', { method: 'POST', body: JSON.stringify({ email, password }) })
      await refresh()
      navigate('/overview')
    } catch (err) {
      setError(err.message)
    }
  }

  return (
    <AuthCard title="Sign in" subtitle="AgentGuard reliability platform">
      {error && <div className="auth-error">{error}</div>}
      <form onSubmit={submit}>
        <div className="field"><label>Email</label><input type="email" autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} /></div>
        <div className="field"><label>Password</label><input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} /></div>
        <button className="primary" style={{ width: '100%' }} type="submit">Sign in</button>
      </form>
      <div className="auth-links">
        <Link to="/forgot-password">Forgot password?</Link>
        <Link to="/signup">Create account</Link>
      </div>
    </AuthCard>
  )
}

export function Signup() {
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState(null)
  const { refresh } = useAuth()
  const navigate = useNavigate()

  async function submit(e) {
    e.preventDefault()
    try {
      await api('/api/auth/signup', { method: 'POST', body: JSON.stringify({ name, email, password }) })
      await refresh()
      navigate('/overview')
    } catch (err) {
      setError(err.message)
    }
  }

  return (
    <AuthCard title="Create your account" subtitle="Start monitoring your agents">
      {error && <div className="auth-error">{error}</div>}
      <form onSubmit={submit}>
        <div className="field"><label>Name</label><input type="text" value={name} onChange={(e) => setName(e.target.value)} /></div>
        <div className="field"><label>Email</label><input type="email" autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} /></div>
        <div className="field"><label>Password</label><input type="password" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} /></div>
        <button className="primary" style={{ width: '100%' }} type="submit">Create account</button>
      </form>
      <div className="auth-links">
        <Link to="/login">Already have an account? Sign in</Link>
      </div>
    </AuthCard>
  )
}

export function ForgotPassword() {
  const [email, setEmail] = useState('')
  const [error, setError] = useState(null)
  const [info, setInfo] = useState(null)

  async function submit(e) {
    e.preventDefault()
    try {
      const result = await api('/api/auth/forgot-password', { method: 'POST', body: JSON.stringify({ email }) })
      setInfo(result)
      setError(null)
    } catch (err) {
      setError(err.message)
    }
  }

  return (
    <AuthCard title="Reset password" subtitle="We'll issue a reset token for this email.">
      {error && <div className="auth-error">{error}</div>}
      {info && (
        <div className="auth-info">
          {info.dev_reset_token ? (
            <>Dev-mode: no email service is configured in this environment. Reset link: <Link to={`/reset-password/${info.dev_reset_token}`}>reset password</Link></>
          ) : info.detail}
        </div>
      )}
      <form onSubmit={submit}>
        <div className="field"><label>Email</label><input type="email" value={email} onChange={(e) => setEmail(e.target.value)} /></div>
        <button className="primary" style={{ width: '100%' }} type="submit">Send reset token</button>
      </form>
      <div className="auth-links"><Link to="/login">Back to sign in</Link></div>
    </AuthCard>
  )
}

export function ResetPassword() {
  const { token } = useParams()
  const [newPassword, setNewPassword] = useState('')
  const [error, setError] = useState(null)
  const [done, setDone] = useState(false)

  async function submit(e) {
    e.preventDefault()
    try {
      await api('/api/auth/reset-password', { method: 'POST', body: JSON.stringify({ token, new_password: newPassword }) })
      setDone(true)
      setError(null)
    } catch (err) {
      setError(err.message)
    }
  }

  return (
    <AuthCard title="Set a new password" subtitle={<>Token: <code>{token.slice(0, 12)}…</code></>}>
      {error && <div className="auth-error">{error}</div>}
      {done ? (
        <div className="auth-success">Password updated. <Link to="/login">Sign in</Link></div>
      ) : (
        <form onSubmit={submit}>
          <div className="field"><label>New password</label><input type="password" value={newPassword} onChange={(e) => setNewPassword(e.target.value)} /></div>
          <button className="primary" style={{ width: '100%' }} type="submit">Set password</button>
        </form>
      )}
    </AuthCard>
  )
}
