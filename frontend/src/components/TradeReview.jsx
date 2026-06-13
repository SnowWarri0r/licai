import { useState, useEffect } from 'react'
import { fetchJSON } from '../hooks/useApi'
import { fmtMoney } from '../helpers'

const pnlColor = (v) => v == null || v === 0 ? 'text-text-dim' : v > 0 ? 'text-bear-bright' : 'text-bull-bright'
const sign = (v) => (v >= 0 ? '+' : '') + fmtMoney(v)

export default function TradeReview() {
  const [d, setD] = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    fetchJSON('/api/portfolio/trade-review').then(setD).catch(() => {}).finally(() => setLoading(false))
  }, [])

  if (loading) return <div className="text-center py-6 text-text-dim text-[12px]">复盘计算中…</div>
  if (!d || !d.overview?.n_stocks) return null
  const o = d.overview

  return (
    <div className="bg-surface-2 border border-border rounded-xl p-4 md:p-5">
      <div className="flex items-baseline gap-2 mb-3">
        <h3 className="text-[14px] font-semibold text-text-bright m-0">交易复盘</h3>
        <span className="text-[10.5px] text-text-muted">A股全周期 · 客观回顾</span>
      </div>

      {/* 总览 */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-2 mb-3 text-[11px]">
        {[
          ['交易股票', `${o.n_stocks} 只`, 'text-text-bright'],
          ['胜率', `${Math.round(o.win_rate * 100)}%`, o.win_rate >= 0.5 ? 'text-bear-bright' : 'text-bull-bright'],
          ['已实现合计', sign(o.total_realized), pnlColor(o.total_realized)],
          ['平均持有', `${o.avg_hold_days} 天`, 'text-text-bright'],
        ].map(([label, val, cls], i) => (
          <div key={i} className="bg-surface-3 rounded-md px-2 py-1.5">
            <div className="text-text-dim text-[10px] mb-0.5">{label}</div>
            <div className={`font-mono font-semibold ${cls}`}>{val}</div>
          </div>
        ))}
      </div>

      {/* 观察 */}
      {(d.observations || []).length > 0 && (
        <ul className="space-y-1 mb-3">
          {d.observations.map((x, i) => (
            <li key={i} className="text-[12px] text-text-dim flex gap-1.5">
              <span className="text-accent shrink-0">·</span><span>{x}</span>
            </li>
          ))}
        </ul>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        {/* 盈亏榜 */}
        <div>
          <div className="text-[11px] text-text-muted mb-1.5 tracking-wider">已实现盈亏榜</div>
          <div className="space-y-1">
            {(d.best || []).map((b, i) => (
              <Row key={'b' + i} name={b.name} val={b.realized} />
            ))}
            {(d.worst || []).map((w, i) => (
              <Row key={'w' + i} name={w.name} val={w.realized} />
            ))}
          </div>
        </div>

        {/* 做T榜 */}
        <div>
          <div className="text-[11px] text-text-muted mb-1.5 tracking-wider">做T频繁榜 (买卖次数 · 净已实现)</div>
          <div className="space-y-1">
            {(d.active_t || []).map((a, i) => (
              <div key={i} className="flex items-baseline gap-2 text-[12px]">
                <span className="text-text-bright min-w-[64px] shrink-0 truncate">{a.name}</span>
                <span className="font-mono text-[10.5px] text-text-muted shrink-0">{a.n_buy}买{a.n_sell}卖</span>
                <span className={`font-mono text-[11.5px] ml-auto ${pnlColor(a.realized)}`}>{sign(a.realized)}</span>
              </div>
            ))}
          </div>
        </div>
      </div>

      <div className="text-[10px] text-text-muted pt-2 mt-2 border-t border-border-subtle">
        仅客观回顾历史交易，不构成任何买卖建议
      </div>
    </div>
  )
}

function Row({ name, val }) {
  return (
    <div className="flex items-baseline gap-2 text-[12px]">
      <span className="text-text-bright min-w-[64px] shrink-0 truncate">{name}</span>
      <span className={`font-mono text-[11.5px] ml-auto ${pnlColor(val)}`}>{sign(val)}</span>
    </div>
  )
}
