import { ASSET_TYPE_TO_KEY } from './constants'
import { fundCategoryOf, stockCategoryOf, stockMarketOfCode } from './helpers'

// Normalize raw A-share holding → unified row
export function normalizeHolding(h) {
  const mv = h.market_value || (h.current_price * h.shares) || 0
  const cost = h.cost_value ?? (h.cost_price * h.shares)
  const originalMarketValue = h.original_market_value ?? (h.current_price ? h.current_price * h.shares : null)
  const originalCostValue = h.original_cost_value ?? (h.cost_price * h.shares)
  return {
    id: `A-${h.stock_code}`,
    type: 'A',
    category: stockCategoryOf(h),
    code: h.stock_code,
    name: h.stock_name,
    broker: h.broker || null,
    mv,
    cost,
    pnl: h.unrealized_pnl ?? (mv - cost),
    pnlPct: h.pnl_pct,
    today: h.price_change_pct,
    _raw: h,
    extra: {
      market: h.market || stockMarketOfCode(h.stock_code),
      currency: h.currency || 'CNY',
      fxRate: h.fx_rate || 1,
      fxTime: h.fx_time || '',
      fxSource: h.fx_source || '',
      originalMarketValue,
      originalCostValue,
      shares: h.shares,
      price: h.current_price,
      avgCost: h.cost_price,
      divPerShare: h.div_per_share,
    },
  }
}

// Normalize 场外 asset → unified row
export function normalizeAsset(a) {
  const key = ASSET_TYPE_TO_KEY[a.asset_type] || 'R'
  const mv = a.current_value
  // 基金/加密用摊薄成本(周期内减仓盈亏摊进成本, 与券商'成本价'同口径), 行内盈亏才和券商一致
  const cost = a.diluted_cost ?? a.cost_amount
  const q = a.quote
  // BOT 的 today 用 OKX floatProfit (浮动盈亏，未实现) 当代理 — 比 lifetime pnl 更接近"今日"
  let today = null
  if (a.asset_type === 'BOT' && q?.float_profit_usdt != null && mv) {
    const rate = q.usdcny || 7.2
    const float_cny = q.float_profit_usdt * rate
    // 反推等价 pct: float / (mv - float)
    const baseValue = mv - float_cny
    today = baseValue > 0 ? (float_cny / baseValue) * 100 : 0
  } else if (a.asset_type === 'FUND') {
    // 净值 T+1: 后端已折算"今日"口径 (净值当天 → change_pct; 滞后 → 底层 proxy; 都没有 → null 不计入)
    today = q?.today_change_pct ?? null
  } else if (q?.change_pct != null) {
    today = q.change_pct
  }
  const cat = key === 'F' ? fundCategoryOf(a.name) : null
  return {
    id: `${key}-${a.id}`,
    type: key,
    category: cat,
    code: a.code,
    name: a.name,
    mv,
    cost,
    pnl: a.pnl,
    pnlPct: a.pnl_pct,
    today,
    _raw: a,
    broker: a.broker || null,
    extra: {
      platform: a.platform,
      broker: a.broker || null,
      nav: q?.nav ?? q?.est_nav,  // 优先官方净值，对齐 App "持有金额" 口径
      realtime: q?.realtime,
      price: q?.price,
      priceCny: q?.price_cny,
      amount: a.shares,
      lockUntil: null,
      okxSynced: q?.auto_synced,
      syncError: q?.sync_error,
      // 同步正常但策略已停止: 数字是最终态而非实时, 钱其实已回现货账户 —— 该归档退出在持
      okxStopped: q?.auto_synced === true && q?.active === false,
      okxState: q?.state,
      okxPnlPct: q?.pnl_pct,
      // OKX 马丁: 总预算 / 已投入 / 未投入 (反推自策略参数 initOrdAmt + safetyOrdAmt × volMult^k)
      okxInvestmentUsdt: q?.investment_usdt,
      okxTotalBudgetUsdt: q?.total_budget_usdt,
      okxAvailableUsdt: q?.available_usdt,
      okxBudgetSource: q?.budget_source,    // 'manual' | 'estimated'
      okxMaxSafetyOrders: q?.max_safety_orders,
      okxSafetyOrderAmt: q?.safety_order_amt,
      okxInitOrderAmt: q?.init_order_amt,
      okxVolMult: q?.vol_mult,
      assetIdRaw: a.id,
      annualYield: q?.annual_yield_rate ?? a.annual_yield_rate,
      impliedYield: q?.implied_yield_rate,  // 用 manual_value 反推的隐含年化
      daysHeld: q?.days_held,
      accruedInterest: q?.accrued_interest,
      // CASH 估算利息流 (基于年化 × 余额)
      dailyInterestEst: q?.daily_interest_est,
      monthlyInterestEst: q?.monthly_interest_est,
      yearlyInterestEst: q?.yearly_interest_est,
      // FUND 代理标的实时涨跌 (底层市场预判基金当日走势)
      proxyChangePct: q?.proxy_change_pct,
      proxyLabel: q?.proxy_label,
      proxyDetails: q?.proxy_details,
    },
  }
}

