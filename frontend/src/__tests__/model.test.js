import { describe, expect, it } from 'vitest'

import { aggregate, normalizeHolding } from '../components/portfolio/model'

const row = (o) => ({ type: 'A', mv: 0, cost: 0, pnl: 0, today: null, ...o })

describe('aggregate — 分组与合计', () => {
  it('空组合不炸, 各项为 0', () => {
    const a = aggregate([])
    expect(a.totalMv).toBe(0)
    expect(a.totalCost).toBe(0)
    expect(a.totalPnl).toBe(0)
    expect(a.totalToday).toBe(0)
    expect(a.groups).toEqual({})
    expect(a.fxExposure).toEqual([])
  })

  it('按 type 分组, 组内 mv/cost/pnl 相加', () => {
    const a = aggregate([
      row({ type: 'A', mv: 100, cost: 80, pnl: 20 }),
      row({ type: 'A', mv: 50, cost: 60, pnl: -10 }),
      row({ type: 'F', mv: 200, cost: 200, pnl: 0 }),
    ])
    expect(a.groups.A.mv).toBe(150)
    expect(a.groups.A.cost).toBe(140)
    expect(a.groups.A.pnl).toBe(10)
    expect(a.groups.A.items).toHaveLength(2)
    expect(a.totalMv).toBe(350)
  })

  it('weight 按市值占比, pnlPct 按成本', () => {
    const a = aggregate([
      row({ type: 'A', mv: 300, cost: 200, pnl: 100 }),
      row({ type: 'F', mv: 100, cost: 100, pnl: 0 }),
    ])
    expect(a.groups.A.weight).toBeCloseTo(0.75, 6)
    expect(a.groups.A.pnlPct).toBeCloseTo(50, 6)
  })

  it('成本为 0 时 pnlPct 给 0, 不给 Infinity/NaN', () => {
    const a = aggregate([row({ type: 'M', mv: 1000, cost: 0, pnl: 5 })])
    expect(a.groups.M.pnlPct).toBe(0)
    expect(Number.isFinite(a.groups.M.pnlPct)).toBe(true)
  })

  it('今日盈亏是从现价往回倒算, 不是 市值×涨幅', () => {
    // mv 是**涨完之后**的市值。涨 10% 的 110 元, 今天赚的是 10 不是 11。
    // 公式 (mv*p/100)/(1+p/100) 正是干这个的 —— 写成 mv*p/100 会系统性高估。
    const a = aggregate([row({ type: 'A', mv: 110, cost: 100, pnl: 10, today: 10 })])
    expect(a.groups.A.todayPnl).toBeCloseTo(10, 9)
    expect(a.totalToday).toBeCloseTo(10, 9)
  })

  it('跌的时候同样成立', () => {
    // 跌 20% 后是 80, 今天亏 20
    const a = aggregate([row({ type: 'A', mv: 80, cost: 100, pnl: -20, today: -20 })])
    expect(a.groups.A.todayPnl).toBeCloseTo(-20, 9)
  })

  it('非交易日: T+1 品种(A/F/W/M)不计今日, 24 小时市场(C/R)照计', () => {
    const rows = [
      row({ type: 'A', mv: 110, today: 10 }),
      row({ type: 'F', mv: 110, today: 10 }),
      row({ type: 'W', mv: 110, today: 10 }),
      row({ type: 'M', mv: 110, today: 10 }),
      row({ type: 'C', mv: 110, today: 10 }),
      row({ type: 'R', mv: 110, today: 10 }),
    ]
    const weekend = aggregate(rows, false)
    for (const t of ['A', 'F', 'W', 'M']) expect(weekend.groups[t].todayPnl).toBe(0)
    for (const t of ['C', 'R']) expect(weekend.groups[t].todayPnl).toBeCloseTo(10, 9)
    expect(weekend.totalToday).toBeCloseTo(20, 9)

    // 交易日则六个都计
    const tradingDay = aggregate(rows, true)
    expect(tradingDay.totalToday).toBeCloseTo(60, 9)
  })

  it('today 缺失的行不参与今日合计', () => {
    const a = aggregate([
      row({ type: 'A', mv: 110, today: 10 }),
      row({ type: 'A', mv: 500, today: null }),
    ])
    expect(a.groups.A.todayPnl).toBeCloseTo(10, 9)
  })

  it('有成本但市值还没到位的行, 不计进总浮动', () => {
    // 行情没加载完时 mv=0, 直接算会把整笔成本当成浮亏, 界面上闪一个吓人的负数。
    // 注意它仍然计进 totalCost —— 只是 pnl 不可信而已。
    const a = aggregate([
      row({ type: 'A', mv: 100, cost: 80, pnl: 20 }),
      row({ type: 'F', mv: 0, cost: 5000, pnl: -5000 }),
    ])
    expect(a.totalPnl).toBe(20)
    expect(a.totalCost).toBe(5080)
  })

  it('成本也为 0 的空行不受上面那条影响', () => {
    const a = aggregate([row({ type: 'A', mv: 0, cost: 0, pnl: 3 })])
    expect(a.totalPnl).toBe(3)
  })

  it('外币敞口只统计 A 类非人民币行, 并按币种归并', () => {
    const a = aggregate([
      row({ type: 'A', mv: 700, extra: { currency: 'USD', originalMarketValue: 100, fxRate: 7 } }),
      row({ type: 'A', mv: 350, extra: { currency: 'USD', originalMarketValue: 50, fxRate: 7 } }),
      row({ type: 'A', mv: 100, extra: { currency: 'CNY', originalMarketValue: 100 } }),
      row({ type: 'F', mv: 900, extra: { currency: 'USD', originalMarketValue: 128 } }),
    ])
    expect(a.fxExposure).toHaveLength(1)
    expect(a.fxExposure[0]).toMatchObject({ currency: 'USD', originalMarketValue: 150, marketValue: 1050, fxRate: 7 })
  })
})

