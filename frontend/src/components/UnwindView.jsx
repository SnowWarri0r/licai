import { useState, useEffect, useCallback } from 'react'
import { fetchJSON } from '../hooks/useApi'
import UnwindCard from './UnwindCard'
import BudgetAllocator from './BudgetAllocator'
import MorningBriefing from './MorningBriefing'
import SectorRadar from './SectorRadar'
import SectorOpportunities from './SectorOpportunities'
import AllocationAdvisor from './AllocationAdvisor'

export default function UnwindView() {
  const [plans, setPlans] = useState([])
  const [loading, setLoading] = useState(true)

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

  if (loading) {
    return <div className="text-center py-8 text-text-dim text-[13px]">加载中...</div>
  }
  if (plans.length === 0) {
    return <div className="text-center py-8 text-text-dim text-[13px]">暂无持仓</div>
  }

  return (
    <div className="space-y-4">
      <MorningBriefing />
      <SectorRadar />
      <SectorOpportunities />
      <AllocationAdvisor />
      <BudgetAllocator onAllocated={loadPlans} />
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {plans.map(p => (
          <UnwindCard key={p.stock_code} plan={p} onChange={loadPlans} />
        ))}
      </div>
    </div>
  )
}