// Aggregate by type → totals per group + grand total.
// `aShareTradingDay` controls whether T+1 markets (A-share, fund) contribute today.
// Crypto/Bot are 24/7 so they always contribute.
export function aggregate(rows, aShareTradingDay = true) {
  const totalMv = rows.reduce((s, r) => s + (r.mv || 0), 0)
  const groups = {}
  const fxExposure = {}
  for (const r of rows) {
    if (!groups[r.type]) groups[r.type] = { items: [], mv: 0, cost: 0, pnl: 0, todayPnl: 0 }
    const g = groups[r.type]
    g.items.push(r)
    g.mv += r.mv || 0
    g.cost += r.cost || 0
    g.pnl += r.pnl || 0
    const currency = r.extra?.currency
    if (r.type === 'A' && currency && currency !== 'CNY') {
      if (!fxExposure[currency]) {
        fxExposure[currency] = {
          currency,
          originalMarketValue: 0,
          marketValue: 0,
          fxRate: r.extra?.fxRate || 1,
          fxTime: r.extra?.fxTime || '',
          fxSource: r.extra?.fxSource || '',
        }
      }
      fxExposure[currency].originalMarketValue += r.extra?.originalMarketValue || 0
      fxExposure[currency].marketValue += r.mv || 0
      fxExposure[currency].fxRate = r.extra?.fxRate || fxExposure[currency].fxRate
      fxExposure[currency].fxTime = r.extra?.fxTime || fxExposure[currency].fxTime
      fxExposure[currency].fxSource = r.extra?.fxSource || fxExposure[currency].fxSource
    }
    // T+1 markets (A-share + fund + wealth + cash货基) — `today` is stale on weekends/holidays.
    // Crypto (C) and Bot (R) trade 24/7 and stay correct.
    const isT1Market = r.type === 'A' || r.type === 'F' || r.type === 'W' || r.type === 'M'
    if (isT1Market && !aShareTradingDay) continue
    if (r.today != null && r.mv != null) {
      g.todayPnl += (r.mv * r.today / 100) / (1 + r.today / 100)
    }
  }
  for (const t in groups) {
    const g = groups[t]
    g.weight = totalMv > 0 ? g.mv / totalMv : 0
    g.pnlPct = g.cost > 0 ? (g.pnl / g.cost) * 100 : 0
  }
  return {
    groups,
    totalMv,
    totalCost: rows.reduce((s, r) => s + (r.cost || 0), 0),
    // 行情未加载(有成本但市值缺失/为0)时, 这笔 pnl 不可信(会把成本瞬时当大额浮亏),
    // 不计入总浮动, 避免调流水/基金净值未确认的中间态出现吓人的负数。等市值到位再计入。
    totalPnl: rows.reduce((s, r) => {
      if ((r.cost || 0) > 0 && !(r.mv > 0)) return s
      return s + (r.pnl || 0)
    }, 0),
    totalToday: Object.values(groups).reduce((s, g) => s + g.todayPnl, 0),
    fxExposure: Object.values(fxExposure).filter(e => e.originalMarketValue > 0),
  }
}
