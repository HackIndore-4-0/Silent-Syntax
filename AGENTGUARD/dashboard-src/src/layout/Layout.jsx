import { useLayoutEffect, useRef, useState } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'
import gsap from 'gsap'
import { PanelLeft, Sun, Moon, LogOut, ChevronLeft, ChevronRight } from 'lucide-react'
import { useAuth } from '../auth/AuthContext'
import { NAV, findGroupForPath } from './nav'
import { Breadcrumbs } from './Breadcrumbs'
import { useTheme } from '../theme'
import { NavIcon } from './icons'
import { BrandMark } from './BrandMark'

const TOP_ITEM = NAV[0].items[0] // Overview — the only route outside a drill-down group
const GROUPS = NAV.slice(1) // each of the rest is a Level-1 drill-down row

export default function Layout() {
  const { user, logout } = useAuth()
  const location = useLocation()
  const [collapsed, setCollapsed] = useState(false)
  const { theme, toggleTheme } = useTheme()
  const initials = (user?.name || '?').split(' ').map((s) => s[0]).join('').slice(0, 2).toUpperCase()

  // openGroup = the drill-down section currently showing (null = main
  // Level-1 list). Seeded from the route so a hard refresh on a child
  // page (e.g. /latency) lands straight in its section, not Level 1.
  const [openGroup, setOpenGroup] = useState(() => findGroupForPath(location.pathname))
  // Section panel keeps rendering the last-opened group's items while it
  // slides out, so content doesn't disappear mid-transition.
  const lastGroupRef = useRef(openGroup)
  if (openGroup) lastGroupRef.current = openGroup
  const sectionGroup = GROUPS.find((g) => g.group === (openGroup || lastGroupRef.current))

  const mainRef = useRef(null)
  const sectionRef = useRef(null)
  const isFirstLayout = useRef(true)
  const prevPathname = useRef(location.pathname)

  // Route-driven sync: only re-derives openGroup when the ROUTE actually
  // changes to a different section (browser back/forward, a link from
  // elsewhere in the app, …). A manual tap on the back control changes
  // openGroup without touching the route, so this never fights it.
  useLayoutEffect(() => {
    if (location.pathname === prevPathname.current) return
    prevPathname.current = location.pathname
    const resolved = findGroupForPath(location.pathname)
    setOpenGroup((current) => (resolved !== current ? resolved : current))
  }, [location.pathname])

  useLayoutEffect(() => {
    if (!mainRef.current || !sectionRef.current) return

    if (isFirstLayout.current) {
      isFirstLayout.current = false
      gsap.set(mainRef.current, { xPercent: openGroup ? -100 : 0 })
      gsap.set(sectionRef.current, { xPercent: openGroup ? 0 : 100 })
      return
    }

    const ctx = gsap.context(() => {
      if (openGroup) {
        gsap.to(mainRef.current, { xPercent: -100, duration: 0.2, ease: 'power2.out' })
        gsap.to(sectionRef.current, { xPercent: 0, duration: 0.24, ease: 'power2.out' })
        gsap.from(sectionRef.current.querySelectorAll('.nav-item'), {
          opacity: 0, y: 6, duration: 0.18, stagger: 0.03, ease: 'power2.out', delay: 0.1,
        })
      } else {
        gsap.to(sectionRef.current, { xPercent: 100, duration: 0.2, ease: 'power2.out' })
        gsap.to(mainRef.current, { xPercent: 0, duration: 0.22, ease: 'power2.out' })
      }
    })
    return () => ctx.revert()
  }, [openGroup])

  return (
    <>
      <div id="sidebar" className={collapsed ? 'collapsed' : ''}>
        <div className="brand">
          <span className="brand-mark"><BrandMark /></span>
          <span className="brand-text">AGENTGUARD</span>
        </div>

        <div className="nav-levels">
          <div className="nav-level" ref={mainRef}>
            <div className="nav-group">
              <NavLink
                to={`/${TOP_ITEM[0]}`}
                className={({ isActive }) => `nav-item${isActive ? ' active' : ''}`}
              >
                <span className="nav-icon"><NavIcon name={TOP_ITEM[2]} /></span>
                <span className="nav-label">{TOP_ITEM[1]}</span>
              </NavLink>
            </div>
            <div className="nav-group">
              {GROUPS.map((section) => (
                <button
                  key={section.group}
                  type="button"
                  className={`nav-item nav-group-row${findGroupForPath(location.pathname) === section.group ? ' active' : ''}`}
                  onClick={() => setOpenGroup(section.group)}
                >
                  <span className="nav-icon"><NavIcon name={section.items[0][2]} /></span>
                  <span className="nav-label">{section.group}</span>
                  <ChevronRight className="nav-chevron" size={15} strokeWidth={1.75} />
                </button>
              ))}
            </div>
          </div>

          <div className="nav-level nav-level-section" ref={sectionRef}>
            {sectionGroup && (
              <div className="nav-group">
                <button type="button" className="nav-section-header" onClick={() => setOpenGroup(null)}>
                  <ChevronLeft size={16} strokeWidth={1.75} />
                  <span>{sectionGroup.group}</span>
                </button>
                {sectionGroup.items.map(([path, label, icon]) => (
                  <NavLink
                    key={path}
                    to={`/${path}`}
                    className={({ isActive }) => `nav-item${isActive ? ' active' : ''}`}
                  >
                    <span className="nav-icon"><NavIcon name={icon} /></span>
                    <span className="nav-label">{label}</span>
                  </NavLink>
                ))}
              </div>
            )}
          </div>
        </div>

        <button id="sidebar-toggle" onClick={() => setCollapsed((c) => !c)} title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}>
          <PanelLeft size={15} strokeWidth={1.75} />
        </button>
      </div>
      <div id="main-col">
        <div id="topbar">
          <Breadcrumbs />
          <div id="topbar-right">
            <button
              id="theme-toggle"
              onClick={toggleTheme}
              title={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
            >
              {theme === 'dark' ? <Sun size={18} strokeWidth={1.75} /> : <Moon size={18} strokeWidth={1.75} />}
            </button>
            <div id="user-menu">
              <div className="user-avatar">{initials}</div>
              <span>{user?.name}</span>
              <button onClick={logout}><LogOut size={14} strokeWidth={1.75} /> Log out</button>
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
