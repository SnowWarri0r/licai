import { useState, useEffect } from 'react'
import { fetchJSON } from '../hooks/useApi'
import { fmtMoney, fmtPct, priceColor } from '../helpers'
import Tooltip from './Tooltip'

function currencySymbol(currency = 'CNY') {
  if (currency === 'USD') return '$'
  if (currency === 'HKD') return 'HK$'
  return '¥'
}

function formatCurrencyMoney(currency, value) {
  return `${currencySymbol(currency)}${fmtMoney(value)}`
}

function fxSourceLabel(source) {
  if (source === 'sina_bid_ask_mid') return '新浪外汇买卖价中间价'
  if (source === 'fallback') return '备用汇率'
  if (source === 'CNY') return '人民币'
  return source || '汇率'
}

export default function Dashboard({ holdings }) {
  const [indices, setIndices] = useState([])
  const [unwindStats, setUnwindStats] = useState(null)
  const [external, setExternal] = useState(null)
  const [tradingDay, setTradingDay] = useState(null)
  const [realized, setRealized] = useState({ stock: 0, asset: 0 })

  useEffect(() => {
    const load = async () => {
      try {
        const [s, a] = await Promise.all([
          fetchJSON('/api/portfolio/realized'),
          fetchJSON('/api/assets/realized'),
        ])
        setRealized({ stock: s.total_realized_pnl || 0, asset: a.total_realized_pnl || 0 })
      } catch {}
    }
    load()
    const t = setInterval(load, 30000)
    return () => clearInterval(t)
  }, [])

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

  // --- Stock aggregates ---
  const aValue = holdings.reduce((s, h) => s + (h.market_value || 0), 0)
  const aCost = holdings.reduce((s, h) => s + (h.cost_value ?? h.cost_price * h.shares), 0)
  const aPnl = holdings.reduce((s, h) => s + (h.unrealized_pnl || 0), 0)
  // 股票今日浮动：先沿用 A 股交易日判断，港美股盘中精细交易时段后续再拆。
  // 兜底：tradingDay 还没加载时用客户端 weekday 判断
  const isTradingDay = tradingDay
    ? !!tradingDay.is_trading_day
    : ![0, 6].includes(new Date().getDay())
  const aTodayPnl = !isTradingDay ? 0 : holdings.reduce((s, h) => {
    if (!h.current_price || !h.price_change_pct) return s
    const mv = h.market_value || h.current_price * h.shares * (h.fx_rate || 1)
    return s + (mv * h.price_change_pct / 100) / (1 + h.price_change_pct / 100)
  }, 0)
  const foreignExposure = Object.values(holdings.reduce((acc, h) => {
    const currency = h.currency
    if (!currency || currency === 'CNY') return acc
    if (!acc[currency]) {
      acc[currency] = {
        currency,
        originalMarketValue: 0,
        marketValue: 0,
        fxRate: h.fx_rate || 1,
        fxTime: h.fx_time || '',
        fxSource: h.fx_source || '',
      }
    }
    acc[currency].originalMarketValue += h.original_market_value || (h.current_price ? h.current_price * h.shares : 0)
    acc[currency].marketValue += h.market_value || 0
    acc[currency].fxRate = h.fx_rate || acc[currency].fxRate
    acc[currency].fxTime = h.fx_time || acc[currency].fxTime
    acc[currency].fxSource = h.fx_source || acc[currency].fxSource
    return acc
  }, {})).filter(e => e.originalMarketValue > 0)

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
  const realizedTotal = (realized?.stock || 0) + (realized?.asset || 0)
  const unrealizedPnl = aPnl + ePnl
  const totalPnl = unrealizedPnl + realizedTotal
  const totalPnlPct = totalCost > 0 ? (totalPnl / totalCost) * 100 : 0

  return (
    <div className="flex items-center gap-4 px-4 py-2 border-b border-border-subtle bg-surface/40 overflow-x-auto"
      style={{ animation: 'fade-up 0.25s ease-out' }}>

      {/* Total (combined stock + external) */}
      <div className="flex items-center gap-1.5 shrink-0">
        <Tooltip content={
          <div className="leading-relaxed">
            <div className="text-text-bright font-semibold mb-1">总资产口径</div>
            <div>主数字统一按人民币估值。</div>
            {foreignExposure.length > 0 && (
              <div className="mt-1 text-text-dim text-[10.5px]">
                港美股按近实时汇率折算，非银行最终结算价。
              </div>
            )}
          </div>
        }>
          <span className="text-[11px] text-text-muted cursor-help">总资产</span>
        </Tooltip>
        <span className="text-[14px] font-mono font-semibold text-text-bright">
          ¥{fmtMoney(totalValue)}
        </span>
        {foreignExposure.length > 0 && (
          <span className="text-[9.5px] text-text-muted">人民币口径</span>
        )}
        {foreignExposure.map(e => (
          <Tooltip key={e.currency} content={
            <div className="leading-relaxed">
              <div className="text-text-bright font-semibold mb-1">{e.currency} 持仓原币市值</div>
              <div>{formatCurrencyMoney(e.currency, e.originalMarketValue)} → ¥{fmtMoney(e.marketValue)}</div>
              <div className="text-text-dim text-[10.5px] mt-1">
                {e.currency}/CNY {Number(e.fxRate || 1).toFixed(4)} · {fxSourceLabel(e.fxSource)} · 5分钟缓存
                {e.fxTime ? ` · ${e.fxTime}` : ''}
              </div>
            </div>
          }>
            <span className="text-[9.5px] font-mono text-text-muted cursor-help hidden sm:inline">
              {formatCurrencyMoney(e.currency, e.originalMarketValue)}
            </span>
          </Tooltip>
        ))}
      </div>
      <div className="flex items-center gap-1.5 shrink-0">
        {realizedTotal !== 0 ? (
          <Tooltip content={
            <div className="leading-relaxed">
              <div className="text-text-bright font-semibold mb-1">总盈亏拆分</div>
              <div className="font-mono text-[11px] space-y-0.5">
                <div>浮动 <span className={priceColor(unrealizedPnl)}>{unrealizedPnl >= 0 ? '+' : ''}¥{fmtMoney(unrealizedPnl)}</span></div>
                <div>已实现 <span className={priceColor(realizedTotal)}>{realizedTotal >= 0 ? '+' : ''}¥{fmtMoney(realizedTotal)}</span></div>
                {realized.stock !== 0 && (
                  <div className="text-text-dim pl-2">  · 股票 <span className={priceColor(realized.stock)}>{realized.stock >= 0 ? '+' : ''}¥{fmtMoney(realized.stock)}</span></div>
                )}
                {realized.asset !== 0 && (
                  <div className="text-text-dim pl-2">  · 基金/理财/加密 <span className={priceColor(realized.asset)}>{realized.asset >= 0 ? '+' : ''}¥{fmtMoney(realized.asset)}</span></div>
                )}
              </div>
            </div>
          }>
            <span className="text-[11px] text-text-muted cursor-help underline decoration-dotted decoration-text-muted/50 underline-offset-2">总盈亏</span>
          </Tooltip>
        ) : (
          <span className="text-[11px] text-text-muted">总盈亏</span>
        )}
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
              股票
            </span>
            <span className="text-[12px] font-mono text-text">¥{fmtMoney(aValue)}</span>
            {foreignExposure.map(e => (
              <Tooltip key={e.currency} content={
                <div className="leading-relaxed">
                  <div className="text-text-bright font-semibold mb-1">{e.currency} 敞口</div>
                  <div>{formatCurrencyMoney(e.currency, e.originalMarketValue)} → ¥{fmtMoney(e.marketValue)}</div>
                  <div className="text-text-dim text-[10.5px] mt-1">
                    {e.currency}/CNY {Number(e.fxRate || 1).toFixed(4)} · {fxSourceLabel(e.fxSource)} · 5分钟缓存
                    {e.fxTime ? ` · ${e.fxTime}` : ''}
                  </div>
                </div>
              }>
                <span className="text-[9.5px] font-mono text-text-muted cursor-help">
                  {formatCurrencyMoney(e.currency, e.originalMarketValue)}
                </span>
              </Tooltip>
            ))}
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