describe('normalizeHolding — 原始持仓 → 统一行', () => {
  const base = {
    stock_code: '600519', stock_name: '贵州茅台', shares: 100,
    cost_price: 1600, current_price: 1700, sector: '食品饮料-白酒',
  }

  it('后端给了市值/成本就用后端的', () => {
    const r = normalizeHolding({ ...base, market_value: 170000, cost_value: 160000, unrealized_pnl: 10000 })
    expect(r.mv).toBe(170000)
    expect(r.cost).toBe(160000)
    expect(r.pnl).toBe(10000)
  })

  it('后端没给就用 价×股 现算', () => {
    const r = normalizeHolding(base)
    expect(r.mv).toBe(170000)
    expect(r.cost).toBe(160000)
    expect(r.pnl).toBe(10000)
  })

  it('cost_value 为 0 时用 0, 不回退到 价×股(?? 不是 ||)', () => {
    // 清仓后复活那种 0 成本的行, 用 || 会错误地回退成 cost_price*shares
    const r = normalizeHolding({ ...base, cost_value: 0 })
    expect(r.cost).toBe(0)
  })

  it('id 带 A- 前缀, 行业取后端一级行业的第一段', () => {
    const r = normalizeHolding(base)
    expect(r.id).toBe('A-600519')
    expect(r.type).toBe('A')
    expect(r.category).toEqual({ id: '食品饮料', label: '食品饮料' })
  })

  it('港股/美股按代码前缀识别市场, 分类走市场而不是行业', () => {
    const hk = normalizeHolding({ ...base, stock_code: 'HK.00700', stock_name: '腾讯控股' })
    expect(hk.extra.market).toBe('HK')
    expect(hk.category).toEqual({ id: 'hk', label: '港股' })

    const us = normalizeHolding({ ...base, stock_code: 'US.AAPL', stock_name: '苹果' })
    expect(us.extra.market).toBe('US')
    expect(us.category).toEqual({ id: 'us', label: '美股' })
  })

  it('币种默认人民币, 汇率默认 1', () => {
    const r = normalizeHolding(base)
    expect(r.extra.currency).toBe('CNY')
    expect(r.extra.fxRate).toBe(1)
  })
})
