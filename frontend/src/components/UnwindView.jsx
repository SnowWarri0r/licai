import { useState, useEffect, useCallback } from 'react'
import { fetchJSON } from '../hooks/useApi'
import UnwindCard from './UnwindCard'
import MorningBriefing from './MorningBriefing'
import SectorRadar from './SectorRadar'
import SectorOpportunities from './SectorOpportunities'
import AllocationAdvisor from './AllocationAdvisor'
import AShareSectorGap from './AShareSectorGap'
import Cashflow from './Cashflow'
import MacroDashboard from './MacroDashboard'

const TABS = [
  { key: 'sector',   label: '板块',   desc: '动量 / 资金流 / 早盘速览' },
  { key: 'macro',    label: '宏观',   desc: '指数 / 汇率 / 商品' },
  { key: 'config',   label: '配置',   desc: '现金流 / 大类 / A 股缺口' },
  { key: 'holdings', label: '持仓',   desc: '减仓阶梯 / 解套档位' },
]

export default function UnwindView() {
  const [plans, setPlans] = useState([])
  const [loading, setLoading] = useState(true)
  const [tab, setTab] = useState(() => localStorage.getItem('unwindTab') || 'sector')

  const loadPlans = useCallback(async () => {
    try {
      setPlans(await fetchJSON('/api/unwind/plans'))
    } catch (e) {
      console.error(e)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    loadPlans()
    const t = setInterval(loadPlans, 30000)
    return () => clearInterval(t)
  }, [loadPlans])

  const pickTab = (k) => { setTab(k); localStorage.setItem('unwindTab', k) }

  if (loading) {
    return <div className="text-center py-8 text-text-dim text-[13px]">加载中...</div>
  }
  if (plans.length === 0 && tab === 'holdings') {
    return (
      <div className="space-y-4">
        <TabBar tab={tab} onPick={pickTab} />
        <div className="text-center py-8 text-text-dim text-[13px]">暂无持仓</div>
      </div>
    )
  }

  return (
    <div className="space-y-4">
      <TabBar tab={tab} onPick={pickTab} />

      {tab === 'sector' && (
        <>
          <MorningBriefing />
          <SectorRadar />
          <SectorOpportunities />
        </>
      )}

      {tab === 'macro' && (
        <MacroDashboard />
      )}

      {tab === 'config' && (
        <>
          <Cashflow />
          <AllocationAdvisor />
          <AShareSectorGap />
        </>
      )}

      {tab === 'holdings' && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {plans.filter(p => (p.shares || 0) > 0).map(p => (
            <UnwindCard key={p.stock_code} plan={p} onChange={loadPlans} />
          ))}
        </div>
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
