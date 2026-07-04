import React, { useState, useEffect, useMemo, useCallback } from 'react'
import { api, fetchJSON } from '../hooks/useApi'
import { fmtMoney, fmtPct, fmtPrice, priceColor, fundPassthroughType, isOnchainEtf, loadBrokers, estimateFee } from '../helpers'
import Tooltip from './Tooltip'
import StockKlineModal from './StockKlineModal'

// ============================================================
// UnifiedPortfolio — replaces Portfolio + ExternalAssets
// Groups: A股 / 基金 / 加密 / 机器人. Sorted by market value desc
// within groups. Hover-only row actions. Donut allocation header.
// ============================================================

const TYPE_META = {
  A: { label: '股票',   short: '股', tintVar: '--color-accent',     desc: 'A股 / 港股 / 美股' },
  F: { label: '基金',   short: '基', tintVar: '--color-info',       desc: '公募 / ETF / LOF' },
  W: { label: '理财',   short: '理', tintVar: '--color-bull',       desc: '银证理财 (T+30 锁定)' },
  M: { label: '现金',   short: '现', tintVar: '--color-text-dim',   desc: 'T+0 货币基金 / 银行活期' },
  C: { label: '加密',   short: '加', tintVar: '--color-warn',       desc: 'BTC / ETH / …' },
  R: { label: '机器人', short: '量', tintVar: '--color-text-dim',   desc: '量化 / 跟投' },
}
// Fallback color hex for donut strokes (Tailwind arbitrary values can't use var() easily)
const TYPE_COLOR = {
  A: '#c8a876', // accent (sand)
  F: '#85a0b4', // info (slate blue)
  W: '#5fa86c', // bull (理财稳定收益绿)
  M: '#7a9b8e', // sage teal (现金/T+0 流动性)
  C: '#d4a05c', // warn (amber)
  R: '#8a8378', // text-dim grey
}
const TYPE_ORDER = ['A', 'F', 'W', 'M', 'C', 'R']

const ASSET_TYPE_TO_KEY = { FUND: 'F', CRYPTO: 'C', BOT: 'R', WEALTH: 'W', CASH: 'M' }
const KEY_TO_ASSET_TYPE = { F: 'FUND', C: 'CRYPTO', R: 'BOT', W: 'WEALTH', M: 'CASH' }

// 券商佣金率（默认万 2.5; 在 config.py 改为你自己的费率）。影响"按股买"模式的手续费自动估算.
const BROKER_COMMISSION_RATE = 0.00025
const BROKER_COMMISSION_MIN = 5

const STOCK_MARKETS = {
  A: { label: 'A股', placeholder: '600362', hint: '6位代码', minShares: 100, step: 100 },
  HK: { label: '港股', placeholder: '00700', hint: '港股5位代码', minShares: 1, step: 1 },
  US: { label: '美股', placeholder: 'AAPL', hint: '美股Ticker', minShares: 1, step: 1 },
}

function stockMarketOfCode(code = '') {
  const c = String(code).toUpperCase()
  if (c.startsWith('HK.')) return 'HK'
  if (c.startsWith('US.')) return 'US'
  return 'A'
}

function stockCodeForMarket(market, code) {
  const raw = String(code || '').trim().toUpperCase()
  if (market === 'HK') return `HK.${raw.replace(/^HK\.?/, '').padStart(5, '0')}`
  if (market === 'US') return `US.${raw.replace(/^US\.?/, '')}`
  return raw.replace(/^A\./, '')
}

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

function FxHint({ extra, children }) {
  if (!extra?.currency || extra.currency === 'CNY') return children
  return (
    <Tooltip content={
      <div className="leading-relaxed">
        <div className="text-text-bright font-semibold mb-1">人民币折算口径</div>
        <div>{extra.currency}/CNY {Number(extra.fxRate || 1).toFixed(4)}</div>
        <div className="text-text-dim text-[10.5px] mt-1">
          {fxSourceLabel(extra.fxSource)} · 5分钟缓存
          {extra.fxTime ? ` · ${extra.fxTime}` : ''}
        </div>
      </div>
    }>
      {children}
    </Tooltip>
  )
}

// Fund subcategory: derived from name keywords. 顺序即匹配优先级 —— 具体主题在前,
// 宽基在后(否则 "创业板人工智能ETF" 会被 "创业板" 抢成宽基)。
const FUND_CATEGORIES = [
  { id: 'gold',     label: '黄金',     match: /黄金|金ETF/ },
  { id: 'silver',   label: '白银',     match: /白银|银ETF/ },
  { id: 'bond',     label: '债券',     match: /债券|利率债|国债|信用债|可转债|纯债/ },
  { id: 'cash',     label: '货币',     match: /货币|活期|现金/ },
  { id: 'overseas', label: '海外股票', match: /QDII|纳斯达克|纳指|标普|道琼斯|美股|港股|恒生|中概|海外|日经|德国|法国|全球|港美/ },
  { id: 'semi',     label: '半导体',   match: /半导体|芯片|集成电路|存储/ },
  { id: 'ai',       label: '人工智能', match: /人工智能|智能|算力|机器人|AI/ },
  { id: 'tmt',      label: '科技TMT',  match: /通信|信息|数字经济|科技|计算机|软件|传媒|游戏|电子|互联|云计算|大数据/ },
  { id: 'newenergy',label: '新能源',   match: /新能源|光伏|锂电|储能|电池|风电|绿电|高端制造/ },
  { id: 'military', label: '军工',     match: /军工|国防|航天|航空/ },
  { id: 'medical',  label: '医药',     match: /医药|生物|医疗|疫苗|创新药|医疗器械|CXO/ },
  { id: 'consumer', label: '消费',     match: /消费|食品|白酒|酒|家电|零售|养殖|农业|旅游/ },
  { id: 'finance',  label: '金融',     match: /证券|券商|银行|保险|金融|地产/ },
  { id: 'commodity',label: '大宗商品', match: /原油|能源|煤炭|有色|铜|铁矿|豆粕|商品/ },
  { id: 'aindex',   label: 'A股宽基',  match: /沪深300|中证500|中证1000|中证2000|上证50|创业板|科创50|科创100|科创板|双创|A股|中证|国证|MSCI|价值|红利|低波/ },
]
function fundCategoryOf(name = '') {
  for (const c of FUND_CATEGORIES) if (c.match.test(name)) return c
  return { id: 'other', label: '其他' }
}

// A-share industry: keyword-driven. 优先匹配后端拉的真实行业字符串
// (e.g. "电气设备-电源设备")，否则回退到股票名兜底。
const A_SECTORS = [
  { id: 'metals',    label: '有色金属', match: /有色|铜|铝|锌|镍|铅|稀土|钼|黄金|白银|钢铁/ },
  { id: 'newenergy', label: '电气新能源', match: /电气设备|电源设备|储能|光伏|锂电|动力电池|电池|风电设备|新能源(?!车)/ },
  { id: 'energy',    label: '能源',     match: /石油|煤|燃气|电力|核电|风电|公用事业|采掘/ },
  { id: 'finance',   label: '金融',     match: /银行|证券|保险|期货|信托|非银金融/ },
  { id: 'realestate',label: '地产',     match: /地产|置业|建设|建筑/ },
  { id: 'tech',      label: '科技',     match: /科技|半导体|芯片|软件|信息|电子|通信|计算|传媒/ },
  { id: 'consumer',  label: '消费',     match: /消费|食品|酒|乳业|家电|家用电器|零售|百货|医美|纺织|休闲服务/ },
  { id: 'medical',   label: '医药',     match: /医药|生物|医疗|制药|疫苗/ },
  { id: 'auto',      label: '汽车',     match: /汽车|新能源车|整车/ },
  { id: 'materials', label: '材料',     match: /钢|水泥|玻璃|化工|塑料|纤维|建筑材料|建筑装饰|轻工制造/ },
  { id: 'machinery', label: '机械',     match: /机械|装备|工程|重工|军工|国防/ },
]
function aShareCategoryOf(name = '', sector = '') {
  // 优先直接用后端东财真实一级行业(如 "有色金属"/"电子设备"/"电气设备")当分类 ——
  // 这是权威数据, 比粗分桶准。后端拿不到(网络失败)才回退按股票名 regex 兜底。
  const s = String(sector || '').split('-')[0].trim()
  if (s) return { id: s, label: s }
  for (const c of A_SECTORS) if (c.match.test(name)) return c
  return { id: 'other', label: '其他' }
}

function stockCategoryOf(h) {
  const market = h.market || stockMarketOfCode(h.stock_code)
  if (market === 'HK') return { id: 'hk', label: '港股' }
  if (market === 'US') return { id: 'us', label: '美股' }
  return aShareCategoryOf(h.stock_name, h.sector)
}

// Cross-type risk family: identifies same underlying exposure across A股 / 基金.
// Returns array of family ids (a row can belong to multiple, e.g. 白银 is both silver + metals).
function riskFamiliesOf(row) {
  if (row.type === 'A') {
    const name = row.name || ''
    const fams = []
    if (/白银/.test(name)) fams.push('silver', 'metals')
    else if (/黄金/.test(name)) fams.push('gold')
    else if (/有色|铜|铝|锌|镍|铅|锂|钴|稀土|钼/.test(name)) fams.push('metals')
    return fams
  }
  if (row.type === 'F') {
    const cid = row.category?.id
    if (cid === 'silver') return ['silver', 'metals']
    if (cid === 'gold') return ['gold']
    if (cid === 'commodity') return ['metals']
    if (cid === 'overseas') return ['overseas']
    if (cid === 'aindex') return ['cn_broad']
    return []
  }
  return []
}
const FAMILY_LABEL = {
  silver: '白银', gold: '黄金', metals: '有色金属',
  overseas: '海外股票', cn_broad: 'A股宽基',
}

