import '@fontsource-variable/inter/index.css'
import '@fontsource-variable/jetbrains-mono/index.css'
import { StrictMode, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { AnimatePresence, motion } from 'framer-motion'
import { X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { EngineLeftPanel } from './components/EngineLeftPanel'
import { EngineSidebar } from './components/EngineSidebar'
import { Header } from './components/Header'
import { NiftyLiveChart } from './components/NiftyLiveChart'
import { TerminalMobileBar, type TerminalMobileSection } from './components/TerminalMobileBar'
import { dashboardPreviewRuntime } from './dashboard/previewData'
import { appPath } from './appRoutes'
import { TradingActivityTabs } from './trading/TradingActivityTabs'
import type { ChartTimeframe, NiftyCandleSeries } from './api'
import type { ActiveTrade, MarketSnapshot } from './types'
import './index.css'

document.documentElement.dataset.mode = 'paper'

const market: MarketSnapshot = {
  niftySpot: 22_948.35,
  dayChangePct: 0.46,
  marketStatus: 'open',
  lastUpdatedAt: '2026-07-23T14:58:00+05:30',
  latestSignal: {},
  activeOptionLtp: 98.55,
}

const activeTrade: ActiveTrade = {
  positionId: 'preview-position',
  mode: 'paper',
  symbol: 'NIFTY 22950 CE',
  strike: 22_950,
  optType: 'CE',
  qty: 65,
  avgPrice: 88.4,
  ltp: 98.55,
  pnl: 660,
  pnlPct: 11.48,
  expiry: '2026-07-30',
  securityId: '50241',
  orderId: 'PAPER-01982',
  correlationId: 'preview-correlation',
  status: 'OPEN',
  quoteSource: 'dhan-market-feed',
  quoteStatus: 'live',
  quoteStale: false,
}

function loadPreviewCandles(timeframe: ChartTimeframe = '1m'): Promise<NiftyCandleSeries> {
  const minutes = Number.parseInt(timeframe, 10)
  const count = Math.floor(375 / minutes) + 1
  const sessionStart = Date.parse('2026-07-23T09:15:00+05:30') / 1000
  const candles = Array.from({ length: count }, (_, index) => {
    const open = 22_820 + index * minutes * 0.34 + Math.sin(index / 3.2) * 24
    const close = open + Math.sin(index * 1.7) * 8
    return {
      time: sessionStart + index * minutes * 60,
      open: Number(open.toFixed(2)),
      high: Number((Math.max(open, close) + 5 + (index % 4)).toFixed(2)),
      low: Number((Math.min(open, close) - 5 - (index % 3)).toFixed(2)),
      close: Number(close.toFixed(2)),
      volume: 1_200 + (index % 9) * 170,
    }
  })
  return Promise.resolve({
    symbol: 'NIFTY', interval: timeframe, source: 'terminal_preview', status: 'ready', market_state: 'open',
    timezone: 'Asia/Kolkata', trading_date: '2026-07-23', session_start: '2026-07-23T09:15:00+05:30',
    session_end: '2026-07-23T15:30:00+05:30', updated_at: '2026-07-23T14:58:00+05:30',
    candle_count: candles.length, candles,
  })
}

function TerminalPreview() {
  const [mobileSection, setMobileSection] = useState<TerminalMobileSection | null>(null)

  const marketPanel = (
    <div className="terminal-preview-readonly">
      <EngineLeftPanel
        engineMode="paper"
        activeTrade={activeTrade}
        runtimePositionOpen
        runtime={dashboardPreviewRuntime}
        onStop={() => undefined}
        onSaveConfig={async () => undefined}
        side="BOTH"
        onSend={() => undefined}
      />
    </div>
  )
  const accountPanel = (section: 'all' | 'account' | 'risk' = 'all') => (
    <div className="terminal-preview-readonly">
      <EngineSidebar
        state="LIVE"
        wallet={202_420}
        marginUtilized={43_258}
        realizedPnl={2_735}
        activeTrade={activeTrade}
        lotSize={65}
        side="BOTH"
        engineMode="paper"
        runtime={dashboardPreviewRuntime}
        marketSnapshot={market}
        onSend={() => undefined}
        section={section}
      />
    </div>
  )

  return (
    <main className="nova-app terminal-preview-page">
      <div className="nova-shell">
        <div className="nova-main">
          <Header
            route="trading"
            status="live"
            runtime={dashboardPreviewRuntime}
            engineLive
            engineMode="paper"
            setupState="LIVE"
            user={{ id: 'preview', email: 'preview@nova.local', name: 'Terminal Preview', picture_url: null, is_admin: false, is_dev: true }}
            market={market}
            onNavigate={(route) => { window.location.href = appPath(route) }}
            onKill={() => undefined}
            onLogout={() => undefined}
            onMode={() => undefined}
            onSaveConfig={() => undefined}
            onPaperReset={() => undefined}
          />

          <section className="terminal-preview-note" role="status">
            <strong>Safe Paper terminal preview</strong>
            <span>Production components with simulated candles and order/engine mutations disabled.</span>
          </section>

          <section className="engine-shell engine-shell-grid" aria-label="Nova trading session preview">
            <aside className="desktop-engine-panel engine-panel-left">
              <div className="engine-panel-shell"><div className="panel-scroll">{marketPanel}</div></div>
            </aside>

            <div className="engine-main-pane engine-live-layout lg:h-full flex flex-col min-h-[450px] lg:min-h-0">
              <div className="live-engine-stack">
                <NiftyLiveChart engineMode="paper" candleLoader={loadPreviewCandles} />
                <TradingActivityTabs mode="paper" />
              </div>
            </div>

            <aside className="desktop-engine-panel engine-panel-right">
              <div className="engine-panel-shell"><div className="panel-scroll">{accountPanel()}</div></div>
            </aside>
          </section>

          <AnimatePresence initial={false}>
            {mobileSection ? (
              <motion.div className="terminal-preview-drawer" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
                <button className="terminal-preview-drawer-backdrop" type="button" aria-label="Close terminal panel" onClick={() => setMobileSection(null)} />
                <motion.section
                  className="terminal-preview-drawer-sheet"
                  initial={{ y: '100%' }}
                  animate={{ y: 0 }}
                  exit={{ y: '100%' }}
                  transition={{ duration: 0.22 }}
                >
                  <div className="terminal-preview-drawer-head">
                    <strong>{mobileSection === 'market' ? 'Market & Order' : mobileSection === 'risk' ? 'Bias & Risk' : 'Account & P&L'}</strong>
                    <Button variant="unstyled" type="button" aria-label="Close terminal panel" onClick={() => setMobileSection(null)}><X size={17} /></Button>
                  </div>
                  {mobileSection === 'market' ? marketPanel : accountPanel(mobileSection)}
                </motion.section>
              </motion.div>
            ) : null}
          </AnimatePresence>

          <TerminalMobileBar active={mobileSection} onSelect={(section) => setMobileSection((current) => current === section ? null : section)} />
        </div>
      </div>
    </main>
  )
}

createRoot(document.getElementById('root')!).render(
  <StrictMode><TerminalPreview /></StrictMode>,
)
