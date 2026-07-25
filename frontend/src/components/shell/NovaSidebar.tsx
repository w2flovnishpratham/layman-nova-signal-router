import {
  Activity, BarChart3, Bot, FileText, Gauge, KeyRound, LayoutDashboard,
  LineChart, PanelLeftClose, PanelLeftOpen, Settings, ShieldCheck, Webhook,
} from 'lucide-react'
import type { ComponentType } from 'react'
import { APP_ROUTES, ROUTE_META, isImplemented, type AppRoute } from '../../appRoutes'

const ICONS: Partial<Record<AppRoute, ComponentType<{ size?: number }>>> = {
  dashboard: LayoutDashboard,
  trading: LineChart,
  strategies: BarChart3,
  signals: Activity,
  automations: Bot,
  webhooks: Webhook,
  credentials: KeyRound,
  risk: ShieldCheck,
  reports: FileText,
  settings: Settings,
}

// "strategies/new" is reached from within Strategies, not the rail.
const RAIL_ROUTES = APP_ROUTES.filter((r) => r !== 'strategies/new')
const MANAGE: AppRoute[] = ['dashboard', 'trading', 'strategies', 'signals', 'automations']
const CONFIGURE: AppRoute[] = ['webhooks', 'credentials', 'risk', 'reports', 'settings']

interface Props {
  route: AppRoute
  open: boolean
  collapsed: boolean
  onNavigate: (route: AppRoute) => void
  onToggleCollapsed: () => void
  onClose: () => void
}

export function NovaSidebar({ route, open, collapsed, onNavigate, onToggleCollapsed, onClose }: Props) {
  const item = (r: AppRoute) => {
    const Icon = ICONS[r] ?? Gauge
    const active = route === r || (r === 'strategies' && route === 'strategies/new')
    return (
      <button
        key={r}
        type="button"
        className="nova-navitem"
        aria-current={active ? 'page' : undefined}
        onClick={() => { onNavigate(r); onClose() }}
      >
        <Icon size={16} />
        <span className="nova-navitem-label">{ROUTE_META[r].label}</span>
        {/* Truthful: routes without a real screen yet are labelled, not faked. */}
        {!isImplemented(r) ? <span className="nova-navitem-soon">Soon</span> : null}
      </button>
    )
  }

  return (
    <nav className="nova-rail" aria-label="Application" data-open={open} data-collapsed={collapsed}>
      <div className="nova-rail-brand">
        <Gauge size={18} />
        <span className="nova-navitem-label">NOVA Signal Router</span>
      </div>

      <div className="nova-rail-group">Manage</div>
      {MANAGE.map(item)}

      <div className="nova-rail-group">Configure</div>
      {CONFIGURE.map(item)}

      <button
        type="button"
        className="nova-navitem"
        style={{ marginTop: 'auto' }}
        onClick={onToggleCollapsed}
        aria-label={collapsed ? 'Expand navigation' : 'Collapse navigation'}
      >
        {collapsed ? <PanelLeftOpen size={16} /> : <PanelLeftClose size={16} />}
        <span className="nova-navitem-label">Collapse</span>
      </button>
    </nav>
  )
}

export { RAIL_ROUTES }
