import React, { useState, useEffect, useMemo, useCallback } from 'react'
import { api, fetchJSON } from '../hooks/useApi'
import { fmtMoney, fmtPct, fmtPrice, priceColor } from '../helpers'
import Tooltip from './Tooltip'

// ============================================================
// UnifiedPortfolio — replaces Portfolio + ExternalAssets
// Groups: A股 / 基金 / 加密 / 机器人. Sorted by market value desc
// within groups. Hover-only row actions. Donut allocation header.
// ============================================================

const TYPE_META = {
  A: { label: 'A股',   short: 'A', tintVar: '--color-accent',     desc: '沪深上市' },
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

// Fund subcategory: derived from name keywords. Order = render order.
const FUND_CATEGORIES = [
  { id: 'gold',     label: '黄金',     match: /黄金|金ETF/ },
  { id: 'silver',   label: '白银',     match: /白银|银ETF/ },
  { id: 'overseas', label: '海外股票', match: /QDII|纳斯达克|纳指|标普|美股|港股|恒生|中概|海外/ },
  { id: 'commodity',label: '大宗商品', match: /原油|能源|有色|铜|铁矿|豆粕|商品/ },
  { id: 'bond',     label: '债券',     match: /债|利率/ },
  { id: 'cash',     label: '货币',     match: /货币|活期|现金/ },
  { id: 'aindex',   label: 'A股宽基', match: /沪深300|中证500|中证1000|上证50|创业板|科创50|A股/ },
  { id: 'asector',  label: 'A股行业', match: /消费|医药|科技|新能源|半导体|军工|银行|证券|地产|周期/ },
]
function fundCategoryOf(name = '') {
  for (const c of FUND_CATEGORIES) if (c.match.test(name)) return c
  return { id: 'other', label: '其他' }
}

// A-share industry: keyword-driven. 优先匹配后端拉的真实行业字符串
// (e.g. "电气设备-电源设备")，否则回退到股票名兜底。
const A_SECTORS = [
  { id: 'metals',    label: '有色金属', match: /有色|铜|铝|锌|镍|铅|稀土|钼|黄金|白银/ },
  { id: 'newenergy', label: '电气新能源', match: /电气设备|电源设备|储能|光伏|锂电|动力电池|电池|风电设备|新能源(?!车)/ },
  { id: 'energy',    label: '能源',     match: /石油|煤|燃气|电力|核电|风电/ },
  { id: 'finance',   label: '金融',     match: /银行|证券|保险|期货|信托/ },
  { id: 'realestate',label: '地产',     match: /地产|置业|建设|建筑/ },
  { id: 'tech',      label: '科技',     match: /科技|半导体|芯片|软件|信息|电子|通信|计算/ },
  { id: 'consumer',  label: '消费',     match: /消费|食品|酒|乳业|家电|零售|百货|医美/ },
  { id: 'medical',   label: '医药',     match: /医药|生物|医疗|制药|疫苗/ },
  { id: 'auto',      label: '汽车',     match: /汽车|新能源车|整车/ },
  { id: 'materials', label: '材料',     match: /钢|水泥|玻璃|化工|塑料|纤维/ },
  { id: 'machinery', label: '机械',     match: /机械|装备|工程|重工/ },
]
function aShareCategoryOf(name = '', sector = '') {
  // 优先用后端从东方财富拉的真实行业（如 "有色金属-二次资源利用-钴"），
  // 没有时回退到从股票名 regex 推断（兜底）。
  const probe = sector || name
  for (const c of A_SECTORS) if (c.match.test(probe)) return c
  return { id: 'other', label: '其他' }
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

const daysSince = (iso) => {
  if (!iso) return null
  const d = Date.parse(iso)
  if (isNaN(d)) return null
  return Math.max(0, Math.floor((Date.now() - d) / 86400000))
}

// Normalize raw A-share holding → unified row
function normalizeHolding(h) {
  const mv = h.market_value || (h.current_price * h.shares) || 0
  const cost = h.cost_price * h.shares
  return {
    id: `A-${h.stock_code}`,
    type: 'A',
    category: aShareCategoryOf(h.stock_name, h.sector),
    code: h.stock_code,
    name: h.stock_name,
    mv,
    cost,
    pnl: h.unrealized_pnl ?? (mv - cost),
    pnlPct: h.pnl_pct,
    today: h.price_change_pct,
    days: daysSince(h.created_at),
    _raw: h,
    extra: {
      shares: h.shares,
      price: h.current_price,
      avgCost: h.cost_price,
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
    days: daysSince(a.created_at),
    _raw: a,
    extra: {
      platform: a.platform,
      nav: q?.nav ?? q?.est_nav,  // 优先官方净值，对齐 App "持有金额" 口径
      realtime: q?.realtime,
      price: q?.price,
      priceCny: q?.price_cny,
      amount: a.shares,
      lockUntil: null,
      okxSynced: q?.auto_synced,
      okxPnlPct: q?.pnl_pct,
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
  for (const r of rows) {
    if (!groups[r.type]) groups[r.type] = { items: [], mv: 0, cost: 0, pnl: 0, todayPnl: 0 }
    const g = groups[r.type]
    g.items.push(r)
    g.mv += r.mv || 0
    g.cost += r.cost || 0
    g.pnl += r.pnl || 0
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
    totalPnl: rows.reduce((s, r) => s + (r.pnl || 0), 0),
    totalToday: Object.values(groups).reduce((s, g) => s + g.todayPnl, 0),
  }
}

// ============================================================
// Allocation donut
// ============================================================
function AllocationDonut({ groups, totalMv, size = 140 }) {
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
    <div className="flex items-center gap-5 shrink-0">
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
        <text x={cx} y={cx + 13} textAnchor="middle" className="font-mono text-[14px] font-bold"
          fill="var(--color-text-bright)">
          ¥{fmtMoney(totalMv)}
        </text>
      </svg>
      <div className="flex flex-col gap-1.5 min-w-[180px]">
        {order.map(type => {
          const g = groups[type]
          return (
            <div key={type} className="flex items-center gap-2 text-[11px]">
              <div className="w-2 h-2 rounded-sm shrink-0" style={{ background: TYPE_COLOR[type] }} />
              <span className="text-text min-w-[32px]">{TYPE_META[type].label}</span>
              <span className="font-mono text-text-bright tabular-nums min-w-[40px]">
                {(g.weight * 100).toFixed(1)}%
              </span>
              <span className="font-mono text-text-dim text-[10px] ml-auto">
                ¥{fmtMoney(g.mv)}
              </span>
              <span className={`font-mono text-[10px] min-w-[52px] text-right ${priceColor(g.pnl)}`}>
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
      <span className="font-mono text-[11.5px] font-semibold tabular-nums min-w-[46px] text-right"
        style={{ color }}>
        {change === 0 ? '0.00%' : (change > 0 ? '+' : '') + change.toFixed(2) + '%'}
      </span>
      <div className="relative flex items-center justify-center" style={{ width, height: 14 }}>
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
        <span className="font-mono text-[11.5px] font-semibold tabular-nums min-w-[46px] text-right"
          style={{ color }}>
          {change === 0 ? '0.00%' : (change > 0 ? '+' : '') + change.toFixed(2) + '%'}
          <span className="text-[8.5px] opacity-70 ml-0.5 align-top">代</span>
        </span>
        <div className="relative flex items-center justify-center" style={{ width, height: 14 }}>
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

function TypeMiniInfo({ row, unwindByCode }) {
  const { type, extra, code } = row
  if (type === 'A') {
    const plan = unwindByCode?.[code]
    if (!plan) {
      return <span className="text-[10.5px] text-text-muted font-mono">
        {extra.shares} 股 · ¥{fmtPrice(extra.avgCost)}
      </span>
    }
    // unwind progress: pnl% recovered relative to initial loss
    const prog = plan.progress ?? 0
    const color = prog >= 1 ? 'var(--color-bull-bright)' :
      prog >= 0.5 ? 'var(--color-accent)' : 'var(--color-bear-bright)'
    return (
      <span className="inline-flex items-center gap-1.5 text-[10.5px] text-text-dim">
        <span>解套</span>
        <div className="h-[3px] rounded-sm overflow-hidden" style={{ width: 36, background: 'var(--color-surface-3)' }}>
          <div className="h-full rounded-sm" style={{ width: `${Math.min(100, prog * 100)}%`, background: color }} />
        </div>
        <span className="font-mono min-w-[28px]" style={{ color }}>
          {(prog * 100).toFixed(0)}%
        </span>
      </span>
    )
  }
  if (type === 'F') {
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
    return (
      <span className="text-[10.5px] text-text-dim">
        {extra.platform || '理财'}
        {yieldRate != null && (
          <> · 年化 <span className="font-mono text-bull">
            {isImplied && '≈'}{(yieldRate * 100).toFixed(2)}%
          </span>{isImplied && (
            <Tooltip content={
              <div className="leading-relaxed">
                <div>基于<span className="text-text-bright">当前总额 ÷ 本金 − 1</span></div>
                <div>除以<span className="text-text-bright">持有天数</span>得隐含年化</div>
                <div className="text-text-dim mt-1 text-[10.5px]">非产品标定年化，仅作参考</div>
              </div>
            }>
              <span className="text-text-muted ml-0.5 cursor-help underline decoration-dotted decoration-text-muted underline-offset-2">反推</span>
            </Tooltip>
          )}</>
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
function RowActions({ row, visible, onEdit, onHistory, onRemove, onAddLot }) {
  const btnClass = 'px-2 py-[3px] rounded border border-border-med bg-surface-2 text-text-dim ' +
    'text-[10.5px] hover:border-accent hover:text-accent transition-colors cursor-pointer whitespace-nowrap'
  const actions = []
  if (row.type === 'A') {
    actions.push({ label: '历史', fn: () => onHistory?.(row._raw) })
    actions.push({ label: '编辑', fn: () => onEdit?.(row) })
  } else {
    // 加仓 only applies to FUND / CRYPTO / WEALTH (not BOT — OKX synced; not CASH — edit balance directly)
    if (row.type === 'F' || row.type === 'C' || row.type === 'W') {
      actions.push({ label: '加仓', fn: () => onAddLot?.(row) })
    }
    actions.push({ label: '编辑', fn: () => onEdit?.(row) })
    actions.push({ label: '删除', fn: () => onRemove?.(row), danger: true })
  }
  return (
    <div className="flex gap-1 justify-end items-center" style={{
      opacity: visible ? 1 : 0,
      transform: visible ? 'translateX(0)' : 'translateX(4px)',
      transition: 'opacity .18s, transform .18s',
      pointerEvents: visible ? 'auto' : 'none',
    }}>
      {actions.map(a => (
        <button key={a.label} onClick={a.fn}
          className={btnClass}
          onMouseEnter={a.danger ? e => {
            e.currentTarget.style.borderColor = 'var(--color-bear)'
            e.currentTarget.style.color = 'var(--color-bear)'
          } : undefined}
          onMouseLeave={a.danger ? e => {
            e.currentTarget.style.borderColor = ''
            e.currentTarget.style.color = ''
          } : undefined}
        >{a.label}</button>
      ))}
    </div>
  )
}

// ============================================================
// Summary strip
// ============================================================
function SummaryStrip({ agg, aShareClosed }) {
  const items = [
    { label: '总资产', val: `¥${fmtMoney(agg.totalMv)}`, big: true, color: 'text-text-bright' },
    {
      label: '总盈亏',
      val: `${agg.totalPnl >= 0 ? '+' : ''}${fmtMoney(agg.totalPnl)}`,
      color: priceColor(agg.totalPnl),
      sub: `(${agg.totalCost > 0 ? fmtPct(agg.totalPnl / agg.totalCost * 100) : '--'})`,
    },
    {
      label: aShareClosed ? '今日浮动 (A股闭市)' : '今日浮动',
      val: `${agg.totalToday >= 0 ? '+' : ''}${fmtMoney(agg.totalToday)}`,
      color: priceColor(agg.totalToday),
    },
  ]
  return (
    <div className="flex gap-7 items-baseline flex-wrap">
      {items.map((it, i) => (
        <div key={i} className="flex flex-col gap-0.5">
          <span className="text-[10.5px] text-text-dim tracking-wide">{it.label}</span>
          <span className="inline-flex items-baseline gap-1.5">
            <span className={`font-mono font-bold tabular-nums ${it.color}`}
              style={{ fontSize: it.big ? 22 : 15, letterSpacing: '-.01em' }}>{it.val}</span>
            {it.sub && <span className={`font-mono text-[11px] opacity-80 ${it.color}`}>{it.sub}</span>}
          </span>
        </div>
      ))}
    </div>
  )
}

// ============================================================
// Main
// ============================================================
export default function UnifiedPortfolio({ holdings, onEdit, onHistory, onAdd }) {
  const [assets, setAssets] = useState([])
  const [assetsLoaded, setAssetsLoaded] = useState(false)
  const [unwindPlans, setUnwindPlans] = useState({})
  const [tradingDay, setTradingDay] = useState(null)
  const [collapsed, setCollapsed] = useState({})
  const [hoverId, setHoverId] = useState(null)
  const [filter, setFilter] = useState('ALL')
  const [sortKey, setSortKey] = useState('mv')
  const [addTarget, setAddTarget] = useState(null) // null | 'A' | 'F' | 'C' | 'R'
  const [editAsset, setEditAsset] = useState(null)
  const [addLotAsset, setAddLotAsset] = useState(null)

  const loadAssets = useCallback(async () => {
    try {
      const d = await fetchJSON('/api/assets')
      setAssets(d.assets || [])
    } catch {} finally { setAssetsLoaded(true) }
  }, [])

  useEffect(() => {
    loadAssets()
    // Crypto & OKX bots are 24/7 markets; use shorter interval. Server-side
    // caches handle upstream rate limits (crypto 30s, fund 120s).
    const t = setInterval(loadAssets, 20000)
    return () => clearInterval(t)
  }, [loadAssets])

  useEffect(() => {
    fetchJSON('/api/market/trading-day').then(setTradingDay).catch(() => {})
    const t = setInterval(() => fetchJSON('/api/market/trading-day').then(setTradingDay).catch(() => {}), 3600000)
    return () => clearInterval(t)
  }, [])

  useEffect(() => {
    fetchJSON('/api/unwind/plans').then(plans => {
      const map = {}
      for (const p of plans || []) {
        // 解套进度 = max(price_progress, cost_progress), clamped to 1 when 可清仓.
        // price_progress: how far price has climbed from 60d low toward cost.
        // cost_progress:  how much avg cost has been reduced toward current price.
        let progress = Math.max(p.price_progress || 0, p.cost_progress || 0)
        if (p.can_unwind_now) progress = 1
        map[p.stock_code] = {
          progress,
          exit_price: p.unwind_exit_price,
          can_unwind: p.can_unwind_now,
        }
      }
      setUnwindPlans(map)
    }).catch(() => {})
  }, [holdings.length])

  const rows = useMemo(() => {
    const a = (holdings || []).map(normalizeHolding)
    const e = (assets || []).map(normalizeAsset)
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
  const visibleTypes = TYPE_ORDER.filter(t => agg.groups[t])

  // Risk insights: concentration warnings + cross-type overlap families.
  // Always computed from ALL rows (not filtered) so toggling filter doesn't change advice.
  const insights = useMemo(() => {
    const allAgg = aggregate(rows, true)
    const total = allAgg.totalMv || 0
    const warnings = []
    // 1. Major class concentration
    for (const t of TYPE_ORDER) {
      const g = allAgg.groups[t]
      if (!g || total === 0) continue
      const pct = g.weight * 100
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
      <div className="px-6 py-5 border-b border-border flex flex-wrap justify-between items-center gap-8"
        style={{ background: 'linear-gradient(180deg, var(--color-surface-2), var(--color-surface))' }}>
        <div className="flex flex-col gap-3 flex-1 min-w-[340px]">
          <div className="flex items-baseline gap-3">
            <h2 className="text-[14px] font-semibold text-text-bright tracking-wide m-0">持仓总览</h2>
            <span className="text-[11px] text-text-dim">A股 · 基金 · 理财 · 现金 · 加密 · 机器人</span>
          </div>
          {isEmpty
            ? <div className="text-text-dim text-[12px] py-2">还没有持仓,点击下方「+ 添加」开始</div>
            : <SummaryStrip agg={agg} aShareClosed={!aShareTradingDay} />
          }
        </div>
        {!isEmpty && <AllocationDonut groups={agg.groups} totalMv={agg.totalMv} />}
      </div>

      {/* Risk insights strip */}
      {!isEmpty && insights.warnings.length > 0 && (
        <div className="px-6 py-2.5 border-b border-border bg-surface-2/60 flex flex-col gap-1.5">
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
      <div className="px-6 py-2 border-b border-border flex justify-between items-center gap-3 flex-wrap"
        style={{ background: 'var(--color-surface-2)' }}>
        <div className="flex gap-1.5 flex-wrap">
          {[['ALL', '全部'], ['A', 'A股'], ['F', '基金'], ['C', '加密'], ['R', '机器人']].map(([k, l]) => {
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
        <div className="px-6 py-3 border-b border-border bg-surface-2/60 flex flex-wrap gap-2 items-center">
          <span className="text-[11px] text-text-dim mr-2">添加到哪一类?</span>
          {TYPE_ORDER.map(t => (
            <button key={t} onClick={() => setAddTarget(t)}
              className="px-3 py-1 rounded border border-border-med text-[12px] text-text hover:border-accent hover:text-accent transition-colors cursor-pointer">
              + {TYPE_META[t].label}
            </button>
          ))}
          <button onClick={() => setAddTarget(null)}
            className="ml-auto text-[11px] text-text-dim hover:text-text cursor-pointer">取消</button>
        </div>
      )}

      {addTarget === 'A' && (
        <AddAShareForm onDone={() => { setAddTarget(null); onAdd?.() }} onCancel={() => setAddTarget(null)} />
      )}
      {(addTarget === 'F' || addTarget === 'C' || addTarget === 'R' || addTarget === 'W' || addTarget === 'M') && (
        <AddAssetForm typeKey={addTarget}
          onDone={() => { setAddTarget(null); loadAssets() }}
          onCancel={() => setAddTarget(null)} />
      )}

      {editAsset && (
        <EditAssetRow asset={editAsset}
          onDone={() => { setEditAsset(null); loadAssets() }}
          onCancel={() => setEditAsset(null)} />
      )}

      {addLotAsset && (
        <AddLotRow asset={addLotAsset}
          onDone={() => { setAddLotAsset(null); loadAssets() }}
          onCancel={() => setAddLotAsset(null)} />
      )}

      {/* Column headers */}
      {!isEmpty && (
        <div className="grid px-6 py-2 text-[10.5px] text-text-dim tracking-wider font-medium border-b border-border bg-surface"
          style={{ gridTemplateColumns: '22% 10% 9% 14% 14% 13% 5% 13%' }}>
          <div className="text-left">名称 · 代码</div>
          <div className="text-right">市值</div>
          <div className="text-right">成本</div>
          <div className="text-right">浮动盈亏</div>
          <div className="text-right">今日</div>
          <div className="text-right">占比</div>
          <div className="text-right">天</div>
          <div className="text-left pl-2">类型信息 / 操作</div>
        </div>
      )}

      {/* Groups + rows */}
      {visibleTypes.map(type => {
        const g = agg.groups[type]
        const isCol = collapsed[type]
        const items = [...g.items].sort((a, b) =>
          sortKey === 'pnl'
            ? (b.pnl || 0) - (a.pnl || 0)
            : (b.mv || 0) - (a.mv || 0)
        )
        return (
          <div key={type}>
            {/* Group strip */}
            <div onClick={() => setCollapsed(c => ({ ...c, [type]: !c[type] }))}
              className="grid px-6 py-2 border-b border-border cursor-pointer select-none items-center text-[11px] font-semibold text-text"
              style={{
                gridTemplateColumns: '22% 10% 9% 14% 14% 13% 5% 13%',
                background: 'var(--color-surface-2)',
              }}>
              <div className="flex items-center gap-2">
                <span className="inline-block w-2 text-text-dim transition-transform"
                  style={{ transform: isCol ? 'rotate(-90deg)' : 'rotate(0)' }}>▾</span>
                <div className="w-[3px] h-[13px] rounded-sm" style={{ background: TYPE_COLOR[type] }} />
                <span>{TYPE_META[type].label}</span>
                <span className="text-text-dim font-normal text-[10.5px]">{items.length} 项</span>
              </div>
              <div className="text-right font-mono text-text">¥{fmtMoney(g.mv)}</div>
              <div className="text-right font-mono text-text-dim text-[10.5px]">¥{fmtMoney(g.cost)}</div>
              <div className={`text-right font-mono ${priceColor(g.pnl)}`}>
                {g.pnl >= 0 ? '+' : ''}{fmtMoney(g.pnl)}
                <span className="text-[10px] opacity-80 ml-1.5">({fmtPct(g.pnlPct)})</span>
              </div>
              <div />
              <div className="text-right font-mono text-text">{(g.weight * 100).toFixed(1)}%</div>
              <div />
              <div />
            </div>

            {/* Rows (with optional fund subcategory headers) */}
            {!isCol && (() => {
              const renderRow = (row, isLast) => (
                <div key={row.id}
                  onMouseEnter={() => setHoverId(row.id)}
                  onMouseLeave={() => setHoverId(null)}
                  className="grid px-6 py-[11px] items-center transition-colors"
                  style={{
                    gridTemplateColumns: '22% 10% 9% 14% 14% 13% 5% 13%',
                    borderBottom: isLast ? '1px solid var(--color-border)' : '1px solid var(--color-border-subtle)',
                    background: hoverId === row.id ? 'var(--color-surface-2)' : 'transparent',
                  }}>
                  <div className="flex flex-col gap-0.5 min-w-0">
                    <div className="flex items-center gap-1.5">
                      <span className="text-[13px] font-semibold text-text-bright whitespace-nowrap">{row.name}</span>
                      <TypeChip type={row.type} compact />
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
                  <div className="text-right font-mono text-[12.5px] text-text-bright tabular-nums">
                    ¥{fmtMoney(row.mv)}
                  </div>
                  <div className="text-right font-mono text-[11px] text-text-dim">
                    ¥{fmtMoney(row.cost)}
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
                  <div className="text-right">
                    <WeightBar weight={row.mv / (agg.totalMv || 1)} color={TYPE_COLOR[row.type]} />
                  </div>
                  <div className="text-right font-mono text-[10.5px] text-text-dim">
                    {row.days ?? '--'}
                  </div>
                  <div className="relative pl-2">
                    <div className="transition-opacity" style={{ opacity: hoverId === row.id ? 0 : 1 }}>
                      <TypeMiniInfo row={row} unwindByCode={unwindPlans} />
                    </div>
                    <div className="absolute inset-0 flex items-center justify-end pr-0">
                      <RowActions row={row} visible={hoverId === row.id}
                        onEdit={handleEdit} onHistory={onHistory} onRemove={removeAsset} onAddLot={handleAddLot} />
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
                        <div className="grid px-6 py-1.5 items-center text-[10.5px] text-text-dim border-b border-border-subtle"
                          style={{ gridTemplateColumns: '22% 10% 9% 14% 14% 13% 5% 13%', background: 'var(--color-surface)' }}>
                          <div className="flex items-center gap-1.5 pl-4">
                            <span className="inline-block w-1 h-1 rounded-full" style={{ background: TYPE_COLOR[type] }} />
                            <span className="font-medium text-text">{cl.label}</span>
                            <span className="opacity-70">{cl.items.length} 项</span>
                          </div>
                          <div className="text-right font-mono">¥{fmtMoney(cl.mv)}</div>
                          <div />
                          <div className={`text-right font-mono ${priceColor(cl.pnl)}`}>
                            {cl.pnl >= 0 ? '+' : ''}{fmtMoney(cl.pnl)}
                          </div>
                          <div /><div /><div /><div />
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
    </section>
  )
}

// ============================================================
// Add A-share form — compact, inline
// ============================================================
function AddAShareForm({ onDone, onCancel }) {
  const [form, setForm] = useState({ code: '', name: '', shares: '', cost: '' })
  const [submitting, setSubmitting] = useState(false)
  const [nameLooking, setNameLooking] = useState(false)

  const lookup = useCallback(async (code) => {
    if (code.length !== 6) return
    setNameLooking(true)
    try {
      const q = await fetchJSON(`/api/market/quote/${code}`)
      if (q?.stock_name) setForm(f => ({ ...f, name: q.stock_name }))
    } catch {}
    setNameLooking(false)
  }, [])

  const submit = async () => {
    if (!form.code || form.code.length !== 6) return alert('请输入6位股票代码')
    if (!form.shares || parseInt(form.shares) < 100) return alert('持仓数量至少100股')
    if (!form.cost || parseFloat(form.cost) <= 0) return alert('请输入成本价')
    setSubmitting(true)
    try {
      const res = await api.addHolding({
        stock_code: form.code, stock_name: form.name,
        shares: parseInt(form.shares), cost_price: parseFloat(form.cost),
      })
      if (res.message) onDone?.()
      else alert(res.detail || '添加失败')
    } finally { setSubmitting(false) }
  }

  const inp = 'bg-bg border border-border rounded px-2 py-1.5 text-[13px] text-text outline-none focus:border-accent'

  return (
    <div className="px-6 py-3 bg-surface-2/50 border-b border-border flex flex-wrap gap-2 items-end">
      <div className="flex flex-col gap-1">
        <label className="text-[11px] text-text-dim">代码</label>
        <input className={`${inp} w-24 font-mono`} placeholder="601212" maxLength={6} value={form.code}
          onChange={e => { setForm({ ...form, code: e.target.value }); if (e.target.value.length === 6) lookup(e.target.value) }} />
      </div>
      <div className="flex flex-col gap-1">
        <label className="text-[11px] text-text-dim">名称</label>
        <input className={`${inp} w-28`} placeholder={nameLooking ? '查询中...' : '可留空'}
          value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} />
      </div>
      <div className="flex flex-col gap-1">
        <label className="text-[11px] text-text-dim">数量</label>
        <input type="number" className={`${inp} w-24 font-mono`}
          placeholder="300" min={100} step={100} value={form.shares}
          onChange={e => setForm({ ...form, shares: e.target.value })} />
      </div>
      <div className="flex flex-col gap-1">
        <label className="text-[11px] text-text-dim">成本价</label>
        <input type="number" className={`${inp} w-28 font-mono`}
          placeholder="12.7401" step={0.0001} value={form.cost}
          onChange={e => setForm({ ...form, cost: e.target.value })} />
      </div>
      <button onClick={submit} disabled={submitting}
        className="px-4 py-1.5 rounded-md bg-accent text-bg font-medium text-[13px] hover:opacity-90 transition-opacity disabled:opacity-50 cursor-pointer">
        {submitting ? '...' : '确认'}
      </button>
      <button onClick={onCancel}
        className="px-3 py-1.5 rounded-md border border-border text-text-dim text-[13px] hover:text-text transition-colors cursor-pointer">
        取消
      </button>
    </div>
  )
}

// ============================================================
// Add asset form — covers F/C/R with type-specific fields
// (trimmed version of the original ExternalAssets AddAssetForm)
// ============================================================
function AddAssetForm({ typeKey, onDone, onCancel }) {
  const assetType = KEY_TO_ASSET_TYPE[typeKey]
  const [code, setCode] = useState('')
  const [name, setName] = useState('')
  const [platform, setPlatform] = useState('')
  const [shares, setShares] = useState('')
  const [cost, setCost] = useState('')
  const [costTouched, setCostTouched] = useState(false)
  const [manualValue, setManualValue] = useState('')
  const [unitPrice, setUnitPrice] = useState('')
  const [fee, setFee] = useState('')
  const [feeTouched, setFeeTouched] = useState(false)
  const [note, setNote] = useState('')
  const [lookingUp, setLookingUp] = useState(false)
  const [hint, setHint] = useState('')
  // OKX integration (only for BOT)
  const [okxBots, setOkxBots] = useState(null)
  const [okxAlgoId, setOkxAlgoId] = useState('')
  const [okxBotType, setOkxBotType] = useState('')
  // WEALTH (理财)
  const [annualYield, setAnnualYield] = useState('')
  const [startDate, setStartDate] = useState(new Date().toISOString().slice(0, 10))

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
      const est = Math.max(amount * BROKER_COMMISSION_RATE, BROKER_COMMISSION_MIN)
      setFee(est.toFixed(2))
    }
  }, [shares, unitPrice, feeTouched, assetType])

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
          保存
        </button>
        <button onClick={onCancel}
          className="px-3 py-1.5 rounded border border-border text-text-dim text-[12px] hover:text-text cursor-pointer">
          取消
        </button>
      </div>

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
function EditAssetRow({ asset, onDone, onCancel }) {
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
        // Save total cost = unit cost × shares (DB stores total)
        payload.cost_amount = u > 0 && s > 0 ? Number((u * s).toFixed(4)) : (cost !== '' ? parseFloat(cost) : null)
        payload.shares = shares !== '' ? parseFloat(shares) : null
        payload.manual_value = lockMv && mv !== '' ? parseFloat(mv) : null
        payload.pending_amount = pending !== '' ? parseFloat(pending) : 0
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
            <label className="text-[11px] text-text-dim">当前总额 ¥ (二选一)</label>
            <input type="number" step="0.01" value={manualValue} onChange={e => setManualValue(e.target.value)} className={`${inp} w-32`} placeholder="本金+利息" />
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
    </div>
  )
}

// ============================================================
// AddLotRow — 加仓表单. Computes new aggregate cost/shares/start_date
// for FUND/CRYPTO/WEALTH; shows a live preview before save.
// ============================================================
function AddLotRow({ asset, onDone, onCancel }) {
  const [principal, setPrincipal] = useState('')
  const [shares, setShares] = useState('')        // FUND/CRYPTO: 新增份额
  const [unitPrice, setUnitPrice] = useState('')  // FUND/CRYPTO: 单价 (净值 / 币价)
  const [fee, setFee] = useState('')              // FUND/CRYPTO: 手续费 ¥ (场内 ETF / 加密 taker fee)
  const [feeTouched, setFeeTouched] = useState(false)
  const [lotStartDate, setLotStartDate] = useState(new Date().toISOString().slice(0, 10))
  const [lotYield, setLotYield] = useState('')   // WEALTH: 加投年化 %
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const rootRef = React.useRef(null)
  useEffect(() => {
    rootRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }, [])

  const t = asset.asset_type
  const isFund = t === 'FUND'
  const isCrypto = t === 'CRYPTO'
  const isWealth = t === 'WEALTH'

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
      const est = Math.max(amount * BROKER_COMMISSION_RATE, BROKER_COMMISSION_MIN)
      setFee(est.toFixed(2))
    }
  }, [shares, unitPrice, feeTouched, isFund, isCrypto])

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
    } else if (isWealth) {
      if (!(p > 0)) { setErr('请输入本金'); return }
      body = { principal: p }
      if (lotStartDate) body.lot_start_date = lotStartDate
      if (lotYield !== '') body.lot_yield_rate = parseFloat(lotYield) / 100
    } else {
      setErr('该资产类型不支持加仓'); return
    }
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

  const inp = 'bg-bg border border-border rounded px-2 py-1 text-[12px] text-text font-mono outline-none focus:border-bull'

  return (
    <div ref={rootRef}
      className="px-6 py-3 border-b-2 border-bull bg-bull/5 flex flex-wrap gap-3 items-end"
      style={{ animation: 'fade-up 0.2s ease-out' }}>
      <span className="text-[11px] text-bull-bright font-semibold mr-2 basis-full">
        ⊕ 加仓 <span className="text-text-bright">{asset.name}</span>
        {(isFund || isCrypto) && (
          <span className="ml-2 text-[10px] text-text-muted font-normal">
            · 单价+份额 = 按股买；本金+(单价或份额) = 确认型；仅本金 = 待确认
          </span>
        )}
      </span>
      <div className="flex flex-col gap-1">
        <label className="text-[11px] text-text-dim">本金 ¥{(isFund || isCrypto) ? ' (按股买可空)' : ' *'}</label>
        <input type="number" step="0.01" autoFocus value={principal} onChange={e => setPrincipal(e.target.value)}
          className={`${inp} w-28`} placeholder="1000" />
      </div>
      {(isFund || isCrypto) && (
        <>
          <div className="flex flex-col gap-1">
            <label className="text-[11px] text-text-dim">{isFund ? '成交净值' : '单价'}</label>
            <input type="number" step="0.0001" value={unitPrice} onChange={e => setUnitPrice(e.target.value)}
              className={`${inp} w-28`} placeholder="3.4399" />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-[11px] text-text-dim">{isFund ? '成交份额' : '成交数量'}</label>
            <input type="number" step="0.0001" value={shares} onChange={e => setShares(e.target.value)}
              className={`${inp} w-28`} placeholder="290.7" />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-[11px] text-text-dim">
              手续费 ¥
              <span className="ml-1 text-[9.5px] text-text-muted">
                (场内 ETF 默认万2.5 / 最低5；场外公募基金填 0)
              </span>
            </label>
            <input type="number" step="0.01" value={fee}
              onChange={e => { setFee(e.target.value); setFeeTouched(true) }}
              className={`${inp} w-24`} placeholder="5.00" />
          </div>
        </>
      )}
      {isWealth && (
        <>
          <div className="flex flex-col gap-1">
            <label className="text-[11px] text-text-dim">本笔起投日</label>
            <input type="date" value={lotStartDate} onChange={e => setLotStartDate(e.target.value)}
              className={`${inp} w-36`} />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-[11px] text-text-dim">本笔年化 % (可空)</label>
            <input type="number" step="0.001" value={lotYield} onChange={e => setLotYield(e.target.value)}
              className={`${inp} w-24`} placeholder="2.15" />
          </div>
        </>
      )}
      <button onClick={save} disabled={busy || !((isFund || isCrypto) ? (p > 0 || (sNum > 0 && uNum > 0)) : p > 0)}
        className="px-4 py-1.5 rounded bg-bull text-bg font-semibold text-[12px] hover:opacity-90 cursor-pointer disabled:opacity-50">
        {busy ? '...' : '加仓'}
      </button>
      <button onClick={onCancel}
        className="px-3 py-1.5 rounded border border-border text-text-dim text-[12px] hover:text-text cursor-pointer">
        取消
      </button>
      {preview && (
        <div className="basis-full text-[11px] text-text-dim flex flex-wrap gap-x-4 gap-y-1 mt-1 pt-2 border-t border-border-subtle">
          {Object.entries(preview).map(([k, v]) => (
            <span key={k}>
              <span className="text-text-muted">{k}:</span>{' '}
              <span className="font-mono text-bull-bright">{v}</span>
            </span>
          ))}
        </div>
      )}
      {err && (
        <div className="basis-full text-[11px] text-bear-bright">{err}</div>
      )}
    </div>
  )
}
