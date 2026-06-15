import { useState } from 'react'
import MorningBriefing from './MorningBriefing'
import SentimentThermometer from './SentimentThermometer'
import HotRank from './HotRank'
import SectorMatrix from './SectorMatrix'
import SectorRadar from './SectorRadar'
import SectorOpportunities from './SectorOpportunities'
import AllocationAdvisor from './AllocationAdvisor'
import AShareSectorGap from './AShareSectorGap'
import BenchmarkCompare from './BenchmarkCompare'
import Cashflow from './Cashflow'
import MacroDashboard from './MacroDashboard'
import PortfolioNews from './PortfolioNews'
import DailyReview from './DailyReview'
import AITradeReview from './AITradeReview'

const TABS = [
  { key: 'sector',   label: '板块',   desc: '动量 / 资金流 / 早盘速览' },
  { key: 'macro',    label: '宏观',   desc: '指数 / 汇率 / 商品' },
  { key: 'news',     label: '资讯',   desc: '收盘复盘 / 持仓新闻' },
  { key: 'config',   label: '复盘',   desc: '交易复盘 / 现金流 / 跑赢基准' },
]
const TAB_KEYS = TABS.map(t => t.key)

export default function UnwindView() {
  const [tab, setTab] = useState(() => {
    const saved = localStorage.getItem('unwindTab')
    return TAB_KEYS.includes(saved) ? saved : 'sector'  // 旧的 'holdings' 已移除, 回退板块
  })

  const pickTab = (k) => { setTab(k); localStorage.setItem('unwindTab', k) }

  return (
    <div className="space-y-4">
      <TabBar tab={tab} onPick={pickTab} />

      {tab === 'sector' && (
        <>
          <MorningBriefing />
          <SentimentThermometer />
          <SectorMatrix />
          <HotRank />
          <SectorRadar />
          <SectorOpportunities />
        </>
      )}

      {tab === 'macro' && (
        <MacroDashboard />
      )}

      {tab === 'news' && (
        <div className="space-y-4">
          <DailyReview />
          <PortfolioNews />
        </div>
      )}

      {tab === 'config' && (
        <>
          <AITradeReview />
          <BenchmarkCompare />
          <Cashflow />
          <AllocationAdvisor />
          <AShareSectorGap />
        </>
      )}
    </div>
  )
}

function TabBar({ tab, onPick }) {
  return (
    <div className="flex gap-1 border-b border-border-subtle overflow-x-auto"
      style={{ scrollbarWidth: 'none' }}>
      {TABS.map(t => {
        const active = tab === t.key
        return (
          <button key={t.key} onClick={() => onPick(t.key)}
            className="px-3 py-2 text-[12px] whitespace-nowrap cursor-pointer transition-colors relative"
            style={{
              color: active ? 'var(--color-text-bright)' : 'var(--color-text-dim)',
              fontWeight: active ? 600 : 400,
            }}>
            {t.label}
            <span className="ml-1.5 text-[10.5px] text-text-muted font-normal">{t.desc}</span>
            {active && (
              <span className="absolute left-2 right-2 -bottom-px h-[2px] rounded-full bg-accent" />
            )}
          </button>
        )
      })}
    </div>
  )
}
