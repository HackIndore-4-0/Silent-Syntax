import { useState } from 'react'
import { NavLink, Outlet } from 'react-router-dom'
import { useAuth } from '../auth/AuthContext'
import { NAV } from './nav'
import { Breadcrumbs } from './Breadcrumbs'

export default function Layout() {
  const { user, logout } = useAuth()
  const [collapsed, setCollapsed] = useState(false)
  const initials = (user?.name || '?').split(' ').map((s) => s[0]).join('').slice(0, 2).toUpperCase()

  return (
    <>
      <div id="sidebar" className={collapsed ? 'collapsed' : ''}>
        <div className="brand">
          <span className="brand-mark">AG</span>
          <span className="brand-text">AGENTGUARD</span>
        </div>
        {NAV.map((section, i) => (
          <div className="nav-group" key={i}>
            {section.group && <div className="nav-group-title">{section.group}</div>}
            {section.items.map(([path, label, icon]) => (
              <NavLink
                key={path}
                to={`/${path}`}
                className={({ isActive }) => `nav-item${isActive ? ' active' : ''}`}
              >
                <span className="nav-icon">{icon}</span>
                <span className="nav-label">{label}</span>
              </NavLink>
            ))}
          </div>
        ))}
        <button id="sidebar-toggle" onClick={() => setCollapsed((c) => !c)}>{'⟨⟩'}</button>
      </div>
      <div id="main-col">
        <div id="topbar">
          <Breadcrumbs />
          <div id="topbar-right">
            <div id="user-menu">
              <div className="user-avatar">{initials}</div>
              <span>{user?.name}</span>
              <button onClick={logout}>Log out</button>
            </div>
          </div>
        </div>
        <div id="content">
          <Outlet />
        </div>
      </div>
    </>
  )
}
