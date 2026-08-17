import { useCallback } from 'react'
import { createPortal } from 'react-dom'
import { fetchJSON } from '../hooks/useApi'
import KlineChart from './KlineChart'

// 宏观指标 K 线放大图. item: {symbol, name, price, change_pct, prev_close, open?, high?, low?, amount?, kline?}
// 周期切换 + 真 K 线由 KlineChart 负责; 本组件只管外壳 + 口径统计行。
// items + onPick(symbol) 可选: 给一排横向滚动的切换钮(顶部指数条用它换看哪个指数),
// 不传就只显示 item 自己(宏观仪表盘点开某格的用法)。
export default function MacroKlineModal({ item, items, onPick, onClose }) {
  const sym = item?.symbol || ''

  const fetchByDays = useCallback(
    (days) => fetchJSON(`/api/market/macro/kline/${encodeURIComponent(sym)}?days=${days}`)
      .then(d => d?.kline || []).catch(() => []),
    [sym]
  )

  if (!item) return null

  // 指数点位保留两位小数 + 千分位(3983 看不出 3982.65 那 0.65 点); 商品/期货仍按量级取整
  const isIndex = /^(sh|sz|bj|hk|gb_|int_|znb_)/.test(sym)
  const fmtVal = (v) => {
    if (v == null) return '--'
    if (sym.startsWith('fx_')) return v.toFixed(4)
    if (isIndex) return v.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
    if (Math.abs(v) >= 1000) return v.toFixed(0)
    if (Math.abs(v) >= 100) return v.toFixed(1)
    return v.toFixed(2)
  }
  const fmtPct = (v) => v == null ? '--' : (v >= 0 ? '+' : '') + v.toFixed(2) + '%'
  const colorPct = (v) => v == null ? 'text-text-dim' : v >= 0 ? 'text-bear-bright' : 'text-bull-bright'
  // 成交额: 源给的是元(A股)/港元(港股), 按量级折成 万亿/亿/万
  const fmtAmt = (v) => {
    if (!v) return null
    if (v >= 1e12) return (v / 1e12).toFixed(2) + '万亿'
    if (v >= 1e8) return (v / 1e8).toFixed(1) + '亿'
    return (v / 1e4).toFixed(0) + '万'
  }

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
          <div className="flex items-baseline gap-2">
            <span className={`text-[13px] font-mono ${colorPct(item.change_pct)}`}>{fmtPct(item.change_pct)}</span>
            <button onClick={onClose}
              className="text-text-dim hover:text-text text-[18px] leading-none px-2 cursor-pointer">×</button>
          </div>
        </div>

        {/* 指数切换条: 横向滚动, 一行放不下就滑(A股 / 港股 / 美股 / 海外之间加竖线分组) */}
        {items?.length > 1 && (
          <div className="flex items-stretch gap-1 overflow-x-auto pb-2 mb-2 -mx-1 px-1">
            {items.map((it, i) => (
              <div key={it.symbol} className="flex items-stretch gap-1 shrink-0">
                {i > 0 && it.group !== items[i - 1].group && <div className="w-px bg-border self-stretch my-1" />}
                <button onClick={() => onPick?.(it.symbol)}
                  className={`shrink-0 text-left rounded-md border px-2 py-1 cursor-pointer transition-colors ${
                    it.symbol === sym ? 'border-accent bg-accent/10' : 'border-border hover:border-text-muted'}`}>
                  <div className="text-[10.5px] text-text-dim whitespace-nowrap">{it.name}</div>
                  <div className={`text-[11px] font-mono ${colorPct(it.change_pct)}`}>{fmtPct(it.change_pct)}</div>
                </button>
              </div>
            ))}
          </div>
        )}

        <KlineChart
          key={sym}
          fetchByDays={fetchByDays}
          initialSeries={item.kline || []}
          defaultDays={60}
          fmtVal={fmtVal}
          renderStats={renderStats}
          footerExtra={<>
            <span>昨收 <span className="text-text font-mono">{fmtVal(item.prev_close)}</span></span>
            {item.open != null && <span>今开 <span className="text-text font-mono">{fmtVal(item.open)}</span></span>}
            {item.high != null && <span>今高 <span className="text-text font-mono">{fmtVal(item.high)}</span></span>}
            {item.low != null && <span>今低 <span className="text-text font-mono">{fmtVal(item.low)}</span></span>}
            {fmtAmt(item.amount) && <span>成交额 <span className="text-text font-mono">{fmtAmt(item.amount)}</span></span>}
          </>}
        />
      </div>
    </div>,
    document.body
  )
}
