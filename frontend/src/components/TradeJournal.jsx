import { useState, useEffect } from 'react'
import { fetchJSON } from '../hooks/useApi'
import Tooltip from './Tooltip'

// A股: 涨红 跌绿
const pctColor = (v) => v == null || v === 0 ? 'text-text-dim' : v > 0 ? 'text-bear-bright' : 'text-bull-bright'

export default function TradeJournal() {
  const [d, setD] = useState(null)
  const [loading, setLoading] = useState(true)
  const [filter, setFilter] = useState('all')  // all / buy / sell

  useEffect(() => {
    fetchJSON('/api/portfolio/trade-journal?limit=120').then(setD).catch(() => {}).finally(() => setLoading(false))
  }, [])

  if (loading) return <div className="text-center py-6 text-text-dim text-[12px]">逐笔复盘计算中…</div>
  if (!d || !d.total) return null

  const trades = (d.trades || []).filter(t => filter === 'all' || t.kind === filter)

  return (
    <div className="bg-surface-2 border border-border rounded-xl p-4 md:p-5">
      <div className="flex items-baseline justify-between gap-2 mb-3 flex-wrap">
        <div className="flex items-baseline gap-2">
          <h3 className="text-[14px] font-semibold text-text-bright m-0">逐笔复盘</h3>
          <Tooltip content="对照现价看每笔: 买入命中=现价高于你买入价(买在了低位); 卖出命中=现价低于你卖出价(卖完躲过下跌)。">
            <span className="text-[10.5px] text-text-muted cursor-help">每笔 vs 现价 ⓘ</span>
          </Tooltip>
        </div>
        <div className="flex gap-1">
          {[['all', '全部'], ['buy', '买入'], ['sell', '卖出']].map(([k, label]) => (
            <button key={k} onClick={() => setFilter(k)}
              className={`text-[11px] px-2 py-0.5 rounded border ${filter === k ? 'bg-accent/20 text-accent border-accent/40' : 'bg-surface-3 text-text-dim border-transparent hover:text-text'}`}>
              {label}
            </button>
          ))}
        </div>
      </div>

      {/* 命中率 */}
      <div className="grid grid-cols-2 gap-2 mb-3 text-[11px]">
        <div className="bg-surface-3 rounded-md px-2.5 py-1.5">
          <div className="text-text-dim text-[10px] mb-0.5">买入命中率 <span className="text-text-muted">(买在低位)</span></div>
          <div className="font-mono font-semibold text-text-bright">
            {Math.round(d.buy_hit_rate * 100)}% <span className="text-text-muted text-[10px]">{d.buy_hit}/{d.buy_count}</span>
          </div>
        </div>
        <div className="bg-surface-3 rounded-md px-2.5 py-1.5">
          <div className="text-text-dim text-[10px] mb-0.5">卖出命中率 <span className="text-text-muted">(卖完躲跌)</span></div>
          <div className="font-mono font-semibold text-text-bright">
            {Math.round(d.sell_hit_rate * 100)}% <span className="text-text-muted text-[10px]">{d.sell_hit}/{d.sell_count}</span>
          </div>
        </div>
      </div>

      {/* 逐笔列表 */}
      <div className="space-y-0.5 max-h-[420px] overflow-y-auto pr-1">
        {trades.map((t, i) => (
          <div key={i} className="flex items-center gap-2 text-[11.5px] py-1 border-b border-border-subtle/50">
            <span className="font-mono text-[10px] text-text-muted shrink-0 w-[58px]">{(t.date || '').slice(5)}</span>
            <span className={`text-[10px] px-1 rounded shrink-0 ${t.kind === 'buy' ? 'bg-bear/15 text-bear-bright' : 'bg-bull/15 text-bull-bright'}`}>
              {t.kind === 'buy' ? '买' : '卖'}
            </span>
            <span className="text-text-bright truncate min-w-[60px] max-w-[90px]">{t.name}</span>
            <span className="font-mono text-text-dim text-[10.5px] shrink-0">@{t.price}<span className="text-text-muted">×{Math.round(t.shares)}</span></span>
            <span className="font-mono text-[10px] text-text-muted shrink-0 hidden sm:inline">现 {t.current}</span>
            <span className={`font-mono text-[11px] ml-auto shrink-0 ${pctColor(t.pct)}`}>{t.pct >= 0 ? '+' : ''}{t.pct}%</span>
            <span className={`text-[11px] shrink-0 w-[14px] text-center ${t.hit ? 'text-accent' : 'text-text-muted'}`}>
              {t.hit ? '✓' : '·'}
            </span>
          </div>
        ))}
      </div>
      {d.total > trades.length && filter === 'all' && (
        <div className="text-[10px] text-text-muted mt-1.5">共 {d.total} 笔，显示最近 {trades.length} 笔</div>
      )}
      <div className="text-[10px] text-text-muted pt-2 mt-1 border-t border-border-subtle">仅客观回顾，不构成买卖建议</div>
    </div>
  )
}
