import { useEffect, useState, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Activity,
  ArrowRight,
  CheckCircle2,
  DollarSign,
  Lock,
  RadioTower,
  ShieldCheck,
  Webhook,
  Zap,
  Play,
  RotateCcw,
  Sparkles,
  Terminal,
  Layers,
  ArrowUpRight,
  ShieldAlert,
  Sliders,
  BookOpen
} from 'lucide-react'
import { getHealth } from '../api/dashboard'

type BackendStatus = 'checking' | 'online' | 'offline'

interface LogItem {
  id: string
  time: string
  type: 'info' | 'warn' | 'error' | 'success'
  text: string
}

type PresetType = 'buy' | 'sell' | 'duplicate' | 'violation' | 'timeout'

interface Preset {
  id: PresetType
  name: string
  action: string
  desc: string
  symbol: string
  qty: number
  badge: string
}

const PRESETS: Preset[] = [
  { id: 'buy', name: 'Nifty Buy Alert', action: 'BUY', desc: 'Valid long alert, passes risk checks & executes', symbol: 'NIFTY', qty: 50, badge: 'Standard flow' },
  { id: 'sell', name: 'Banknifty Sell Alert', action: 'SELL', desc: 'Valid short alert, preflights and routes', symbol: 'BANKNIFTY', qty: 25, badge: 'Standard flow' },
  { id: 'duplicate', name: 'Duplicate Trigger', action: 'BUY', desc: 'Locked at risk gates: double-fire block', symbol: 'NIFTY', qty: 50, badge: 'Risk lock' },
  { id: 'violation', name: 'Size limit violation', action: 'BUY', desc: 'Rejected at risk gates: quantity limit exceeded', symbol: 'NIFTY', qty: 50000, badge: 'Risk reject' },
  { id: 'timeout', name: 'Broker Timeout', action: 'SELL', desc: 'Aborted at preflight: connection lost', symbol: 'FINNIFTY', qty: 40, badge: 'Broker failure' },
]

const SYSTEM_ADVANTAGES = [
  {
    icon: Lock,
    title: 'Duplicate Guard Lock',
    desc: 'Prevents double-entry market risk by hashing and caching incoming alerts. Automatically discards rapid double-fires within a customizable 5-second gate.'
  },
  {
    icon: Sliders,
    title: 'Real-time Preflight',
    desc: 'Directly queries Dhan API to check current position exposure, buying power margin, and existing orders before committing any execution routing.'
  },
  {
    icon: ShieldCheck,
    title: 'Safe Vault local credentials',
    desc: 'Dhan API tokens stay securely in your local environment variables and memory. No cloud database storage, eliminating leak vector vulnerabilities.'
  },
  {
    icon: Activity,
    title: 'Emergency Cut-offs',
    desc: 'Equipped with a physical Global Kill Switch and one-click Emergency Stop to immediately cancel open positions and block incoming webhooks.'
  }
]

