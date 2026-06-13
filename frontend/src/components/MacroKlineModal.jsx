import { useCallback } from 'react'
import { createPortal } from 'react-dom'
import { fetchJSON } from '../hooks/useApi'
import KlineChart from './KlineChart'

// 宏观指标 K 线放大图. item: {symbol, name, price, change_pct, prev_close, kline?}
// 周期切换 + 真 K 线由 KlineChart 负责; 本组件只管外壳 + 口径统计行。
export default function MacroKlineModal({ item, onClose }) {
  const sym = item?.symbol || ''

  const fetchByDays = useCallback(
    (days) => fetchJSON(`/api/market/macro/kline/${encodeURIComponent(sym)}?days=${days}`)
      .then(d => d?.kline || []).catch(() => []),
    [sym]
  )

  if (!item) return null

  const fmtVal = (v) => {
    if (v == null) return '--'
    if (sym.startsWith('fx_')) return v.toFixed(4)
    if (Math.abs(v) >= 1000) return v.toFixed(0)
    if (Math.abs(v) >= 100) return v.toFixed(1)
    return v.toFixed(2)
  }
  const fmtPct = (v) => v == null ? '--' : (v >= 0 ? '+' : '') + v.toFixed(2) + '%'
  const colorPct = (v) => v == null ? 'text-text-dim' : v >= 0 ? 'text-bear-bright' : 'text-bull-bright'

  const renderStats = (series, periodPct) => {
    const closes = series.map(d => d.close).filter(c => c > 0)
    const calcPct = (lb) => {
      if (closes.length < lb + 1) return null
      const a = closes[closes.length - 1 - lb], b = closes[closes.length - 1]
      return a > 0 ? ((b / a) - 1) * 100 : null
    }
    const boxes = [
      ['今日', item.change_pct],
      ['5 日', calcPct(5)],
      ['20 日', calcPct(20)],
      [`${series.length} 日`, periodPct],
    ]
    return (
      <div className="grid grid-cols-2 md:grid-cols-4 gap-2 mb-3 text-[11px]">
        {boxes.map(([label, v], i) => (
          <div key={i} className="bg-surface-3 rounded-md px-2 py-1.5">
            <div className="text-text-dim text-[10px] mb-0.5">{label}</div>
            <div className={`font-mono font-semibold ${colorPct(v)}`}>{fmtPct(v)}</div>
          </div>
        ))}
      </div>
    )
  }

  return createPortal(
    <div className="fixed inset-0 z-[200] flex items-center justify-center bg-black/60 backdrop-blur-sm" onClick={onClose}>
      <div className="bg-surface-2 border border-border rounded-xl p-4 md:p-5 w-[760px] max-w-[95vw]"
        onClick={e => e.stopPropagation()}>
        <div className="flex items-baseline justify-between gap-3 mb-3 flex-wrap">
          <div className="flex items-baseline gap-2 flex-wrap">
            <h3 className="text-[15px] font-semibold text-text-bright m-0">{item.name}</h3>
            <span className="text-[11px] font-mono text-text-dim">{item.symbol}</span>
            <span className="text-[14px] font-mono text-text-bright">{fmtVal(item.price)}</span>
          </div>
          <button onClick={onClose}
            className="text-text-dim hover:text-text text-[18px] leading-none px-2 cursor-pointer">×</button>
        </div>

        <KlineChart
          fetchByDays={fetchByDays}
          initialSeries={item.kline || []}
          defaultDays={60}
          fmtVal={fmtVal}
          renderStats={renderStats}
          footerExtra={<span>昨收 <span className="text-text font-mono">{fmtVal(item.prev_close)}</span></span>}
        />
      </div>
    </div>,
    document.body
  )
}
