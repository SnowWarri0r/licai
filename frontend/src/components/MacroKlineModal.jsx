import { useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { fetchJSON } from '../hooks/useApi'

// 宏观指标 60 日 K 线放大图. 接受 item: {symbol, name, price, change_pct, prev_close}
// kline 优先用 prop 里的 (从 MacroDashboard 已经拿到的 30 日), 同时异步拉 60 日扩展.
export default function MacroKlineModal({ item, onClose }) {
  const [hover, setHover] = useState(null)
  const [series, setSeries] = useState(item?.kline || [])
  const [loading, setLoading] = useState(false)
  const svgRef = useRef(null)

  useEffect(() => {
    if (!item?.symbol) return
    setLoading(true)
    fetchJSON(`/api/market/macro/kline/${encodeURIComponent(item.symbol)}?days=60`)
      .then(d => {
        const k = d?.kline || []
        if (k.length > 0) setSeries(k)
      })
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [item?.symbol])

  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const closes = series.map(d => d.close).filter(c => c > 0)
  const min = closes.length ? Math.min(...closes) : 0
  const max = closes.length ? Math.max(...closes) : 1
  const range = max - min || 1
  const start = closes[0]
  const end = closes[closes.length - 1]
  const periodPct = start && end ? ((end / start) - 1) * 100 : null

  const W = 720, H = 320, P = { l: 64, r: 16, t: 16, b: 28 }
  const innerW = W - P.l - P.r
  const innerH = H - P.t - P.b

  const points = useMemo(() => {
    if (series.length < 2) return []
    return series.map((d, i) => {
      const x = P.l + (i / (series.length - 1)) * innerW
      const y = P.t + innerH - ((d.close - min) / range) * innerH
      return { ...d, x, y, i }
    })
  }, [series, innerH, innerW, min, range])

  const linePath = useMemo(() =>
    points.length ? points.map((p, i) => (i === 0 ? 'M' : 'L') + p.x.toFixed(1) + ' ' + p.y.toFixed(1)).join(' ') : ''
  , [points])

  const yTicks = useMemo(() => {
    if (!closes.length) return []
    const N = 4
    const step = range / N
    return Array.from({ length: N + 1 }, (_, i) => {
      const v = min + step * i
      return { v, y: P.t + innerH - ((v - min) / range) * innerH }
    })
  }, [closes.length, min, range, innerH])

  const xTicks = useMemo(() => {
    if (points.length < 2) return []
    const idxs = [0, Math.floor((points.length - 1) * 0.25), Math.floor((points.length - 1) * 0.5),
                  Math.floor((points.length - 1) * 0.75), points.length - 1]
    return idxs.map(i => points[i])
  }, [points])

  const handleMouseMove = (e) => {
    if (!svgRef.current || !points.length) return
    const rect = svgRef.current.getBoundingClientRect()
    const cursorX = ((e.clientX - rect.left) / rect.width) * W
    if (cursorX < P.l || cursorX > P.l + innerW) {
      setHover(null); return
    }
    const i = Math.round(((cursorX - P.l) / innerW) * (points.length - 1))
    setHover(points[Math.max(0, Math.min(points.length - 1, i))])
  }

  const isUp = end >= start
  const lineColor = isUp ? '#cf5c5c' : '#5fa86c'
  const fillColor = isUp ? 'rgba(207,92,92,0.10)' : 'rgba(95,168,108,0.10)'

  const areaPath = useMemo(() => {
    if (!points.length) return ''
    const top = points.map((p, i) => (i === 0 ? 'M' : 'L') + p.x.toFixed(1) + ' ' + p.y.toFixed(1)).join(' ')
    return top + ` L ${points[points.length - 1].x.toFixed(1)} ${(P.t + innerH).toFixed(1)} L ${points[0].x.toFixed(1)} ${(P.t + innerH).toFixed(1)} Z`
  }, [points, innerH])

  if (!item) return null

  // 价格格式化: 汇率 4 位小数, 商品/指数按量级
  const fmtVal = (v) => {
    if (v == null) return '--'
    if (item.symbol.startsWith('fx_')) return v.toFixed(4)
    if (Math.abs(v) >= 1000) return v.toFixed(0)
    if (Math.abs(v) >= 100) return v.toFixed(1)
    return v.toFixed(2)
  }
  const fmtPct = (v) => v == null ? '--' : (v >= 0 ? '+' : '') + v.toFixed(2) + '%'
  const colorPct = (v) => v == null ? 'text-text-dim' : v >= 0 ? 'text-bear-bright' : 'text-bull-bright'

  // 多段时间窗口涨跌幅
  const calcPct = (lookback) => {
    if (closes.length < lookback + 1) return null
    const a = closes[closes.length - 1 - lookback]
    const b = closes[closes.length - 1]
    return a > 0 ? ((b / a) - 1) * 100 : null
  }
  const pct1d = item.change_pct
  const pct5d = calcPct(5)
  const pct20d = calcPct(20)

  return createPortal(
    <div className="fixed inset-0 z-[200] flex items-center justify-center bg-black/60 backdrop-blur-sm" onClick={onClose}>
      <div className="bg-surface-2 border border-border rounded-xl p-4 md:p-5 w-[760px] max-w-[95vw]"
        onClick={e => e.stopPropagation()}>
        <div className="flex items-baseline justify-between gap-3 mb-3 flex-wrap">
          <div className="flex items-baseline gap-2 flex-wrap">
            <h3 className="text-[15px] font-semibold text-text-bright m-0">{item.name}</h3>
            <span className="text-[11px] font-mono text-text-dim">{item.symbol}</span>
            <span className="text-[14px] font-mono text-text-bright">{fmtVal(item.price)}</span>
            {loading && <span className="text-[10.5px] text-text-muted">加载 60 日…</span>}
          </div>
          <button onClick={onClose}
            className="text-text-dim hover:text-text text-[18px] leading-none px-2 cursor-pointer">×</button>
        </div>

        <div className="grid grid-cols-2 md:grid-cols-4 gap-2 mb-3 text-[11px]">
          <div className="bg-surface-3 rounded-md px-2 py-1.5">
            <div className="text-text-dim text-[10px] mb-0.5">今日</div>
            <div className={`font-mono font-semibold ${colorPct(pct1d)}`}>{fmtPct(pct1d)}</div>
          </div>
          <div className="bg-surface-3 rounded-md px-2 py-1.5">
            <div className="text-text-dim text-[10px] mb-0.5">5 日</div>
            <div className={`font-mono font-semibold ${colorPct(pct5d)}`}>{fmtPct(pct5d)}</div>
          </div>
          <div className="bg-surface-3 rounded-md px-2 py-1.5">
            <div className="text-text-dim text-[10px] mb-0.5">20 日</div>
            <div className={`font-mono font-semibold ${colorPct(pct20d)}`}>{fmtPct(pct20d)}</div>
          </div>
          <div className="bg-surface-3 rounded-md px-2 py-1.5">
            <div className="text-text-dim text-[10px] mb-0.5">{series.length} 日</div>
            <div className={`font-mono font-semibold ${colorPct(periodPct)}`}>{fmtPct(periodPct)}</div>
          </div>
        </div>

        <div className="bg-surface-3 rounded-md p-2 relative">
          {points.length < 2 ? (
            <div className="h-[320px] flex items-center justify-center text-text-dim text-[12px]">
              {loading ? '加载中…' : '暂无 K 线数据 (数据源限频, 稍后重试)'}
            </div>
          ) : (
            <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} className="w-full h-auto select-none cursor-crosshair"
              onMouseMove={handleMouseMove} onMouseLeave={() => setHover(null)}>
              {yTicks.map((t, i) => (
                <g key={'y'+i}>
                  <line x1={P.l} y1={t.y} x2={W - P.r} y2={t.y}
                    stroke="var(--color-border-subtle)" strokeWidth="1"
                    strokeDasharray={i === 0 || i === yTicks.length - 1 ? '0' : '2 3'} />
                  <text x={P.l - 6} y={t.y + 3} fontSize="10" fill="var(--color-text-dim)" textAnchor="end" fontFamily="monospace">
                    {fmtVal(t.v)}
                  </text>
                </g>
              ))}
              {xTicks.map((t, i) => (
                <text key={'x'+i} x={t.x} y={H - 8} fontSize="10" fill="var(--color-text-dim)" textAnchor="middle" fontFamily="monospace">
                  {(t.date || '').slice(5)}
                </text>
              ))}
              <path d={areaPath} fill={fillColor} />
              <path d={linePath} fill="none" stroke={lineColor} strokeWidth="1.5" />
              {hover && (
                <g>
                  <line x1={hover.x} y1={P.t} x2={hover.x} y2={P.t + innerH}
                    stroke="var(--color-text-muted)" strokeWidth="1" strokeDasharray="2 3" />
                  <line x1={P.l} y1={hover.y} x2={W - P.r} y2={hover.y}
                    stroke="var(--color-text-muted)" strokeWidth="1" strokeDasharray="2 3" />
                  <circle cx={hover.x} cy={hover.y} r="3.5" fill={lineColor} stroke="var(--color-bg)" strokeWidth="1.5" />
                </g>
              )}
            </svg>
          )}
          {hover && (
            <div className="absolute top-2 right-2 bg-surface-2 border border-border-med rounded-md px-2.5 py-1.5 text-[11px] font-mono pointer-events-none">
              <div className="text-text-dim">{hover.date}</div>
              <div className="text-text-bright">{fmtVal(hover.close)}</div>
              {start > 0 && (
                <div className={colorPct(((hover.close / start) - 1) * 100)}>
                  {fmtPct(((hover.close / start) - 1) * 100)}
                </div>
              )}
            </div>
          )}
        </div>

        <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-text-dim">
          <span>区间高 <span className="text-text font-mono">{fmtVal(max)}</span></span>
          <span>区间低 <span className="text-text font-mono">{fmtVal(min)}</span></span>
          <span>昨收 <span className="text-text font-mono">{fmtVal(item.prev_close)}</span></span>
          <span className="text-text-muted ml-auto">仅展示数据，不构成投资建议</span>
        </div>
      </div>
    </div>,
    document.body
  )
}
