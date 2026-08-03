import '@fontsource-variable/inter/index.css'
import '@fontsource-variable/jetbrains-mono/index.css'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { Header } from './components/Header'
import { PortfolioDashboard } from './dashboard/PortfolioDashboard'
import { dashboardPreviewBundle, dashboardPreviewHealth, dashboardPreviewRuntime } from './dashboard/previewData'
import './index.css'

document.documentElement.dataset.mode = 'paper'

export function DashboardPreview() {
  return (
    <main className="nova-app">
      <div className="nova-shell">
        <div className="nova-main">
        <Header
          route="dashboard"
          status="live"
          runtime={dashboardPreviewRuntime}
          engineLive={true}
          engineMode="paper"
          setupState="LIVE"
          user={{ id: 'preview', email: 'alfikri@example.com', name: 'Alfikri Djati', picture_url: null, is_admin: false, is_dev: false }}
          market={{
            niftySpot: 22_948.35,
            dayChangePct: 0.46,
            marketStatus: 'open',
            lastUpdatedAt: '2026-07-23T13:38:21+05:30',
            latestSignal: {},
            activeOptionLtp: 172.2,
          }}
          onNavigate={() => undefined}
          onKill={() => undefined}
          onLogout={() => undefined}
          onMode={() => undefined}
          onSaveConfig={() => undefined}
          onPaperReset={() => undefined}
        />
          <PortfolioDashboard
            runtime={dashboardPreviewRuntime}
            health={dashboardPreviewHealth}
            preview={dashboardPreviewBundle}
            onKill={() => undefined}
            onManageStrategies={() => undefined}
            onViewHealth={() => undefined}
          />
        </div>
      </div>
    </main>
  )
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <DashboardPreview />
  </StrictMode>,
)
