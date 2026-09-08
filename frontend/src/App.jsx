import { useState, useEffect, useCallback, useRef } from 'react'
import { useWebSocket } from './hooks/useWebSocket'
import { api } from './hooks/useApi'
import Header from './components/Header'
import Sidebar, { NAV } from './components/Sidebar'
import Dashboard from './components/Dashboard'
import RiskBanner from './components/RiskBanner'
import StaleBundleNotice from './components/StaleBundleNotice'
import UnifiedPortfolio from './components/UnifiedPortfolio'
import Rankings from './components/Rankings'
import StockAsk from './components/StockAsk'
import Settings from './components/Settings'
import EditModal from './components/EditModal'
import TransactionHistory from './components/TransactionHistory'
// 市场·开盘情绪
import MorningBriefing from './components/MorningBriefing'
import SentimentThermometer from './components/SentimentThermometer'
// 市场·板块
import SectorShare from './components/SectorShare'
import SectorMatrix from './components/SectorMatrix'
import SectorOpportunities from './components/SectorOpportunities'
import EtfXray from './components/EtfXray'
// 市场·资金机构 / 宏观 / 资讯
import KplInstTheme from './components/KplInstTheme'
import MacroDashboard from './components/MacroDashboard'
import PortfolioNews from './components/PortfolioNews'
// 我的·资金现金流
import Cashflow from './components/Cashflow'
// 我的·绩效基准
import BenchmarkCompare from './components/BenchmarkCompare'
import SectorRadar from './components/SectorRadar'
import PortfolioCurve from './components/PortfolioCurve'
import PortfolioCorrelation from './components/PortfolioCorrelation'
// 我的·配置建议
import AllocationAdvisor from './components/AllocationAdvisor'
import AShareSectorGap from './components/AShareSectorGap'
// 我的·复盘
import AITradeReview from './components/AITradeReview'

// 合法 view key。setView 与 hash 初始化共用 —— 只在初始化校验的话,
// 运行时传入非法 key(历史上有已删视图的残留调用)会让内容区渲染成空白。
// 直接从侧栏 NAV 派生, 不再手抄一份: 两份列表漂移时两个方向都是静默失败
// (NAV 多一个 → 点了跳回持仓; 这边多一个 → 一个谁也点不到的页), 都不报错。
const VIEWS = NAV.flatMap(s => s.items.map(i => i.key))
const normalizeView = (v) => (VIEWS.includes(v) ? v : 'portfolio')

// 与 BenchmarkCompare/SectorRadar 同款的 section 外壳 + 标题带。给"自身没有卡壳"的
// 组件用: 那类组件原先住在别的卡内部(根节点只是 mb-3), 单独上页会浮在背景上。
// [&>div]:mb-0 抹掉组件自带的 mb-3, 否则会和页面 space-y-3 叠成双份间距。
function CardShell({ title, sub, children }) {
  return (
    <section className="rounded-xl border border-border bg-surface/60 overflow-hidden"
      style={{ animation: 'fade-up 0.4s ease-out' }}>
      <div className="px-3 md:px-5 py-3 border-b border-border flex items-baseline gap-2 flex-wrap"
        style={{ background: 'linear-gradient(180deg, var(--color-surface-2), var(--color-surface))' }}>
        <h3 className="text-[13px] font-semibold text-text-bright m-0">{title}</h3>
        <span className="text-[11px] text-text-dim">{sub}</span>
      </div>
      <div className="px-3 md:px-5 py-3 [&>div]:mb-0">
        {children}
      </div>
    </section>
  )
}

export default function App() {
  const [holdings, setHoldings] = useState([])
  const [marketOpen, setMarketOpen] = useState(false)
  const [editTarget, setEditTarget] = useState(null)
  const [historyTarget, setHistoryTarget] = useState(null)
  const [lastUpdate, setLastUpdate] = useState(null)
  const [view, _setView] = useState(() => {
    // 支持 #view?k=v 形式的 deep-link(子参数由各组件自行读取)
    const h = (window.location.hash || '').slice(1).split('?')[0]
    return normalizeView(h)
  })
  const setView = (v) => {
    const next = normalizeView(v)
    _setView(next)
    try { window.location.hash = next } catch {}
  }
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
    <div className="h-screen flex flex-col overflow-hidden">
      <Header
        marketOpen={marketOpen}
        lastUpdate={lastUpdate}
        onRefresh={loadPortfolio}
        onSettings={() => setView('settings')}
      />

      <div className="flex flex-1 min-h-0">
        <Sidebar active={view} onNav={setView} open={sidebarOpen} onToggle={() => setSidebarOpen(o => !o)} />

        <main className="flex-1 min-w-0 flex flex-col min-h-0">
          {/* 仪表盘概览条 + 风险条: 固定在内容区顶部, 不随内容滚动 */}
          <div className="shrink-0">
            <Dashboard holdings={holdings} />
            <RiskBanner holdings={holdings} />
          </div>

          {/* 视图内容: 唯一滚动区 — 侧边栏/顶栏/仪表盘全部固定 */}
          <div className="flex-1 min-h-0 overflow-y-auto">

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
              <SectorShare />
              <SectorMatrix />
              <SectorOpportunities />
              <EtfXray mode="theme" />
            </div>
          )}

          {view === 'cashflow' && (
            <div className={`${PAD} space-y-3 md:space-y-4`}>
              <Cashflow />
            </div>
          )}

          {view === 'performance' && (
            <div className={`${PAD} space-y-3 md:space-y-4`}>
              <BenchmarkCompare />
              <SectorRadar />
              {/* 这两张卡原先住在 AI 复盘卡内部, 自身没有卡壳 —— 外壳见上面的 CardShell */}
              <CardShell title="盈亏曲线" sub="时间加权 · 对照沪深300">
                <PortfolioCurve />
              </CardShell>

              <CardShell title="同源风险" sub="在持标的两两相关性 · 分散是否名义">
                <PortfolioCorrelation />
              </CardShell>
            </div>
          )}

          {view === 'allocation' && (
            <div className={`${PAD} space-y-3 md:space-y-4`}>
              <AllocationAdvisor />
              <AShareSectorGap />
              <EtfXray mode="mine" />
            </div>
          )}

          {view === 'open' && (
            <div className={`${PAD} space-y-3 md:space-y-4`}>
              <MorningBriefing />
              <SentimentThermometer />
            </div>
          )}

          {view === 'capital' && (
            <div className={`${PAD} space-y-3 md:space-y-4`}>
              <KplInstTheme />
            </div>
          )}

          {view === 'rankings' && (
            // 榜单是"占满一屏"的视图: 外壳撑满滚动区, 面板自己吃掉剩余高度。
            // 原来面板写死 h-[calc(100vh-11rem)] —— 那 11rem 是照着某个窗口估的顶栏高度,
            // 窗口一变高、顶栏一换行就对不上, 底下留出一条谁也用不上的空带。
            <div className={`${PAD} h-full flex flex-col`}>
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
              <PortfolioNews />
            </div>
          )}

          {view === 'review' && (
            <div className={`${PAD} space-y-3 md:space-y-4`}>
              <AITradeReview />
            </div>
          )}

          {view === 'ask' && (
            <div className={`${PAD} max-w-[900px] h-[calc(100vh-8rem)]`}>
              <StockAsk page />
            </div>
          )}

          {view === 'settings' && (
            <div className={`${PAD} max-w-[900px]`}>
              <Settings onClose={() => setView('portfolio')} />
            </div>
          )}
          </div>
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
      <StaleBundleNotice />
    </div>
  )
}