export default function LandingPage() {
  const navigate = useNavigate()
  const [backendStatus, setBackendStatus] = useState<BackendStatus>('checking')
  
  // Simulation states
  const [simulating, setSimulating] = useState(false)
  const [activePreset, setActivePreset] = useState<PresetType | null>(null)
  const [logs, setLogs] = useState<LogItem[]>([])
  const [simLatency, setSimLatency] = useState('0.0 ms')
  const [signalsCount, setSignalsCount] = useState(142)
  const [blocksCount, setBlocksCount] = useState(8)
  
  // Pipeline nodes status state
  const [nodeStates, setNodeStates] = useState({
    webhook: 'idle' as 'idle' | 'active' | 'success',
    risk: 'idle' as 'idle' | 'active' | 'success' | 'blocked' | 'failed',
    preflight: 'idle' as 'idle' | 'active' | 'success' | 'failed',
    route: 'idle' as 'idle' | 'active' | 'success'
  })

  // Pipeline connections state
  const [pathStates, setPathStates] = useState({
    p1: 'idle' as 'idle' | 'active' | 'success' | 'blocked' | 'failed',
    p2: 'idle' as 'idle' | 'active' | 'success' | 'failed',
    p3: 'idle' as 'idle' | 'active' | 'success' | 'failed'
  })

  const timeoutsRef = useRef<number[]>([])
  const terminalRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    getHealth()
      .then(() => setBackendStatus('online'))
      .catch(() => setBackendStatus('offline'))
    
    return () => clearAllTimeouts()
  }, [])

  // Auto scroll terminal logs container directly without viewport sliding
  useEffect(() => {
    if (terminalRef.current) {
      terminalRef.current.scrollTop = terminalRef.current.scrollHeight
    }
  }, [logs])

  const clearAllTimeouts = () => {
    timeoutsRef.current.forEach(window.clearTimeout)
    timeoutsRef.current = []
  }

  const resetSimulation = () => {
    clearAllTimeouts()
    setSimulating(false)
    setActivePreset(null)
    setLogs([])
    setSimLatency('0.0 ms')
    setNodeStates({ webhook: 'idle', risk: 'idle', preflight: 'idle', route: 'idle' })
    setPathStates({ p1: 'idle', p2: 'idle', p3: 'idle' })
  }

  const runPreset = (presetType: PresetType) => {
    clearAllTimeouts()
    setSimulating(true)
    setActivePreset(presetType)
    setLogs([])
    
    setNodeStates({ webhook: 'active', risk: 'idle', preflight: 'idle', route: 'idle' })
    setPathStates({ p1: 'idle', p2: 'idle', p3: 'idle' })

    const addLog = (text: string, type: 'info' | 'warn' | 'error' | 'success' = 'info') => {
      const time = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
      setLogs(prev => [...prev, { id: Math.random().toString(), time, type, text }])
    }

    const jitterLatency = () => {
      const val = (2.2 + Math.random() * 2.1).toFixed(1)
      setSimLatency(`${val} ms`)
    }

    const targetPreset = PRESETS.find(p => p.id === presetType)
    const labelAction = targetPreset?.action ?? 'BUY'
    const labelSym = targetPreset?.symbol ?? 'NIFTY'
    const labelQty = targetPreset?.qty ?? 50

    // Step 0: Webhook incoming
    addLog(`Incoming webhook request POST /api/alerts...`, 'info')
    addLog(`Parsed signal payload: { action: "${labelAction}", symbol: "${labelSym}", qty: ${labelQty} }`, 'info')
    jitterLatency()

    const t1 = window.setTimeout(() => {
      addLog(`Verifying payload cryptographic signature... Secret verified.`, 'success')
      setNodeStates(prev => ({ ...prev, webhook: 'success' }))
      setPathStates(prev => ({ ...prev, p1: 'active' }))
      addLog(`[Signal Pipeline] Routing payload details to NOVA Risk gates...`, 'info')
      jitterLatency()
    }, 1200)
    timeoutsRef.current.push(t1)

    // Step 1: Risk Gates Check
    const t2 = window.setTimeout(() => {
      setNodeStates(prev => ({ ...prev, risk: 'active' }))
      setPathStates(prev => ({ ...prev, p1: 'success' }))
      addLog(`[Risk Gate] Evaluation active. Querying active state locks...`, 'info')
      jitterLatency()
    }, 2400)
    timeoutsRef.current.push(t2)

    const t3 = window.setTimeout(() => {
      if (presetType === 'duplicate') {
        addLog(`[Risk Gate] Duplicate detection rule warning: identical payload received within 5s window.`, 'warn')
        const tBlock = window.setTimeout(() => {
          addLog(`[Risk Gate] DUPLICATE_LOCK_BLOCK: Dropping signal to prevent double execution.`, 'error')
          setNodeStates(prev => ({ ...prev, risk: 'blocked' }))
          setPathStates(prev => ({ ...prev, p1: 'blocked' }))
          setSimulating(false)
          setBlocksCount(c => c + 1)
        }, 800)
        timeoutsRef.current.push(tBlock)
        return
      }

      if (presetType === 'violation') {
        addLog(`[Risk Gate] Safety limit check: requested quantity ${labelQty} exceeds maximum allowed limit (10,000).`, 'warn')
        const tReject = window.setTimeout(() => {
          addLog(`[Risk Gate] RISK_LIMIT_REJECTED: Signal rejected due to safety threshold violation.`, 'error')
          setNodeStates(prev => ({ ...prev, risk: 'failed' }))
          setPathStates(prev => ({ ...prev, p1: 'failed' }))
          setSimulating(false)
          setBlocksCount(c => c + 1)
        }, 800)
        timeoutsRef.current.push(tReject)
        return
      }

      addLog(`[Risk Gate] Lock, margin exposure, and quantity validation: PASS.`, 'success')
      const tPass = window.setTimeout(() => {
        setNodeStates(prev => ({ ...prev, risk: 'success' }))
        setPathStates(prev => ({ ...prev, p2: 'active' }))
        addLog(`[Signal Pipeline] Routing to Dhan Preflight Engine...`, 'info')
        jitterLatency()
      }, 600)
      timeoutsRef.current.push(tPass)
    }, 3600)
    timeoutsRef.current.push(t3)

    // Step 2: Dhan Preflight (Only for passing signals)
    if (presetType === 'duplicate' || presetType === 'violation') return

    const t4 = window.setTimeout(() => {
      setNodeStates(prev => ({ ...prev, preflight: 'active' }))
      setPathStates(prev => ({ ...prev, p2: 'success' }))
      addLog(`[Dhan Preflight] Handshaking with Dhan API endpoints...`, 'info')
      jitterLatency()
    }, 5000)
    timeoutsRef.current.push(t4)

    const t5 = window.setTimeout(() => {
      if (presetType === 'timeout') {
        addLog(`[Dhan Preflight] Error connecting to Dhan API gateway (Connection timed out).`, 'warn')
        const tFail = window.setTimeout(() => {
          addLog(`[Dhan Preflight] DHAN_GATEWAY_OFFLINE: Skipping execution. Routing abort triggered.`, 'error')
          setNodeStates(prev => ({ ...prev, preflight: 'failed' }))
          setPathStates(prev => ({ ...prev, p2: 'failed' }))
          setSimulating(false)
          setBlocksCount(c => c + 1)
        }, 800)
        timeoutsRef.current.push(tFail)
        return
      }

      addLog(`[Dhan Preflight] Connected. Available Margin: ₹85,200. Account preflight: PASS.`, 'success')
      const tPass = window.setTimeout(() => {
        setNodeStates(prev => ({ ...prev, preflight: 'success' }))
        setPathStates(prev => ({ ...prev, p3: 'active' }))
        addLog(`[Signal Pipeline] Forwarding verified routing order info to Order execution...`, 'info')
        jitterLatency()
      }, 600)
      timeoutsRef.current.push(tPass)
    }, 6200)
    timeoutsRef.current.push(t5)

    // Step 3: Order Route (Only for buy/sell presets)
    if (presetType === 'timeout') return

    const t6 = window.setTimeout(() => {
      setNodeStates(prev => ({ ...prev, route: 'active' }))
      setPathStates(prev => ({ ...prev, p3: 'success' }))
      addLog(`[Order Route] Packaging API payload and pushing live order to Dhan broker...`, 'info')
      jitterLatency()
    }, 7600)
    timeoutsRef.current.push(t6)

    const t7 = window.setTimeout(() => {
      const orderId = 'dn_' + Math.random().toString(36).substring(2, 8)
      addLog(`[Order Route] Dhan Broker Accepted. Order placed successfully. Order ID: ${orderId}`, 'success')
      const tFinal = window.setTimeout(() => {
        setNodeStates(prev => ({ ...prev, route: 'success' }))
        addLog(`[Signal Processed] Status: SUCCESS. Latency: ${simLatency}. Exposure adjusted.`, 'success')
        setSimulating(false)
        setSignalsCount(c => c + 1)
      }, 600)
      timeoutsRef.current.push(tFinal)
    }, 8800)
    timeoutsRef.current.push(t7)
  }

  // Node UI rendering helper
  const getNodeStyles = (status: string) => {
    switch (status) {
      case 'active':
        return {
          stroke: '#98e94d',
          fill: '#1b1a17',
          iconColor: '#98e94d',
          textColor: '#f4f1ea',
          pulseCls: 'animate-pulse',
          borderWidth: 2,
        }
      case 'success':
        return {
          stroke: '#98e94d',
          fill: '#11170d',
          iconColor: '#98e94d',
          textColor: '#98e94d',
          pulseCls: '',
          borderWidth: 2.5,
        }
      case 'blocked':
        return {
          stroke: '#f59e0b',
          fill: '#1c160c',
          iconColor: '#f59e0b',
          textColor: '#f59e0b',
          pulseCls: '',
          borderWidth: 2.5,
        }
      case 'failed':
        return {
          stroke: '#ef4444',
          fill: '#1c0c0c',
          iconColor: '#ef4444',
          textColor: '#ef4444',
          pulseCls: '',
          borderWidth: 2.5,
        }
      case 'idle':
      default:
        return {
          stroke: '#2b2a26',
          fill: '#151513',
          iconColor: '#5e5a53',
          textColor: '#77736c',
          pulseCls: '',
          borderWidth: 1.5,
        }
    }
  }

  const getPathStyles = (status: string) => {
    switch (status) {
      case 'active':
        return {
          stroke: '#98e94d',
          strokeDasharray: '8 6',
          className: 'animate-flow',
          opacity: 1,
        }
      case 'success':
        return {
          stroke: '#98e94d',
          strokeDasharray: 'none',
          className: '',
          opacity: 0.8,
        }
      case 'failed':
        return {
          stroke: '#ef4444',
          strokeDasharray: 'none',
          className: '',
          opacity: 0.8,
        }
      case 'blocked':
        return {
          stroke: '#f59e0b',
          strokeDasharray: 'none',
          className: '',
          opacity: 0.8,
        }
      case 'idle':
      default:
        return {
          stroke: '#24231f',
          strokeDasharray: 'none',
          className: '',
          opacity: 0.3,
        }
    }
  }

  const statusDot =
    backendStatus === 'online'
      ? 'dot-green dot-pulse'
      : backendStatus === 'offline'
        ? 'dot-red'
        : 'dot-gray dot-pulse'

  return (
    <div className="min-h-screen bg-[#090908] bg-tech-grid text-[#f4f1ea] relative selection:bg-[#98e94d] selection:text-[#090908] overflow-x-hidden">
      
      {/* Background ambient glowing radial lights */}
      <div className="absolute top-0 left-1/2 -translate-x-1/2 w-full max-w-7xl h-[600px] bg-[radial-gradient(circle_at_top,_rgba(152,233,77,0.06)_0%,_rgba(0,0,0,0)_60%)] pointer-events-none" />
      <div className="absolute top-[400px] left-1/4 w-[350px] h-[350px] bg-[radial-gradient(circle,_rgba(152,233,77,0.025)_0%,_rgba(0,0,0,0)_70%)] pointer-events-none blur-3xl" />
      <div className="absolute top-[800px] right-1/4 w-[400px] h-[400px] bg-[radial-gradient(circle,_rgba(239,68,68,0.015)_0%,_rgba(0,0,0,0)_70%)] pointer-events-none blur-3xl" />

      {/* Header */}
      <header className="sticky top-0 z-50 flex items-center justify-between border-b border-[#1c1c19] bg-[#090908]/80 px-5 py-4 backdrop-blur-md sm:px-8">
        <div className="flex items-center gap-3">
          <div className="flex h-9 w-9 items-center justify-center rounded-full bg-[#f4f1ea] text-sm font-black text-[#090908] shadow-[0_0_15px_rgba(244,241,234,0.15)]">
            N
          </div>
          <div>
            <p className="text-sm font-bold leading-tight tracking-wide">NOVA Signal Router</p>
            <p className="text-xs leading-tight text-[#77736c] font-medium">TradingView to Dhan Control Desk</p>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <span className="hidden items-center gap-2 rounded-full border border-[#24231f] bg-[#121210] px-3.5 py-1.5 text-xs text-[#9a968f] sm:inline-flex">
            <span className={`status-dot ${statusDot}`} />
            {backendStatus === 'online' ? 'Engine online' : backendStatus === 'offline' ? 'Engine offline' : 'Checking connection'}
          </span>
          <button
            onClick={() => navigate('/app/setup')}
            className="hidden rounded-full border border-[#2b2a26] bg-[#141412] px-4 py-1.5 text-xs font-semibold text-[#d8d3c8] transition-all hover:bg-[#1c1b18] hover:text-[#f4f1ea] hover:border-[#3a3933] sm:inline-flex cursor-pointer"
            type="button"
          >
            Setup
          </button>
          <button
            onClick={() => navigate('/app/dashboard')}
            className="btn-primary rounded-full px-4 py-1.5 text-xs cursor-pointer shadow-[0_0_20px_rgba(152,233,77,0.15)]"
            type="button"
          >
            Dashboard <ArrowRight size={13} />
          </button>
        </div>
      </header>

      {/* Main Section */}
      <main className="mx-auto flex w-full max-w-7xl flex-col gap-10 px-5 py-8 sm:px-8">
        
        {/* Hero Section */}
        <section className="flex flex-col items-center text-center max-w-4xl mx-auto py-10 sm:py-16 gap-6 relative">
          <div className="inline-flex items-center gap-2 rounded-full border border-[#26331d] bg-[#11170d]/80 px-4 py-1.5 text-xs font-semibold text-[#a9f060] shadow-[inset_0_1px_0_rgba(169,240,96,0.1)]">
            <span className="status-dot dot-green dot-pulse" />
            Live alert execution monitoring active
          </div>
          
          <h1 className="text-4xl font-bold tracking-tight text-[#f4f1ea] sm:text-6xl max-w-3xl leading-[1.1]">
            Restructure Alert Routing with <span className="text-[#98e94d] bg-gradient-to-r from-[#98e94d] to-[#bcf383] bg-clip-text text-transparent">Zero Exposure Leak</span>
          </h1>
          
          <p className="max-w-2xl text-sm leading-relaxed text-[#9a968f] sm:text-base md:text-lg">
            NOVA routes webhook alerts from TradingView to Dhan API instantly, gated by a state-aware risk layer running duplicate lock checking, exposure validation, and margin preflights locally.
          </p>

          <div className="flex flex-wrap items-center justify-center gap-3.5 mt-2">
            <button
              onClick={() => navigate('/app/dashboard')}
              className="btn-primary rounded-full px-6 py-3 text-sm cursor-pointer shadow-[0_0_30px_rgba(152,233,77,0.25)] hover:scale-[1.02] transition-transform"
              type="button"
            >
              Open control desk <ArrowRight size={15} />
            </button>
            <button
              onClick={() => navigate('/app/controls')}
              className="rounded-full border border-[#2b2a26] bg-[#141412] px-6 py-3 text-sm font-semibold text-[#d8d3c8] transition-all hover:bg-[#1c1b18] hover:text-[#f4f1ea] hover:border-[#3a3933] cursor-pointer"
              type="button"
            >
              Risk configurations
            </button>
            <a 
              href="https://github.com" 
              target="_blank" 
              rel="noreferrer"
              className="hidden md:inline-flex items-center gap-1.5 rounded-full px-4 py-3 text-sm font-semibold text-[#77736c] hover:text-[#d8d3c8] transition-colors"
            >
              <BookOpen size={15} /> Documentation
            </a>
          </div>

          {/* Quick Metrics display */}
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 w-full mt-10 text-left">
            {[
              { label: 'Engine status', value: 'Armed', detail: 'Webhook listening', color: 'text-[#98e94d]' },
              { label: 'Broker exposure', value: 'Flat', detail: 'Positions in sync', color: 'text-nova-50' },
              { label: 'Guard state', value: 'Secure', detail: 'Risk gates ready', color: 'text-nova-50' },
              { label: 'Local Latency', value: '4.2 ms', detail: 'Avg router process', color: 'text-[#98e94d]' }
            ].map((m, idx) => (
              <div key={idx} className="landing-card bg-[#11110f]/60 backdrop-blur-sm border-[#1c1c19] p-4 flex flex-col gap-1 hover:border-[#2b2a26] transition-all">
                <span className="text-[11px] font-semibold tracking-wider text-[#77736c] uppercase">{m.label}</span>
                <span className={`text-xl font-bold ${m.color}`}>{m.value}</span>
                <span className="text-xs text-[#9a968f]">{m.detail}</span>
              </div>
            ))}
          </div>
        </section>

        {/* Interactive SVG Simulator Workspace */}
        <section className="flex flex-col gap-4">
          <div className="flex flex-col md:flex-row md:items-end justify-between gap-3">
            <div>
              <h2 className="text-2xl font-bold tracking-wide flex items-center gap-2">
                <Sparkles size={18} className="text-[#98e94d]" />
                Interactive Signal Pipeline Flow
              </h2>
              <p className="text-sm text-[#9a968f]">
                Inject test signals to simulate how NOVA evaluates webhooks, intercepts risk violations, and forwards orders to Dhan.
              </p>
            </div>
            {simulating && (
              <div className="inline-flex items-center gap-2 text-xs bg-[rgba(152,233,77,0.08)] border border-[rgba(152,233,77,0.2)] text-[#98e94d] px-3 py-1 rounded-full animate-pulse">
                <span className="status-dot dot-green" /> Running signal simulation
              </div>
            )}
          </div>

          {/* Simulator Desktop Console Shell */}
          <div className="landing-card bg-[#0d0d0c] border-[#1d1c19] overflow-hidden p-0 shadow-[0_0_50px_-12px_rgba(152,233,77,0.15)]">
            
            {/* Header controls mimic mac window */}
            <div className="flex flex-col sm:flex-row sm:items-center justify-between px-4 py-3 bg-[#111110] border-b border-[#1d1c19] gap-2">
              <div className="flex items-center gap-2">
                <span className="w-3 h-3 rounded-full bg-[#ef4444]/40" />
                <span className="w-3 h-3 rounded-full bg-[#fbbf24]/40" />
                <span className="w-3 h-3 rounded-full bg-[#22c55e]/40" />
                <span className="hidden sm:inline text-xs font-mono text-[#5e5a53] ml-2">nova_sandbox_router_log</span>
              </div>
              <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs font-mono text-[#77736c]">
                <div>Signals: <span className="text-[#98e94d] font-bold">{signalsCount}</span></div>
                <div>Blocks: <span className="text-[#ef4444] font-bold">{blocksCount}</span></div>
                <div>Latency: <span className="text-[#d8d3c8] font-bold">{simLatency}</span></div>
              </div>
            </div>

            {/* Main Console Split Pane */}
            <div className="grid lg:grid-cols-[300px_1fr] min-h-[460px]">
              
              {/* Left Pane - Preset Controllers */}
              <div className="border-b lg:border-b-0 lg:border-r border-[#1d1c19] bg-[#111110]/50 p-5 flex flex-col justify-between gap-6">
                <div className="space-y-4">
                  <span className="text-xs font-bold text-[#77736c] uppercase tracking-wider block">Signal Injection Presets</span>
                  <div className="flex flex-col gap-2.5">
                    {PRESETS.map((p) => {
                      const isActive = activePreset === p.id
                      return (
                        <button
                          key={p.id}
                          onClick={() => !simulating && runPreset(p.id)}
                          disabled={simulating}
                          className={`w-full text-left p-3 rounded-xl border transition-all relative flex flex-col gap-1.5 cursor-pointer ${
                            isActive
                              ? 'border-[#98e94d] bg-[#11170d]/50 shadow-[0_0_12px_rgba(152,233,77,0.06)]'
                              : 'border-[#1c1b18] bg-[#141412]/80 hover:bg-[#1a1916] hover:border-[#2b2a26] disabled:opacity-40 disabled:cursor-not-allowed'
                          }`}
                        >
                          <div className="flex items-center justify-between">
                            <span className={`text-xs font-bold ${isActive ? 'text-[#98e94d]' : 'text-[#f4f1ea]'}`}>
                              {p.name}
                            </span>
                            <span className={`text-[9px] px-1.5 py-0.5 rounded font-mono ${
                              p.id === 'buy' || p.id === 'sell' ? 'bg-[#98e94d]/10 text-[#98e94d]' :
                              p.id === 'duplicate' ? 'bg-[#f59e0b]/10 text-[#f59e0b]' : 'bg-[#ef4444]/10 text-[#ef4444]'
                            }`}>
                              {p.badge}
                            </span>
                          </div>
                          <span className="text-[11px] text-[#77736c] leading-tight">
                            {p.desc}
                          </span>
                        </button>
                      )
                    })}
                  </div>
                </div>

                <div className="pt-4 border-t border-[#1c1b18] flex items-center justify-between">
                  <button
                    onClick={resetSimulation}
                    className="flex items-center gap-1 text-xs text-[#77736c] hover:text-[#f4f1ea] transition-colors cursor-pointer"
                  >
                    <RotateCcw size={13} /> Reset simulator
                  </button>
                  <span className="text-[10px] font-mono text-[#5e5a53] uppercase">Status: Armed</span>
                </div>
              </div>

              {/* Right Pane - SVG visual and Terminal Logs */}
              <div className="flex flex-col">
                
                {/* SVG Visual Flow Panel */}
                <div className="p-6 bg-[#090908] border-b border-[#1c1c19] flex items-center justify-center overflow-x-auto relative">
                  
                  {/* Desktop flowchart (horizontal) */}
                  <svg 
                    viewBox="0 0 760 160" 
                    className="hidden md:block w-full max-w-[700px] h-[150px] overflow-visible select-none flex-shrink-0"
                  >
                    <defs>
                      <filter id="glow-green" x="-20%" y="-20%" width="140%" height="140%">
                        <feGaussianBlur stdDeviation="3.5" result="blur" />
                        <feComposite in="SourceGraphic" in2="blur" operator="over" />
                      </filter>
                      <filter id="glow-orange" x="-20%" y="-20%" width="140%" height="140%">
                        <feGaussianBlur stdDeviation="3.5" result="blur" />
                        <feComposite in="SourceGraphic" in2="blur" operator="over" />
                      </filter>
                      <filter id="glow-red" x="-20%" y="-20%" width="140%" height="140%">
                        <feGaussianBlur stdDeviation="3.5" result="blur" />
                        <feComposite in="SourceGraphic" in2="blur" operator="over" />
                      </filter>
                    </defs>

                    {/* Wavy curved background tracks */}
                    <path
                      d="M 80 80 C 180 50, 180 50, 280 80"
                      fill="none"
                      stroke="#1d1c19"
                      strokeWidth="3.5"
                      strokeLinecap="round"
                    />
                    <path
                      d="M 280 80 C 380 110, 380 110, 480 80"
                      fill="none"
                      stroke="#1d1c19"
                      strokeWidth="3.5"
                      strokeLinecap="round"
                    />
                    <path
                      d="M 480 80 C 580 50, 580 50, 680 80"
                      fill="none"
                      stroke="#1d1c19"
                      strokeWidth="3.5"
                      strokeLinecap="round"
                    />

                    {/* Animated Flow Overlays */}
                    <path
                      d="M 80 80 C 180 50, 180 50, 280 80"
                      fill="none"
                      opacity={getPathStyles(pathStates.p1).opacity}
                      stroke={getPathStyles(pathStates.p1).stroke}
                      strokeWidth="3.5"
                      strokeLinecap="round"
                      strokeDasharray={getPathStyles(pathStates.p1).strokeDasharray}
                      className={getPathStyles(pathStates.p1).className}
                    />
                    <path
                      d="M 280 80 C 380 110, 380 110, 480 80"
                      fill="none"
                      opacity={getPathStyles(pathStates.p2).opacity}
                      stroke={getPathStyles(pathStates.p2).stroke}
                      strokeWidth="3.5"
                      strokeLinecap="round"
                      strokeDasharray={getPathStyles(pathStates.p2).strokeDasharray}
                      className={getPathStyles(pathStates.p2).className}
                    />
                    <path
                      d="M 480 80 C 580 50, 580 50, 680 80"
                      fill="none"
                      opacity={getPathStyles(pathStates.p3).opacity}
                      stroke={getPathStyles(pathStates.p3).stroke}
                      strokeWidth="3.5"
                      strokeLinecap="round"
                      strokeDasharray={getPathStyles(pathStates.p3).strokeDasharray}
                      className={getPathStyles(pathStates.p3).className}
                    />

                    {/* Pipeline Stage Nodes */}
                    
                    {/* Node 1: Webhook */}
                    <g className="cursor-pointer">
                      <circle
                        cx="80"
                        cy="80"
                        r="25"
                        fill={getNodeStyles(nodeStates.webhook).fill}
                        stroke={getNodeStyles(nodeStates.webhook).stroke}
                        strokeWidth={getNodeStyles(nodeStates.webhook).borderWidth}
                        className={getNodeStyles(nodeStates.webhook).pulseCls}
                        style={{ filter: nodeStates.webhook !== 'idle' ? 'url(#glow-green)' : 'none' }}
                      />
                      <foreignObject x="66" y="66" width="28" height="28">
                        <div className="flex items-center justify-center w-full h-full" style={{ color: getNodeStyles(nodeStates.webhook).iconColor }}>
                          <Webhook size={16} />
                        </div>
                      </foreignObject>
                      <text x="80" y="125" textAnchor="middle" fill={getNodeStyles(nodeStates.webhook).textColor} className="text-[11px] font-semibold tracking-wide">TV Alert</text>
                      <text x="80" y="140" textAnchor="middle" fill="#77736c" className="text-[9px] font-mono">Webhook</text>
                    </g>

                    {/* Node 2: Risk Gate */}
                    <g>
                      <circle
                        cx="280"
                        cy="80"
                        r="25"
                        fill={getNodeStyles(nodeStates.risk).fill}
                        stroke={getNodeStyles(nodeStates.risk).stroke}
                        strokeWidth={getNodeStyles(nodeStates.risk).borderWidth}
                        className={getNodeStyles(nodeStates.risk).pulseCls}
                        style={{ filter: nodeStates.risk === 'success' || nodeStates.risk === 'active' ? 'url(#glow-green)' : nodeStates.risk === 'blocked' ? 'url(#glow-orange)' : nodeStates.risk === 'failed' ? 'url(#glow-red)' : 'none' }}
                      />
                      <foreignObject x="266" y="66" width="28" height="28">
                        <div className="flex items-center justify-center w-full h-full" style={{ color: getNodeStyles(nodeStates.risk).iconColor }}>
                          <ShieldCheck size={16} />
                        </div>
                      </foreignObject>
                      <text x="280" y="125" textAnchor="middle" fill={getNodeStyles(nodeStates.risk).textColor} className="text-[11px] font-semibold tracking-wide">Risk Gate</text>
                      <text x="280" y="140" textAnchor="middle" fill="#77736c" className="text-[9px] font-mono">Locks & Limits</text>
                    </g>

                    {/* Node 3: Dhan Preflight */}
                    <g>
                      <circle
                        cx="480"
                        cy="80"
                        r="25"
                        fill={getNodeStyles(nodeStates.preflight).fill}
                        stroke={getNodeStyles(nodeStates.preflight).stroke}
                        strokeWidth={getNodeStyles(nodeStates.preflight).borderWidth}
                        className={getNodeStyles(nodeStates.preflight).pulseCls}
                        style={{ filter: nodeStates.preflight === 'success' || nodeStates.preflight === 'active' ? 'url(#glow-green)' : nodeStates.preflight === 'failed' ? 'url(#glow-red)' : 'none' }}
                      />
                      <foreignObject x="466" y="66" width="28" height="28">
                        <div className="flex items-center justify-center w-full h-full" style={{ color: getNodeStyles(nodeStates.preflight).iconColor }}>
                          <RadioTower size={16} />
                        </div>
                      </foreignObject>
                      <text x="480" y="125" textAnchor="middle" fill={getNodeStyles(nodeStates.preflight).textColor} className="text-[11px] font-semibold tracking-wide">Broker Check</text>
                      <text x="480" y="140" textAnchor="middle" fill="#77736c" className="text-[9px] font-mono">Dhan Preflight</text>
                    </g>

                    {/* Node 4: Order Route */}
                    <g>
                      <circle
                        cx="680"
                        cy="80"
                        r="25"
                        fill={getNodeStyles(nodeStates.route).fill}
                        stroke={getNodeStyles(nodeStates.route).stroke}
                        strokeWidth={getNodeStyles(nodeStates.route).borderWidth}
                        className={getNodeStyles(nodeStates.route).pulseCls}
                        style={{ filter: nodeStates.route !== 'idle' ? 'url(#glow-green)' : 'none' }}
                      />
                      <foreignObject x="666" y="66" width="28" height="28">
                        <div className="flex items-center justify-center w-full h-full" style={{ color: getNodeStyles(nodeStates.route).iconColor }}>
                          <Zap size={16} />
                        </div>
                      </foreignObject>
                      <text x="680" y="125" textAnchor="middle" fill={getNodeStyles(nodeStates.route).textColor} className="text-[11px] font-semibold tracking-wide">Route Order</text>
                      <text x="680" y="140" textAnchor="middle" fill="#77736c" className="text-[9px] font-mono">Dhan execution</text>
                    </g>
                  </svg>

                  {/* Mobile flowchart (vertical) */}
                  <svg 
                    viewBox="0 0 240 440" 
                    className="block md:hidden w-full max-w-[300px] h-[400px] overflow-visible select-none flex-shrink-0"
                  >
                    <defs>
                      <filter id="glow-green-mobile" x="-20%" y="-20%" width="140%" height="140%">
                        <feGaussianBlur stdDeviation="3.5" result="blur" />
                        <feComposite in="SourceGraphic" in2="blur" operator="over" />
                      </filter>
                      <filter id="glow-orange-mobile" x="-20%" y="-20%" width="140%" height="140%">
                        <feGaussianBlur stdDeviation="3.5" result="blur" />
                        <feComposite in="SourceGraphic" in2="blur" operator="over" />
                      </filter>
                      <filter id="glow-red-mobile" x="-20%" y="-20%" width="140%" height="140%">
                        <feGaussianBlur stdDeviation="3.5" result="blur" />
                        <feComposite in="SourceGraphic" in2="blur" operator="over" />
                      </filter>
                    </defs>

                    {/* Vertical background connector lines */}
                    <path
                      d="M 60 50 C 30 105, 30 105, 60 160"
                      fill="none"
                      stroke="#1d1c19"
                      strokeWidth="3.5"
                      strokeLinecap="round"
                    />
                    <path
                      d="M 60 160 C 90 215, 90 215, 60 270"
                      fill="none"
                      stroke="#1d1c19"
                      strokeWidth="3.5"
                      strokeLinecap="round"
                    />
                    <path
                      d="M 60 270 C 30 325, 30 325, 60 380"
                      fill="none"
                      stroke="#1d1c19"
                      strokeWidth="3.5"
                      strokeLinecap="round"
                    />

                    {/* Vertical active flow overlay paths */}
                    <path
                      d="M 60 50 C 30 105, 30 105, 60 160"
                      fill="none"
                      opacity={getPathStyles(pathStates.p1).opacity}
                      stroke={getPathStyles(pathStates.p1).stroke}
                      strokeWidth="3.5"
                      strokeLinecap="round"
                      strokeDasharray={getPathStyles(pathStates.p1).strokeDasharray}
                      className={getPathStyles(pathStates.p1).className}
                    />
                    <path
                      d="M 60 160 C 90 215, 90 215, 60 270"
                      fill="none"
                      opacity={getPathStyles(pathStates.p2).opacity}
                      stroke={getPathStyles(pathStates.p2).stroke}
                      strokeWidth="3.5"
                      strokeLinecap="round"
                      strokeDasharray={getPathStyles(pathStates.p2).strokeDasharray}
                      className={getPathStyles(pathStates.p2).className}
                    />
                    <path
                      d="M 60 270 C 30 325, 30 325, 60 380"
                      fill="none"
                      opacity={getPathStyles(pathStates.p3).opacity}
                      stroke={getPathStyles(pathStates.p3).stroke}
                      strokeWidth="3.5"
                      strokeLinecap="round"
                      strokeDasharray={getPathStyles(pathStates.p3).strokeDasharray}
                      className={getPathStyles(pathStates.p3).className}
                    />

                    {/* Nodes (Vertical) */}
                    
                    {/* Node 1: Webhook */}
                    <g>
                      <circle
                        cx="60"
                        cy="50"
                        r="22"
                        fill={getNodeStyles(nodeStates.webhook).fill}
                        stroke={getNodeStyles(nodeStates.webhook).stroke}
                        strokeWidth={getNodeStyles(nodeStates.webhook).borderWidth}
                        className={getNodeStyles(nodeStates.webhook).pulseCls}
                        style={{ filter: nodeStates.webhook !== 'idle' ? 'url(#glow-green-mobile)' : 'none' }}
                      />
                      <foreignObject x="48" y="38" width="24" height="24">
                        <div className="flex items-center justify-center w-full h-full" style={{ color: getNodeStyles(nodeStates.webhook).iconColor }}>
                          <Webhook size={14} />
                        </div>
                      </foreignObject>
                      <text x="100" y="46" textAnchor="start" dominantBaseline="middle" fill={getNodeStyles(nodeStates.webhook).textColor} className="text-[12px] font-semibold tracking-wide">TV Alert</text>
                      <text x="100" y="58" textAnchor="start" dominantBaseline="middle" fill="#77736c" className="text-[10px] font-mono">Webhook</text>
                    </g>

                    {/* Node 2: Risk Gate */}
                    <g>
                      <circle
                        cx="60"
                        cy="160"
                        r="22"
                        fill={getNodeStyles(nodeStates.risk).fill}
                        stroke={getNodeStyles(nodeStates.risk).stroke}
                        strokeWidth={getNodeStyles(nodeStates.risk).borderWidth}
                        className={getNodeStyles(nodeStates.risk).pulseCls}
                        style={{ filter: nodeStates.risk === 'success' || nodeStates.risk === 'active' ? 'url(#glow-green-mobile)' : nodeStates.risk === 'blocked' ? 'url(#glow-orange-mobile)' : nodeStates.risk === 'failed' ? 'url(#glow-red-mobile)' : 'none' }}
                      />
                      <foreignObject x="48" y="148" width="24" height="24">
                        <div className="flex items-center justify-center w-full h-full" style={{ color: getNodeStyles(nodeStates.risk).iconColor }}>
                          <ShieldCheck size={14} />
                        </div>
                      </foreignObject>
                      <text x="100" y="156" textAnchor="start" dominantBaseline="middle" fill={getNodeStyles(nodeStates.risk).textColor} className="text-[12px] font-semibold tracking-wide">Risk Gate</text>
                      <text x="100" y="168" textAnchor="start" dominantBaseline="middle" fill="#77736c" className="text-[10px] font-mono">Locks & Limits</text>
                    </g>

                    {/* Node 3: Dhan Preflight */}
                    <g>
                      <circle
                        cx="60"
                        cy="270"
                        r="22"
                        fill={getNodeStyles(nodeStates.preflight).fill}
                        stroke={getNodeStyles(nodeStates.preflight).stroke}
                        strokeWidth={getNodeStyles(nodeStates.preflight).borderWidth}
                        className={getNodeStyles(nodeStates.preflight).pulseCls}
                        style={{ filter: nodeStates.preflight === 'success' || nodeStates.preflight === 'active' ? 'url(#glow-green-mobile)' : nodeStates.preflight === 'failed' ? 'url(#glow-red-mobile)' : 'none' }}
                      />
                      <foreignObject x="48" y="258" width="24" height="24">
                        <div className="flex items-center justify-center w-full h-full" style={{ color: getNodeStyles(nodeStates.preflight).iconColor }}>
                          <RadioTower size={14} />
                        </div>
                      </foreignObject>
                      <text x="100" y="266" textAnchor="start" dominantBaseline="middle" fill={getNodeStyles(nodeStates.preflight).textColor} className="text-[12px] font-semibold tracking-wide">Broker Check</text>
                      <text x="100" y="278" textAnchor="start" dominantBaseline="middle" fill="#77736c" className="text-[10px] font-mono">Dhan Preflight</text>
                    </g>

                    {/* Node 4: Order Route */}
                    <g>
                      <circle
                        cx="60"
                        cy="380"
                        r="22"
                        fill={getNodeStyles(nodeStates.route).fill}
                        stroke={getNodeStyles(nodeStates.route).stroke}
                        strokeWidth={getNodeStyles(nodeStates.route).borderWidth}
                        className={getNodeStyles(nodeStates.route).pulseCls}
                        style={{ filter: nodeStates.route !== 'idle' ? 'url(#glow-green-mobile)' : 'none' }}
                      />
                      <foreignObject x="48" y="368" width="24" height="24">
                        <div className="flex items-center justify-center w-full h-full" style={{ color: getNodeStyles(nodeStates.route).iconColor }}>
                          <Zap size={14} />
                        </div>
                      </foreignObject>
                      <text x="100" y="376" textAnchor="start" dominantBaseline="middle" fill={getNodeStyles(nodeStates.route).textColor} className="text-[12px] font-semibold tracking-wide">Route Order</text>
                      <text x="100" y="388" textAnchor="start" dominantBaseline="middle" fill="#77736c" className="text-[10px] font-mono">Dhan execution</text>
                    </g>
                  </svg>
                </div>

                {/* Simulated Logs Terminal Feed */}
                <div className="flex-1 flex flex-col bg-[#080807] p-5 font-mono text-[12px] h-[300px] overflow-hidden justify-between">
                  <div className="flex items-center justify-between border-b border-[#1c1c19] pb-2 text-[#5e5a53] select-none text-[11px]">
                    <div className="flex items-center gap-1.5">
                      <Terminal size={12} />
                      <span>NOVA SIGNAL ROUTER RUNTIME SHELL</span>
                    </div>
                    <span>LOG_LEVEL: TRACE</span>
                  </div>

                  <div 
                    ref={terminalRef}
                    className="flex-1 overflow-y-auto mt-3 space-y-1.5 pr-2 terminal-scrollbar"
                  >
                    {logs.length === 0 ? (
                      <div className="text-[#555555] italic h-full flex items-center justify-center">
                        Waiting to receive webhook alerts. Click a preset to simulate an execution stream.
                      </div>
                    ) : (
                      logs.map((log) => {
                        const colorClass =
                          log.type === 'success' ? 'text-[#98e94d]' :
                          log.type === 'warn' ? 'text-[#f59e0b]' :
                          log.type === 'error' ? 'text-[#ef4444]' : 'text-[#77736c]'

                        return (
                          <div key={log.id} className="flex gap-2 log-row-enter">
                            <span className="text-[#444444] select-none">[{log.time}]</span>
                            <span className={`font-bold select-none ${
                              log.type === 'success' ? 'text-[#98e94d]' :
                              log.type === 'warn' ? 'text-[#f59e0b]' :
                              log.type === 'error' ? 'text-[#ef4444]' : 'text-[#8b8780]'
                            }`}>
                              {log.type.toUpperCase()}
                            </span>
                            <span className={colorClass}>{log.text}</span>
                          </div>
                        )
                      })
                    )}
                  </div>
                </div>

              </div>

            </div>

          </div>
        </section>

        {/* Why NOVA section: Grid Details */}
        <section className="flex flex-col gap-6 py-4">
          <div className="text-center max-w-2xl mx-auto space-y-2">
            <h2 className="text-2xl font-bold tracking-wide">Broker-Aware Gateway Security</h2>
            <p className="text-sm text-[#9a968f]">
              Unlike basic forwarding bots, NOVA intercepts, verifies, and preflights every signal to guarantee clean trade states.
            </p>
          </div>

          <div className="grid md:grid-cols-2 gap-5 mt-4">
            {SYSTEM_ADVANTAGES.map((adv, i) => {
              const Icon = adv.icon
              return (
                <div key={i} className="landing-card bg-[#11110f]/60 hover:border-[#98e94d]/30 transition-all flex gap-4 items-start p-5 group">
                  <div className="flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-xl bg-[#1c1c19] border border-[#2b2a26] text-[#9a968f] group-hover:text-[#98e94d] group-hover:border-[#98e94d]/20 group-hover:bg-[#11170d]/20 transition-all">
                    <Icon size={16} />
                  </div>
                  <div className="space-y-1">
                    <h3 className="text-sm font-bold text-[#f4f1ea]">{adv.title}</h3>
                    <p className="text-xs text-[#77736c] leading-relaxed">{adv.desc}</p>
                  </div>
                </div>
              )
            })}
          </div>
        </section>

        {/* Security & Token Storage */}
        <section className="rounded-2xl p-6 border border-[#24231f] bg-gradient-to-r from-[#111110] to-[#0d0d0c] relative overflow-hidden flex flex-col md:flex-row md:items-center justify-between gap-6">
          <div className="absolute top-0 right-0 w-[300px] h-[300px] bg-[radial-gradient(circle_at_top_right,_rgba(152,233,77,0.015)_0%,_rgba(0,0,0,0)_70%)] pointer-events-none" />
          
          <div className="space-y-2 max-w-xl">
            <div className="inline-flex items-center gap-1.5 text-xs text-[#98e94d] bg-[#11170d]/60 border border-[#26331d] px-2.5 py-1 rounded-full font-medium">
              <DollarSign size={12} /> Local Credential Isolation
            </div>
            <h3 className="text-lg font-bold">Encrypted Local Key Management</h3>
            <p className="text-xs text-[#77736c] leading-relaxed">
              Your broker API tokens are stored locally on your server filesystem or process environment variables. No database stores your keys, ensuring that your trading funds remain entirely secure.
            </p>
          </div>

          <div className="flex items-center gap-4 flex-shrink-0">
            <div className="text-right hidden sm:block">
              <p className="text-xs text-[#77736c]">Dhan API Latency</p>
              <p className="text-xs font-mono font-bold text-[#98e94d]">&lt; 5ms average</p>
            </div>
            <button
              onClick={() => navigate('/app/setup')}
              className="rounded-full bg-[#1b1a17] border border-[#2b2a26] text-xs font-semibold px-5 py-3 text-[#d8d3c8] hover:bg-[#24231f] hover:text-[#f4f1ea] hover:border-[#3a3933] cursor-pointer transition-all flex items-center gap-1.5"
            >
              Configure Token <Layers size={13} />
            </button>
          </div>
        </section>

      </main>
      
      {/* Footer */}
      <footer className="border-t border-[#1c1c19] bg-[#090908] py-8 text-center text-xs text-[#5e5a53] font-mono mt-10">
        <p>© 2026 NOVA Signal Router. Running sandbox engine build v2.4.0</p>
      </footer>
    </div>
  )
}
