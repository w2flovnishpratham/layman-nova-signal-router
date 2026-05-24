import { NavLink, Outlet } from 'react-router-dom'

const links = [
  { to: '/', label: 'Setup' },
  { to: '/dashboard', label: 'Dashboard' },
  { to: '/logs', label: 'Logs' },
  { to: '/settings', label: 'Settings' },
]

export default function Layout() {
  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 font-sans">
      <header className="border-b border-slate-800 bg-slate-950/95">
        <div className="mx-auto flex max-w-7xl flex-col gap-3 px-4 py-4 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <p className="text-lg font-semibold tracking-normal">NOVA Signal Router</p>
            <p className="text-xs text-slate-400">TradingView alerts route through this backend before Dhan.</p>
          </div>
          <nav className="flex flex-wrap gap-2">
            {links.map((link) => (
              <NavLink
                key={link.to}
                to={link.to}
                end={link.to === '/'}
                className={({ isActive }) => (isActive ? 'nav-link-active' : 'nav-link')}
              >
                {link.label}
              </NavLink>
            ))}
          </nav>
        </div>
      </header>
      <main className="mx-auto w-full max-w-7xl flex-1 px-4 py-6">
        <Outlet />
      </main>
    </div>
  )
}