// Normalize raw A-share holding → unified row
function normalizeHolding(h) {
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
function normalizeAsset(a) {
  const key = ASSET_TYPE_TO_KEY[a.asset_type] || 'R'
  const mv = a.current_value
  const cost = a.cost_amount
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
function aggregate(rows, aShareTradingDay = true) {
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

// ============================================================
// Allocation donut
// ============================================================
function AllocationDonut({ groups, totalMv }) {
  // 移动端用更小的环 + 紧凑 legend, 桌面用大环
  const isMobile = typeof window !== 'undefined' && window.innerWidth < 768
  const size = isMobile ? 100 : 140
  const r = size / 2 - 14
  const cx = size / 2
  const circ = 2 * Math.PI * r
  const order = TYPE_ORDER.filter(t => groups[t])
  let offset = 0
  const arcs = order.map(type => {
    const frac = groups[type].weight
    const length = frac * circ
    const arc = { type, frac, length, offset, color: TYPE_COLOR[type] }
    offset += length
    return arc
  })
  return (
    <div className="flex items-center gap-3 md:gap-5 w-full md:w-auto min-w-0">
      <svg width={size} height={size} className="shrink-0">
        <circle cx={cx} cy={cx} r={r} fill="none" stroke="var(--color-surface-3)" strokeWidth="12" />
        {arcs.map(a => (
          <circle key={a.type} cx={cx} cy={cx} r={r} fill="none"
            stroke={a.color} strokeWidth="12"
            strokeDasharray={`${a.length} ${circ}`}
            strokeDashoffset={-a.offset}
            transform={`rotate(-90 ${cx} ${cx})`}
            style={{ transition: 'stroke-dasharray 0.4s' }} />
        ))}
        <text x={cx} y={cx - 4} textAnchor="middle" className="text-[10px]"
          fill="var(--color-text-dim)">总资产</text>
        <text x={cx} y={cx + 13} textAnchor="middle" className="font-mono text-[12px] md:text-[14px] font-bold"
          fill="var(--color-text-bright)">
          ¥{fmtMoney(totalMv)}
        </text>
      </svg>
      <div className="flex flex-col gap-1 md:gap-1.5 flex-1 min-w-0">
        {order.map(type => {
          const g = groups[type]
          return (
            <div key={type} className="flex items-center gap-1.5 md:gap-2 text-[10.5px] md:text-[11px]">
              <div className="w-2 h-2 rounded-sm shrink-0" style={{ background: TYPE_COLOR[type] }} />
              <span className="text-text shrink-0">{TYPE_META[type].label}</span>
              <span className="font-mono text-text-bright tabular-nums shrink-0">
                {(g.weight * 100).toFixed(1)}%
              </span>
              {/* 金额和盈亏只在桌面显示 (移动端空间不够, 数据已在持仓表里) */}
              <span className="hidden md:inline font-mono text-text-dim text-[10px] ml-auto truncate">
                ¥{fmtMoney(g.mv)}
              </span>
              <span className={`hidden md:inline font-mono text-[10px] text-right shrink-0 ${priceColor(g.pnl)}`}>
                {g.pnl >= 0 ? '+' : ''}{fmtMoney(g.pnl)}
              </span>
            </div>
          )
        })}
      </div>
    </div>
  )
}

// ============================================================
// Small row primitives
// ============================================================
function TypeChip({ type, compact = false }) {
  const color = TYPE_COLOR[type]
  return (
    <span className="inline-flex items-center rounded font-semibold shrink-0"
      style={{
        padding: compact ? '1px 5px' : '2px 7px',
        border: `1px solid ${color}50`,
        background: `${color}18`,
        color,
        fontSize: compact ? 9.5 : 10.5,
        letterSpacing: '.02em',
        lineHeight: 1.2,
      }}>
      {compact ? TYPE_META[type].short : TYPE_META[type].label}
    </span>
  )
}

function MarketChip({ market }) {
  if (!market || market === 'A') return null
  const label = market === 'HK' ? '港' : market === 'US' ? '美' : market
  const color = market === 'HK' ? '#5fa86c' : market === 'US' ? '#85a0b4' : '#8a8378'
  return (
    <span className="inline-flex items-center rounded font-semibold shrink-0 px-1 py-[1px] text-[9.5px]"
      style={{ color, background: `${color}18`, border: `1px solid ${color}40` }}>
      {label}
    </span>
  )
}

function WeightBar({ weight, color, width = 48 }) {
  const pct = Math.min(1, weight || 0) * 100
  return (
    <div className="inline-flex items-center gap-1.5">
      <span className="font-mono text-[11px] text-text tabular-nums min-w-[36px] text-right">
        {(weight * 100).toFixed(1)}%
      </span>
      <div className="h-1 rounded-sm overflow-hidden" style={{ width, background: 'var(--color-surface-3)' }}>
        <div className="h-full rounded-sm" style={{ width: `${pct}%`, background: color }} />
      </div>
    </div>
  )
}

function TodayPulse({ change, width = 44 }) {
  if (change == null) return <span className="text-text-muted text-[11px]">--</span>
  const abs = Math.abs(change)
  const clamped = Math.min(abs, 5) / 5
  const isUp = change > 0
  const color = isUp ? 'var(--color-bear-bright)' : change < 0 ? 'var(--color-bull-bright)' : 'var(--color-text-dim)'
  return (
    <div className="inline-flex items-center gap-1.5">
      <span className="font-mono text-[11.5px] font-semibold tabular-nums md:min-w-[46px] text-right"
        style={{ color }}>
        {change === 0 ? '0.00%' : (change > 0 ? '+' : '') + change.toFixed(2) + '%'}
      </span>
      <div className="relative hidden md:flex items-center justify-center" style={{ width, height: 14 }}>
        <div className="absolute left-0 right-0 top-1/2 h-px" style={{ background: 'var(--color-border)', transform: 'translateY(-0.5px)' }} />
        {change !== 0 && (
          <div className="absolute"
            style={{
              left: isUp ? '50%' : `${50 - clamped * 50}%`,
              width: `${clamped * 50}%`,
              top: isUp ? 1 : 7,
              height: 6,
              background: color,
              borderRadius: 1,
              opacity: 0.85,
            }} />
        )}
      </div>
    </div>
  )
}

// 基金代理标的脉搏：用底层市场（沪金/纳指期货/恒生等）实时涨跌预判基金当日走势.
function ProxyPulse({ change, label, fallbackToday, details, width = 44 }) {
  if (change == null) return <TodayPulse change={fallbackToday} width={width} />
  const abs = Math.abs(change)
  const clamped = Math.min(abs, 5) / 5
  const isUp = change > 0
  const color = isUp ? 'var(--color-bear-bright)' : change < 0 ? 'var(--color-bull-bright)' : 'var(--color-text-dim)'
  const colorOf = (v) => v > 0 ? 'var(--color-bear-bright)' : v < 0 ? 'var(--color-bull-bright)' : 'var(--color-text-dim)'

  const marketLabel = (m) => {
    if (!m) return ''
    if (m === 'US') return '美'
    if (m === 'HK') return '港'
    if (m === 'CN_SH') return '沪'
    if (m === 'CN_SZ') return '深'
    return m
  }
  const marketColor = (m) => {
    if (m === 'US') return '#85a0b4'
    if (m === 'HK') return '#5fa86c'
    if (m === 'CN_SH' || m === 'CN_SZ') return '#c8a876'
    return '#8a8378'
  }
  const tip = details && details.length > 0 ? (
    <div className="flex flex-col gap-1.5" style={{ minWidth: 240 }}>
      <div className="text-text-dim text-[10px] uppercase tracking-wider mb-0.5">{label || '代理标的'}</div>
      {details.map(d => (
        <div key={d.code} className="flex justify-between items-baseline gap-3">
          <span className="flex items-baseline gap-1.5 text-text">
            {d.market && (
              <span className="inline-block text-[9px] px-1 rounded font-semibold"
                style={{ background: marketColor(d.market) + '22', color: marketColor(d.market), border: `1px solid ${marketColor(d.market)}40` }}>
                {marketLabel(d.market)}
              </span>
            )}
            <span>{d.name}</span>
          </span>
          <span className="font-mono text-[11px] tabular-nums shrink-0" style={{ color: colorOf(d.change_pct) }}>
            {d.change_pct >= 0 ? '+' : ''}{d.change_pct.toFixed(2)}%
            <span className="text-text-muted text-[10px] ml-1">×{(d.weight * 100).toFixed(1)}%</span>
          </span>
        </div>
      ))}
      <div className="flex justify-between pt-1 mt-0.5 border-t border-border-subtle">
        <span className="text-text-dim text-[10px]">加权平均</span>
        <span className="font-mono text-[11.5px] font-semibold tabular-nums" style={{ color }}>
          {change >= 0 ? '+' : ''}{change.toFixed(2)}%
        </span>
      </div>
      <div className="text-text-muted text-[10px] mt-0.5 leading-snug">
        盘中实时持仓加权，预判基金当日走势
      </div>
    </div>
  ) : (label || '基金代理标的预判')

  return (
    <Tooltip content={tip} maxWidth={300}>
      <div className="inline-flex items-center gap-1.5">
        <span className="font-mono text-[11.5px] font-semibold tabular-nums md:min-w-[46px] text-right"
          style={{ color }}>
          {change === 0 ? '0.00%' : (change > 0 ? '+' : '') + change.toFixed(2) + '%'}
          <span className="text-[8.5px] opacity-70 ml-0.5 align-top">代</span>
        </span>
        <div className="relative hidden md:flex items-center justify-center" style={{ width, height: 14 }}>
          <div className="absolute left-0 right-0 top-1/2 h-px" style={{ background: 'var(--color-border)', transform: 'translateY(-0.5px)' }} />
          {change !== 0 && (
            <div className="absolute"
              style={{
                left: isUp ? '50%' : `${50 - clamped * 50}%`,
                width: `${clamped * 50}%`,
                top: isUp ? 1 : 7,
                height: 6,
                background: color,
                borderRadius: 1,
                opacity: 0.85,
              }} />
          )}
        </div>
      </div>
    </Tooltip>
  )
}

function TypeMiniInfo({ row }) {
  const { type, extra } = row
  if (type === 'A') {
    return (
      <span className="text-[10.5px] text-text-muted font-mono">
        {extra.shares} 股 · {currencySymbol(extra.currency)}{fmtPrice(extra.avgCost)}
        {extra.divPerShare > 0 && (
          <span className="text-accent/80 ml-1" title={`持有期已收每股现金分红 ¥${extra.divPerShare}，已按券商摊薄成本口径从成本中扣减`}>
            含红{currencySymbol(extra.currency)}{extra.divPerShare}
          </span>
        )}
      </span>
    )
  }
  if (type === 'F') {
    // 场内 ETF/LOF: 按股票口径展示 (持有份额 · 成本价), 它本就是市价实时成交, 不是净值 T+1.
    if (isOnchainEtf(row.code)) {
      const sh = Number(row._raw?.shares) || 0
      const ca = Number(row._raw?.cost_amount) || 0
      const avg = sh > 0 ? ca / sh : 0
      return (
        <span className="text-[10.5px] text-text-muted font-mono">
          {sh} 份 · ¥{fmtPrice(avg)}
        </span>
      )
    }
    // 场外公募基金: 净值 T+1 口径.
    return (
      <span className="text-[10.5px] text-text-dim">
        {extra.platform || '基金'}
        {extra.nav != null && <> · 净值 <span className="font-mono text-text">{Number(extra.nav).toFixed(4)}</span>
          {!extra.realtime && <span className="text-text-muted ml-1">T+1</span>}
        </>}
      </span>
    )
  }
  if (type === 'W') {
    const yieldRate = extra.annualYield ?? extra.impliedYield
    const isImplied = extra.annualYield == null && extra.impliedYield != null
    // 反推 tooltip 触发器放在内容最前面 (ⓘ icon), 避免被 RowActions hover 覆盖
    return (
      <span className="text-[10.5px] text-text-dim">
        {isImplied && (
          <Tooltip content={
            <div className="leading-relaxed">
              <div className="text-text-bright font-semibold mb-0.5">反推年化</div>
              <div>基于<span className="text-text-bright">当前总额 ÷ 本金 − 1</span></div>
              <div>除以<span className="text-text-bright">持有天数</span>得隐含年化</div>
              <div className="text-text-dim mt-1 text-[10.5px]">非产品标定年化，仅作参考</div>
            </div>
          }>
            <span className="cursor-help text-text-muted mr-0.5">ⓘ</span>
          </Tooltip>
        )}
        {extra.platform || '理财'}
        {yieldRate != null && (
          <> · 年化 <span className="font-mono text-bull">
            {isImplied && '≈'}{(yieldRate * 100).toFixed(2)}%
          </span></>
        )}
        {extra.daysHeld != null && <> · 持有 <span className="font-mono">{extra.daysHeld}天</span></>}
      </span>
    )
  }
  if (type === 'C') {
    return (
      <span className="text-[10.5px] text-text-dim">
        {extra.platform || 'OKX'}
        {extra.price != null && <> · <span className="font-mono text-text">${extra.price}</span></>}
        {extra.amount != null && <> · <span className="font-mono">{extra.amount}</span></>}
      </span>
    )
  }
  if (type === 'R') {
    return (
      <span className="inline-flex items-center gap-1.5 text-[10.5px] text-text-dim">
        {extra.platform || '量化'}
        {/* OKX 同步指示器移到了名称栏，这里不再重复 */}
      </span>
    )
  }
  return null
}

// ============================================================
// Hover action buttons
// ============================================================
function RowActions({ row, visible, onEdit, onHistory, onRemove, onAddLot, onReduceLot, onShowActions, onKline, onThesis, hasThesis }) {
  // 桌面: hover 才显示 (opacity 控制); 移动: 始终显示, 1 字按钮
  const btnBase = 'rounded border border-border-med bg-surface-2 text-text-dim ' +
    'hover:border-accent hover:text-accent transition-colors cursor-pointer whitespace-nowrap'
  const thesisAction = { short: hasThesis ? '记✓' : '记', label: hasThesis ? '逻辑✓' : '逻辑',
    fn: () => onThesis?.(row), highlight: hasThesis }
  const actions = []
  if (row.type === 'A') {
    actions.push({ short: '线', label: 'K 线', fn: () => onKline?.(row._raw) })
    actions.push({ short: '史', label: '历史', fn: () => onHistory?.(row._raw) })
    actions.push(thesisAction)
    actions.push({ short: '改', label: '编辑', fn: () => onEdit?.(row) })
  } else {
    // 场内 ETF/LOF (沪 5xxxxx / 深 1xxxxx): 有实时行情, 给 K 线详情页入口.
    // 场外基金 (净值 T+1, 无分时/盘口) 不给. holding 字段映射成 modal 期望的口径.
    if (row.type === 'F' && isOnchainEtf(row.code)) {
      const r = row._raw || {}
      const sh = Number(r.shares) || 0
      actions.push({ short: '线', label: 'K 线', fn: () => onKline?.({
        stock_code: row.code,
        stock_name: row.name,
        asset_id: r.id,                 // 场外 asset 主键, modal 据此走 /api/assets/{id}/actions 取 BS 流水
        cost_price: sh > 0 ? (Number(r.cost_amount) || 0) / sh : 0,
        current_price: r.quote?.nav ?? null,
        price_change_pct: row.today,
      }) })
    }
    if (row.type === 'F' || row.type === 'C' || row.type === 'W' || row.type === 'M') {
      actions.push({ short: '加', label: '加仓', fn: () => onAddLot?.(row) })
      actions.push({ short: '减', label: '减仓', fn: () => onReduceLot?.(row) })
      const pendingN = row._raw?.pending_actions_count || 0
      const histLabel = pendingN > 0 ? `流水 (${pendingN}!)` : '流水'
      actions.push({ short: pendingN > 0 ? `史${pendingN}` : '史', label: histLabel,
        fn: () => onShowActions?.(row), highlight: pendingN > 0 })
    }
    actions.push(thesisAction)
    actions.push({ short: '改', label: '编辑', fn: () => onEdit?.(row) })
    actions.push({ short: '删', label: '删除', fn: () => onRemove?.(row), danger: true })
  }
  const dangerHover = (a) => a.danger ? {
    onMouseEnter: e => { e.currentTarget.style.borderColor = 'var(--color-bear)'; e.currentTarget.style.color = 'var(--color-bear)' },
    onMouseLeave: e => { e.currentTarget.style.borderColor = ''; e.currentTarget.style.color = '' },
  } : {}
  return (
    <>
      {/* 移动端: 始终显示, 1 字按钮, 紧凑 */}
      <div className="flex md:hidden gap-0.5 justify-end items-center">
        {actions.map(a => (
          <button key={a.label} onClick={a.fn}
            className={`${btnBase} px-1.5 py-[2px] text-[11px] min-w-[20px] ${a.highlight ? 'border-warn/60 text-warn' : ''}`}
            {...dangerHover(a)}
          >{a.short}</button>
        ))}
      </div>
      {/* 桌面: hover 显示, 全名. 容器 pointer-events:none, 按钮自己 auto;
          按钮间空隙能让 hover 事件穿透到下层 TypeMiniInfo (反推 tooltip 等). */}
      <div className="hidden md:flex gap-1 justify-end items-center"
        style={{
          opacity: visible ? 1 : 0,
          transform: visible ? 'translateX(0)' : 'translateX(4px)',
          transition: 'opacity .18s, transform .18s',
          pointerEvents: 'none',
        }}>
        {actions.map(a => (
          <button key={a.label} onClick={a.fn}
            className={`${btnBase} px-2 py-[3px] text-[10.5px] ${a.highlight ? 'border-warn/60 text-warn' : ''}`}
            style={{ pointerEvents: visible ? 'auto' : 'none' }}
            {...dangerHover(a)}
          >{a.label}</button>
        ))}
      </div>
    </>
  )
}

// ============================================================
// Summary strip
// ============================================================
function SummaryStrip({ agg, aShareClosed, realized }) {
  const fxExposure = agg.fxExposure || []
  const realizedTotal = (realized?.stock || 0) + (realized?.asset || 0)
  const grandPnl = (agg.totalPnl || 0) + realizedTotal
  const items = [
    {
      label: '总资产',
      val: `¥${fmtMoney(agg.totalMv)}`,
      big: true,
      color: 'text-text-bright',
      note: fxExposure.length ? '人民币口径 · 含外币折算' : '人民币口径',
    },
    {
      label: '总盈亏',
      val: `${grandPnl >= 0 ? '+' : ''}${fmtMoney(grandPnl)}`,
      color: priceColor(grandPnl),
      sub: agg.totalCost > 0 ? `(${fmtPct(grandPnl / agg.totalCost * 100)})` : '',
      tooltip: realizedTotal !== 0 ? (
        <div className="leading-relaxed">
          <div className="text-text-bright font-semibold mb-1">总盈亏拆分</div>
          <div className="font-mono text-[11px] space-y-0.5">
            <div>浮动盈亏 <span className={priceColor(agg.totalPnl)}>{agg.totalPnl >= 0 ? '+' : ''}¥{fmtMoney(agg.totalPnl)}</span></div>
            <div>已实现盈亏 <span className={priceColor(realizedTotal)}>{realizedTotal >= 0 ? '+' : ''}¥{fmtMoney(realizedTotal)}</span></div>
            {realized?.stock !== 0 && (
              <div className="text-text-dim pl-2">  · 股票 <span className={priceColor(realized.stock)}>{realized.stock >= 0 ? '+' : ''}¥{fmtMoney(realized.stock)}</span></div>
            )}
            {realized?.asset !== 0 && (
              <div className="text-text-dim pl-2">  · 基金/理财/加密 <span className={priceColor(realized.asset)}>{realized.asset >= 0 ? '+' : ''}¥{fmtMoney(realized.asset)}</span></div>
            )}
          </div>
        </div>
      ) : null,
    },
    {
      label: aShareClosed ? '今日浮动 (A股闭市)' : '今日浮动',
      val: `${agg.totalToday >= 0 ? '+' : ''}${fmtMoney(agg.totalToday)}`,
      color: priceColor(agg.totalToday),
    },
  ]
  return (
    <div className="flex gap-4 md:gap-7 items-baseline flex-wrap">
      {items.map((it, i) => {
        const valueSpan = (
          <span className="inline-flex items-baseline gap-1 md:gap-1.5 flex-wrap">
            <span className={`font-mono font-bold tabular-nums ${it.color} ${it.big ? 'text-[18px] md:text-[22px]' : 'text-[14px] md:text-[15px]'} ${it.tooltip ? 'cursor-help underline decoration-dotted decoration-text-muted/60 underline-offset-4' : ''}`}
              style={{ letterSpacing: '-.01em' }}>{it.val}</span>
            {it.sub && <span className={`font-mono text-[10.5px] md:text-[11px] opacity-80 ${it.color}`}>{it.sub}</span>}
          </span>
        )
        return (
        <div key={i} className="flex flex-col gap-0.5">
          <span className="text-[10.5px] text-text-dim tracking-wide">{it.label}</span>
          {it.tooltip ? <Tooltip content={it.tooltip}>{valueSpan}</Tooltip> : valueSpan}
          {it.note && <span className="text-[9.5px] text-text-muted">{it.note}</span>}
          {i === 0 && fxExposure.length > 0 && (
            <div className="flex items-center gap-1.5 flex-wrap mt-0.5">
              {fxExposure.map(e => (
                <FxHint key={e.currency} extra={{ currency: e.currency, fxRate: e.fxRate, fxTime: e.fxTime, fxSource: e.fxSource }}>
                  <span className="font-mono text-[9.5px] px-1.5 py-[1px] rounded border border-border-med text-text-dim cursor-help bg-surface/50">
                    {formatCurrencyMoney(e.currency, e.originalMarketValue)} → ¥{fmtMoney(e.marketValue)}
                  </span>
                </FxHint>
              ))}
            </div>
          )}
        </div>
        )
      })}
    </div>
  )
}

// ============================================================
// Main
// ============================================================
export default function UnifiedPortfolio({ holdings, onEdit, onHistory, onAdd, dataVersion = 0 }) {
  const [assets, setAssets] = useState([])
  const [brokers, setBrokers] = useState([])
  useEffect(() => { loadBrokers().then(setBrokers) }, [])
  const [assetsLoaded, setAssetsLoaded] = useState(false)
  const [tradingDay, setTradingDay] = useState(null)
  const [collapsed, setCollapsed] = useState({})
  const [hoverId, setHoverId] = useState(null)
  const [filter, setFilter] = useState('ALL')
  const [sortKey, setSortKey] = useState('mv')
  const [addTarget, setAddTarget] = useState(null) // null | 'A' | 'F' | 'C' | 'R'
  const [editAsset, setEditAsset] = useState(null)
  const [addLotAsset, setAddLotAsset] = useState(null)
  const [reduceAsset, setReduceAsset] = useState(null)
  const [actionsAsset, setActionsAsset] = useState(null)
  const [klineHolding, setKlineHolding] = useState(null)
  const [thesisTarget, setThesisTarget] = useState(null)
  const [thesisCodes, setThesisCodes] = useState(() => new Set())
  const loadThesisCodes = useCallback(async () => {
    try {
      const list = await fetchJSON('/api/portfolio/thesis')
      setThesisCodes(new Set((list || []).map(t => t.code)))
    } catch {}
  }, [])
  useEffect(() => { loadThesisCodes() }, [loadThesisCodes])
  const [realized, setRealized] = useState({ stock: 0, asset: 0 })
  const [settling, setSettling] = useState(false)
  const [settleMsg, setSettleMsg] = useState('')

  const loadAssets = useCallback(async () => {
    try {
      const d = await fetchJSON('/api/assets')
      setAssets(d.assets || [])
    } catch {} finally { setAssetsLoaded(true) }
  }, [])

  // 一键确认所有到期定投: 后端按每条 trade_date 拉当日净值自动结算份额。
  const settlePending = useCallback(async () => {
    setSettling(true); setSettleMsg('')
    try {
      const res = await fetch('/api/assets/settle-pending', { method: 'POST' })
      const d = await res.json()
      if (!res.ok) throw new Error(d.detail || '确认失败')
      const parts = [`确认 ${d.settled || 0} 笔`]
      if (d.skipped > 0) parts.push(`${d.skipped} 笔净值未出, 留着下次`)
      setSettleMsg(parts.join(' · '))
      await loadAssets()
    } catch (e) {
      setSettleMsg('失败: ' + e.message)
    } finally { setSettling(false) }
  }, [loadAssets])

  // 费率改了之后重算已确认的定投 (只动设了申购费率的基金, 按当日净值内扣重算份额)
  const recomputeDca = useCallback(async () => {
    setSettling(true); setSettleMsg('')
    try {
      const res = await fetch('/api/assets/recompute-dca', { method: 'POST' })
      const d = await res.json()
      if (!res.ok) throw new Error(d.detail || '重算失败')
      setSettleMsg(d.recomputed > 0 ? `重算 ${d.recomputed} 笔` : '没有需要重算的（份额已是最新）')
      await loadAssets()
    } catch (e) {
      setSettleMsg('失败: ' + e.message)
    } finally { setSettling(false) }
  }, [loadAssets])

  const loadRealized = useCallback(async () => {
    try {
      const [s, a] = await Promise.all([
        fetchJSON('/api/portfolio/realized'),
        fetchJSON('/api/assets/realized'),
      ])
      // 总盈亏 = 浮动(行内 pnl) + 已实现. 但有两处会重复算, 必须从已实现里剔掉:
      //  1) 股票: 当前持仓段的 realized 已被「综合成本法」摊进剩余成本 → 行内浮动已含。
      //     只补 realized_carry (已平仓段 + 分红, 后端算好), 含清仓后又复活的旧那轮。
      //  2) CASH 的 realized(利息) 已作为现金行的 pnl 计入浮动 → 资产已实现里排除 CASH。
      // FUND/CRYPTO/WEALTH 的成本只算剩余 lot, 浮动不含 realized, 所以全额计入。
      const stockCarry = s.total_realized_carry != null
        ? s.total_realized_carry
        : (s.items || []).filter(it => !it.still_holding).reduce((sum, it) => sum + (it.realized_pnl || 0), 0)
      const assetExclCash = (a.items || [])
        .filter(it => it.asset_type !== 'CASH')
        .reduce((sum, it) => sum + (it.realized_pnl || 0), 0)
      setRealized({
        stock: Math.round(stockCarry * 100) / 100,
        asset: Math.round(assetExclCash * 100) / 100,
        stockItems: s.items || [],
        assetItems: a.items || [],
      })
    } catch (e) { console.error('realized load failed', e) }
  }, [])

  useEffect(() => {
    loadAssets()
    loadRealized()
    // Crypto & OKX bots are 24/7 markets; use shorter interval. Server-side
    // caches handle upstream rate limits (crypto 30s, fund 120s).
    const t = setInterval(() => { loadAssets(); loadRealized() }, 20000)
    return () => clearInterval(t)
  }, [loadAssets, loadRealized])

  useEffect(() => {
    fetchJSON('/api/market/trading-day').then(setTradingDay).catch(() => {})
    const t = setInterval(() => fetchJSON('/api/market/trading-day').then(setTradingDay).catch(() => {}), 3600000)
    return () => clearInterval(t)
  }, [])

  // 交易流水编辑后 (dataVersion 变化): 立即重载已实现/已清仓/资产, 不用等 20s 轮询
  useEffect(() => {
    if (dataVersion > 0) { loadRealized(); loadAssets() }
  }, [dataVersion, loadRealized, loadAssets])

  const rows = useMemo(() => {
    // 0 持仓的股票/基金 不在主列表显示, 走 "已清仓" 区块
    const a = (holdings || []).filter(h => (h.shares || 0) > 0).map(normalizeHolding)
    // BOT/CASH 常驻; 理财(WEALTH)/基金/加密 已清仓(无成本/无市值/无在途)的不进主列表, 走"已清仓"区块
    const e = (assets || []).filter(x => x.asset_type === 'BOT' || x.asset_type === 'CASH'
      || (x.shares || 0) > 0 || (x.cost_amount || 0) > 0
      || (x.current_value || 0) > 0 || (x.pending_amount || 0) > 0).map(normalizeAsset)
    return [...a, ...e]
  }, [holdings, assets])

  const filtered = useMemo(
    () => filter === 'ALL' ? rows : rows.filter(r => r.type === filter),
    [rows, filter]
  )

  const aShareTradingDay = tradingDay
    ? !!tradingDay.is_trading_day
    : ![0, 6].includes(new Date().getDay())
  const agg = useMemo(() => aggregate(filtered, aShareTradingDay), [filtered, aShareTradingDay])
  // 个股「历史已实现」(清仓段+分红, 不在当前浮动里). 用来在行内补「真实总盈亏」,
  // 否则清仓后复活的票只看浮动会误判 (中钨高新浮动+245 实则全周期 -373)。
  const carryByCode = useMemo(() => {
    const m = {}
    for (const it of (realized?.stockItems || [])) {
      if (it.realized_carry) m[it.stock_code] = it.realized_carry
    }
    return m
  }, [realized])
  // tabTypes: always show all types regardless of holdings
  const tabTypes = TYPE_ORDER
  // visibleTypes: from filtered rows — drives content section rendering
  const visibleTypes = TYPE_ORDER.filter(t => agg.groups[t])

  // Risk insights: concentration warnings + cross-type overlap families.
  // Always computed from ALL rows (not filtered) so toggling filter doesn't change advice.
  const insights = useMemo(() => {
    const allAgg = aggregate(rows, true)
    const total = allAgg.totalMv || 0
    const warnings = []
    // 1. Major class concentration — 穿透后口径
    //    FUND 按 fundPassthroughType 拆到 A/F/W/M/C 再算各桶占比.
    //    避免"我买了海外/黄金 ETF 但被堆在基金桶, 然后说股票集中"的误判.
    const passthroughBuckets = { A: 0, F: 0, W: 0, M: 0, C: 0, R: 0 }
    for (const r of rows) {
      const v = r.mv || 0
      if (r.type === 'F') {
        const target = fundPassthroughType(r.name || '')
        passthroughBuckets[target] = (passthroughBuckets[target] || 0) + v
      } else {
        passthroughBuckets[r.type] = (passthroughBuckets[r.type] || 0) + v
      }
    }
    for (const t of TYPE_ORDER) {
      const mv = passthroughBuckets[t] || 0
      if (mv === 0 || total === 0) continue
      const pct = (mv / total) * 100
      if (pct >= 50) {
        warnings.push({
          level: pct >= 70 ? 'high' : 'med',
          text: `${TYPE_META[t].label} 占比 ${pct.toFixed(1)}%，集中度过高`,
        })
      }
    }
    // 2. Sub-category single-track within FUND / A股
    for (const t of ['A', 'F']) {
      const g = allAgg.groups[t]
      if (!g || g.items.length < 2) continue
      const subs = {}
      for (const it of g.items) {
        const cid = it.category?.id || 'other'
        subs[cid] = (subs[cid] || 0) + (it.mv || 0)
      }
      const entries = Object.entries(subs)
      if (entries.length === 1) {
        const cat = g.items[0].category?.label || '同一类'
        warnings.push({
          level: 'high',
          text: `${TYPE_META[t].label}全押「${cat}」，板块未分散`,
        })
      } else {
        const top = entries.sort((a, b) => b[1] - a[1])[0]
        const topPct = top[1] / g.mv * 100
        if (topPct >= 70) {
          const cat = g.items.find(it => (it.category?.id || 'other') === top[0])?.category?.label || '某类'
          warnings.push({
            level: 'med',
            text: `${TYPE_META[t].label}内「${cat}」占 ${topPct.toFixed(0)}%，板块过度集中`,
          })
        }
      }
    }
    // 3. Cross-type overlap (A股 vs 基金 共享同源风险)
    const familyRows = {}
    for (const r of rows) {
      const fams = riskFamiliesOf(r)
      for (const f of fams) {
        if (!familyRows[f]) familyRows[f] = { types: new Set(), rows: [], mv: 0 }
        familyRows[f].types.add(r.type)
        familyRows[f].rows.push(r)
        familyRows[f].mv += r.mv || 0
      }
    }
    const overlapRowIds = new Set()
    const overlapByRow = {}
    for (const [fam, info] of Object.entries(familyRows)) {
      if (info.types.size >= 2) {
        const pct = total > 0 ? (info.mv / total * 100) : 0
        warnings.push({
          level: 'med',
          text: `「${FAMILY_LABEL[fam] || fam}」横跨 A股 + 基金，合计 ${pct.toFixed(1)}% — 实际敞口被低估`,
        })
        for (const r of info.rows) {
          overlapRowIds.add(r.id)
          if (!overlapByRow[r.id]) overlapByRow[r.id] = []
          overlapByRow[r.id].push(FAMILY_LABEL[fam] || fam)
        }
      }
    }
    return { warnings, overlapRowIds, overlapByRow }
  }, [rows])

  const removeAsset = async (row) => {
    if (!confirm(`删除 ${row.name}？`)) return
    await fetchJSON(`/api/assets/${row._raw.id}`, { method: 'DELETE' })
    loadAssets()
  }

  const handleEdit = (row) => {
    if (row.type === 'A') onEdit?.(row._raw)
    else setEditAsset(row._raw)
  }
  const handleAddLot = (row) => {
    if (row.type === 'A') return  // A股 走 TransactionHistory
    setAddLotAsset(row._raw)
  }
  const handleReduceLot = (row) => {
    if (row.type === 'A') return
    setReduceAsset(row._raw)
  }
  const handleShowActions = (row) => {
    if (row.type === 'A') return
    setActionsAsset(row._raw)
  }

  const isEmpty = rows.length === 0
  if (isEmpty && !assetsLoaded) {
    return (
      <section className="rounded-xl border border-border bg-surface/60 px-4 py-8 text-center text-text-dim text-[12px]"
        style={{ animation: 'fade-up 0.4s ease-out' }}>加载中...</section>
    )
  }

  return (
    <section className="rounded-xl border border-border bg-surface/60 overflow-hidden"
      style={{ animation: 'fade-up 0.4s ease-out' }}>
      {/* Header: summary + donut */}
      <div className="px-3 md:px-6 py-3 md:py-5 border-b border-border flex flex-col md:flex-row md:flex-wrap md:justify-between md:items-center gap-4 md:gap-8"
        style={{ background: 'linear-gradient(180deg, var(--color-surface-2), var(--color-surface))' }}>
        <div className="flex flex-col gap-3 w-full md:flex-1 md:w-auto min-w-0 md:min-w-[340px]">
          <div className="flex items-baseline gap-3">
            <h2 className="text-[14px] font-semibold text-text-bright tracking-wide m-0">持仓总览</h2>
            <span className="text-[11px] text-text-dim">股票 · 基金 · 理财 · 现金 · 加密 · 机器人</span>
          </div>
          {isEmpty
            ? <div className="text-text-dim text-[12px] py-2">还没有持仓,点击下方「+ 添加」开始</div>
            : <SummaryStrip agg={agg} aShareClosed={!aShareTradingDay} realized={realized} />
          }
        </div>
        {!isEmpty && <AllocationDonut groups={agg.groups} totalMv={agg.totalMv} />}
      </div>

      {/* 定投待确认 banner: 一键按当日净值自动结算所有 pending 申购 */}
      {(() => {
        const pendN = (assets || []).reduce((s, a) => s + (a.pending_actions_count || 0), 0)
        const hasRateFund = (assets || []).some(a => a.asset_type === 'FUND' && a.purchase_fee_rate != null)
        if (pendN === 0 && !hasRateFund && !settleMsg) return null
        return (
          <div className="px-3 md:px-6 py-2.5 border-b border-border bg-accent/5 flex items-center gap-3 flex-wrap">
            <span className="inline-flex items-center justify-center w-4 h-4 rounded-full text-[10px] shrink-0 bg-accent/15 text-accent border border-accent/50">⏳</span>
            <span className="text-[11.5px] text-text">
              {pendN > 0 ? `${pendN} 笔定投待确认份额` : '定投'}
              {settleMsg && <span className="text-text-dim ml-2">· {settleMsg}</span>}
            </span>
            <div className="ml-auto flex items-center gap-2">
              {hasRateFund && (
                <button onClick={recomputeDca} disabled={settling}
                  title="改了申购费率后, 按当日净值重算已确认的定投份额"
                  className="px-3 py-1 rounded text-[11.5px] border border-border-med text-text-dim hover:text-text hover:border-accent transition-colors cursor-pointer disabled:opacity-50">
                  {settling ? '处理中…' : '按费率重算'}
                </button>
              )}
              {pendN > 0 && (
                <button onClick={settlePending} disabled={settling}
                  className="px-3 py-1 rounded text-[11.5px] border border-accent/50 bg-accent/10 text-accent hover:bg-accent/20 transition-colors cursor-pointer disabled:opacity-50">
                  {settling ? '确认中…' : '一键按净值确认'}
                </button>
              )}
            </div>
          </div>
        )
      })()}

      {/* Risk insights strip */}
      {!isEmpty && insights.warnings.length > 0 && (
        <div className="px-3 md:px-6 py-2.5 border-b border-border bg-surface-2/60 flex flex-col gap-1.5">
          {insights.warnings.map((w, i) => {
            const color = w.level === 'high' ? '#e58a8a' : '#d4a05c'
            return (
              <div key={i} className="flex items-center gap-2 text-[11.5px]">
                <span className="inline-flex items-center justify-center w-4 h-4 rounded-full text-[10px] font-bold shrink-0"
                  style={{ background: `${color}1f`, color, border: `1px solid ${color}60` }}>!</span>
                <span className="text-text">{w.text}</span>
              </div>
            )
          })}
        </div>
      )}

      {/* Toolbar */}
      <div className="px-3 md:px-6 py-2 border-b border-border flex justify-between items-center gap-3 flex-wrap"
        style={{ background: 'var(--color-surface-2)' }}>
        <div className="flex gap-1.5 flex-wrap">
          {[['ALL', '全部'], ...tabTypes.map(t => [t, TYPE_META[t].label])].map(([k, l]) => {
            const active = filter === k
            const c = k === 'ALL' ? '#c8a876' : TYPE_COLOR[k]
            return (
              <button key={k} onClick={() => setFilter(k)}
                className="px-2.5 py-[3px] rounded-md text-[11px] border transition-colors cursor-pointer"
                style={{
                  borderColor: active ? c : 'var(--color-border-med)',
                  background: active ? `${c}1a` : 'transparent',
                  color: active ? c : 'var(--color-text-dim)',
                }}>
                {l}
              </button>
            )
          })}
        </div>
        <div className="flex gap-1.5">
          <button onClick={() => setSortKey(sortKey === 'mv' ? 'pnl' : 'mv')}
            className="px-2.5 py-[3px] rounded-md text-[11px] border border-border-med bg-transparent text-text-dim hover:text-text transition-colors cursor-pointer">
            排序 · {sortKey === 'mv' ? '市值' : '盈亏'}
          </button>
          <button onClick={() => setAddTarget('menu')}
            className="px-2.5 py-[3px] rounded-md text-[11px] border border-accent/40 bg-accent/10 text-accent hover:bg-accent/20 transition-colors cursor-pointer">
            + 添加
          </button>
        </div>
      </div>

      {/* Add menu / form */}
      {addTarget === 'menu' && (
        <div className="px-3 md:px-6 py-3 border-b border-border bg-surface-2/60 flex flex-wrap gap-2 items-center">
          <span className="text-[11px] text-text-dim mr-2">添加到哪一类?</span>
          {[
            ['A:A', 'A股'],
            ['A:HK', '港股'],
            ['A:US', '美股'],
            ...TYPE_ORDER.filter(t => t !== 'A').map(t => [t, TYPE_META[t].label]),
          ].map(([target, label]) => (
            <button key={target} onClick={() => setAddTarget(target)}
              className="px-3 py-1 rounded border border-border-med text-[12px] text-text hover:border-accent hover:text-accent transition-colors cursor-pointer">
              + {label}
            </button>
          ))}
          <button onClick={() => setAddTarget(null)}
            className="ml-auto text-[11px] text-text-dim hover:text-text cursor-pointer">取消</button>
        </div>
      )}

      {String(addTarget || '').startsWith('A:') && (
        <AddAShareForm initialMarket={addTarget.split(':')[1]}
          brokers={brokers}
          onDone={() => { setAddTarget(null); onAdd?.() }} onCancel={() => setAddTarget(null)} />
      )}
      {(addTarget === 'F' || addTarget === 'C' || addTarget === 'R' || addTarget === 'W' || addTarget === 'M') && (
        <AddAssetForm typeKey={addTarget}
          brokers={brokers}
          onDone={() => { setAddTarget(null); loadAssets() }}
          onCancel={() => setAddTarget(null)} />
      )}

      {editAsset && (
        <EditAssetRow asset={editAsset}
          brokers={brokers}
          onDone={() => { setEditAsset(null); loadAssets() }}
          onCancel={() => setEditAsset(null)} />
      )}

      {addLotAsset && (
        <AddLotRow asset={addLotAsset}
          brokers={brokers}
          onDone={() => { setAddLotAsset(null); loadAssets(); loadRealized() }}
          onCancel={() => setAddLotAsset(null)} />
      )}

      {reduceAsset && (
        <ReduceLotRow asset={reduceAsset}
          onDone={() => { setReduceAsset(null); loadAssets(); loadRealized() }}
          onCancel={() => setReduceAsset(null)} />
      )}

      {actionsAsset && (
        <AssetActionsModal asset={actionsAsset}
          onClose={() => setActionsAsset(null)}
          onChanged={() => { loadAssets(); loadRealized() }} />
      )}

      {klineHolding && (
        <StockKlineModal holding={klineHolding} onClose={() => setKlineHolding(null)} />
      )}
      {thesisTarget && (
        <ThesisModal row={thesisTarget} onClose={() => setThesisTarget(null)}
          onSaved={() => { loadThesisCodes(); setThesisTarget(null) }} />
      )}

      {/* Column headers */}
      {!isEmpty && (
        <div className="licai-row px-3 md:px-6 py-2 text-[10.5px] text-text-dim tracking-wider font-medium border-b border-border bg-surface">
          <div className="text-left">名称 · 代码</div>
          <div className="text-right">市值</div>
          <div className="text-right licai-md-only">成本</div>
          <div className="text-right">浮动盈亏</div>
          <div className="text-right">今日</div>
          <div className="text-right licai-md-only">占比</div>
          <div className="text-left pl-2">操作</div>
        </div>
      )}

      {/* Groups + rows */}
      {visibleTypes.map(type => {
        const g = agg.groups[type]
        const isCol = collapsed[type]
        const groupFxExposure = type === 'A' ? (agg.fxExposure || []) : []
        const items = [...g.items].sort((a, b) =>
          sortKey === 'pnl'
            ? (b.pnl || 0) - (a.pnl || 0)
            : (b.mv || 0) - (a.mv || 0)
        )
        return (
          <div key={type}>
            {/* Group strip */}
            <div onClick={() => setCollapsed(c => ({ ...c, [type]: !c[type] }))}
              className="licai-row px-3 md:px-6 py-2 border-b border-border cursor-pointer select-none items-center text-[11px] font-semibold text-text"
              style={{ background: 'var(--color-surface-2)' }}>
              <div className="flex items-center gap-2">
                <span className="inline-block w-2 text-text-dim transition-transform"
                  style={{ transform: isCol ? 'rotate(-90deg)' : 'rotate(0)' }}>▾</span>
                <div className="w-[3px] h-[13px] rounded-sm" style={{ background: TYPE_COLOR[type] }} />
                <span>{TYPE_META[type].label}</span>
                <span className="text-text-dim font-normal text-[10.5px]">{items.length} 项</span>
              </div>
              <div className="text-right flex flex-col items-end">
                <span className="font-mono text-text">¥{fmtMoney(g.mv)}</span>
                {groupFxExposure.length > 0 && (
                  <span className="font-mono text-[9.5px] text-text-muted hidden md:inline">
                    {groupFxExposure.map(e => formatCurrencyMoney(e.currency, e.originalMarketValue)).join(' / ')}
                  </span>
                )}
              </div>
              <div className="text-right font-mono text-text-dim text-[10.5px] licai-md-only">¥{fmtMoney(g.cost)}</div>
              <div className={`text-right font-mono ${priceColor(g.pnl)}`}>
                {g.pnl >= 0 ? '+' : ''}{fmtMoney(g.pnl)}
                <span className="text-[10px] opacity-80 ml-1.5">({fmtPct(g.pnlPct)})</span>
              </div>
              <div />
              <div className="text-right font-mono text-text licai-md-only">{(g.weight * 100).toFixed(1)}%</div>
              <div />
            </div>

            {/* Rows (with optional fund subcategory headers) */}
            {!isCol && (() => {
              const renderRow = (row, isLast) => (
                <div key={row.id}
                  onMouseEnter={() => setHoverId(row.id)}
                  onMouseLeave={() => setHoverId(null)}
                  className="licai-row px-3 md:px-6 py-[11px] items-center transition-colors"
                  style={{
                    borderBottom: isLast ? '1px solid var(--color-border)' : '1px solid var(--color-border-subtle)',
                    background: hoverId === row.id ? 'var(--color-surface-2)' : 'transparent',
                  }}>
                  <div className="flex flex-col gap-0.5 min-w-0">
                    <div className="flex items-center gap-1.5 min-w-0">
                      <span className="text-[13px] font-semibold text-text-bright truncate">{row.name}</span>
                      <TypeChip type={row.type} compact />
                      {row.type === 'A' && <MarketChip market={row.extra?.market} />}
                      {row.broker && (
                        <span className="text-[10px] px-1.5 py-[1px] rounded bg-info/15 text-info border border-info/40 shrink-0 whitespace-nowrap font-medium">
                          {row.broker}
                        </span>
                      )}
                      {row.extra?.okxSynced && (
                        <Tooltip content="OKX 自动同步中">
                          <span className="text-bull cursor-help text-[12px] leading-none">🔗</span>
                        </Tooltip>
                      )}
                      {row.extra?.okxSynced === false && (
                        <Tooltip content="OKX 凭证失效，请到设置重新配置">
                          <span className="text-warn cursor-help text-[12px] leading-none">⚠︎</span>
                        </Tooltip>
                      )}
                      {insights.overlapRowIds.has(row.id) && (
                        <Tooltip content={
                          <div>
                            <div className="text-text-bright font-semibold mb-1">同源风险家族</div>
                            <div className="flex flex-col gap-0.5">
                              {(insights.overlapByRow[row.id] || []).map(f => (
                                <div key={f} className="text-text">· {f}</div>
                              ))}
                            </div>
                            <div className="text-text-dim mt-1.5 text-[10.5px] leading-snug">
                              你在 A股 + 基金里都重仓同一类资产，实际敞口被低估
                            </div>
                          </div>
                        }>
                          <span className="inline-flex items-center gap-0.5 text-[9.5px] font-semibold px-1 py-[1px] rounded shrink-0 cursor-help"
                            style={{ color: '#e58a8a', background: '#e58a8a18', border: '1px solid #e58a8a40' }}>↔ 同源</span>
                        </Tooltip>
                      )}
                    </div>
                    <span className="font-mono text-[10px] text-text-muted truncate">{row.code}</span>
                  </div>
                  <div className="text-right flex flex-col items-end">
                    <span className="font-mono text-[12.5px] text-text-bright tabular-nums">
                      ¥{fmtMoney(row.mv)}
                    </span>
                    {row.extra?.currency && row.extra.currency !== 'CNY' && row.extra?.originalMarketValue != null && (
                      <FxHint extra={row.extra}>
                        <span className="font-mono text-[10px] text-text-muted tabular-nums cursor-help">
                          {formatCurrencyMoney(row.extra.currency, row.extra.originalMarketValue)}
                        </span>
                      </FxHint>
                    )}
                    {row.type === 'R' && row.extra?.okxTotalBudgetUsdt > 0 && row.extra?.okxAvailableUsdt != null && (
                      <Tooltip content={
                        <div className="leading-relaxed">
                          <div className="text-text-bright font-semibold mb-1">
                            马丁策略预算 {row.extra.okxBudgetSource === 'manual' ? '(手填)' : '(算法估算)'}
                          </div>
                          <div>首单 {row.extra.okxInitOrderAmt}U · 安全单基础 {row.extra.okxSafetyOrderAmt}U × {row.extra.okxMaxSafetyOrders} 档 · 量倍数 {row.extra.okxVolMult}</div>
                          <div className="mt-1 text-[10.5px]">
                            已投 <span className="text-text font-mono">{row.extra.okxInvestmentUsdt}U</span>
                            <span className="mx-1">/</span>
                            预算 <span className="text-text font-mono">{row.extra.okxTotalBudgetUsdt}U</span>
                            <span className="mx-1">·</span>
                            待投 <span className="text-bull-bright font-mono">{row.extra.okxAvailableUsdt}U</span>
                          </div>
                          {row.extra.okxBudgetSource !== 'manual' && (
                            <div className="mt-1 text-[10px] text-warn">
                              OKX raw 没"总预算"字段, 算法反推可能不准. 点击下面"改预算"用 OKX 客户端实际值覆盖.
                            </div>
                          )}
                        </div>
                      }>
                        <span
                          onClick={async (e) => {
                            e.stopPropagation()
                            const cur = row.extra.okxTotalBudgetUsdt
                            const next = prompt(
                              `「${row.name}」马丁实际总预算 (USDT)\n填 OKX 客户端策略详情看到的"总投资额", 留空恢复算法估算`,
                              cur ? String(cur) : ''
                            )
                            if (next === null) return
                            const v = next.trim() === '' ? null : parseFloat(next)
                            if (next.trim() !== '' && (!v || v <= 0)) { alert('总预算必须为正数'); return }
                            try {
                              await fetchJSON(`/api/assets/${row.extra.assetIdRaw}`, {
                                method: 'PUT',
                                body: JSON.stringify({ bot_budget_override_usdt: v }),
                              })
                              const d = await fetchJSON('/api/assets')
                              setAssets(d.assets || [])
                            } catch (err) { alert('保存失败: ' + (err?.message || '')) }
                          }}
                          className="font-mono text-[10px] text-text-muted tabular-nums cursor-pointer hover:text-accent mt-0.5 whitespace-nowrap"
                          title="点击改总预算">
                          投 {row.extra.okxInvestmentUsdt}U / {row.extra.okxTotalBudgetUsdt}U <span className="text-bull-bright">余 {row.extra.okxAvailableUsdt}U</span>{row.extra.okxBudgetSource !== 'manual' && (
                            <span className="text-warn ml-1" title="算法估算, 可能不准">~</span>
                          )}
                        </span>
                      </Tooltip>
                    )}
                  </div>
                  <div className="text-right flex flex-col items-end licai-md-only">
                    <span className="font-mono text-[11px] text-text-dim tabular-nums">
                      ¥{fmtMoney(row.cost)}
                    </span>
                    {row.extra?.currency && row.extra.currency !== 'CNY' && row.extra?.originalCostValue != null && (
                      <FxHint extra={row.extra}>
                        <span className="font-mono text-[9.5px] text-text-muted tabular-nums cursor-help">
                          {formatCurrencyMoney(row.extra.currency, row.extra.originalCostValue)}
                        </span>
                      </FxHint>
                    )}
                  </div>
                  <div className="text-right flex flex-col items-end">
                    {row.type === 'M' && row.extra?.monthlyInterestEst ? (
                      <Tooltip content={
                        <div className="leading-relaxed">
                          <div className="text-text-bright font-semibold mb-1">月息流估算</div>
                          <div>当前余额 ¥{fmtMoney(row.mv)} × 年化 {(row.extra.annualYield * 100).toFixed(2)}% / 12</div>
                          <div className="mt-1 text-[10.5px] text-text-dim">实际利率每天微变，估算 ±5% 误差</div>
                          <div className="mt-1 text-[10.5px] text-bull-bright">
                            日息 ≈ +¥{(row.extra.dailyInterestEst || 0).toFixed(2)} · 年息 ≈ +¥{fmtMoney(row.extra.yearlyInterestEst || 0)}
                          </div>
                        </div>
                      }>
                        <div className="flex flex-col items-end cursor-help">
                          <span className="font-mono text-[12.5px] font-semibold tabular-nums text-bull-bright">
                            ≈ +¥{fmtMoney(row.extra.monthlyInterestEst)}/月
                          </span>
                          <span className="font-mono text-[10px] text-text-dim">
                            年化 {(row.extra.annualYield * 100).toFixed(2)}%
                          </span>
                        </div>
                      </Tooltip>
                    ) : (
                      <>
                        <span className={`font-mono text-[12.5px] font-semibold tabular-nums ${priceColor(row.pnl)}`}>
                          {row.pnl != null ? (row.pnl >= 0 ? '+' : '') + fmtMoney(row.pnl) : '--'}
                        </span>
                        <span className={`font-mono text-[10px] opacity-85 ${priceColor(row.pnlPct)}`}>
                          {row.pnlPct != null ? fmtPct(row.pnlPct) : ''}
                        </span>
                        {row.type === 'A' && Math.abs(carryByCode[row.code] || 0) > 0.5 && (() => {
                          const total = (row.pnl || 0) + carryByCode[row.code]
                          return (
                            <Tooltip content="全周期真实盈亏 = 当前持仓浮动 + 历史已实现(清仓段/部分卖出)。清仓后又买回的票, 行内浮动只算这手, 这里把历史亏赚补回来。">
                              <div className={`font-mono text-[9.5px] ${priceColor(total)} opacity-90 cursor-help`}>
                                真实 {total >= 0 ? '+' : ''}{fmtMoney(total)}
                              </div>
                            </Tooltip>
                          )
                        })()}
                      </>
                    )}
                  </div>
                  <div className="text-right">
                    {row.type === 'M' && row.extra?.dailyInterestEst ? (
                      <Tooltip content={
                        <div>
                          <div className="text-text-bright font-semibold mb-1">日息估算</div>
                          <div>当前余额 × 年化 / 365</div>
                          <div className="mt-1 text-text-dim text-[10.5px]">货币基金每日结息</div>
                        </div>
                      }>
                        <span className="font-mono text-[11px] text-bull-bright cursor-help">
                          +¥{row.extra.dailyInterestEst.toFixed(2)}
                        </span>
                      </Tooltip>
                    ) : row.type === 'F' && row.extra?.proxyChangePct != null ? (
                      <ProxyPulse
                        change={row.extra.proxyChangePct}
                        label={row.extra.proxyLabel}
                        details={row.extra.proxyDetails}
                        fallbackToday={row.today} />
                    ) : (
                      <TodayPulse change={row.today} />
                    )}
                  </div>
                  <div className="text-right licai-md-only">
                    <WeightBar weight={row.mv / (agg.totalMv || 1)} color={TYPE_COLOR[row.type]} />
                  </div>
                  <div className="relative pl-1 md:pl-2">
                    {/* TypeMiniInfo: 桌面始终可见 (不再 hover 隐藏); 移动隐藏 */}
                    <div className="hidden md:block">
                      <TypeMiniInfo row={row} />
                    </div>
                    {/* RowActions: 桌面 hover 显示, 绝对覆盖右半. 容器 pointer-events-none 让事件
                        穿透到 TypeMiniInfo (反推 tooltip 之类), 子元素重置 auto 接收点击.
                        hover 时给个 surface-2 渐变遮罩, 与 row hover 背景色一致, 视觉自然. */}
                    <div className="md:absolute md:inset-y-0 md:right-0 flex items-center md:pr-3 md:pointer-events-none [&>div]:pointer-events-auto transition-opacity"
                      style={{
                        background: hoverId === row.id
                          ? 'linear-gradient(to right, transparent 0%, var(--color-surface-2) 18%, var(--color-surface-2) 100%)'
                          : 'transparent',
                        transition: 'background .18s',
                      }}>
                      <RowActions row={row} visible={hoverId === row.id}
                        onEdit={handleEdit} onHistory={onHistory} onRemove={removeAsset}
                        onAddLot={handleAddLot} onReduceLot={handleReduceLot}
                        onShowActions={handleShowActions}
                        onKline={setKlineHolding}
                        onThesis={setThesisTarget} hasThesis={thesisCodes.has(row.code)} />
                    </div>
                  </div>
                </div>
              )

              // For FUND / A股 with >1 items: render category subgroups.
              if ((type === 'F' || type === 'A') && items.length > 1) {
                const clusters = {}
                for (const it of items) {
                  const cid = it.category?.id || 'other'
                  if (!clusters[cid]) clusters[cid] = { id: cid, label: it.category?.label || '其他', items: [], mv: 0, cost: 0, pnl: 0 }
                  clusters[cid].items.push(it)
                  clusters[cid].mv += it.mv || 0
                  clusters[cid].cost += it.cost || 0
                  clusters[cid].pnl += it.pnl || 0
                }
                const ordered = Object.values(clusters).sort((a, b) => b.mv - a.mv)
                if (ordered.length > 1) {
                  return ordered.map((cl, ci) => {
                    const isLastCluster = ci === ordered.length - 1
                    return (
                      <React.Fragment key={cl.id}>
                        <div className="licai-row px-3 md:px-6 py-1.5 items-center text-[10.5px] text-text-dim border-b border-border-subtle"
                          style={{ background: 'var(--color-surface)' }}>
                          <div className="flex items-center gap-1.5 pl-4">
                            <span className="inline-block w-1 h-1 rounded-full" style={{ background: TYPE_COLOR[type] }} />
                            <span className="font-medium text-text">{cl.label}</span>
                            <span className="opacity-70">{cl.items.length} 项</span>
                          </div>
                          <div className="text-right font-mono">¥{fmtMoney(cl.mv)}</div>
                          <div className="licai-md-only" />
                          <div className={`text-right font-mono ${priceColor(cl.pnl)}`}>
                            {cl.pnl >= 0 ? '+' : ''}{fmtMoney(cl.pnl)}
                          </div>
                          <div />
                          <div className="licai-md-only" />
                          <div />
                        </div>
                        {cl.items.map((row, ri) =>
                          renderRow(row, isLastCluster && ri === cl.items.length - 1))}
                      </React.Fragment>
                    )
                  })
                }
              }
              return items.map((row, ri) => renderRow(row, ri === items.length - 1))
            })()}
          </div>
        )
      })}

      {filtered.length === 0 && !isEmpty && (
        <div className="py-8 text-center text-text-dim text-[12px]">
          当前筛选下没有持仓
        </div>
      )}

      <ClosedPositionsBlock
        items={[
          ...(realized?.stockItems || []).filter(i => !i.still_holding),
          // 已清仓的理财/基金/加密 (排除 CASH/BOT): 同一只基金分多次买卖会记成多条(不同 asset_id),
          // 按 code 合并、已实现盈亏求和, 一只基金只显示一行(避免同名重复)。
          ...Object.values(
            (realized?.assetItems || [])
              .filter(i => !i.still_holding && i.asset_type !== 'CASH' && i.asset_type !== 'BOT'
                && (i.realized_pnl || 0) !== 0)
              .reduce((acc, i) => {
                const key = i.code || `#${i.asset_id}`
                if (!acc[key]) acc[key] = { stock_code: key, stock_name: i.name, realized_pnl: 0,
                  _asset: true, asset_id: i.asset_id, code: i.code }
                acc[key].realized_pnl += (i.realized_pnl || 0)
                return acc
              }, {})
          ),
        ]}
        onHistory={onHistory} onKline={setKlineHolding} />
    </section>
  )
}

// 已清仓股票区块 — 列出持仓已删但还有交易历史的标的, 看到累计已实现盈亏 + 翻历史
function ClosedPositionsBlock({ items, onHistory, onKline }) {
  const [open, setOpen] = useState(false)
  if (!items || items.length === 0) return null
  const total = items.reduce((s, it) => s + (it.realized_pnl || 0), 0)
  return (
    <div className="px-3 md:px-6 py-2 border-t border-border-subtle bg-surface/40">
      <button onClick={() => setOpen(o => !o)}
        className="w-full flex items-center justify-between text-[11.5px] text-text-dim hover:text-text cursor-pointer transition-colors">
        <span className="flex items-baseline gap-2">
          <span>已清仓 {items.length} 笔</span>
          <span className="text-text-muted text-[10.5px]">流水保留, 不在主列表显示</span>
        </span>
        <span className="flex items-baseline gap-2">
          <span className={priceColor(total)}>
            累计 {total >= 0 ? '+' : ''}¥{fmtMoney(total)}
          </span>
          <span className="text-text-muted text-[10px]">{open ? '▾' : '▸'}</span>
        </span>
      </button>
      {open && (
        <div className="mt-2 pt-2 border-t border-border-subtle space-y-1">
          {items.map(it => (
            <div key={it.stock_code}
              className="flex items-baseline justify-between text-[11.5px] py-1 px-1 rounded hover:bg-surface-2 transition-colors">
              <span className="flex items-baseline gap-2 min-w-0">
                <span className="text-text-bright truncate">{it.stock_name || '--'}</span>
                {!it._asset && <span className="font-mono text-[10px] text-text-muted">{it.stock_code}</span>}
                {it._asset && <span className="text-[9.5px] px-1 rounded bg-surface-3 text-text-muted">理财/基金</span>}
              </span>
              <span className="flex items-baseline gap-3 shrink-0">
                <span className={`font-mono ${priceColor(it.realized_pnl)}`}>
                  {it.realized_pnl >= 0 ? '+' : ''}¥{fmtMoney(it.realized_pnl)}
                </span>
                {/* A股清仓 / 场内ETF清仓: 有K线; BS标记按保留的流水照常标出买卖点 */}
                {((!it._asset) || (it._asset && isOnchainEtf(it.code))) && (
                  <button onClick={() => onKline?.({
                    stock_code: it.code || it.stock_code, stock_name: it.stock_name,
                    asset_id: it._asset ? it.asset_id : undefined,
                  })}
                    className="text-[10.5px] text-text-dim hover:text-accent cursor-pointer">
                    K线
                  </button>
                )}
                {!it._asset && (
                  <button onClick={() => onHistory?.({ stock_code: it.stock_code, stock_name: it.stock_name })}
                    className="text-[10.5px] text-text-dim hover:text-accent cursor-pointer">
                    流水
                  </button>
                )}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ============================================================
// Add stock form — compact, inline
// ============================================================
function ThesisModal({ row, onClose, onSaved }) {
  const code = row.code
  const name = row.name || ''
  const [text, setText] = useState('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [meta, setMeta] = useState(null)
  useEffect(() => {
    let alive = true
    fetchJSON(`/api/portfolio/thesis/${encodeURIComponent(code)}`)
      .then(d => { if (alive) { setText(d?.thesis || ''); setMeta(d || null) } })
      .catch(() => {})
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [code])
  const created = (meta?.created_at || '').slice(0, 10)
  const updated = (meta?.updated_at || '').slice(0, 10)
  const save = async () => {
    setSaving(true)
    try {
      await fetchJSON(`/api/portfolio/thesis/${encodeURIComponent(code)}`, {
        method: 'PUT', body: JSON.stringify({ thesis: text, name }),
      })
      onSaved?.()
    } catch (e) { console.error(e) } finally { setSaving(false) }
  }
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" onClick={onClose}>
      <div className="bg-surface-2 border border-border rounded-xl w-full max-w-md p-4 md:p-5" onClick={e => e.stopPropagation()}>
        <div className="flex items-baseline gap-2 mb-1">
          <h3 className="text-[14px] font-semibold text-text-bright m-0">买入逻辑</h3>
          <span className="text-[12px] text-text-bright">{name}</span>
          <span className="font-mono text-[10.5px] text-text-muted">{code}</span>
          <button onClick={onClose} className="ml-auto text-text-muted hover:text-text cursor-pointer">✕</button>
        </div>
        <p className="text-[10.5px] text-text-muted mb-2">记下当初为什么买、看中什么、预期。以后问 AI「这只逻辑还成立吗」会照这个客观复盘。</p>
        <textarea value={text} onChange={e => setText(e.target.value)} disabled={loading}
          rows={6}
          placeholder={loading ? '加载中…' : '例: 国产算力龙头, 中科院系国资背景; 看好 AI 数据中心需求; 等存储涨价兑现到业绩'}
          className="w-full text-[12px] px-3 py-2 rounded-lg bg-surface-3 border border-border text-text placeholder:text-text-muted focus:border-accent/50 outline-none resize-y" />
        <div className="flex items-center gap-2 mt-3">
          {created && <span className="text-[10px] text-text-muted">记于 {created}{updated && updated !== created ? ` · 改于 ${updated}` : ''}</span>}
          <button onClick={save} disabled={saving || loading}
            className="ml-auto text-[12px] px-3.5 py-1.5 rounded-lg bg-accent/20 text-accent border border-accent/40 hover:bg-accent/30 transition-colors cursor-pointer disabled:opacity-40">
            {saving ? '保存中…' : '保存'}
          </button>
        </div>
      </div>
    </div>
  )
}

function AddAShareForm({ initialMarket = 'A', onDone, onCancel, brokers = [] }) {
  const [form, setForm] = useState({
    code: '', name: '', shares: '', cost: '',
    tradeDate: new Date().toISOString().slice(0, 10),
    tradeTime: '',
  })
  const [market, setMarket] = useState(initialMarket)
  const [submitting, setSubmitting] = useState(false)
  const [nameLooking, setNameLooking] = useState(false)
  const [broker, setBroker] = useState('')

  const lookup = useCallback(async (nextCode, nextMarket = market) => {
    const fullCode = stockCodeForMarket(nextMarket, nextCode)
    if (!nextCode) return
    setNameLooking(true)
    try {
      const q = await fetchJSON(`/api/market/quote/${encodeURIComponent(fullCode)}`)
      if (q?.stock_name) setForm(f => ({ ...f, name: q.stock_name }))
    } catch {}
    setNameLooking(false)
  }, [market])

  const submit = async () => {
    const meta = STOCK_MARKETS[market]
    const stockCode = stockCodeForMarket(market, form.code)
    if (!form.code) return alert('请输入股票代码')
    if (market === 'A' && !/^\d{6}$/.test(stockCode)) return alert('请输入6位A股代码')
    if (market === 'HK' && !/^HK\.\d{5}$/.test(stockCode)) return alert('请输入港股5位代码')
    if (market === 'US' && !/^US\.[A-Z.]+$/.test(stockCode)) return alert('请输入美股Ticker')
    if (!form.shares || parseInt(form.shares) < meta.minShares) return alert(`持仓数量至少${meta.minShares}`)
    if (!form.cost || parseFloat(form.cost) <= 0) return alert('请输入成本价')
    setSubmitting(true)
    try {
      const res = await api.addHolding({
        stock_code: stockCode, stock_name: form.name,
        shares: parseInt(form.shares), cost_price: parseFloat(form.cost),
        trade_date: form.tradeDate || undefined,
        trade_time: (market === 'A' && form.tradeTime) ? form.tradeTime : undefined,
        broker: broker || null,
      })
      if (res.message) onDone?.()
      else alert(res.detail || '添加失败')
    } finally { setSubmitting(false) }
  }

  const inp = 'bg-bg border border-border rounded px-2 py-1.5 text-[13px] text-text outline-none focus:border-accent'

  return (
    <div className="px-6 py-3 bg-surface-2/50 border-b border-border flex flex-wrap gap-2 items-end">
      <div className="flex flex-col gap-1">
        <label className="text-[11px] text-text-dim">市场</label>
        <select className={`${inp} w-24`} value={market}
          onChange={e => {
            const nextMarket = e.target.value
            setMarket(nextMarket)
            setForm({ code: '', name: '', shares: '', cost: '' })
          }}>
          {Object.entries(STOCK_MARKETS).map(([k, v]) => (
            <option key={k} value={k}>{v.label}</option>
          ))}
        </select>
      </div>
      <div className="flex flex-col gap-1">
        <label className="text-[11px] text-text-dim">代码</label>
        <input className={`${inp} w-28 font-mono`} placeholder={STOCK_MARKETS[market].placeholder}
          value={form.code}
          onChange={e => {
            const v = e.target.value.toUpperCase()
            setForm({ ...form, code: v })
            if ((market === 'A' && v.length === 6) || (market === 'HK' && v.length >= 4) || (market === 'US' && v.length >= 1)) {
              lookup(v, market)
            }
          }} />
      </div>
      <div className="flex flex-col gap-1">
        <label className="text-[11px] text-text-dim">名称</label>
        <input className={`${inp} w-28`} placeholder={nameLooking ? '查询中...' : '可留空'}
          value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} />
      </div>
      <div className="flex flex-col gap-1">
        <label className="text-[11px] text-text-dim">数量</label>
        <input type="number" className={`${inp} w-24 font-mono`}
          placeholder={market === 'US' ? '10' : '300'} min={STOCK_MARKETS[market].minShares} step={STOCK_MARKETS[market].step} value={form.shares}
          onChange={e => setForm({ ...form, shares: e.target.value })} />
      </div>
      <div className="flex flex-col gap-1">
        <label className="text-[11px] text-text-dim">成本价</label>
        <input type="number" className={`${inp} w-28 font-mono`}
          placeholder="12.7401" step={0.0001} value={form.cost}
          onChange={e => setForm({ ...form, cost: e.target.value })} />
      </div>
      <div className="flex flex-col gap-1">
        <label className="text-[11px] text-text-dim">买入日期</label>
        <input type="date" className={`${inp} w-36 font-mono`}
          value={form.tradeDate}
          onChange={e => setForm({ ...form, tradeDate: e.target.value })} />
      </div>
      {market === 'A' && (
        <div className="flex flex-col gap-1">
          <label className="text-[11px] text-text-dim">时刻 <span className="text-text-muted text-[10px]">可空</span></label>
          <input type="time" className={`${inp} w-28 font-mono text-text-dim`}
            value={form.tradeTime}
            onChange={e => setForm({ ...form, tradeTime: e.target.value })}
            title="成交时刻(可选), 留空用录入时间, 供分时图打点" />
        </div>
      )}
      <div className="flex flex-col gap-1">
        <label className="text-[11px] text-text-dim">券商</label>
        <select className={`${inp} w-28`} value={broker} onChange={e => setBroker(e.target.value)}>
          <option value="">默认</option>
          {brokers.map(b => <option key={b.id} value={b.name}>{b.name}</option>)}
        </select>
      </div>
      <button onClick={submit} disabled={submitting}
        className="px-4 py-1.5 rounded-md bg-accent text-bg font-medium text-[13px] hover:opacity-90 transition-opacity disabled:opacity-50 cursor-pointer">
        {submitting ? '...' : '确认'}
      </button>
      <button onClick={onCancel}
        className="px-3 py-1.5 rounded-md border border-border text-text-dim text-[13px] hover:text-text transition-colors cursor-pointer">
        取消
      </button>
      <div className="text-[10px] text-text-muted pb-1">
        {STOCK_MARKETS[market].hint} · 成本价按{market === 'US' ? '美元' : market === 'HK' ? '港币' : '人民币'}录入，总资产自动折人民币
      </div>
    </div>
  )
}

// ============================================================
// Add asset form — covers F/C/R with type-specific fields
// (trimmed version of the original ExternalAssets AddAssetForm)
// ============================================================
function AddAssetForm({ typeKey, onDone, onCancel, brokers = [] }) {
  const assetType = KEY_TO_ASSET_TYPE[typeKey]
  const [code, setCode] = useState('')
  const [name, setName] = useState('')
  const [platform, setPlatform] = useState('')
  const [shares, setShares] = useState('')
  const [cost, setCost] = useState('')
  const [costTouched, setCostTouched] = useState(false)
  const [selectedBroker, setSelectedBroker] = useState('')
  const [manualValue, setManualValue] = useState('')
  const [unitPrice, setUnitPrice] = useState('')
  const [fee, setFee] = useState('')
  const [feeTouched, setFeeTouched] = useState(false)
  const [note, setNote] = useState('')
  const [tradeTime, setTradeTime] = useState('')     // 成交时刻 HH:MM (场内 ETF/加密, 可选)
  const [feeRatePct, setFeeRatePct] = useState('')   // FUND 申购费率 %, C 类填 0
  const [lookingUp, setLookingUp] = useState(false)
  const [hint, setHint] = useState('')
  // OKX integration (only for BOT)
  const [okxBots, setOkxBots] = useState(null)
  const [okxAlgoId, setOkxAlgoId] = useState('')
  const [okxBotType, setOkxBotType] = useState('')
  // WEALTH (理财)
  const [annualYield, setAnnualYield] = useState('')
  const [startDate, setStartDate] = useState(new Date().toISOString().slice(0, 10))
  // DCA 定投 (FUND/CRYPTO only, 可选)
  const [dcaEnabled, setDcaEnabled] = useState(false)
  const [dcaValue, setDcaValue] = useState('')
  const [dcaFrequency, setDcaFrequency] = useState('monthly')
  const [dcaDayOfMonth, setDcaDayOfMonth] = useState(15)
  const [dcaDayOfWeek, setDcaDayOfWeek] = useState(1)

  useEffect(() => {
    if (assetType !== 'BOT') return
    fetchJSON('/api/assets/okx/status').then(s => {
      if (!s.configured) { setOkxBots([]); return }
      fetchJSON('/api/assets/okx/bots').then(r => setOkxBots(r.bots || [])).catch(() => setOkxBots([]))
    }).catch(() => setOkxBots([]))
  }, [assetType])

  // 按股买: 自动估算手续费 (场内 ETF 默认; 场外公募手动改 0)
  useEffect(() => {
    if (!(assetType === 'FUND' || assetType === 'CRYPTO') || feeTouched) return
    const s = parseFloat(shares); const u = parseFloat(unitPrice)
    if (s > 0 && u > 0) {
      const amount = s * u
      const kind = isOnchainEtf(code) ? 'etf' : 'stock'
      const est = estimateFee(amount, brokers, selectedBroker, kind)
      setFee(est.toFixed(2))
    }
  }, [shares, unitPrice, feeTouched, assetType, code, brokers, selectedBroker])

  // 按股买: 累计投入 = 单价 × 份额 + 手续费 (用户没手填本金时自动)
  useEffect(() => {
    if (!(assetType === 'FUND' || assetType === 'CRYPTO') || costTouched) return
    const s = parseFloat(shares); const u = parseFloat(unitPrice); const f = parseFloat(fee) || 0
    if (s > 0 && u > 0) {
      setCost((s * u + f).toFixed(2))
    }
  }, [shares, unitPrice, fee, costTouched, assetType])

  const pickOkxBot = (bot) => {
    if (!bot) { setOkxAlgoId(''); setOkxBotType(''); return }
    setOkxAlgoId(bot.algo_id)
    setOkxBotType(bot.bot_type)
    setCode(`OKX-${bot.algo_id.slice(-6)}`)
    setName(`OKX ${bot.inst_id} ${bot.kind_label}`)
    setPlatform('OKX')
    const rate = 7.2
    setCost((bot.investment_usdt * rate).toFixed(2))
    setManualValue((bot.current_value_usdt * rate).toFixed(2))
    setHint(`✓ 已绑定 · 投入 ${bot.investment_usdt}U · 当前 ${bot.current_value_usdt}U · ${bot.pnl_pct >= 0 ? '+' : ''}${bot.pnl_pct}%`)
  }

  const lookupCode = async () => {
    if (!code || assetType === 'BOT' || assetType === 'WEALTH' || assetType === 'CASH') return
    setLookingUp(true)
    setHint('')
    try {
      if (assetType === 'FUND') {
        const q = await fetchJSON(`/api/assets/quote/fund/${code}`)
        if (q?.name) { setName(q.name); setHint(`✓ ${q.realtime ? '估值' : '昨净值'} ${q.est_nav || q.nav}`) }
      } else if (assetType === 'CRYPTO') {
        const q = await fetchJSON(`/api/assets/quote/crypto/${code}`)
        if (q?.price) { if (!name) setName(code); setHint(`✓ 现价 $${q.price}`) }
      }
    } catch { setHint('✗ 查询失败,可手填') }
    finally { setLookingUp(false) }
  }

  const submit = async () => {
    // CASH: balance maps to cost_amount + manual_value; optional yield used for monthly est
    const isYieldType = assetType === 'WEALTH' || assetType === 'CASH'
    const costLabel = assetType === 'CASH' ? '当前余额必填'
      : (assetType === 'BOT' || assetType === 'WEALTH') ? '投入本金必填' : '累计投入必填'
    if (!cost) return alert(costLabel)
    if (!code || !name) return alert('代码/名称必填')
    if (assetType === 'BOT' && !manualValue && !okxAlgoId) return alert('当前资产必填（或绑定 OKX 自动同步）')

    // 内联 DCA 校验 (仅 FUND/CRYPTO)
    let dcaPayload = null
    if (dcaEnabled && (assetType === 'FUND' || assetType === 'CRYPTO')) {
      const v = parseFloat(dcaValue)
      if (!(v > 0)) return alert('定投金额必填且 > 0')
      dcaPayload = {
        mode: 'amount',
        value: v,
        frequency: dcaFrequency,
        day_of_month: dcaFrequency === 'monthly' ? parseInt(dcaDayOfMonth, 10) : null,
        day_of_week: dcaFrequency === 'weekly' ? parseInt(dcaDayOfWeek, 10) : null,
        note: '',
      }
    }

    await fetchJSON('/api/assets', {
      method: 'POST',
      body: JSON.stringify({
        asset_type: assetType, code: code.trim(), name: name.trim(), platform: platform.trim(),
        cost_amount: parseFloat(cost),
        shares: shares ? parseFloat(shares) : null,
        manual_value: manualValue !== '' ? parseFloat(manualValue) : null,
        note: note.trim(),
        okx_algo_id: okxAlgoId || null,
        okx_bot_type: okxBotType || null,
        annual_yield_rate: isYieldType && annualYield !== ''
          ? parseFloat(annualYield) / 100  // user inputs %, store as decimal
          : null,
        start_date: isYieldType ? (startDate || null) : null,
        trade_time: ((assetType === 'FUND' && isOnchainEtf(code)) || assetType === 'CRYPTO') && tradeTime ? tradeTime : null,
        purchase_fee_rate: assetType === 'FUND' && !isOnchainEtf(code) && feeRatePct !== '' ? parseFloat(feeRatePct) / 100 : null,
        broker: assetType === 'FUND' && isOnchainEtf(code) && selectedBroker ? selectedBroker : null,
        // 手续费单独透传 (cost 已含它), 后端单存到初始流水的 fee 字段, 避免在流水里"消失"
        fee: (assetType === 'FUND' || assetType === 'CRYPTO') && parseFloat(fee) > 0 ? parseFloat(fee) : null,
        dca: dcaPayload,
      }),
    })
    onDone?.()
  }

  const inp = 'bg-bg border border-border rounded px-2 py-1 text-[12px] text-text outline-none focus:border-accent'

  return (
    <div className="px-6 py-3 bg-surface-2/50 border-b border-border space-y-2.5 text-[12px]">
      {/* OKX bot picker — only for BOT */}
      {assetType === 'BOT' && okxBots !== null && (
        <div className="rounded border border-border-subtle bg-surface-3/40 px-2.5 py-2">
          {okxBots.length === 0 ? (
            <div className="text-[10.5px] text-text-muted">
              OKX 无可用机器人。手动录入即可，或去 设置 → OKX 配置凭证。
            </div>
          ) : (
            <div className="flex items-center gap-2">
              <label className="text-[10.5px] text-text-muted shrink-0">从 OKX 绑定</label>
              <select value={okxAlgoId}
                onChange={e => pickOkxBot(okxBots.find(b => b.algo_id === e.target.value))}
                className={`${inp} flex-1`}>
                <option value="">-- 不绑定，手动录入 --</option>
                {okxBots.map(b => (
                  <option key={b.algo_id} value={b.algo_id}>
                    {b.active ? '●' : '○'} {b.kind_label} · {b.inst_id} · 投入 {b.investment_usdt}U · {b.pnl_pct >= 0 ? '+' : ''}{b.pnl_pct}%
                  </option>
                ))}
              </select>
            </div>
          )}
        </div>
      )}

      <div className="flex flex-wrap gap-2 items-end">
        <div className="flex flex-col gap-1">
          <label className="text-[11px] text-text-dim">
            {assetType === 'FUND' ? '基金代码' : assetType === 'CRYPTO' ? '币对' :
             assetType === 'WEALTH' ? '产品代码' :
             assetType === 'CASH' ? '账户标识' : '标识'}
          </label>
          <div className="flex gap-1.5">
            <input value={code} onChange={e => setCode(e.target.value)}
              onBlur={lookupCode}
              className={`${inp} w-36 font-mono`}
              placeholder={assetType === 'FUND' ? '161226' : assetType === 'CRYPTO' ? 'BTC-USDT' :
                assetType === 'WEALTH' ? 'YC040204 / 周周宝' :
                assetType === 'CASH' ? 'yuebao / zlt / cczb' : '自定义ID'} />
            {assetType === 'FUND' || assetType === 'CRYPTO' ? (
              <button onClick={lookupCode} disabled={lookingUp}
                className="px-2 py-1 rounded border border-accent/40 text-accent hover:bg-accent/10 text-[11px] cursor-pointer">
                {lookingUp ? '...' : '查询'}
              </button>
            ) : null}
          </div>
        </div>
        <div className="flex flex-col gap-1 flex-1 min-w-[160px]">
          <label className="text-[11px] text-text-dim">名称</label>
          <input value={name} onChange={e => setName(e.target.value)} className={`${inp} w-full`}
            placeholder={
              assetType === 'BOT' ? 'OKX BTC 现货马丁' :
              assetType === 'WEALTH' ? '招商月添利 / 周周宝' :
              assetType === 'CASH' ? '货币基金 / 银行活期' :
              '查询后自动填充'
            } />
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-[11px] text-text-dim">平台</label>
          <input value={platform} onChange={e => setPlatform(e.target.value)} className={`${inp} w-28`}
            placeholder={assetType === 'BOT' ? 'OKX' : assetType === 'WEALTH' ? '银行' :
              assetType === 'CASH' ? '支付平台 / 银行' : '基金平台 / 交易所'} />
        </div>
      </div>

      {hint && <div className={`text-[10px] font-mono ${hint.startsWith('✓') ? 'text-bull' : 'text-bear'}`}>{hint}</div>}

      {/* Pending hint: 场外基金/CRYPTO 只填了金额没填份额 → T+1 待确认 (场内 ETF 按市价即时成交, 不适用) */}
      {((assetType === 'FUND' && !isOnchainEtf(code)) || assetType === 'CRYPTO') && parseFloat(cost) > 0 && !shares && (
        <div className="text-[10.5px] font-mono text-accent">
          💡 没填{assetType === 'FUND' ? '份额' : '数量'} → 当作 T+1 待确认，¥{parseFloat(cost).toFixed(2)} 先记到 pending。
          {assetType === 'FUND' ? '基金' : '币'} 到账后回来编辑补份额自动结算。
        </div>
      )}

      {/* WEALTH 双向估算预览 (CASH 不需要这种估算 — 只录余额) */}
      {assetType === 'WEALTH' && cost && startDate && (() => {
        const principal = parseFloat(cost) || 0
        const days = Math.max(0, Math.floor((new Date() - new Date(startDate)) / 86400000))
        if (days === 0 || principal === 0) return null
        if (annualYield) {
          const r = parseFloat(annualYield) / 100
          const accrued = principal * (1 + r * days / 365)
          return <div className="text-[10px] font-mono text-bull">
            ✓ 持有 {days}天 · 年化 {annualYield}% → 当前总额 ≈ ¥{accrued.toFixed(2)} (利息 +¥{(accrued - principal).toFixed(2)})
          </div>
        }
        if (manualValue) {
          const mv = parseFloat(manualValue) || 0
          const r = (mv / principal - 1) * 365 / days
          return <div className="text-[10px] font-mono text-accent">
            ✓ 持有 {days}天 · 当前 ¥{mv} → 反推年化 ≈ {(r * 100).toFixed(3)}% (利息 +¥{(mv - principal).toFixed(2)})
          </div>
        }
        return null
      })()}

      <div className="flex flex-wrap gap-2 items-end">
        {/* CASH: 当前余额 + 可选 7日年化 */}
        {assetType === 'CASH' && (
          <>
            <div className="flex flex-col gap-1">
              <label className="text-[11px] text-text-dim">当前余额 ¥</label>
              <input type="number" step="0.01" value={cost}
                onChange={e => { setCost(e.target.value); setManualValue(e.target.value) }}
                className={`${inp} w-36 font-mono`} placeholder="3000" />
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-[11px] text-text-dim">7日年化 % (可选)</label>
              <input type="number" step="0.001" value={annualYield}
                onChange={e => setAnnualYield(e.target.value)}
                className={`${inp} w-24 font-mono`} placeholder="1.17" />
            </div>
          </>
        )}

        {assetType === 'FUND' || assetType === 'CRYPTO' ? (
          <>
            <div className="flex flex-col gap-1">
              <label className="text-[11px] text-text-dim">
                {assetType === 'FUND' ? '份额' : '数量'}
              </label>
              <input type="number" step="0.0001" value={shares} onChange={e => setShares(e.target.value)}
                className={`${inp} w-28 font-mono`} placeholder={assetType === 'FUND' ? '1500.23' : '0.012'} />
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-[11px] text-text-dim">
                {assetType === 'FUND' ? '净值/单价' : '单价 $'}
              </label>
              <input type="number" step="0.0001" value={unitPrice} onChange={e => setUnitPrice(e.target.value)}
                className={`${inp} w-28 font-mono`} placeholder={assetType === 'FUND' ? '3.4915' : '40000'} />
            </div>
          </>
        ) : null}
        {assetType !== 'CASH' && (
          <div className="flex flex-col gap-1">
            <label className="text-[11px] text-text-dim">
              {assetType === 'BOT' || assetType === 'WEALTH' ? '投入本金 ¥' : '累计投入 ¥'}
              {(assetType === 'FUND' || assetType === 'CRYPTO') && !costTouched && parseFloat(shares) > 0 && parseFloat(unitPrice) > 0 && (
                <span className="text-[9.5px] text-accent ml-1">自动算</span>
              )}
            </label>
            <input type="number" step="0.01" value={cost}
              onChange={e => { setCost(e.target.value); setCostTouched(true) }}
              className={`${inp} w-32 font-mono`} placeholder="5000" />
          </div>
        )}
        {(assetType === 'FUND' || assetType === 'CRYPTO') && (
          <div className="flex flex-col gap-1">
            <label className="text-[11px] text-text-dim">
              手续费 ¥
              <Tooltip content={
                <div className="leading-relaxed">
                  <div className="text-text-bright font-semibold mb-0.5">手续费 (单笔)</div>
                  <div className="text-text-dim text-[10.5px]">
                    场内 ETF: 默认按 config.commission_rate (万 {(BROKER_COMMISSION_RATE * 10000).toFixed(2)}) + 最低 ¥{BROKER_COMMISSION_MIN}<br/>
                    场外公募 (天天基金 / 支付宝): 通常 0 (C 类) 或申购费<br/>
                    加密货币: 按交易所费率
                  </div>
                </div>
              }>
                <span className="ml-0.5 cursor-help text-text-muted">ⓘ</span>
              </Tooltip>
            </label>
            <input type="number" step="0.01" value={fee}
              onChange={e => { setFee(e.target.value); setFeeTouched(true) }}
              className={`${inp} w-24 font-mono`} placeholder="5.00" />
          </div>
        )}
        {((assetType === 'FUND' && isOnchainEtf(code)) || assetType === 'CRYPTO') && (
          <div className="flex flex-col gap-1">
            <label className="text-[11px] text-text-dim">成交时刻 <span className="text-text-muted text-[10px]">可空</span></label>
            <input type="time" value={tradeTime} onChange={e => setTradeTime(e.target.value)}
              className={`${inp} w-28 font-mono text-text-dim`} title="今日建仓的成交时刻(可选), 留空用录入时间, 供分时图打点" />
          </div>
        )}
        {assetType === 'FUND' && !isOnchainEtf(code) && (
          <div className="flex flex-col gap-1">
            <label className="text-[11px] text-text-dim">申购费率 %</label>
            <input type="number" step="0.01" value={feeRatePct} onChange={e => setFeeRatePct(e.target.value)}
              className={`${inp} w-24 font-mono`} placeholder="C类填0"
              title="定投批量确认按此费率内扣算份额。A类填折后实际费率(如0.15), C类/无申购费填0或留空" />
          </div>
        )}
        {assetType === 'FUND' && isOnchainEtf(code) && (
          <div className="flex flex-col gap-1">
            <label className="text-[11px] text-text-dim">券商</label>
            <select className={`${inp} w-28 font-mono`} value={selectedBroker} onChange={e => setSelectedBroker(e.target.value)}>
              <option value="">默认</option>
              {brokers.map(b => <option key={b.id} value={b.name}>{b.name}</option>)}
            </select>
          </div>
        )}
        {assetType !== 'WEALTH' && assetType !== 'CASH' && (
          <div className="flex flex-col gap-1">
            <label className="text-[11px] text-text-dim">
              {assetType === 'BOT'
                ? (okxAlgoId ? '当前资产 ¥ (OKX 自动同步)' : '当前资产 ¥')
                : '手动市值 ¥ (可空)'}
            </label>
            <input type="number" step="0.01" value={manualValue} onChange={e => setManualValue(e.target.value)}
              className={`${inp} w-32 font-mono`}
              placeholder={
                assetType === 'BOT' ? (okxAlgoId ? '可留空' : '必填') :
                '留空=实时算'
              } />
          </div>
        )}
        {assetType === 'WEALTH' && (
          <>
            <div className="flex flex-col gap-1">
              <label className="text-[11px] text-text-dim">起投日</label>
              <input type="date" value={startDate} onChange={e => setStartDate(e.target.value)}
                className={`${inp} w-36 font-mono`} />
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-[11px] text-text-dim">年化 % (二选一)</label>
              <input type="number" step="0.001" value={annualYield} onChange={e => setAnnualYield(e.target.value)}
                className={`${inp} w-24 font-mono`} placeholder="2.15" />
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-[11px] text-text-dim">当前总额 ¥ (二选一)</label>
              <input type="number" step="0.01" value={manualValue} onChange={e => setManualValue(e.target.value)}
                className={`${inp} w-32 font-mono`} placeholder="本金+利息" />
            </div>
          </>
        )}
        <div className="flex flex-col gap-1 flex-1 min-w-[160px]">
          <label className="text-[11px] text-text-dim">备注</label>
          <input value={note} onChange={e => setNote(e.target.value)} className={`${inp} w-full`} />
        </div>
        <button onClick={submit}
          className="px-4 py-1.5 rounded bg-accent text-bg font-semibold text-[12px] hover:opacity-90 cursor-pointer">
          保存{dcaEnabled && (assetType === 'FUND' || assetType === 'CRYPTO') ? ' + 建定投' : ''}
        </button>
        <button onClick={onCancel}
          className="px-3 py-1.5 rounded border border-border text-text-dim text-[12px] hover:text-text cursor-pointer">
          取消
        </button>
      </div>

      {/* 内联定投 — FUND/CRYPTO 可勾选, 资产创建后顺手建一条 DCA */}
      {(assetType === 'FUND' || assetType === 'CRYPTO') && (
        <div className="rounded border border-border-subtle bg-surface-3/30 px-2.5 py-2 space-y-2">
          <label className="flex items-center gap-2 text-[11.5px] text-text cursor-pointer">
            <input type="checkbox" checked={dcaEnabled}
              onChange={e => setDcaEnabled(e.target.checked)} />
            <span>同时建定投计划 ({assetType === 'FUND' ? '基金' : '币'} DCA)</span>
          </label>
          {dcaEnabled && (
            <div className="flex flex-wrap gap-2 items-end pl-5">
              <div className="flex flex-col gap-1">
                <label className="text-[10.5px] text-text-dim">每期金额 ¥</label>
                <input type="number" step="0.01" value={dcaValue}
                  onChange={e => setDcaValue(e.target.value)}
                  className={`${inp} w-28 font-mono`} placeholder="500" />
              </div>
              <div className="flex flex-col gap-1">
                <label className="text-[10.5px] text-text-dim">频率</label>
                <select value={dcaFrequency} onChange={e => setDcaFrequency(e.target.value)}
                  className={`${inp} w-32`}>
                  <option value="monthly">每月</option>
                  <option value="weekly">每周</option>
                  <option value="daily_trading">每个交易日</option>
                </select>
              </div>
              {dcaFrequency === 'monthly' && (
                <div className="flex flex-col gap-1">
                  <label className="text-[10.5px] text-text-dim">每月几号</label>
                  <input type="number" min="1" max="31" value={dcaDayOfMonth}
                    onChange={e => setDcaDayOfMonth(e.target.value)}
                    className={`${inp} w-20 font-mono`} />
                </div>
              )}
              {dcaFrequency === 'weekly' && (
                <div className="flex flex-col gap-1">
                  <label className="text-[10.5px] text-text-dim">每周几</label>
                  <select value={dcaDayOfWeek} onChange={e => setDcaDayOfWeek(e.target.value)}
                    className={`${inp} w-24`}>
                    <option value={1}>周一</option>
                    <option value={2}>周二</option>
                    <option value={3}>周三</option>
                    <option value={4}>周四</option>
                    <option value={5}>周五</option>
                    <option value={6}>周六</option>
                    <option value={7}>周日</option>
                  </select>
                </div>
              )}
              <div className="text-[10.5px] text-text-muted">触发后写一条 pending ADD，等你回来补份额结算。</div>
            </div>
          )}
        </div>
      )}

      {/* 按股买预览 (FUND/CRYPTO 同时填了份额 + 单价) */}
      {(assetType === 'FUND' || assetType === 'CRYPTO') && parseFloat(shares) > 0 && parseFloat(unitPrice) > 0 && (() => {
        const s = parseFloat(shares); const u = parseFloat(unitPrice); const f = parseFloat(fee) || 0
        const gross = s * u
        const total = gross + f
        const avg = total / s
        return (
          <div className="text-[10.5px] font-mono text-bull">
            ✓ 按股买: {s.toFixed(4)} × ¥{u.toFixed(4)} = ¥{gross.toFixed(2)}
            {f > 0 && ` + 手续费 ¥${f.toFixed(2)} = ¥${total.toFixed(2)}`}
            <span className="ml-2 text-text-dim">持有成本 ¥{avg.toFixed(4)}/{assetType === 'FUND' ? '份' : '币'}</span>
          </div>
        )
      })()}
    </div>
  )
}

// Inline edit row for external asset
function EditAssetRow({ asset, onDone, onCancel, brokers = [] }) {
  const isBot = asset.asset_type === 'BOT'
  const isFund = asset.asset_type === 'FUND'
  const isCrypto = asset.asset_type === 'CRYPTO'
  const isWealth = asset.asset_type === 'WEALTH'
  const isCash = asset.asset_type === 'CASH'
  const boundToOkx = !!asset.okx_algo_id

  // --- Smart-linked fields for FUND/CRYPTO ---
  // Field semantics (matching mainstream 公募基金 App convention):
  //   持有成本 = unit cost (RMB / 份)            — derived: cost_amount / shares
  //   持有份额 = total shares
  //   基金净值 = unit NAV (RMB / 份)              — from live quote, editable
  //   持有金额 = total market value              = shares × NAV + 待确认金额
  //   待确认金额 = pending settlement (no shares yet)
  //
  // DB columns: cost_amount (total), shares, manual_value (mv override), pending_amount
  // 优先官方公布净值 (跟主流基金 App 显示一致)，est_nav 仅作 fallback
  const liveNav = asset.quote?.nav ?? asset.quote?.est_nav ?? null
  const liveNavDate = asset.quote?.nav_date || asset.quote?.est_time || ''
  const initShares = asset.shares ?? ''
  const initTotalCost = asset.cost_amount ?? ''
  const initUnitCost = (parseFloat(initShares) > 0 && parseFloat(initTotalCost) > 0)
    ? (parseFloat(initTotalCost) / parseFloat(initShares)).toFixed(4) : ''
  const initNav = liveNav != null ? String(liveNav) : ''
  const initPending = asset.pending_amount ? String(asset.pending_amount) : ''
  const pendingNum = parseFloat(initPending) || 0
  const initMv = asset.manual_value != null
    ? String(asset.manual_value)
    : (parseFloat(initShares) > 0 && parseFloat(initNav) > 0
        ? (parseFloat(initShares) * parseFloat(initNav) + pendingNum).toFixed(2)
        : '')

  const [unitCost, setUnitCost] = useState(initUnitCost)
  const [shares, setShares] = useState(String(initShares ?? ''))
  const [nav, setNav] = useState(initNav ?? '')
  const [mv, setMv] = useState(initMv ?? '')
  const [pending, setPending] = useState(initPending)
  const [lockMv, setLockMv] = useState(asset.manual_value != null)
  // 申购费率以百分比展示 (DB 存小数, 0.0015 → "0.15")
  const [feeRatePct, setFeeRatePct] = useState(
    asset.purchase_fee_rate != null ? String(+(asset.purchase_fee_rate * 100).toFixed(4)) : ''
  )
  const [broker, setBroker] = useState(asset.broker || '')

  // WEALTH-only fields
  const [cost, setCost] = useState(String(initTotalCost ?? ''))  // also reused by BOT
  const [manualValue, setManualValue] = useState(asset.manual_value ?? '')
  const [annualYield, setAnnualYield] = useState(
    asset.annual_yield_rate != null ? (asset.annual_yield_rate * 100).toString() : ''
  )
  const [startDate, setStartDate] = useState(asset.start_date || '')

  const [busy, setBusy] = useState(false)
  const rootRef = React.useRef(null)
  useEffect(() => {
    rootRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }, [])

  // Field-linkage. Only the source input keeps the user's raw string; derived
  // fields get fixed-precision strings.
  // Relationships (FUND/CRYPTO):
  //   mv = shares × nav + pending
  //   shares = (mv - pending) / nav     (when user types mv)
  //   total_cost = unitCost × shares    (computed at save time)
  const update = (field, value) => {
    const next = { unitCost, shares, nav, mv, pending }
    next[field] = value
    const numOf = (k) => parseFloat(next[k]) || 0
    const u = numOf('unitCost'), s = numOf('shares'), n = numOf('nav'), p = numOf('pending')

    if (field === 'unitCost') {
      // unit cost change doesn't affect shares/nav/mv/pending — just recomputes total at save
    } else if (field === 'shares') {
      if (n > 0) next.mv = (s * n + p).toFixed(2)
    } else if (field === 'nav') {
      if (s > 0) next.mv = (s * n + p).toFixed(2)
    } else if (field === 'mv') {
      const m = parseFloat(value) || 0
      if (n > 0) {
        const confirmedValue = m - p
        if (confirmedValue >= 0) next.shares = (confirmedValue / n).toFixed(4)
      }
    } else if (field === 'pending') {
      if (s > 0 && n > 0) next.mv = (s * n + parseFloat(value || 0)).toFixed(2)
    }
    setUnitCost(next.unitCost); setShares(next.shares); setNav(next.nav); setMv(next.mv); setPending(next.pending)
  }

  const save = async () => {
    setBusy(true)
    try {
      const payload = {}
      if (isFund || isCrypto) {
        const u = parseFloat(unitCost) || 0
        const s = parseFloat(shares) || 0
        // FUND/CRYPTO 的 cost/shares 是流水推算出来的. 只在用户"实质改了"时才回传 —
        // 否则单价是 4 位小数显示, 单价×份额 跟账本真值差几分钱, 后端会误判成本变了,
        // 注入一条莫名其妙的"利息/分红 (adjust)"流水。差值在阈值内就不传, 不动账本。
        const newCost = u > 0 && s > 0 ? Number((u * s).toFixed(4)) : (cost !== '' ? parseFloat(cost) : null)
        const newShares = shares !== '' ? parseFloat(shares) : null
        const curCost = asset.cost_amount != null ? Number(asset.cost_amount) : null
        const curShares = asset.shares != null ? Number(asset.shares) : null
        if (newShares != null && (curShares == null || Math.abs(newShares - curShares) > 1e-4)) {
          payload.shares = newShares
        }
        if (newCost != null && (curCost == null || Math.abs(newCost - curCost) > 0.5)) {
          payload.cost_amount = newCost
        }
        payload.manual_value = lockMv && mv !== '' ? parseFloat(mv) : null
        payload.pending_amount = pending !== '' ? parseFloat(pending) : 0
        // 申购费率: 百分比 → 小数. 留空写 null. 场内 ETF 走佣金无申购费, 不传。
        if (isFund && !isOnchainEtf(asset.code)) payload.purchase_fee_rate = feeRatePct !== '' ? parseFloat(feeRatePct) / 100 : null
        if (isFund && isOnchainEtf(asset.code)) payload.broker = broker || null
      } else if (isWealth) {
        payload.cost_amount = cost !== '' ? parseFloat(cost) : null
        payload.shares = null
        payload.manual_value = manualValue !== '' ? parseFloat(manualValue) : null
        payload.annual_yield_rate = annualYield !== '' ? parseFloat(annualYield) / 100 : null
        payload.start_date = startDate || null
      } else if (isCash) {
        // CASH: 当前余额映射到 cost_amount = manual_value = balance；可选年化用于估月息
        const balance = manualValue !== '' ? parseFloat(manualValue) : (cost !== '' ? parseFloat(cost) : null)
        payload.cost_amount = balance
        payload.manual_value = balance
        payload.shares = null
        payload.annual_yield_rate = annualYield !== '' ? parseFloat(annualYield) / 100 : null
      } else { // BOT
        payload.cost_amount = cost !== '' ? parseFloat(cost) : null
        payload.shares = null
        payload.manual_value = manualValue !== '' ? parseFloat(manualValue) : null
      }
      await fetchJSON(`/api/assets/${asset.id}`, {
        method: 'PUT',
        body: JSON.stringify(payload),
      })
      onDone?.()
    } finally { setBusy(false) }
  }

  const unbindOkx = async () => {
    if (!confirm('解除 OKX 绑定？市值将改为手动维护。')) return
    setBusy(true)
    try {
      await fetchJSON(`/api/assets/${asset.id}`, {
        method: 'PUT',
        body: JSON.stringify({ okx_algo_id: '', okx_bot_type: '' }),
      })
      onDone?.()
    } finally { setBusy(false) }
  }

  const inp = 'bg-bg border border-border rounded px-2 py-1 text-[12px] text-text font-mono outline-none focus:border-accent'

  return (
    <div ref={rootRef}
      className="px-6 py-3 border-b-2 border-accent bg-accent/5 flex flex-wrap gap-3 items-end"
      style={{ animation: 'fade-up 0.2s ease-out' }}>
      <span className="text-[11px] text-accent font-semibold mr-2 basis-full">
        ✎ 编辑 <span className="text-text-bright">{asset.name}</span>
        {boundToOkx && <span className="ml-2 text-[10px] text-bull">🔗 OKX 同步中</span>}
        {(isFund || isCrypto) && (
          <span className="ml-2 text-[10px] text-text-muted font-normal">
            · 改任一字段，其余自动算
          </span>
        )}
      </span>

      {/* FUND / CRYPTO: 持有成本(单价) / 持有份额 / 基金净值(单价) / 持有金额(总) / 待确认金额 */}
      {(isFund || isCrypto) && (
        <>
          <div className="flex flex-col gap-1">
            <label className="text-[11px] text-text-dim">持有成本 ¥/{isFund ? '份' : '个'}</label>
            <input type="number" step="0.0001" value={unitCost} onChange={e => update('unitCost', e.target.value)} className={`${inp} w-24`} placeholder="2.4856" />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-[11px] text-text-dim">{isFund ? '持有份额' : '持有数量'}</label>
            <input type="number" step="0.0001" value={shares} onChange={e => update('shares', e.target.value)} className={`${inp} w-28`} />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-[11px] text-text-dim">
              {isFund ? '基金净值' : '单价'}
              {liveNav != null && (
                <span className="ml-1 text-[9.5px] text-text-muted">
                  实时 {liveNav}{liveNavDate ? ` · ${String(liveNavDate).slice(0, 10)}` : ''}
                </span>
              )}
            </label>
            <input type="number" step="0.0001" value={nav} onChange={e => update('nav', e.target.value)} className={`${inp} w-24`} />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-[11px] text-text-dim">持有金额 ¥（含待确认）</label>
            <input type="number" step="0.01" value={mv} onChange={e => update('mv', e.target.value)} className={`${inp} w-32`} />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-[11px] text-text-dim">待确认金额 ¥</label>
            <input type="number" step="0.01" value={pending} onChange={e => update('pending', e.target.value)} className={`${inp} w-28`} placeholder="0" />
          </div>
          {isFund && !isOnchainEtf(asset.code) && (
            <div className="flex flex-col gap-1">
              <label className="text-[11px] text-text-dim">申购费率 %</label>
              <input type="number" step="0.01" value={feeRatePct} onChange={e => setFeeRatePct(e.target.value)}
                className={`${inp} w-24`} placeholder="C类填0" title="定投批量确认按此费率内扣算份额。A类填折后实际费率(如0.15), C类/无申购费填0或留空" />
            </div>
          )}
          {isFund && isOnchainEtf(asset.code) && (
            <div className="flex flex-col gap-1">
              <label className="text-[11px] text-text-dim">券商</label>
              <select className={`${inp} w-28`} value={broker} onChange={e => setBroker(e.target.value)}>
                <option value="">默认</option>
                {brokers.map(b => <option key={b.id} value={b.name}>{b.name}</option>)}
              </select>
            </div>
          )}
          <label className="flex items-center gap-1.5 text-[11px] text-text-dim cursor-pointer select-none ml-1">
            <input type="checkbox" checked={lockMv} onChange={e => setLockMv(e.target.checked)} />
            锁定市值
          </label>
          {parseFloat(unitCost) > 0 && parseFloat(shares) > 0 && (
            <div className="basis-full text-[10px] text-text-muted pt-1">
              累计本金 ≈ ¥{(parseFloat(unitCost) * parseFloat(shares)).toFixed(2)}
              （= 持有成本 × 持有份额）
            </div>
          )}
        </>
      )}

      {/* WEALTH: cost + 起投日 + 年化/手动总额二选一 */}
      {isWealth && (
        <>
          <div className="flex flex-col gap-1">
            <label className="text-[11px] text-text-dim">本金 ¥</label>
            <input type="number" step="0.01" value={cost} onChange={e => setCost(e.target.value)} className={`${inp} w-28`} />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-[11px] text-text-dim">起投日</label>
            <input type="date" value={startDate} onChange={e => setStartDate(e.target.value)} className={`${inp} w-36`} />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-[11px] text-text-dim">年化 % (二选一)</label>
            <input type="number" step="0.001" value={annualYield} onChange={e => setAnnualYield(e.target.value)} className={`${inp} w-24`} placeholder="2.15" />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-[11px] text-text-dim">当前余额 ¥ (App 显示数)</label>
            <input type="number" step="0.01" value={manualValue} onChange={e => setManualValue(e.target.value)} className={`${inp} w-36`} placeholder="去 App 抄数" />
          </div>
        </>
      )}

      {/* CASH: 当前余额 + 可选 7日年化 (用于估月利息) */}
      {isCash && (
        <>
          <div className="flex flex-col gap-1">
            <label className="text-[11px] text-text-dim">当前余额 ¥（直接抄你 App 上看到的数）</label>
            <input type="number" step="0.01" autoFocus value={manualValue}
              onChange={e => { setManualValue(e.target.value); setCost(e.target.value) }}
              className={`${inp} w-44`} placeholder="3000" />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-[11px] text-text-dim">7日年化 % (可选,用于估月利息)</label>
            <input type="number" step="0.001" value={annualYield}
              onChange={e => setAnnualYield(e.target.value)}
              className={`${inp} w-24`} placeholder="1.17" />
          </div>
        </>
      )}

      {/* BOT: cost + 当前资产 */}
      {isBot && (
        <>
          <div className="flex flex-col gap-1">
            <label className="text-[11px] text-text-dim">投入本金 ¥</label>
            <input type="number" step="0.01" value={cost} onChange={e => setCost(e.target.value)} className={`${inp} w-28`} />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-[11px] text-text-dim">
              {boundToOkx ? '当前资产 ¥ (OKX 同步,编辑将覆盖)' : '当前资产 ¥'}
            </label>
            <input type="number" step="0.01" value={manualValue} onChange={e => setManualValue(e.target.value)} className={`${inp} w-32`} />
          </div>
        </>
      )}

      <button onClick={save} disabled={busy}
        className="px-4 py-1.5 rounded bg-accent text-bg font-semibold text-[12px] hover:opacity-90 cursor-pointer disabled:opacity-50">
        {busy ? '...' : '保存'}
      </button>
      {boundToOkx && (
        <button onClick={unbindOkx} disabled={busy}
          className="px-3 py-1.5 rounded border border-border text-text-dim text-[12px] hover:text-bear cursor-pointer">
          解绑 OKX
        </button>
      )}
      <button onClick={onCancel}
        className="px-3 py-1.5 rounded border border-border text-text-dim text-[12px] hover:text-text cursor-pointer">
        取消
      </button>

      {(isFund || isCrypto) && (
        <DcaPanel assetId={asset.id} />
      )}
    </div>
  )
}

// ============================================================
// AddLotRow — 加仓 modal. 与 ReduceLotRow 对称.
// OTC 基金 (场外): 只填本金 + 日期, 写 pending 流水, T+1 净值出来后回流水"确认"补份额
// 场内 ETF / CRYPTO: 三选二 (本金/份额/单价) + 手续费 + 日期, 立即 confirmed
// WEALTH/CASH: 本金 + 日期 (+ WEALTH 可填本笔起投日 / 年化)
// ============================================================
function AddLotRow({ asset, onDone, onCancel, brokers = [] }) {
  const [principal, setPrincipal] = useState('')
  const [shares, setShares] = useState('')        // FUND/CRYPTO: 新增份额
  const [unitPrice, setUnitPrice] = useState('')  // FUND/CRYPTO: 单价 (净值 / 币价)
  const [fee, setFee] = useState('')              // FUND/CRYPTO: 手续费 ¥ (场内 ETF / 加密 taker fee)
  const [feeTouched, setFeeTouched] = useState(false)
  const [lotStartDate, setLotStartDate] = useState(new Date().toISOString().slice(0, 10))
  const [lotTime, setLotTime] = useState('')     // 成交时刻 HH:MM (可选, 供分时图打点)
  const [lotYield, setLotYield] = useState('')   // WEALTH: 加投年化 %
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  React.useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onCancel?.() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onCancel])

  const t = asset.asset_type
  const isFund = t === 'FUND'
  const isCrypto = t === 'CRYPTO'
  const isWealth = t === 'WEALTH'
  const isCash = t === 'CASH'
  const isOtcFund = isFund && !isEtfCode(asset.code)
  const isShareBased = isFund || isCrypto

  // --- Live preview ---
  const oldCost = parseFloat(asset.cost_amount) || 0
  const oldShares = parseFloat(asset.shares) || 0
  const oldPending = parseFloat(asset.pending_amount) || 0
  const p = parseFloat(principal) || 0
  const sNum = parseFloat(shares) || 0
  const uNum = parseFloat(unitPrice) || 0
  const feeNum = parseFloat(fee) || 0

  // Auto-suggest 手续费 for 按股买 mode (场内 ETF: 万 3, 最低 ¥5)
  useEffect(() => {
    if (!(isFund || isCrypto) || feeTouched) return
    if (sNum > 0 && uNum > 0) {
      const amount = sNum * uNum
      const kind = isOnchainEtf(asset.code) ? 'etf' : 'stock'
      const est = estimateFee(amount, brokers, asset.broker, kind)
      setFee(est.toFixed(2))
    }
  }, [shares, unitPrice, feeTouched, isFund, isCrypto, brokers, asset.broker, asset.code])

  let preview = null
  if (isFund || isCrypto) {
    const hasShares = sNum > 0
    const hasUnit = uNum > 0
    const hasPrincipal = p > 0
    // 模式判定:
    //   按股买:    shares + unit_price → 本金 = 单价×份额 + 手续费
    //   本金+单价: principal + unit_price → 后端算 shares = (principal-fee)/price
    //   本金+份额: principal + shares
    //   待确认:    只有 principal
    if (hasShares && hasUnit) {
      const gross = sNum * uNum
      const totalCost = gross + feeNum
      const newCost = oldCost + totalCost
      const newShares = oldShares + sNum
      const newAvg = newShares > 0 ? newCost / newShares : 0
      preview = {
        模式: '✓ 按股买（本金自动算）',
        本笔成交: `${sNum.toFixed(4)} × ¥${uNum.toFixed(4)} = ¥${gross.toFixed(2)}` + (feeNum > 0 ? ` + 手续费 ¥${feeNum.toFixed(2)} = ¥${totalCost.toFixed(2)}` : ''),
        新累计本金: '¥' + fmtMoney(newCost),
        新累计份额: newShares.toFixed(4),
        新持有成本: '¥' + newAvg.toFixed(4) + '/份',
      }
    } else if (hasPrincipal && (hasShares || hasUnit)) {
      const totalP = p + feeNum
      const incShares = hasShares ? sNum : (p / uNum)
      const newCost = oldCost + totalP
      const newShares = oldShares + incShares
      const newAvg = newShares > 0 ? newCost / newShares : 0
      preview = {
        模式: '✓ 确认型',
        新累计本金: '¥' + fmtMoney(newCost) + (feeNum > 0 ? ` (含手续费 ¥${feeNum.toFixed(2)})` : ''),
        新累计份额: newShares.toFixed(4),
        新持有成本: '¥' + newAvg.toFixed(4) + '/份',
        加仓份额: incShares.toFixed(4),
      }
    } else if (hasPrincipal) {
      const newPending = oldPending + p
      preview = {
        模式: '⏳ 待确认型',
        说明: '只填了金额，进入待确认。基金 T+1/T+2 出份额后回来编辑：清空待确认 → 填入实际份额 + 净值',
        新待确认金额: '¥' + fmtMoney(newPending),
        原累计本金: '¥' + fmtMoney(oldCost) + '（不变）',
        原持有份额: oldShares.toFixed(4) + '（不变）',
      }
    }
  } else if (p > 0) {
    if (isWealth) {
      const today = new Date()
      let oldStart
      try { oldStart = new Date(asset.start_date || asset.created_at) } catch { oldStart = today }
      const daysOld = Math.max(0, Math.floor((today - oldStart) / 86400000))
      let lotStart
      try { lotStart = new Date(lotStartDate) } catch { lotStart = today }
      const daysLot = Math.max(0, Math.floor((today - lotStart) / 86400000))
      const newCost = oldCost + p
      const wDays = newCost > 0 ? (oldCost * daysOld + p * daysLot) / newCost : 0
      const newStart = new Date(today.getTime() - Math.round(wDays) * 86400000)
      preview = {
        新累计本金: '¥' + fmtMoney(newCost),
        新有效起投日: newStart.toISOString().slice(0, 10),
        '(原起投日)': asset.start_date || '--',
      }
      if (lotYield !== '' && asset.annual_yield_rate != null) {
        const blended = (oldCost * asset.annual_yield_rate + p * (parseFloat(lotYield) / 100)) / newCost
        preview['新混合年化'] = (blended * 100).toFixed(3) + '%'
      }
    }
  }

  const save = async () => {
    setErr('')
    let body
    if (isFund || isCrypto) {
      // 4 种模式 (实际入账本金 = 名义本金 + 手续费):
      //   1) 按股买: shares + unit_price → 名义 = shares × unit_price; 实际 = 名义 + fee
      //   2) 本金 + 份额 → 实际 = principal + fee; shares 直接累加
      //   3) 本金 + 单价 → 实际 = principal + fee; 后端算 shares = principal / unit_price
      //      (注意: 这种模式下 unit_price 应是裸净值, 后端算的份额未考虑 fee)
      //   4) 仅本金 → 待确认（pending_amount, 也含 fee）
      if (sNum > 0 && uNum > 0) {
        body = { principal: Number((sNum * uNum + feeNum).toFixed(4)), shares: sNum }
      } else if (p > 0 && sNum > 0) {
        body = { principal: Number((p + feeNum).toFixed(4)), shares: sNum }
      } else if (p > 0 && uNum > 0) {
        body = { principal: Number((p + feeNum).toFixed(4)), unit_price: uNum }
      } else if (p > 0) {
        body = { principal: Number((p + feeNum).toFixed(4)) }
      } else {
        setErr('至少填本金，或同时填单价 + 份额（按股买）'); return
      }
      // 手续费单独透传, 后端单存到流水的 fee 字段 (principal 已含 fee, 算份额用净额),
      // 这样流水编辑里能看到/改手续费, 不会"消失"。
      if (feeNum > 0) body.fee = feeNum
    } else if (isWealth) {
      if (!(p > 0)) { setErr('请输入本金'); return }
      body = { principal: p }
      if (lotStartDate) body.lot_start_date = lotStartDate
      if (lotYield !== '') body.lot_yield_rate = parseFloat(lotYield) / 100
    } else {
      setErr('该资产类型不支持加仓'); return
    }
    // 所有类型都把日期透传 (后端 lot_start_date 会写到 action.trade_date)
    if (lotStartDate && !body.lot_start_date) body.lot_start_date = lotStartDate
    if (lotTime) body.trade_time = lotTime
    setBusy(true)
    try {
      const r = await fetchJSON(`/api/assets/${asset.id}/add-lot`, {
        method: 'POST', body: JSON.stringify(body),
      })
      if (r.message === 'lot added') onDone?.()
      else setErr(JSON.stringify(r))
    } catch (e) { setErr(String(e)) }
    finally { setBusy(false) }
  }

  return (
    <div className="fixed inset-0 z-[200] flex items-center justify-center bg-black/60 backdrop-blur-sm" onClick={onCancel}>
      <div className="bg-surface-2 border border-border rounded-xl p-5 w-[480px] max-w-[95vw] space-y-3"
        onClick={e => e.stopPropagation()}>
        <div className="flex items-baseline justify-between">
          <h3 className="text-[14px] font-semibold text-text-bright m-0">⊕ 加仓 / 申购 <span className="text-text">{asset.name}</span></h3>
          <button onClick={onCancel} className="text-text-dim hover:text-text text-[18px] leading-none px-2 cursor-pointer">×</button>
        </div>
        <div className="text-[11px] text-text-dim">
          当前: cost ¥{fmtMoney(oldCost)}
          {isShareBased && asset.shares && <> · shares {parseFloat(asset.shares).toFixed(4)}</>}
          {isOtcFund && (
            <span className="ml-2 px-1.5 py-[1px] rounded bg-warn/15 text-warn text-[10px] border border-warn/40">OTC 场外基金</span>
          )}
        </div>

        {isOtcFund ? (
          <>
            <div>
              <label className="text-[11.5px] text-text-dim block mb-1">申购金额 (CNY)</label>
              <input type="number" inputMode="decimal" placeholder="例: 1000" autoFocus
                value={principal} onChange={e => setPrincipal(e.target.value)}
                className="w-full bg-bg border border-border rounded-lg px-3 py-2 text-[13px] font-mono text-bull-bright outline-none focus:border-accent" />
            </div>
            <div className="text-[10.5px] text-text-muted leading-relaxed bg-warn/5 border border-warn/30 rounded px-2 py-1.5">
              场外基金 T+1/T+2 才出净值。这一步只记申请，会标 <span className="text-warn font-mono">pending</span>，
              暂不进总盈亏。等净值确认后回 <span className="text-text">流水</span> 里点 <span className="text-text">确认</span>，补份额/净值再入账。
            </div>
          </>
        ) : isShareBased ? (
          <>
            <div>
              <label className="text-[11.5px] text-text-dim block mb-1">本金 (CNY) <span className="text-text-muted">— 按股买可空</span></label>
              <input type="number" inputMode="decimal" placeholder="1000" autoFocus
                value={principal} onChange={e => setPrincipal(e.target.value)}
                className="w-full bg-bg border border-border rounded-lg px-3 py-2 text-[13px] font-mono text-bull-bright outline-none focus:border-accent" />
            </div>
            <div className="grid grid-cols-2 gap-2">
              <div>
                <label className="text-[11.5px] text-text-dim block mb-1">{isFund ? '成交净值' : '单价'}</label>
                <input type="number" inputMode="decimal" placeholder="3.4399"
                  value={unitPrice} onChange={e => setUnitPrice(e.target.value)}
                  className="w-full bg-bg border border-border rounded-lg px-3 py-2 text-[13px] font-mono outline-none focus:border-accent" />
              </div>
              <div>
                <label className="text-[11.5px] text-text-dim block mb-1">{isFund ? '成交份额' : '成交数量'}</label>
                <input type="number" inputMode="decimal" placeholder="290.7"
                  value={shares} onChange={e => setShares(e.target.value)}
                  className="w-full bg-bg border border-border rounded-lg px-3 py-2 text-[13px] font-mono outline-none focus:border-accent" />
              </div>
            </div>
            <div>
              <label className="text-[11.5px] text-text-dim block mb-1">
                手续费 ¥ <span className="text-text-muted text-[10px]">— 场内 ETF 默认万2.5/最低5; 场外公募填 0</span>
              </label>
              <input type="number" inputMode="decimal" placeholder="5.00"
                value={fee} onChange={e => { setFee(e.target.value); setFeeTouched(true) }}
                className="w-full bg-bg border border-border rounded-lg px-3 py-2 text-[13px] font-mono outline-none focus:border-accent" />
            </div>
          </>
        ) : (
          <div>
            <label className="text-[11.5px] text-text-dim block mb-1">本金 (CNY)</label>
            <input type="number" inputMode="decimal" placeholder="1000" autoFocus
              value={principal} onChange={e => setPrincipal(e.target.value)}
              className="w-full bg-bg border border-border rounded-lg px-3 py-2 text-[13px] font-mono text-bull-bright outline-none focus:border-accent" />
          </div>
        )}

        <div className={isWealth ? 'grid grid-cols-2 gap-2' : ''}>
          <div>
            <label className="text-[11.5px] text-text-dim block mb-1">{isWealth ? '本笔起投日' : '日期'}</label>
            <input type="date" value={lotStartDate} onChange={e => setLotStartDate(e.target.value)}
              className="bg-bg border border-border rounded-lg px-3 py-2 text-[13px] font-mono outline-none focus:border-accent w-full" />
            {isShareBased && (
              <input type="time" value={lotTime} onChange={e => setLotTime(e.target.value)}
                title="成交时刻(可选), 留空用录入时间, 供分时图打点"
                className="bg-bg border border-border rounded-lg px-3 py-1.5 text-[12px] font-mono outline-none focus:border-accent w-full mt-1.5 text-text-dim" />
            )}
          </div>
          {isWealth && (
            <div>
              <label className="text-[11.5px] text-text-dim block mb-1">本笔年化 % <span className="text-text-muted text-[10px]">可空</span></label>
              <input type="number" inputMode="decimal" placeholder="2.15"
                value={lotYield} onChange={e => setLotYield(e.target.value)}
                className="bg-bg border border-border rounded-lg px-3 py-2 text-[13px] font-mono outline-none focus:border-accent w-full" />
            </div>
          )}
        </div>

        {preview && (
          <div className="bg-surface-3 rounded-md px-3 py-2 space-y-0.5 text-[11px]">
            {Object.entries(preview).map(([k, v]) => (
              <div key={k} className="flex items-baseline justify-between gap-2">
                <span className="text-text-muted">{k}</span>
                <span className="font-mono text-bull-bright">{v}</span>
              </div>
            ))}
          </div>
        )}

        {err && <div className="text-[11px] text-bear-bright">{err}</div>}

        <div className="flex gap-2 pt-1">
          <button onClick={save}
            disabled={busy || !(isShareBased ? (p > 0 || (sNum > 0 && uNum > 0)) : p > 0)}
            className="flex-1 px-4 py-2 rounded-lg bg-bull text-bg font-medium text-[13px] hover:opacity-90 disabled:opacity-50 cursor-pointer">
            {busy ? '...' : '确认加仓'}
          </button>
          <button onClick={onCancel}
            className="px-4 py-2 rounded-lg border border-border text-text-dim hover:text-text hover:border-border-med text-[13px] cursor-pointer">
            取消
          </button>
        </div>
      </div>
    </div>
  )
}

// 场内 ETF/LOF 判定 — 统一走 isOnchainEtf (深 1xxxxx / 沪 5xxxxx)。
// 旧规则只认 5xxxxx/159xxx 会把 16xxxx 的 LOF (如互联网QD 160644) 误判成场外。
const isEtfCode = isOnchainEtf

// ReduceLotRow — 减仓 / 赎回 modal.
// OTC 基金 (场外): 只填份额 + 日期, 写 pending 流水, 等 T+1 净值出来后回来"确认"
// ETF/CRYPTO: 三选二 (amount/shares/unit_price), 直接写 confirmed
// WEALTH/CASH: 仅 amount
function ReduceLotRow({ asset, onDone, onCancel }) {
  const t = asset.asset_type
  const isShareBased = t === 'FUND' || t === 'CRYPTO'
  const isOtcFund = t === 'FUND' && !isEtfCode(asset.code)
  const isImmediate = !isOtcFund  // ETF/CRYPTO/WEALTH/CASH 立即结算

  const [amount, setAmount] = React.useState('')
  const [shares, setShares] = React.useState('')
  const [unitPrice, setUnitPrice] = React.useState('')
  const [tradeDate, setTradeDate] = React.useState(() => new Date().toISOString().slice(0, 10))
  const [tradeTime, setTradeTime] = React.useState('')         // 成交时刻 HH:MM (可选, 供分时图打点)
  const [interestPart, setInterestPart] = React.useState('')   // WEALTH/CASH: 其中利息部分
  const [busy, setBusy] = React.useState(false)
  const [err, setErr] = React.useState('')

  const f = (v) => v === '' ? null : parseFloat(v) || 0
  const a = f(amount), s = f(shares), u = f(unitPrice)

  // 实时推算第三个字段 (ETF/CRYPTO 即时模式)
  const inferred = React.useMemo(() => {
    if (!isShareBased || isOtcFund) return null
    if (a && s && (!u || u === 0)) return { unit_price: (a / s).toFixed(4) }
    if (a && u && (!s || s === 0)) return { shares: (a / u).toFixed(4) }
    if (s && u && (!a || a === 0)) return { amount: (s * u).toFixed(2) }
    return null
  }, [a, s, u, isShareBased, isOtcFund])

  // 估算实现盈亏 (按比例摊销当前 cost)
  const estRealized = React.useMemo(() => {
    const curCost = parseFloat(asset.cost_amount || 0)
    if (curCost <= 0) return null
    if (isOtcFund) {
      // OTC 阶段没有 amount, 只能算"按当前持仓平均成本算的占用成本"
      const curShares = parseFloat(asset.shares || 0)
      if (!s || s <= 0 || curShares <= 0) return null
      const matchedCost = (s / curShares) * curCost
      return { type: 'occupied_cost', val: matchedCost }
    }
    if (isShareBased) {
      if (!a || a <= 0) return null
      const curShares = parseFloat(asset.shares || 0)
      const consumeShares = s || (u && u > 0 ? a / u : 0)
      if (curShares <= 0 || !consumeShares) return null
      const matchedCost = (consumeShares / curShares) * curCost
      return { type: 'realized', val: a - matchedCost }
    }
    return null
  }, [a, s, u, isShareBased, isOtcFund, asset])

  const submit = async () => {
    setErr('')
    // ETF/CRYPTO 三选二: 缺哪个就用另两个反推
    let finalAmount = a, finalShares = s, finalUnit = u
    if (!isOtcFund && isShareBased) {
      if (!finalAmount && finalShares > 0 && finalUnit > 0) finalAmount = finalShares * finalUnit
      if (!finalShares && finalAmount > 0 && finalUnit > 0) finalShares = finalAmount / finalUnit
      if (!finalUnit && finalAmount > 0 && finalShares > 0) finalUnit = finalAmount / finalShares
    }

    if (isOtcFund) {
      if (!finalShares || finalShares <= 0) { setErr('请填卖出份额'); return }
    } else if (isShareBased) {
      if (!finalAmount || finalAmount <= 0) { setErr('金额 / 份额 / 单价 至少填两个'); return }
    } else {
      // WEALTH / CASH 仅金额
      if (!finalAmount || finalAmount <= 0) { setErr('赎回金额必须为正数'); return }
    }
    setBusy(true)
    try {
      const body = { trade_date: tradeDate }
      if (isShareBased && tradeTime) body.trade_time = tradeTime
      if (isOtcFund) {
        body.amount = 0
        body.shares = finalShares
      } else {
        body.amount = Number(finalAmount.toFixed(4))
        if (isShareBased) {
          if (finalShares) body.shares = Number(finalShares.toFixed(6))
          if (finalUnit) body.unit_price = Number(finalUnit.toFixed(6))
        }
      }
      // WEALTH/CASH 拆出利息部分 (可选), 写入 realized_pnl
      if ((t === 'WEALTH' || t === 'CASH') && interestPart !== '') {
        const ip = parseFloat(interestPart) || 0
        if (ip > 0) body.interest_part = Number(ip.toFixed(2))
      }
      const res = await fetch(`/api/assets/${asset.id}/reduce-lot`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || '减仓失败')
      onDone?.()
    } catch (e) {
      setErr(e.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="fixed inset-0 z-[200] flex items-center justify-center bg-black/60 backdrop-blur-sm" onClick={onCancel}>
      <div className="bg-surface-2 border border-border rounded-xl p-5 w-[440px] max-w-[95vw] space-y-3"
        onClick={e => e.stopPropagation()}>
        <div className="flex items-baseline justify-between">
          <h3 className="text-[14px] font-semibold text-text-bright m-0">⊖ 减仓 / 赎回 <span className="text-text">{asset.name}</span></h3>
          <button onClick={onCancel} className="text-text-dim hover:text-text text-[18px] leading-none px-2 cursor-pointer">×</button>
        </div>
        <div className="text-[11px] text-text-dim">
          当前: cost ¥{fmtMoney(parseFloat(asset.cost_amount || 0))}
          {isShareBased && asset.shares && <> · shares {parseFloat(asset.shares).toFixed(4)}</>}
          {isOtcFund && (
            <span className="ml-2 px-1.5 py-[1px] rounded bg-warn/15 text-warn text-[10px] border border-warn/40">OTC 场外基金</span>
          )}
        </div>

        {isOtcFund ? (
          <>
            <div>
              <label className="text-[11.5px] text-text-dim block mb-1">卖出份额</label>
              <input type="number" inputMode="decimal" placeholder="例: 500"
                value={shares} onChange={e => setShares(e.target.value)}
                className="w-full bg-bg border border-border rounded-lg px-3 py-2 text-[13px] font-mono text-bear-bright outline-none focus:border-accent" />
            </div>
            <div className="text-[10.5px] text-text-muted leading-relaxed bg-warn/5 border border-warn/30 rounded px-2 py-1.5">
              场外基金 T+1/T+2 才出净值。这一步只记申请，会标 <span className="text-warn font-mono">pending</span>，
              暂不进总盈亏。等净值确认后回来 <span className="text-text">确认</span>，补金额/净值再入账。
            </div>
          </>
        ) : (
          <>
            <div>
              <label className="text-[11.5px] text-text-dim block mb-1">赎回金额 (CNY)</label>
              <input type="number" inputMode="decimal" placeholder={inferred?.amount || '0'}
                value={amount} onChange={e => setAmount(e.target.value)}
                className="w-full bg-bg border border-border rounded-lg px-3 py-2 text-[13px] font-mono text-bear-bright outline-none focus:border-accent" />
            </div>
            {(t === 'WEALTH' || t === 'CASH') && (
              <div>
                <label className="text-[11.5px] text-text-dim block mb-1">
                  其中利息 ¥ (可选)
                  <span className="ml-1 text-text-muted text-[10.5px]">— 留空则全当本金返还</span>
                </label>
                <input type="number" step="0.01" inputMode="decimal" placeholder="0.00"
                  value={interestPart} onChange={e => setInterestPart(e.target.value)}
                  className="w-full bg-bg border border-border rounded-lg px-3 py-2 text-[13px] font-mono text-bull outline-none focus:border-accent" />
                {parseFloat(amount) > 0 && parseFloat(interestPart) > 0 && (
                  <div className="text-[10.5px] font-mono text-text-muted mt-1">
                    → 本金消耗 ¥{(parseFloat(amount) - parseFloat(interestPart)).toFixed(2)} · 利息计入已实现 +¥{parseFloat(interestPart).toFixed(2)}
                  </div>
                )}
              </div>
            )}
            {isShareBased && (
              <div className="grid grid-cols-2 gap-2">
                <div>
                  <label className="text-[11.5px] text-text-dim block mb-1">赎回份额</label>
                  <input type="number" inputMode="decimal" placeholder={inferred?.shares || '可选'}
                    value={shares} onChange={e => setShares(e.target.value)}
                    className="w-full bg-bg border border-border rounded-lg px-3 py-2 text-[13px] font-mono outline-none focus:border-accent" />
                </div>
                <div>
                  <label className="text-[11.5px] text-text-dim block mb-1">单价 / 净值</label>
                  <input type="number" inputMode="decimal" placeholder={inferred?.unit_price || '可选'}
                    value={unitPrice} onChange={e => setUnitPrice(e.target.value)}
                    className="w-full bg-bg border border-border rounded-lg px-3 py-2 text-[13px] font-mono outline-none focus:border-accent" />
                </div>
              </div>
            )}
          </>
        )}

        <div>
          <label className="text-[11.5px] text-text-dim block mb-1">日期{isShareBased && <span className="text-text-muted text-[10px]"> · 时刻可空</span>}</label>
          <div className="flex gap-2">
            <input type="date" value={tradeDate} onChange={e => setTradeDate(e.target.value)}
              className="bg-bg border border-border rounded-lg px-3 py-2 text-[13px] font-mono outline-none focus:border-accent" />
            {isShareBased && (
              <input type="time" value={tradeTime} onChange={e => setTradeTime(e.target.value)}
                title="成交时刻(可选), 留空用录入时间, 供分时图打点"
                className="bg-bg border border-border rounded-lg px-3 py-2 text-[13px] font-mono outline-none focus:border-accent text-text-dim" />
            )}
          </div>
        </div>

        {estRealized && (
          <div className="bg-surface-3 rounded-md px-3 py-2 flex items-center justify-between">
            <span className="text-[11.5px] text-text-dim">
              {estRealized.type === 'realized' ? '预估实现盈亏 (FIFO 比例)' : '占用成本 (赎回份额所占成本)'}
            </span>
            <span className={`font-mono font-semibold text-[13px] ${
              estRealized.type === 'realized'
                ? (estRealized.val >= 0 ? 'text-bull-bright' : 'text-bear-bright')
                : 'text-text'
            }`}>
              {estRealized.type === 'realized' && (estRealized.val >= 0 ? '+' : '')}
              ¥{fmtMoney(Math.abs(estRealized.val))}
            </span>
          </div>
        )}

        {err && <div className="text-[11px] text-bear-bright">{err}</div>}

        <div className="flex gap-2 pt-1">
          <button onClick={submit} disabled={busy}
            className="flex-1 px-4 py-2 rounded-lg bg-bear text-bg font-medium text-[13px] hover:opacity-90 disabled:opacity-50 cursor-pointer">
            {busy ? '...' : '确认减仓'}
          </button>
          <button onClick={onCancel}
            className="px-4 py-2 rounded-lg border border-border text-text-dim hover:text-text hover:border-border-med text-[13px] cursor-pointer">
            取消
          </button>
        </div>
      </div>
    </div>
  )
}

// AssetActionsModal — 流水查看 + pending 确认.
function AssetActionsModal({ asset, onClose, onChanged }) {
  const [actions, setActions] = React.useState([])
  const [state, setState] = React.useState(null)
  const [loading, setLoading] = React.useState(true)
  const [confirmTarget, setConfirmTarget] = React.useState(null)
  const [editTarget, setEditTarget] = React.useState(null)

  const reload = React.useCallback(async () => {
    setLoading(true)
    try {
      const d = await fetchJSON(`/api/assets/${asset.id}/actions`)
      setActions(d.actions || [])
      setState(d.state || null)
    } catch (e) { console.error(e) }
    finally { setLoading(false) }
  }, [asset.id])

  React.useEffect(() => { reload() }, [reload])

  React.useEffect(() => {
    const onKey = e => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const deleteAction = async (id) => {
    if (!confirm('确定删除这条流水？后续 cost/shares 会按剩余流水重新计算')) return
    await fetch(`/api/assets/${asset.id}/actions/${id}`, { method: 'DELETE' })
    onChanged?.()
    reload()
  }

  const editPendingAmount = async (a) => {
    const cur = parseFloat(a.amount) || 0
    const next = prompt(`改这条 pending 流水的金额 (CNY)\n常用于基金限额变化, 状态仍保持 pending`, cur.toFixed(2))
    if (next == null) return
    const v = parseFloat(next)
    if (!v || v <= 0) { alert('金额必须为正数'); return }
    if (Math.abs(v - cur) < 0.005) return
    try {
      const res = await fetch(`/api/assets/${asset.id}/actions/${a.id}`, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ amount: v }),
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || '改金额失败')
      onChanged?.()
      reload()
    } catch (e) { alert(e.message) }
  }

  const colorByType = {
    BUY: 'text-bull-bright', ADD: 'text-bull-bright', DEPOSIT: 'text-bull-bright',
    REDEEM: 'text-bear-bright', WITHDRAW: 'text-bear-bright',
    INTEREST: 'text-info', DIVIDEND: 'text-info',
  }
  const labelByType = {
    BUY: '买入', ADD: '加仓', REDEEM: '赎回',
    DEPOSIT: '存入', WITHDRAW: '取出',
    INTEREST: '利息', DIVIDEND: '分红',
  }

  return (
    <div className="fixed inset-0 z-[200] flex items-center justify-center bg-black/60 backdrop-blur-sm" onClick={onClose}>
      <div className="bg-surface-2 border border-border rounded-xl p-5 w-[640px] max-w-[95vw] max-h-[85vh] overflow-y-auto"
        onClick={e => e.stopPropagation()}>
        <div className="flex items-baseline justify-between mb-3">
          <h3 className="text-[14px] font-semibold text-text-bright m-0">流水 · {asset.name}</h3>
          <button onClick={onClose} className="text-text-dim hover:text-text text-[18px] leading-none px-2 cursor-pointer">×</button>
        </div>

        {state && (
          <div className="grid grid-cols-3 gap-2 mb-3 text-[11.5px]">
            <div className="bg-surface-3 rounded-md px-2 py-1.5">
              <div className="text-text-dim text-[10px] mb-0.5">当前成本</div>
              <div className="font-mono text-text">¥{fmtMoney(state.cost_amount)}</div>
            </div>
            {(asset.asset_type === 'FUND' || asset.asset_type === 'CRYPTO') && (
              <div className="bg-surface-3 rounded-md px-2 py-1.5">
                <div className="text-text-dim text-[10px] mb-0.5">持有份额</div>
                <div className="font-mono text-text">{state.shares?.toFixed(4)}</div>
              </div>
            )}
            <div className="bg-surface-3 rounded-md px-2 py-1.5">
              <div className="text-text-dim text-[10px] mb-0.5">累计已实现</div>
              <div className={`font-mono ${state.realized_pnl >= 0 ? 'text-bull-bright' : 'text-bear-bright'}`}>
                {state.realized_pnl >= 0 ? '+' : ''}¥{fmtMoney(Math.abs(state.realized_pnl))}
              </div>
            </div>
          </div>
        )}

        {loading ? (
          <div className="text-center text-text-dim text-[12px] py-4">加载中...</div>
        ) : actions.length === 0 ? (
          <div className="text-center text-text-dim text-[12px] py-4">暂无流水</div>
        ) : (
          <div className="border border-border-subtle rounded-md overflow-hidden">
            <div className="grid grid-cols-[80px_60px_1fr_1fr_1fr_auto] gap-2 px-2 py-1.5 text-[10px] text-text-dim bg-surface-3 border-b border-border-subtle font-medium tracking-wider">
              <div>日期</div>
              <div>类型</div>
              <div className="text-right">金额</div>
              <div className="text-right">份额</div>
              <div className="text-right">单价</div>
              <div className="w-[140px]"></div>
            </div>
            {[...actions]
              .sort((x, y) => {
                // 展示倒序: 新流水在前. 后端按 trade_date ASC + id ASC 给 ledger 算账, 这里翻过来
                const dx = x.trade_date || (x.created_at || '').slice(0, 10) || ''
                const dy = y.trade_date || (y.created_at || '').slice(0, 10) || ''
                if (dy !== dx) return dy.localeCompare(dx)
                return (y.id || 0) - (x.id || 0)
              })
              .map(a => {
              const isPending = (a.status || 'confirmed') === 'pending'
              return (
                <div key={a.id} className={`grid grid-cols-[80px_60px_1fr_1fr_1fr_auto] gap-2 px-2 py-2 text-[11.5px] items-center border-b border-border-subtle last:border-b-0 ${isPending ? 'bg-warn/5' : ''}`}>
                  <div className="font-mono text-[10.5px] text-text-dim">
                    {(a.trade_date || (a.created_at || '').slice(0, 10) || '').slice(5) || '--'}
                  </div>
                  <div className={`font-medium ${colorByType[a.action_type] || 'text-text'}`}>
                    {labelByType[a.action_type] || a.action_type}
                  </div>
                  <div className="text-right font-mono">
                    {a.amount > 0 ? `¥${fmtMoney(a.amount)}` : <span className="text-text-muted">--</span>}
                  </div>
                  <div className="text-right font-mono text-[11px]">
                    {a.shares != null ? parseFloat(a.shares).toFixed(4) : '--'}
                  </div>
                  <div className="text-right font-mono text-[11px]">
                    {a.unit_price != null ? parseFloat(a.unit_price).toFixed(4) : '--'}
                  </div>
                  <div className="flex gap-1 items-center justify-end w-[160px]">
                    {isPending ? (
                      <>
                        <button onClick={() => editPendingAmount(a)}
                          title="改金额 (限额变化等), 状态保持 pending"
                          className="px-1.5 py-[2px] rounded text-[10px] border border-accent/60 text-accent hover:bg-accent/10 cursor-pointer">
                          改额
                        </button>
                        <button onClick={() => setConfirmTarget(a)}
                          className="px-1.5 py-[2px] rounded text-[10px] border border-warn text-warn hover:bg-warn/10 cursor-pointer">
                          确认
                        </button>
                      </>
                    ) : (
                      <button onClick={() => setEditTarget(a)}
                        title="改金额 / 份额 / 单价 / 手续费"
                        className="px-1.5 py-[2px] rounded text-[10px] border border-accent/60 text-accent hover:bg-accent/10 cursor-pointer">
                        编辑
                      </button>
                    )}
                    {a.note !== 'initial (auto-migrated)' && (
                      <button onClick={() => deleteAction(a.id)}
                        className="px-1.5 py-[2px] rounded text-[10px] border border-bear/40 text-bear hover:bg-bear/10 cursor-pointer">
                        删
                      </button>
                    )}
                    {isPending && (
                      <span className="text-[9.5px] px-1 py-[1px] rounded bg-warn/15 text-warn border border-warn/40">⏳</span>
                    )}
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </div>

      {confirmTarget && (
        <ConfirmActionModal asset={asset} action={confirmTarget}
          onClose={() => setConfirmTarget(null)}
          onDone={() => { setConfirmTarget(null); reload(); onChanged?.() }} />
      )}
      {editTarget && (
        <EditActionModal asset={asset} action={editTarget}
          onClose={() => setEditTarget(null)}
          onDone={() => { setEditTarget(null); reload(); onChanged?.() }} />
      )}
    </div>
  )
}

function EditActionModal({ asset, action, onClose, onDone }) {
  // 已 confirmed 流水的"改"弹窗: 改 amount / shares / unit_price / fee
  // 改完后端会同步 asset cost_amount/shares
  // 联动: 用 lastEdit 追踪用户上次手动改的字段, 另外两个被反推. 改 fee 时 amount 跟着 s*u+fee 走.
  const [amount, setAmount] = React.useState(action.amount != null ? String(action.amount) : '')
  const [shares, setShares] = React.useState(action.shares != null ? String(action.shares) : '')
  const [unitPrice, setUnitPrice] = React.useState(action.unit_price != null ? String(action.unit_price) : '')
  const [fee, setFee] = React.useState(action.fee != null ? String(action.fee) : '')
  const [tradeDate, setTradeDate] = React.useState((action.trade_date || (action.created_at || '').slice(0, 10) || '').slice(0, 10))
  const [tradeTime, setTradeTime] = React.useState((action.trade_time || '').slice(0, 5))   // 成交时刻 HH:MM
  const [lastEdit, setLastEdit] = React.useState(null)  // 'amount' | 'shares' | 'unitPrice'
  const [busy, setBusy] = React.useState(false)
  const [err, setErr] = React.useState('')

  const hasShares = asset.asset_type === 'FUND' || asset.asset_type === 'CRYPTO'

  // 反推: amount = shares × unit_price + fee (净额逻辑)
  // 用户上次改了哪个字段, 就反推另一个 (优先反推 amount, 让 ledger 跟手算一致)
  React.useEffect(() => {
    if (!hasShares) return
    const a = parseFloat(amount), s = parseFloat(shares), u = parseFloat(unitPrice), f = parseFloat(fee) || 0
    // 改 shares: amount = s × u + fee
    if (lastEdit === 'shares' && s > 0 && u > 0) {
      const next = (s * u + f).toFixed(2)
      if (next !== amount) setAmount(next)
    }
    // 改 unitPrice: amount = s × u + fee
    else if (lastEdit === 'unitPrice' && s > 0 && u > 0) {
      const next = (s * u + f).toFixed(2)
      if (next !== amount) setAmount(next)
    }
    // 改 amount: 单价 = (amount − fee) / shares
    else if (lastEdit === 'amount' && a > 0 && s > 0) {
      const net = Math.max(0, a - f)
      const next = (net / s).toFixed(4)
      if (next !== unitPrice) setUnitPrice(next)
    }
  }, [amount, shares, unitPrice, fee, lastEdit, hasShares])

  // fee 单独 effect: 改 fee 时, 如果 shares × unit_price 都有, 自动让 amount 跟上 (净额不变 + 新 fee)
  React.useEffect(() => {
    if (!hasShares || lastEdit === 'amount') return
    const s = parseFloat(shares), u = parseFloat(unitPrice), f = parseFloat(fee) || 0
    if (s > 0 && u > 0) {
      const next = (s * u + f).toFixed(2)
      if (next !== amount) setAmount(next)
    }
  }, [fee])

  React.useEffect(() => {
    const onKey = e => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const submit = async () => {
    setErr('')
    const body = {}
    const a = parseFloat(amount), s = parseFloat(shares), u = parseFloat(unitPrice), f = parseFloat(fee)
    // 只把"用户改过的"字段传给后端 — 简单判定: 跟 action 原值不同就算改了
    if (!isNaN(a) && a !== (action.amount ?? 0)) body.amount = a
    if (hasShares && !isNaN(s) && s !== (action.shares ?? 0)) body.shares = s
    if (!isNaN(u) && u !== (action.unit_price ?? 0)) body.unit_price = u
    if (!isNaN(f) && f !== (action.fee ?? 0)) body.fee = f
    const origDate = (action.trade_date || (action.created_at || '').slice(0, 10) || '').slice(0, 10)
    if (tradeDate && tradeDate !== origDate) body.trade_date = tradeDate
    const origTime = (action.trade_time || '').slice(0, 5)
    if (tradeTime !== origTime) body.trade_time = tradeTime   // "" → 清空回退录入时间
    if (Object.keys(body).length === 0) { onClose(); return }
    setBusy(true)
    try {
      const res = await fetch(`/api/assets/${asset.id}/actions/${action.id}`, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || '保存失败')
      onDone?.()
    } catch (e) { setErr(e.message) }
    finally { setBusy(false) }
  }

  return (
    <div className="fixed inset-0 z-[210] flex items-center justify-center bg-black/70 backdrop-blur-sm" onClick={onClose}>
      <div className="bg-surface-2 border border-border rounded-xl p-5 w-[420px] max-w-[95vw] space-y-3"
        onClick={e => e.stopPropagation()}>
        <div className="flex items-baseline justify-between">
          <h3 className="text-[14px] font-semibold text-text-bright m-0">编辑流水</h3>
          <button onClick={onClose} className="text-text-dim hover:text-text text-[18px] leading-none px-2 cursor-pointer">×</button>
        </div>
        <div className="text-[11px] text-text-dim">
          类型 <span className="font-mono text-text">{action.action_type}</span>
          <span className="mx-1.5">·</span>
          改完后端会按新值重算资产 cost / shares
        </div>

        <div className="grid grid-cols-2 gap-2">
          <div>
            <label className="text-[11.5px] text-text-dim block mb-1">日期</label>
            <input type="date" value={tradeDate} onChange={e => setTradeDate(e.target.value)}
              className="w-full bg-bg border border-border rounded-lg px-3 py-2 text-[13px] font-mono outline-none focus:border-accent" />
          </div>
          <div>
            <label className="text-[11.5px] text-text-dim block mb-1">时刻 <span className="text-text-muted text-[10px]">可空</span></label>
            <input type="time" value={tradeTime} onChange={e => setTradeTime(e.target.value)}
              title="成交时刻(可选), 留空用录入时间, 供分时图打点"
              className="w-full bg-bg border border-border rounded-lg px-3 py-2 text-[13px] font-mono outline-none focus:border-accent text-text-dim" />
          </div>
        </div>

        <div>
          <label className="text-[11.5px] text-text-dim block mb-1">
            金额 (CNY, 含手续费)
            {hasShares && lastEdit && lastEdit !== 'amount' && (
              <span className="text-text-muted text-[10px] ml-1">— 自动 = 份额×单价 + 手续费</span>
            )}
          </label>
          <input type="number" inputMode="decimal" value={amount}
            onChange={e => { setAmount(e.target.value); setLastEdit('amount') }}
            className="w-full bg-bg border border-border rounded-lg px-3 py-2 text-[13px] font-mono outline-none focus:border-accent" />
        </div>

        {hasShares && (
          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="text-[11.5px] text-text-dim block mb-1">份额</label>
              <input type="number" inputMode="decimal" value={shares}
                onChange={e => { setShares(e.target.value); setLastEdit('shares') }}
                className="w-full bg-bg border border-border rounded-lg px-3 py-2 text-[13px] font-mono outline-none focus:border-accent" />
            </div>
            <div>
              <label className="text-[11.5px] text-text-dim block mb-1">
                净值 / 单价
                {lastEdit === 'amount' && (
                  <span className="text-text-muted text-[10px] ml-1">— 自动 = (金额−费)/份额</span>
                )}
              </label>
              <input type="number" inputMode="decimal" value={unitPrice}
                onChange={e => { setUnitPrice(e.target.value); setLastEdit('unitPrice') }}
                className="w-full bg-bg border border-border rounded-lg px-3 py-2 text-[13px] font-mono outline-none focus:border-accent" />
            </div>
          </div>
        )}

        <div>
          <label className="text-[11.5px] text-text-dim block mb-1">手续费 (CNY)</label>
          <input type="number" inputMode="decimal" value={fee} onChange={e => setFee(e.target.value)}
            className="w-full bg-bg border border-border rounded-lg px-3 py-2 text-[13px] font-mono outline-none focus:border-accent" />
        </div>

        {err && <div className="text-[11px] text-bear-bright">{err}</div>}

        <div className="flex gap-2 pt-1">
          <button onClick={submit} disabled={busy}
            className="flex-1 px-4 py-2 rounded-lg bg-accent text-bg font-medium text-[13px] hover:opacity-90 disabled:opacity-50 cursor-pointer">
            {busy ? '...' : '保存'}
          </button>
          <button onClick={onClose}
            className="px-4 py-2 rounded-lg border border-border text-text-dim hover:text-text hover:border-border-med text-[13px] cursor-pointer">
            取消
          </button>
        </div>
      </div>
    </div>
  )
}

function ConfirmActionModal({ asset, action, onClose, onDone }) {
  // 三种 pending 形态:
  //  REDEEM (赎回): shares 已知, 待确认 amount + unit_price
  //  ADD/BUY · amount 模式: amount 已知, 待确认 shares + unit_price
  //  ADD/BUY · shares 模式: shares 已知 (按份额定投), 只需补 unit_price, amount 自算
  const isAdd = action.action_type === 'ADD' || action.action_type === 'BUY'
  const knownShares = action.shares ? parseFloat(action.shares) : 0
  const knownAmount = parseFloat(action.amount) || 0
  const isSharesMode = isAdd && knownShares > 0 && knownAmount === 0

  // shares 模式: 份额 disabled+预填, 金额留空让它从 shares*unit_price 自算
  // amount 模式: 金额预填, 份额留空
  // REDEEM: 份额已知 disabled
  const [amount, setAmount] = React.useState(
    isSharesMode ? '' : (isAdd ? String(knownAmount) : '')
  )
  const [shares, setShares] = React.useState(
    isSharesMode ? String(knownShares) : (isAdd ? '' : String(knownShares))
  )
  const [unitPrice, setUnitPrice] = React.useState('')
  const [fee, setFee] = React.useState('')
  const [busy, setBusy] = React.useState(false)
  const [err, setErr] = React.useState('')

  const a = parseFloat(amount) || 0
  const s = parseFloat(shares) || 0
  const u = parseFloat(unitPrice) || 0
  const fNum = parseFloat(fee) || 0
  // 申购费率外扣: 没手填 fee 时, 对场外基金申购按费率自动算 (净额=金额/(1+费率))
  const feeRate = isAdd ? (parseFloat(asset.purchase_fee_rate) || 0) : 0
  const autoFee = feeRate > 0 && a > 0 ? +(a - a / (1 + feeRate)).toFixed(2) : 0
  const effFee = fNum > 0 ? fNum : autoFee
  // 净额: 用户填的"金额"是含费的总付出, 实际买到份额的净额 = amount - 实际手续费
  const netForShares = Math.max(0, a - effFee)

  // 反推占位 (用净额, 不用 amount)
  const inferredUnit = (netForShares > 0 && s > 0 && !u) ? (netForShares / s).toFixed(4) : ''
  const inferredShares = (netForShares > 0 && u > 0 && !s) ? (netForShares / u).toFixed(4) : ''
  const inferredAmount = (s > 0 && u > 0 && !a) ? (s * u + fNum).toFixed(2) : ''

  const submit = async () => {
    setErr('')
    let finalAmount = a
    if (!finalAmount && s > 0 && u > 0) finalAmount = s * u + effFee
    if (!finalAmount || finalAmount <= 0) { setErr('金额必填'); return }
    if (isAdd && !s && !u) { setErr('申购确认: 至少填份额或净值'); return }
    setBusy(true)
    try {
      const body = { amount: finalAmount }
      if (s > 0) body.shares = s
      if (u > 0) body.unit_price = u
      if (effFee > 0) body.fee = effFee
      const res = await fetch(`/api/assets/${asset.id}/actions/${action.id}/confirm`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || '确认失败')
      onDone?.()
    } catch (e) { setErr(e.message) }
    finally { setBusy(false) }
  }

  return (
    <div className="fixed inset-0 z-[210] flex items-center justify-center bg-black/70 backdrop-blur-sm" onClick={onClose}>
      <div className="bg-surface-2 border border-border rounded-xl p-5 w-[420px] max-w-[95vw] space-y-3"
        onClick={e => e.stopPropagation()}>
        <div className="flex items-baseline justify-between">
          <h3 className="text-[14px] font-semibold text-text-bright m-0">
            确认 {isAdd ? '申购' : '赎回'}
          </h3>
          <button onClick={onClose} className="text-text-dim hover:text-text text-[18px] leading-none px-2 cursor-pointer">×</button>
        </div>
        <div className="text-[11px] text-text-dim">
          原申请: {isSharesMode
            ? <>份额 <span className="font-mono text-text">{knownShares.toFixed(4)}</span> <span className="text-text-muted">(按份额定投, 只需补单价)</span></>
            : isAdd
              ? <>金额 <span className="font-mono text-text">¥{fmtMoney(knownAmount)}</span></>
              : <>份额 <span className="font-mono text-text">{knownShares.toFixed(4)}</span></>
          } · 日期 <span className="font-mono text-text">{action.trade_date || '--'}</span>
        </div>

        <div>
          <label className="text-[11.5px] text-text-dim block mb-1">
            金额 (CNY, 含手续费)
            {isSharesMode
              ? <span className="text-text-muted text-[10px]"> — 留空将自动 = 份额 × 单价 + 手续费</span>
              : isAdd && <span className="text-text-muted text-[10px]"> — 你实际付出的总额, 含手续费</span>}
          </label>
          <input type="number" inputMode="decimal" placeholder={inferredAmount || '0'}
            value={amount} onChange={e => setAmount(e.target.value)}
            className="w-full bg-bg border border-border rounded-lg px-3 py-2 text-[13px] font-mono outline-none focus:border-accent" />
        </div>

        <div className="grid grid-cols-2 gap-2">
          <div>
            <label className="text-[11.5px] text-text-dim block mb-1">
              {isSharesMode ? '成交份额' : isAdd ? '成交份额 *' : '份额'}
            </label>
            <input type="number" inputMode="decimal"
              placeholder={inferredShares || (isAdd && !isSharesMode ? '必填或填净值' : '已知')}
              disabled={!isAdd || isSharesMode}
              value={shares} onChange={e => setShares(e.target.value)}
              className="w-full bg-bg border border-border rounded-lg px-3 py-2 text-[13px] font-mono outline-none focus:border-accent disabled:opacity-60" />
          </div>
          <div>
            <label className="text-[11.5px] text-text-dim block mb-1">
              净值 / 单价 {isSharesMode
                ? <span className="text-warn text-[10px]">*</span>
                : <span className="text-text-muted text-[10px]">可空</span>}
            </label>
            <input type="number" inputMode="decimal" placeholder={inferredUnit || (isSharesMode ? '必填' : '可选')}
              value={unitPrice} onChange={e => setUnitPrice(e.target.value)}
              autoFocus={isSharesMode}
              className="w-full bg-bg border border-border rounded-lg px-3 py-2 text-[13px] font-mono outline-none focus:border-accent" />
          </div>
        </div>

        <div>
          <label className="text-[11.5px] text-text-dim block mb-1">
            手续费 (CNY) <span className="text-text-muted text-[10px]">— 含在上方金额里, 单填方便看净额</span>
            {feeRate > 0 && (
              <span className="text-accent text-[10px] ml-1">
                · 申购费率 {(feeRate * 100).toFixed(2)}% 自动外扣 {autoFee > 0 ? `¥${autoFee.toFixed(2)}` : ''}
              </span>
            )}
          </label>
          <div className="flex gap-2 items-stretch">
            <input type="number" inputMode="decimal"
              placeholder={autoFee > 0 ? `${autoFee.toFixed(2)} (申购费率自动)` : '0 (默认无费)'}
              value={fee} onChange={e => setFee(e.target.value)}
              className="flex-1 bg-bg border border-border rounded-lg px-3 py-2 text-[13px] font-mono outline-none focus:border-accent" />
            <button type="button"
              onClick={() => {
                // 招商证券万1.854 5元起 (跟 stock 端用同一档, 用户已存的费率)
                const calc = Math.max(5, a * 0.0001854)
                setFee(calc.toFixed(2))
              }}
              disabled={!a}
              className="px-2.5 py-1 rounded-lg border border-border text-text-dim hover:text-text hover:border-border-med text-[10.5px] cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed"
              title="按招商证券 万1.854 / 最低 5 元 算一下">
              券商费率
            </button>
          </div>
          {effFee > 0 && a > 0 && (
            <div className="text-[10.5px] text-text-muted mt-1">
              净额 ¥{netForShares.toFixed(2)} (= 金额 ¥{a.toFixed(2)} − 费 ¥{effFee.toFixed(2)}
              {fNum <= 0 && autoFee > 0 && <span className="text-accent"> 申购费率</span>})
              {s > 0 && netForShares > 0 && (
                <span className="ml-1.5">· 实际净值 ≈ <span className="font-mono">{(netForShares / s).toFixed(4)}</span></span>
              )}
            </div>
          )}
        </div>

        {err && <div className="text-[11px] text-bear-bright">{err}</div>}

        <div className="flex gap-2 pt-1">
          <button onClick={submit} disabled={busy}
            className="flex-1 px-4 py-2 rounded-lg bg-accent text-bg font-medium text-[13px] hover:opacity-90 disabled:opacity-50 cursor-pointer">
            {busy ? '...' : '确认入账'}
          </button>
          <button onClick={onClose}
            className="px-4 py-2 rounded-lg border border-border text-text-dim hover:text-text hover:border-border-med text-[13px] cursor-pointer">
            取消
          </button>
        </div>
      </div>
    </div>
  )
}

// DcaPanel — 定投计划管理 (FUND/CRYPTO 嵌在 EditAssetRow 底部)
function DcaPanel({ assetId }) {
  const [schedules, setSchedules] = React.useState([])
  const [loading, setLoading] = React.useState(false)
  const [adding, setAdding] = React.useState(false)
  const [editingId, setEditingId] = React.useState(null)
  const EMPTY_FORM = {
    mode: 'amount', value: '', frequency: 'daily_trading',
    day_of_month: '15', day_of_week: '1', note: '',
  }
  const [form, setForm] = React.useState(EMPTY_FORM)

  const reload = React.useCallback(async () => {
    setLoading(true)
    try {
      const d = await fetchJSON(`/api/dca?asset_id=${assetId}`)
      setSchedules(d.schedules || [])
    } catch {}
    finally { setLoading(false) }
  }, [assetId])

  React.useEffect(() => { reload() }, [reload])

  const closeForm = () => {
    setAdding(false)
    setEditingId(null)
    setForm(EMPTY_FORM)
  }

  const startEdit = (s) => {
    setEditingId(s.id)
    setAdding(false)
    setForm({
      mode: s.mode || 'amount',
      value: String(s.value ?? ''),
      frequency: s.frequency || 'daily_trading',
      day_of_month: String(s.day_of_month ?? '15'),
      day_of_week: String(s.day_of_week ?? '1'),
      note: s.note || '',
    })
  }

  const save = async () => {
    const v = parseFloat(form.value)
    if (!v || v <= 0) return alert('请输入金额或份数')
    const body = {
      mode: form.mode, value: v,
      frequency: form.frequency, note: form.note || '',
    }
    if (form.frequency === 'monthly') {
      const d = parseInt(form.day_of_month)
      if (!d || d < 1 || d > 31) return alert('每月几号: 1-31')
      body.day_of_month = d
    } else if (form.frequency === 'weekly') {
      const d = parseInt(form.day_of_week)
      if (!d || d < 1 || d > 7) return alert('星期几: 1-7 (Mon=1)')
      body.day_of_week = d
    }
    if (editingId) {
      await fetchJSON(`/api/dca/${editingId}`, {
        method: 'PUT', body: JSON.stringify(body),
      })
    } else {
      await fetchJSON('/api/dca', {
        method: 'POST',
        body: JSON.stringify({ asset_id: assetId, ...body }),
      })
    }
    closeForm()
    reload()
  }

  const FREQ_LABEL = {
    daily_trading: '每个交易日',
    weekly: '每周',
    monthly: '每月',
  }
  const WEEK_LABEL = ['', '一', '二', '三', '四', '五', '六', '日']
  const describeSchedule = (s) => {
    const f = s.frequency || 'monthly'
    if (f === 'daily_trading') return '每个交易日'
    if (f === 'weekly') return `每周${WEEK_LABEL[s.day_of_week] || '?'}`
    return `每月 ${s.day_of_month} 号`
  }

  const togglePause = async (s) => {
    await fetchJSON(`/api/dca/${s.id}`, {
      method: 'PUT',
      body: JSON.stringify({ status: s.status === 'active' ? 'paused' : 'active' }),
    })
    reload()
  }

  const remove = async (s) => {
    if (!confirm('确定删除此定投计划？已生成的流水不会受影响。')) return
    await fetchJSON(`/api/dca/${s.id}`, { method: 'DELETE' })
    reload()
  }

  return (
    <div className="basis-full mt-2 pt-2 border-t border-border-subtle">
      <div className="flex items-baseline justify-between mb-1.5">
        <div className="flex items-baseline gap-2">
          <span className="text-[11.5px] text-text-bright font-semibold">定投计划</span>
          <span className="text-[10.5px] text-text-muted">每个交易日 / 每周 / 每月 自动写一条 pending ADD，T+1 净值出来后在流水里确认</span>
        </div>
        {!adding && !editingId && (
          <button onClick={() => { setAdding(true); setEditingId(null) }}
            className="px-2 py-[3px] rounded text-[11px] border border-accent text-accent hover:bg-accent/10 cursor-pointer">
            + 新建
          </button>
        )}
      </div>

      {(adding || editingId) && (
        <div className="bg-surface-3 rounded-md p-2 mb-2 flex flex-wrap gap-2 items-end">
          <div className="flex flex-col gap-0.5">
            <label className="text-[10.5px] text-text-dim">频率</label>
            <select value={form.frequency} onChange={e => setForm(f => ({ ...f, frequency: e.target.value }))}
              className="bg-bg border border-border rounded px-2 py-1 text-[12px] text-text outline-none focus:border-accent">
              <option value="daily_trading">每个交易日 (跳节假日)</option>
              <option value="weekly">每周</option>
              <option value="monthly">每月</option>
            </select>
          </div>
          <div className="flex flex-col gap-0.5">
            <label className="text-[10.5px] text-text-dim">模式</label>
            <select value={form.mode} onChange={e => setForm(f => ({ ...f, mode: e.target.value }))}
              className="bg-bg border border-border rounded px-2 py-1 text-[12px] text-text outline-none focus:border-accent">
              <option value="amount">固定金额 ¥</option>
              <option value="shares">固定份额</option>
            </select>
          </div>
          <div className="flex flex-col gap-0.5">
            <label className="text-[10.5px] text-text-dim">{form.mode === 'amount' ? '金额 ¥' : '份数'}</label>
            <input type="number" inputMode="decimal" value={form.value}
              onChange={e => setForm(f => ({ ...f, value: e.target.value }))}
              className="bg-bg border border-border rounded px-2 py-1 text-[12px] font-mono w-24 outline-none focus:border-accent"
              placeholder={form.mode === 'amount' ? '50' : '13.6'} />
          </div>
          {form.frequency === 'monthly' && (
            <div className="flex flex-col gap-0.5">
              <label className="text-[10.5px] text-text-dim">每月几号</label>
              <input type="number" min={1} max={31} value={form.day_of_month}
                onChange={e => setForm(f => ({ ...f, day_of_month: e.target.value }))}
                className="bg-bg border border-border rounded px-2 py-1 text-[12px] font-mono w-16 outline-none focus:border-accent" />
            </div>
          )}
          {form.frequency === 'weekly' && (
            <div className="flex flex-col gap-0.5">
              <label className="text-[10.5px] text-text-dim">星期</label>
              <select value={form.day_of_week} onChange={e => setForm(f => ({ ...f, day_of_week: e.target.value }))}
                className="bg-bg border border-border rounded px-2 py-1 text-[12px] outline-none focus:border-accent">
                <option value="1">周一</option>
                <option value="2">周二</option>
                <option value="3">周三</option>
                <option value="4">周四</option>
                <option value="5">周五</option>
                <option value="6">周六</option>
                <option value="7">周日</option>
              </select>
            </div>
          )}
          <div className="flex flex-col gap-0.5 flex-1 min-w-[120px]">
            <label className="text-[10.5px] text-text-dim">备注 (可空)</label>
            <input type="text" value={form.note}
              onChange={e => setForm(f => ({ ...f, note: e.target.value }))}
              className="bg-bg border border-border rounded px-2 py-1 text-[12px] outline-none focus:border-accent"
              placeholder="支付宝月定投" />
          </div>
          <button onClick={save}
            className="px-3 py-1 rounded bg-accent text-bg text-[11.5px] font-medium hover:opacity-90 cursor-pointer">
            {editingId ? '保存' : '添加'}
          </button>
          <button onClick={closeForm}
            className="px-3 py-1 rounded border border-border text-text-dim text-[11.5px] hover:text-text cursor-pointer">
            取消
          </button>
        </div>
      )}

      {loading ? (
        <div className="text-[11px] text-text-dim">加载中...</div>
      ) : schedules.length === 0 ? (
        <div className="text-[11px] text-text-dim">还没有定投计划</div>
      ) : (
        <div className="space-y-1">
          {schedules.map(s => {
            const paused = s.status === 'paused'
            return (
              <div key={s.id} className={`flex items-baseline gap-3 px-2 py-1.5 rounded text-[11.5px] ${paused ? 'bg-surface-3 opacity-60' : 'bg-surface-3'}`}>
                <span className="font-mono">
                  {describeSchedule(s)} ·
                  <span className="text-bull-bright ml-1">
                    {s.mode === 'amount' ? `¥${fmtMoney(s.value)}` : `${s.value} 份`}
                  </span>
                </span>
                {s.next_due && <span className="text-text-dim">下次 <span className="font-mono text-text">{s.next_due}</span></span>}
                {s.last_fired_at && <span className="text-text-muted text-[10.5px]">上次 {s.last_fired_at}</span>}
                {s.note && <span className="text-text-dim text-[10.5px]">· {s.note}</span>}
                {paused && <span className="text-warn text-[10px] px-1 border border-warn/40 rounded">已暂停</span>}
                <button onClick={() => startEdit(s)}
                  className="ml-auto text-[10.5px] text-text-dim hover:text-accent cursor-pointer">
                  改
                </button>
                <button onClick={() => togglePause(s)}
                  className="text-[10.5px] text-text-dim hover:text-accent cursor-pointer">
                  {paused ? '激活' : '暂停'}
                </button>
                <button onClick={() => remove(s)}
                  className="text-[10.5px] text-text-dim hover:text-bear cursor-pointer">
                  删
                </button>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
