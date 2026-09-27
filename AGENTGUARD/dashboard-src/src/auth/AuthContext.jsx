import { createContext, useCallback, useContext, useEffect, useState } from 'react'
import { api } from '../api'

const AuthContext = createContext(null)

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null)
  const [workspaces, setWorkspaces] = useState([])
  const [status, setStatus] = useState('loading') // loading | authenticated | anonymous

  const refresh = useCallback(async () => {
    try {
      const me = await api('/api/auth/me')
      setUser(me.user)
      setWorkspaces(me.workspaces)
      setStatus('authenticated')
      return true
    } catch {
      setUser(null)
      setWorkspaces([])
      setStatus('anonymous')
      return false
    }
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  const logout = useCallback(async () => {
    try {
      await api('/api/auth/logout', { method: 'POST' })
    } catch {
      // ignore
    }
    setUser(null)
    setWorkspaces([])
    setStatus('anonymous')
  }, [])

  return (
    <AuthContext.Provider value={{ user, workspaces, status, refresh, logout }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  return useContext(AuthContext)
}
