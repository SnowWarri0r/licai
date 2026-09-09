import { useState } from 'react'
import { fmtMoney, fmtPct, isOnchainEtf, priceColor } from '../../helpers'
import Tooltip from '../Tooltip'
import { FxHint } from './atoms'
import { formatCurrencyMoney } from './helpers'

// ============================================================
// Summary strip
// ============================================================
export function SummaryStrip({ agg, aShareClosed, realized, todayPnl }) {
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
    (() => {
      // 当日盈亏优先用后端券商口径(含今日清仓的已实现 + 今日新建仓以买入价为基准);
      // 端点不可用时退回前端的纯市值变动口径, 并把标签退回「今日浮动」以免口径不符。
      const useSrv = todayPnl && typeof todayPnl.total === 'number'
      const v = useSrv ? todayPnl.total : agg.totalToday
      const closedN = useSrv ? (todayPnl.items || []).filter(i => i.closed_today).length : 0
      const openedN = useSrv ? (todayPnl.items || []).filter(i => i.opened_today).length : 0
      const est = useSrv ? (todayPnl.estimated_part || 0) : 0
      const unk = useSrv ? (todayPnl.unknown || []) : []
      return {
        label: (useSrv ? '今日盈亏' : '今日浮动') + (aShareClosed ? ' (A股闭市)' : ''),
        val: `${v >= 0 ? '+' : ''}${fmtMoney(v)}`,
        color: priceColor(v),
        note: useSrv && (closedN || openedN)
          ? [closedN ? `含今日清仓 ${closedN} 笔` : '', openedN ? `新建 ${openedN} 笔` : '']
              .filter(Boolean).join(' · ')
          : null,
        tooltip: useSrv ? (
          <div className="space-y-1.5">
            <div>当日盈亏 = 现市值 + 今日卖出所得 − 昨收市值 − 今日买入成本</div>
            <div className="text-text-dim">
              今日清仓的已实现算在内; 今天新建的仓以买入价(而非昨收)为基准。金额含手续费。
            </div>
            {(todayPnl.items || []).filter(i => i.today_pnl != null && Math.abs(i.today_pnl) >= 1)
              .slice(0, 8).map(i => (
              <div key={i.code} className="flex gap-2 justify-between font-mono text-[10.5px]">
                <span className="text-text-dim">
                  {i.name?.slice(0, 14) || i.code}
                  {i.closed_today ? ' 清仓' : i.opened_today ? ' 新建' : ''}
                  {i.estimated ? ' 估' : ''}
                </span>
                <span className={priceColor(i.today_pnl)}>
                  {i.today_pnl >= 0 ? '+' : ''}{fmtMoney(i.today_pnl)}
                </span>
              </div>
            ))}
            {est !== 0 && (
              <div className="text-text-muted text-[10px]">
                其中 {fmtMoney(est)} 来自净值 T+1 的场外基金, 按底层代理估, 净值公布后修正
              </div>
            )}
            {unk.length > 0 && (
              <div className="text-text-muted text-[10px]">
                {unk.length} 只净值滞后且无代理标的, 未计入(不冒充)
              </div>
            )}
          </div>
        ) : null,
      }
    })(),
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

// 已清仓股票区块 — 列出持仓已删但还有交易历史的标的, 看到累计已实现盈亏 + 翻历史
export function ClosedPositionsBlock({ items, onHistory, onKline }) {
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
