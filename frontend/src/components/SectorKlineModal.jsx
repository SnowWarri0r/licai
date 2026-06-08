import { useCallback } from 'react'
import { createPortal } from 'react-dom'
import { fetchJSON } from '../hooks/useApi'
import KlineChart from './KlineChart'

// 板块雷达 K 线放大图. 周期切换 + 真 K 线由 KlineChart 负责。
// 数据源: A 股走 THS 板块名(可达), HK/US 走 eastmoney(本网络可能不可达 → 回退已加载的 60d)。
export default function SectorKlineModal({ sector, market, onClose }) {
  // A 股用板块名做 key (THS), 港美股用 ETF/指数 symbol
  const key = (market === 'A' ? sector?.name : (sector?.symbol || sector?.name)) || ''

  const fetchByDays = useCallback(
    (days) => fetchJSON(`/api/sector/kline?market=${encodeURIComponent(market || 'A')}&key=${encodeURIComponent(key)}&days=${days}`)
      .then(d => d?.kline || []).catch(() => []),
    [market, key]
  )

  if (!sector) return null

  const fmtVal = (v) => {
    if (v == null) return '--'
    if (Math.abs(v) >= 1000) return v.toFixed(0)
    if (Math.abs(v) >= 100) return v.toFixed(1)
    return v.toFixed(2)
  }
  const fmtPct = (v) => v == null ? '--' : (v >= 0 ? '+' : '') + v.toFixed(2) + '%'
  const colorPct = (v) => v == null ? 'text-text-dim' : v >= 0 ? 'text-bear-bright' : 'text-bull-bright'
  const marketLabel = market === 'A' ? 'A 股' : market === 'HK' ? '港股' : market === 'US' ? '美股' : ''

  const renderStats = (series, periodPct) => {
    const boxes = [
      ['1 日', sector.change_1d],
      ['5 日', sector.change_5d],
      ['30 日', sector.change_30d],
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
            <h3 className="text-[15px] font-semibold text-text-bright m-0">{sector.name}</h3>
            {marketLabel && (
              <span className="text-[10.5px] px-1.5 py-[1px] rounded border border-info/40 bg-info/10 text-info">{marketLabel}</span>
            )}
            {sector.symbol && <span className="text-[11px] font-mono text-text-dim">{sector.symbol}</span>}
            {sector.held && (
              <span className="text-[10px] px-1 py-0 rounded bg-accent/20 text-accent border border-accent/40">持仓</span>
            )}
          </div>
          <button onClick={onClose}
            className="text-text-dim hover:text-text text-[18px] leading-none px-2 cursor-pointer">×</button>
        </div>

        <KlineChart
          fetchByDays={fetchByDays}
          initialSeries={sector.kline_tail || []}
          defaultDays={60}
          fmtVal={fmtVal}
          renderStats={renderStats}
          footerExtra={sector.etf_code && (
            <span>兜底 ETF <span className="text-text font-mono">{sector.etf_code}</span>
              {sector.etf_name && <span className="ml-1 text-text-muted">{sector.etf_name}</span>}
            </span>
          )}
        />
      </div>
    </div>,
    document.body
  )
}
