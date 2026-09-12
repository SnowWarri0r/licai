import { fmtMoney, fmtPrice, isOnchainEtf, priceColor } from '../../helpers'
import Tooltip from '../Tooltip'
import { TYPE_COLOR, TYPE_META, TYPE_ORDER } from './constants'
import { currencySymbol, fxSourceLabel } from './helpers'

export function FxHint({ extra, children }) {
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

// ============================================================
// Small row primitives
// ============================================================
export function TypeChip({ type, compact = false }) {
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

export function MarketChip({ market }) {
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

export function WeightBar({ weight, color, width = 48 }) {
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

export function TodayPulse({ change, width = 44 }) {
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
export function ProxyPulse({ change, label, fallbackToday, details, width = 44 }) {
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

export function TypeMiniInfo({ row }) {
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
export function RowActions({ row, visible, onEdit, onHistory, onRemove, onAddLot, onReduceLot, onShowActions, onKline, onThesis, hasThesis, onQuality, onCashAdjust }) {
  // 桌面: hover 才显示 (opacity 控制); 移动: 始终显示, 1 字按钮
  const btnBase = 'rounded border border-border-med bg-surface-2 text-text-dim ' +
    'hover:border-accent hover:text-accent transition-colors cursor-pointer whitespace-nowrap'
  const thesisAction = { short: hasThesis ? '记✓' : '记', label: hasThesis ? '逻辑✓' : '逻辑',
    fn: () => onThesis?.(row), highlight: hasThesis }
  const actions = []
  if (row.type === 'A') {
    actions.push({ short: '线', label: 'K 线', fn: () => onKline?.(row._raw) })
    actions.push({ short: '史', label: '历史', fn: () => onHistory?.(row._raw) })
    // 质地只对 A 股给: 7 条去劣指标算的是工商企业的财报, 基金/现金/理财无从谈起
    actions.push({ short: '质', label: '质地', fn: () => onQuality?.(row) })
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
    if (row.type === 'M') {
      // 现金就是一个余额数字: 直接调整(支持 +/- 增减或输新余额), 免交易流水逻辑
      actions.push({ short: '调', label: '调余额', fn: () => onCashAdjust?.(row) })
    } else if (row.type === 'F' || row.type === 'C' || row.type === 'W') {
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
// Allocation donut
// ============================================================
export function AllocationDonut({ groups, totalMv }) {
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
