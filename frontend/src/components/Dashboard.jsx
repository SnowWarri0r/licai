import { useState, useEffect } from 'react'
import { fetchJSON } from '../hooks/useApi'
import { fmtMoney, fmtPct, priceColor } from '../helpers'
import Tooltip from './Tooltip'

export default function Dashboard({ holdings }) {
  const [indices, setIndices] = useState([])
  const [unwindStats, setUnwindStats] = useState(null)
  const [external, setExternal] = useState(null)
  const [tradingDay, setTradingDay] = useState(null)

  useEffect(() => {
    const load = async () => {
      try { setIndices(await fetchJSON('/api/market/indices')) } catch {}
    }
    load()
    const t = setInterval(load, 15000)
    return () => clearInterval(t)
  }, [])

  useEffect(() => {
    const load = async () => {
      try {
        const plans = await fetchJSON('/api/unwind/plans')
        const totalBudget = plans.reduce((s, p) => s + (p.total_budget || 0), 0)
        const usedBudget = plans.reduce((s, p) => s + (p.used_budget || 0), 0)
        const pendingCount = plans.reduce(
          (s, p) => s + (p.tranches || []).filter(t => t.status === 'pending').length, 0
        )
        const dailyOpp = plans.reduce((s, p) => s + (p.daily_opportunity_cost || 0), 0)
        setUnwindStats({ totalBudget, usedBudget, pendingCount, dailyOpp })
      } catch {}
    }
    load()
    const t = setInterval(load, 60000)
    return () => clearInterval(t)
  }, [])

  useEffect(() => {
    const load = async () => {
      try { setExternal(await fetchJSON('/api/assets')) } catch {}
    }
    load()
    // 24/7 crypto + OKX bots — faster refresh, server caches handle upstream rate limits
    const t = setInterval(load, 20000)
    return () => clearInterval(t)
  }, [])

  useEffect(() => {
    fetchJSON('/api/market/trading-day').then(setTradingDay).catch(() => {})
    // refresh once per hour — date changes daily
    const t = setInterval(() => fetchJSON('/api/market/trading-day').then(setTradingDay).catch(() => {}), 3600000)
    return () => clearInterval(t)
  }, [])

  if (!holdings || holdings.length === 0) {
    if (!external || !external.summary?.total_value) return null
  }

  // --- A股 aggregates ---
  const aValue = holdings.reduce((s, h) => s + (h.market_value || 0), 0)
  const aCost = holdings.reduce((s, h) => s + h.cost_price * h.shares, 0)
  const aPnl = holdings.reduce((s, h) => s + (h.unrealized_pnl || 0), 0)
  // A股今日浮动：非交易日（周末/法定假日）显示 0；否则按 price_change_pct × shares 算今日变动
  // 兜底：tradingDay 还没加载时用客户端 weekday 判断
  const isTradingDay = tradingDay
    ? !!tradingDay.is_trading_day
    : ![0, 6].includes(new Date().getDay())
  const aTodayPnl = !isTradingDay ? 0 : holdings.reduce((s, h) => {
    if (!h.current_price || !h.price_change_pct) return s
    const prev = h.current_price / (1 + h.price_change_pct / 100)
    return s + (h.current_price - prev) * h.shares
  }, 0)

  // --- 场外 aggregates ---
  const eValue = external?.summary?.total_value || 0
  const eCost = external?.summary?.total_cost || 0
  const ePnl = external?.summary?.total_pnl || 0
  // 24/7 资产（CRYPTO + BOT）任何时候都算今日浮动。
  // 基金 (FUND) 是 T+1，跟 A 股一样周末/假日不算。
  const cryptoTodayPnl = (external?.assets || []).reduce((s, a) => {
    if (a.asset_type === 'CRYPTO') {
      const pct = a.quote?.change_pct
      if (pct == null || a.current_value == null) return s
      return s + (a.current_value * pct / 100) / (1 + pct / 100)
    }
    if (a.asset_type === 'BOT') {
      // OKX bot: floatProfit (USDT) × usdcny ≈ 当前未实现浮动
      const fp = a.quote?.float_profit_usdt
      const rate = a.quote?.usdcny || 7.2
      if (fp == null) return s
      return s + fp * rate
    }
    return s
  }, 0)
  // 基金今日浮动 (仅交易日,跟 A 股共用 isTradingDay 判断)
  const fundTodayPnl = isTradingDay
    ? (external?.assets || []).reduce((s, a) => {
        if (a.asset_type !== 'FUND') return s
        const pct = a.quote?.change_pct
        if (pct == null || a.current_value == null) return s
        return s + (a.current_value * pct / 100) / (1 + pct / 100)
      }, 0)
    : 0
  const todayPnl = aTodayPnl + fundTodayPnl + cryptoTodayPnl

  // --- Combined ---
  const totalValue = aValue + eValue
  const totalCost = aCost + eCost
  const totalPnl = aPnl + ePnl
  const totalPnlPct = totalCost > 0 ? (totalPnl / totalCost) * 100 : 0

  return (
    <div className="flex items-center gap-4 px-4 py-2 border-b border-border-subtle bg-surface/40 overflow-x-auto"
      style={{ animation: 'fade-up 0.25s ease-out' }}>

      {/* Total (combined A-share + external) */}
      <div className="flex items-center gap-1.5 shrink-0">
        <span className="text-[11px] text-text-muted">总资产</span>
        <span className="text-[14px] font-mono font-semibold text-text-bright">
          ¥{fmtMoney(totalValue)}
        </span>
      </div>
      <div className="flex items-center gap-1.5 shrink-0">
        <span className="text-[11px] text-text-muted">总盈亏</span>
        <span className={`text-[13px] font-mono font-medium ${priceColor(totalPnl)}`}>
          {totalPnl >= 0 ? '+' : ''}{fmtMoney(totalPnl)}
        </span>
        <span className={`text-[11px] font-mono ${priceColor(totalPnlPct)}`}>
          ({totalPnl >= 0 ? '+' : ''}{totalPnlPct.toFixed(2)}%)
        </span>
      </div>

      {/* Per-bucket breakdown (only shown if user has both) */}
      {aValue > 0 && eValue > 0 && (
        <>
          <div className="w-px h-4 bg-border shrink-0" />
          <div className="flex items-center gap-1.5 shrink-0">
            <span className="text-[10px] px-1.5 py-0.5 rounded border border-border text-text-dim">
              A股
            </span>
            <span className="text-[12px] font-mono text-text">¥{fmtMoney(aValue)}</span>
            <span className={`text-[10px] font-mono ${priceColor(aPnl)}`}>
              {aPnl >= 0 ? '+' : ''}{fmtMoney(aPnl)}
            </span>
          </div>
          <div className="flex items-center gap-1.5 shrink-0">
            <span className="text-[10px] px-1.5 py-0.5 rounded border border-accent/30 text-accent">
              场外
            </span>
            <span className="text-[12px] font-mono text-text">¥{fmtMoney(eValue)}</span>
            <span className={`text-[10px] font-mono ${priceColor(ePnl)}`}>
              {ePnl >= 0 ? '+' : ''}{fmtMoney(ePnl)}
            </span>
          </div>
        </>
      )}

      {(aValue > 0 || cryptoTodayPnl !== 0) && (
        <>
          <div className="w-px h-4 bg-border shrink-0" />
          <div className="flex items-center gap-1.5 shrink-0">
            <span className="text-[11px] text-text-muted">
              {!isTradingDay && cryptoTodayPnl !== 0 ? '24h 浮动' : '今日浮动'}
            </span>
            <span className={`text-[13px] font-mono font-medium ${priceColor(todayPnl)}`}>
              {todayPnl >= 0 ? '+' : ''}{fmtMoney(todayPnl)}
            </span>
            {!isTradingDay && aValue > 0 && tradingDay && (
              <Tooltip content={
                <div>
                  <div className="text-text-bright font-semibold">
                    {tradingDay.holiday_name ? `${tradingDay.holiday_name} 假期` : tradingDay.is_weekend ? '周末闭市' : 'A股闭市'}
                  </div>
                  {tradingDay.next_trading_day && (
                    <div className="text-text-dim mt-1 text-[10.5px]">
                      下个交易日: <span className="font-mono text-text">{tradingDay.next_trading_day}</span>
                    </div>
                  )}
                </div>
              }>
                <span className="text-[10px] text-text-muted cursor-help underline decoration-dotted underline-offset-2">
                  {tradingDay.holiday_name ? `${tradingDay.holiday_name} 假期` : 'A股闭市'}
                </span>
              </Tooltip>
            )}
          </div>
        </>
      )}

      {indices.length > 0 && <div className="w-px h-4 bg-border shrink-0" />}
      {indices.map((idx, i) => (
        <div key={i} className="flex items-center gap-1 shrink-0">
          <span className="text-[11px] text-text-muted">{idx.name}</span>
          <span className={`text-[12px] font-mono ${priceColor(idx.change_pct)}`}>
            {idx.change_pct > 0 ? '+' : ''}{idx.change_pct}%
          </span>
        </div>
      ))}

      {unwindStats && unwindStats.totalBudget > 0 && (
        <>
          <div className="w-px h-4 bg-border shrink-0" />
          <div className="flex items-center gap-1.5 shrink-0">
            <span className="text-[11px] text-text-muted">子弹池</span>
            <span className="text-[13px] font-mono font-medium text-accent">
              {fmtMoney(unwindStats.totalBudget)}
            </span>
            <span className="text-[10px] text-text-muted">
              · {unwindStats.pendingCount}档待触发
            </span>
          </div>
          <div className="flex items-center gap-1.5 shrink-0">
            <span className="text-[11px] text-text-muted">每日机会损失</span>
            <span className="text-[12px] font-mono text-bear">
              ¥{unwindStats.dailyOpp.toFixed(2)}
            </span>
          </div>
        </>
      )}
    </div>
  )
}
