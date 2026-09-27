import { Navigate } from 'react-router-dom'
import { useAuth } from '../auth/AuthContext'
import Layout from './Layout'

export default function ProtectedLayout() {
  const { status } = useAuth()
  if (status === 'loading') return null
  if (status === 'anonymous') return <Navigate to="/login" replace />
  return <Layout />
}
