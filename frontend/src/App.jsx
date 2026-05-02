import { useState, useEffect, useCallback, useRef } from 'react'
import { useWebSocket } from './hooks/useWebSocket'
import { api } from './hooks/useApi'
import Header from './components/Header'
import Dashboard from './components/Dashboard'
import RiskBanner from './components/RiskBanner'
import UnifiedPortfolio from './components/UnifiedPortfolio'
import AlertFeed from './components/AlertFeed'
import Settings from './components/Settings'
import EditModal from './components/EditModal'
import UnwindView from './components/UnwindView'
import TransactionHistory from './components/TransactionHistory'

export default function App() {
  const [holdings, setHoldings] = useState([])
  const [marketOpen, setMarketOpen] = useState(false)
  const [alerts, setAlerts] = useState([])
  const [showSettings, setShowSettings] = useState(false)
  const [editTarget, setEditTarget] = useState(null)
  const [historyTarget, setHistoryTarget] = useState(null)
  const [lastUpdate, setLastUpdate] = useState(null)
  const quotesRef = useRef({})

  const loadPortfolio = useCallback(async () => {
    try { setHoldings(await api.getPortfolio()) } catch {}
  }, [])

  useEffect(() => { loadPortfolio() }, [loadPortfolio])

  const handleWsMessage = useCallback((msg) => {
    if (msg.type === 'price_update') {
      quotesRef.current = msg.data
      setMarketOpen(msg.market_open || false)
      setLastUpdate(new Date())
      setHoldings(prev => prev.map(h => {
        const q = msg.data[h.stock_code]
        if (!q) return h
        const currentPrice = q.price
        const pnl = (currentPrice - h.cost_price) * h.shares
        const pnlPct = h.cost_price > 0 ? (currentPrice - h.cost_price) / h.cost_price * 100 : 0
        return {
          ...h,
          current_price: currentPrice,
          price_change_pct: q.change_pct,
          unrealized_pnl: Math.round(pnl * 100) / 100,
          pnl_pct: Math.round(pnlPct * 100) / 100,
          market_value: Math.round(currentPrice * h.shares * 100) / 100,
        }
      }))
    } else if (msg.type === 'alert') {
      setAlerts(prev => [{ ...msg.data, id: Date.now(), time: new Date() }, ...prev].slice(0, 50))
      const notifyOn = localStorage.getItem('notifyEnabled') !== 'false'
      const soundOn = localStorage.getItem('notifySound') !== 'false'
      if (!notifyOn) return
      if ('Notification' in window && Notification.permission === 'granted') {
        new Notification(`持仓提醒: ${msg.data.stock_name}`, { body: msg.data.message })
      }
      if (soundOn) {
        try {
          const ctx = new (window.AudioContext || window.webkitAudioContext)()
          const osc = ctx.createOscillator()
          const gain = ctx.createGain()
          osc.connect(gain).connect(ctx.destination)
          osc.frequency.value = 880
          gain.gain.value = 0.2
          osc.start()
          osc.stop(ctx.currentTime + 0.12)
        } catch {}
      }
    }
  }, [])

  useWebSocket(handleWsMessage)

  const handleRefresh = () => loadPortfolio()
  const handleHoldingChange = () => loadPortfolio()

  return (
    <div className="min-h-screen">
      <Header
        marketOpen={marketOpen}
        lastUpdate={lastUpdate}
        onRefresh={handleRefresh}
        onSettings={() => setShowSettings(!showSettings)}
      />

      <Dashboard holdings={holdings} />
      <RiskBanner holdings={holdings} />

      <main className="max-w-[1440px] mx-auto px-4 py-4 space-y-4">
        {showSettings && <Settings onClose={() => setShowSettings(false)} />}

        <UnifiedPortfolio
          holdings={holdings}
          onEdit={setEditTarget}
          onHistory={setHistoryTarget}
          onAdd={handleHoldingChange}
        />

        <UnwindView />

        <AlertFeed alerts={alerts} onClear={() => setAlerts([])} holdings={holdings} />
      </main>

      {editTarget && (
        <EditModal
          holding={editTarget}
          onClose={() => setEditTarget(null)}
          onChange={handleHoldingChange}
        />
      )}

      {historyTarget && (
        <TransactionHistory
          stockCode={historyTarget.stock_code}
          stockName={historyTarget.stock_name}
          onClose={() => setHistoryTarget(null)}
          onChange={handleHoldingChange}
        />
      )}
    </div>
  )
}
