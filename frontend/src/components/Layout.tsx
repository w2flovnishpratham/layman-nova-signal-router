import { NavLink, Outlet } from 'react-router-dom'
import {
  ClipboardList,
  LayoutDashboard,
  ScrollText,
  Settings,
  Settings2,
  ShieldAlert,
  TrendingUp,
} from 'lucide-react'
import { useSystemStatus, type StatusKind } from '../hooks/useSystemStatus'

// ─── Status pill config ──────────────────────────────────────────────────────

const STATUS_CFG: Record<StatusKind, { dotCls: string; textCls: string; pillCls: string }> = {
  loading: {
    dotCls: 'status-dot dot-gray dot-pulse',
    textCls: 'text-[#666666]',
    pillCls: 'bg-[#151513] border border-[#2b2a26]',
  },
  ready: {
    dotCls: 'status-dot dot-green dot-pulse',
    textCls: 'text-[#98e94d]',
    pillCls: 'bg-[rgba(152,233,77,0.08)] border border-[rgba(152,233,77,0.2)]',
  },
  live: {
    dotCls: 'status-dot dot-red dot-pulse',
    textCls: 'text-red-300 font-semibold',
    pillCls: 'bg-[rgba(239,68,68,0.1)] border border-[rgba(239,68,68,0.3)]',
  },
  blocked: {
    dotCls: 'status-dot dot-amber dot-pulse',
    textCls: 'text-amber-400',
    pillCls: 'bg-[rgba(245,158,11,0.08)] border border-[rgba(245,158,11,0.25)]',
  },
  error: {
    dotCls: 'status-dot dot-red',
    textCls: 'text-red-400',
    pillCls: 'bg-[rgba(239,68,68,0.08)] border border-[rgba(239,68,68,0.25)]',
  },
}

// ─── Nav items ───────────────────────────────────────────────────────────────

interface NavItem {
  to: string
  icon: React.ElementType
  label: string
}

const TOP_LINKS: NavItem[] = [
  { to: '/app/dashboard', icon: LayoutDashboard, label: 'Dashboard' },
  { to: '/app/setup',     icon: Settings2,       label: 'Setup'     },
  { to: '/app/orders',    icon: ClipboardList,   label: 'Orders'    },
  { to: '/app/positions', icon: TrendingUp,      label: 'Positions' },
  { to: '/app/logs',      icon: ScrollText,      label: 'Activity'  },
]

const BOTTOM_LINKS: NavItem[] = [
  { to: '/app/controls', icon: ShieldAlert, label: 'Controls' },
  { to: '/app/settings', icon: Settings,    label: 'Settings' },
]

// ─── Sidebar nav item ────────────────────────────────────────────────────────

function SidebarLink({ to, icon: Icon, label, danger = false }: NavItem & { danger?: boolean }) {
  return (
    <NavLink
      to={to}
      title={label}
      className={({ isActive }) => {
        const base = 'group relative flex items-center justify-center w-10 h-10 rounded-xl transition-all duration-150 '
        if (danger) {
          return (
            base +
            (isActive
              ? 'bg-[rgba(239,68,68,0.15)] text-red-400'
              : 'text-red-500/60 hover:bg-[rgba(239,68,68,0.1)] hover:text-red-400')
          )
        }
        return (
          base +
          (isActive
            ? 'bg-[#1b1a17] text-[#98e94d]'
            : 'text-[#444444] hover:bg-[#1a1a1a] hover:text-[#aaaaaa]')
        )
      }}
    >
      {({ isActive }) => (
        <>
          <Icon size={17} aria-hidden />
          {isActive && (
            <span
              className="absolute left-0 top-1/2 -translate-y-1/2 w-0.5 h-5 rounded-r-full"
              style={{ background: '#98e94d' }}
            />
          )}
          <span
            className="pointer-events-none absolute left-12 z-50 hidden whitespace-nowrap rounded-lg px-2.5 py-1 text-xs shadow-xl group-hover:block"
            style={{ background: '#151513', border: '1px solid #2b2a26', color: '#e0e0e0' }}
          >
            {label}
          </span>
        </>
      )}
    </NavLink>
  )
}

// ─── Layout ──────────────────────────────────────────────────────────────────

export default function Layout() {
  const status = useSystemStatus()
  const cfg = STATUS_CFG[status.kind]
  const hasAlert = status.emergencyStop || status.killSwitch

  return (
    <div className="flex h-screen overflow-hidden" style={{ background: '#0f0f0f', color: '#f0f0f0' }}>
      {/* ── Sidebar ── */}
      <aside
        className="flex flex-col items-center w-14 flex-shrink-0 py-3"
        style={{ background: '#0a0a0a', borderRight: '1px solid #1a1a1a' }}
      >
        {/* Logo mark */}
        <div className="flex items-center justify-center w-10 h-10 mb-4">
          <span
            className="text-sm font-black tracking-widest select-none"
            style={{ color: '#98e94d' }}
          >
            N
          </span>
        </div>

        {/* Top nav */}
        <nav className="flex flex-col gap-1 flex-1" aria-label="Main navigation">
          {TOP_LINKS.map(link => (
            <SidebarLink key={link.to} {...link} />
          ))}
        </nav>

        {/* Bottom nav */}
        <div className="flex flex-col gap-1">
          {BOTTOM_LINKS.map(link => (
            <div key={link.to} className="relative">
              <SidebarLink {...link} danger={link.to === '/app/controls' && hasAlert} />
              {link.to === '/app/controls' && hasAlert && (
                <span
                  className="absolute top-1 right-1 w-2 h-2 rounded-full"
                  style={{ background: '#ef4444', boxShadow: '0 0 0 2px #0a0a0a' }}
                />
              )}
            </div>
          ))}
        </div>
      </aside>

      {/* ── Main column ── */}
      <div className="flex flex-col flex-1 min-w-0">
        {/* Topbar */}
        <header
          className="flex items-center justify-between h-14 px-5 flex-shrink-0"
          style={{ borderBottom: '1px solid #1a1a1a', background: '#0a0a0a' }}
        >
          <div>
            <p className="text-sm font-semibold leading-tight" style={{ color: '#f0f0f0' }}>
              NOVA Signal Router
            </p>
            <p className="text-xs leading-tight" style={{ color: '#555555' }}>
              TradingView → Dhan
            </p>
          </div>

          <div className="flex items-center gap-3">
            {status.dhanMode !== 'UNKNOWN' && (
              <span className={status.dhanMode === 'REAL' ? 'badge-red-solid' : 'badge-green-solid'}>
                {status.dhanMode}
              </span>
            )}

            {status.balance != null && (
              <span
                className="hidden sm:block text-xs font-mono tabular-nums"
                style={{ color: '#888888' }}
              >
                ₹{status.balance.toLocaleString('en-IN', { maximumFractionDigits: 0 })}
              </span>
            )}

            <div className={`flex items-center gap-2 rounded-full px-3 py-1.5 text-xs ${cfg.pillCls}`}>
              <span className={cfg.dotCls} />
              <span className={cfg.textCls}>{status.label}</span>
            </div>
          </div>
        </header>

        {/* Page content */}
        <main className="flex-1 overflow-y-auto" style={{ background: '#0f0f0f' }}>
          <div className="mx-auto w-full max-w-7xl px-6 py-6">
            <Outlet />
          </div>
        </main>
      </div>
    </div>
  )
}
