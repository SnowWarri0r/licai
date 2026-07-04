import { useState, useEffect, useCallback, useRef } from 'react'
import { useWebSocket } from './hooks/useWebSocket'
import { api } from './hooks/useApi'
import Header from './components/Header'
import Sidebar from './components/Sidebar'
import Dashboard from './components/Dashboard'
import RiskBanner from './components/RiskBanner'
import UnifiedPortfolio from './components/UnifiedPortfolio'
import Rankings from './components/Rankings'
import StockAsk from './components/StockAsk'
import Settings from './components/Settings'
import EditModal from './components/EditModal'
import TransactionHistory from './components/TransactionHistory'
// 板块
import MorningBriefing from './components/MorningBriefing'
import SentimentThermometer from './components/SentimentThermometer'
import SectorMatrix from './components/SectorMatrix'
import HotRank from './components/HotRank'
import SectorRadar from './components/SectorRadar'
import SectorOpportunities from './components/SectorOpportunities'
// 宏观
import MacroDashboard from './components/MacroDashboard'
// 资讯
import Jin10Flash from './components/Jin10Flash'
import DailyReview from './components/DailyReview'
import PortfolioNews from './components/PortfolioNews'
// 复盘
import AITradeReview from './components/AITradeReview'
import BenchmarkCompare from './components/BenchmarkCompare'
import Cashflow from './components/Cashflow'
import AllocationAdvisor from './components/AllocationAdvisor'
import AShareSectorGap from './components/AShareSectorGap'

export default function App() {
  const [holdings, setHoldings] = useState([])
  const [marketOpen, setMarketOpen] = useState(false)
  const [editTarget, setEditTarget] = useState(null)
  const [historyTarget, setHistoryTarget] = useState(null)
  const [lastUpdate, setLastUpdate] = useState(null)
  const _VIEWS = ['dashboard', 'portfolio', 'sector', 'rankings', 'macro', 'news', 'review', 'ask', 'settings']
  const [view, _setView] = useState(() => {
    const h = (window.location.hash || '').slice(1)
    return _VIEWS.includes(h) ? h : 'dashboard'
  })
  const setView = (v) => { _setView(v); try { window.location.hash = v } catch {} }
  const [sidebarOpen, setSidebarOpen] = useState(true)
  const [dataVersion, setDataVersion] = useState(0)
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
        const fxRate = q.fx_rate || h.fx_rate || 1
        const originalCostValue = h.cost_price * h.shares
        const originalMarketValue = currentPrice * h.shares
        const pnl = (originalMarketValue - originalCostValue) * fxRate
        const pnlPct = h.cost_price > 0 ? (currentPrice - h.cost_price) / h.cost_price * 100 : 0
        return {
          ...h,
          current_price: currentPrice,
          fx_rate: fxRate,
          fx_time: q.fx_time || h.fx_time || '',
          fx_source: q.fx_source || h.fx_source || '',
          price_change_pct: q.change_pct,
          unrealized_pnl: Math.round(pnl * 100) / 100,
          pnl_pct: Math.round(pnlPct * 100) / 100,
          original_cost_value: Math.round(originalCostValue * 100) / 100,
          original_market_value: Math.round(originalMarketValue * 100) / 100,
          cost_value: Math.round(originalCostValue * fxRate * 100) / 100,
          market_value: Math.round(originalMarketValue * fxRate * 100) / 100,
        }
      }))
    }
  }, [])
  useWebSocket(handleWsMessage)

  const handleHoldingChange = () => { loadPortfolio(); setDataVersion(v => v + 1) }

  const PAD = 'max-w-[1440px] mx-auto px-2 md:px-4 py-3 md:py-4'

  return (
    <div className="min-h-screen flex flex-col">
      <Header
        marketOpen={marketOpen}
        lastUpdate={lastUpdate}
        onRefresh={loadPortfolio}
        onSettings={() => setView('settings')}
      />

      <div className="flex flex-1 min-h-0">
        <Sidebar active={view} onNav={setView} open={sidebarOpen} onToggle={() => setSidebarOpen(o => !o)} />

        <main className="flex-1 min-w-0 overflow-y-auto">
          {view === 'dashboard' && (
            <>
              <Dashboard holdings={holdings} />
              <RiskBanner holdings={holdings} />
            </>
          )}

          {view === 'portfolio' && (
            <div className={`${PAD} space-y-3 md:space-y-4`}>
              <UnifiedPortfolio
                holdings={holdings}
                onEdit={setEditTarget}
                onHistory={setHistoryTarget}
                onAdd={handleHoldingChange}
                dataVersion={dataVersion}
              />
            </div>
          )}

          {view === 'sector' && (
            <div className={`${PAD} space-y-3 md:space-y-4`}>
              <MorningBriefing />
              <SentimentThermometer />
              <SectorMatrix />
              <HotRank />
              <SectorRadar />
              <SectorOpportunities />
            </div>
          )}

          {view === 'rankings' && (
            <div className={PAD}>
              <Rankings />
            </div>
          )}

          {view === 'macro' && (
            <div className={`${PAD} space-y-3 md:space-y-4`}>
              <MacroDashboard />
            </div>
          )}

          {view === 'news' && (
            <div className={`${PAD} space-y-3 md:space-y-4`}>
              <Jin10Flash />
              <DailyReview />
              <PortfolioNews />
            </div>
          )}

          {view === 'review' && (
            <div className={`${PAD} space-y-3 md:space-y-4`}>
              <AITradeReview />
              <BenchmarkCompare />
              <Cashflow />
              <AllocationAdvisor />
              <AShareSectorGap />
            </div>
          )}

          {view === 'ask' && (
            <div className={`${PAD} max-w-[900px]`}>
              <StockAsk page />
            </div>
          )}

          {view === 'settings' && (
            <div className={`${PAD} max-w-[900px]`}>
              <Settings onClose={() => setView('dashboard')} />
            </div>
          )}
        </main>
      </div>

      {editTarget && (
        <EditModal holding={editTarget} onClose={() => setEditTarget(null)} onChange={handleHoldingChange} />
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
