import { useEffect, useMemo, useRef, useState } from 'react'

// 共用 K 线图: 周期切换(30/60/半年/1年) + 真 K 线蜡烛(有 OHLC 时)/折线(仅 close)。
// 用在宏观放大图 (MacroKlineModal) 和板块雷达放大图 (SectorKlineModal)。
// props:
//   fetchByDays(days) -> Promise<[{date,close,open?,high?,low?}]>  按周期拉数据
//   initialSeries: 打开时先显示的已有数据 (秒出, 不留白)
//   defaultDays: 默认周期 (默认 60)
//   fmtVal(v): 价格格式化
//   renderStats(series, periodPct, loading): 顶部统计行 (各调用方自带口径)
//   footerExtra: 区间高/低 右侧附加内容

const PERIODS = [
  { label: '30日', days: 30 },
  { label: '60日', days: 60 },
  { label: '半年', days: 120 },
  { label: '1年', days: 250 },
]

export default function KlineChart({ fetchByDays, initialSeries = [], defaultDays = 60, fmtVal, renderStats, footerExtra }) {
  const [days, setDays] = useState(defaultDays)
  const [series, setSeries] = useState(initialSeries)
  const [loading, setLoading] = useState(false)
  const [hover, setHover] = useState(null)
  const svgRef = useRef(null)
  const fetchRef = useRef(fetchByDays)
  fetchRef.current = fetchByDays

  // 周期变化(含首次)就拉数据; 只依赖 days, 避免父级重建 fetch 函数导致死循环
  useEffect(() => {
    let alive = true
    setLoading(true)
    Promise.resolve(fetchRef.current(days))
      .then(k => { if (alive && Array.isArray(k) && k.length) setSeries(k) })
      .catch(() => {})
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [days])

  const closes = series.map(d => d.close).filter(c => c > 0)
  const hasCandle = series.length >= 2 &&
    series.filter(d => d.high != null && d.low != null && d.open != null).length >= series.length * 0.6
  const highs = series.map(d => (hasCandle ? (d.high ?? d.close) : d.close)).filter(c => c > 0)
  const lows = series.map(d => (hasCandle ? (d.low ?? d.close) : d.close)).filter(c => c > 0)
  const min = lows.length ? Math.min(...lows) : 0
  const max = highs.length ? Math.max(...highs) : 1
  const range = max - min || 1
  const start = closes[0]
  const end = closes[closes.length - 1]
  const periodPct = start && end ? ((end / start) - 1) * 100 : null

  const W = 720, H = 320, P = { l: 64, r: 16, t: 16, b: 28 }
  const innerW = W - P.l - P.r
  const innerH = H - P.t - P.b
  const yOf = (v) => P.t + innerH - ((v - min) / range) * innerH

  const points = useMemo(() => {
    if (series.length < 2) return []
    return series.map((d, i) => ({
      ...d,
      x: P.l + (i / (series.length - 1)) * innerW,
      y: yOf(d.close),
      yOpen: d.open != null ? yOf(d.open) : null,
      yHigh: d.high != null ? yOf(d.high) : null,
      yLow: d.low != null ? yOf(d.low) : null,
      i,
    }))
  }, [series, innerH, innerW, min, range])

  const candleW = points.length > 1
    ? Math.max(1.2, Math.min(11, (innerW / points.length) * 0.62))
    : 4

  const linePath = useMemo(() =>
    points.length ? points.map((p, i) => (i === 0 ? 'M' : 'L') + p.x.toFixed(1) + ' ' + p.y.toFixed(1)).join(' ') : ''
  , [points])
  const areaPath = useMemo(() => {
    if (!points.length) return ''
    const top = points.map((p, i) => (i === 0 ? 'M' : 'L') + p.x.toFixed(1) + ' ' + p.y.toFixed(1)).join(' ')
    return top + ` L ${points[points.length - 1].x.toFixed(1)} ${(P.t + innerH).toFixed(1)} L ${points[0].x.toFixed(1)} ${(P.t + innerH).toFixed(1)} Z`
  }, [points, innerH])

  const yTicks = useMemo(() => {
    if (!closes.length) return []
    const N = 4, step = range / N
    return Array.from({ length: N + 1 }, (_, i) => {
      const v = min + step * i
      return { v, y: yOf(v) }
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
    if (cursorX < P.l || cursorX > P.l + innerW) { setHover(null); return }
    const i = Math.round(((cursorX - P.l) / innerW) * (points.length - 1))
    setHover(points[Math.max(0, Math.min(points.length - 1, i))])
  }

  const isUp = end >= start
  const lineColor = isUp ? '#cf5c5c' : '#5fa86c'
  const fillColor = isUp ? 'rgba(207,92,92,0.10)' : 'rgba(95,168,108,0.10)'
  const colorPct = (v) => v == null ? 'text-text-dim' : v >= 0 ? 'text-bear-bright' : 'text-bull-bright'
  const fmtPct = (v) => v == null ? '--' : (v >= 0 ? '+' : '') + v.toFixed(2) + '%'

  return (
    <div>
      {/* 周期切换 */}
      <div className="flex items-center gap-1 mb-2">
        {PERIODS.map(p => (
          <button key={p.days} onClick={() => setDays(p.days)}
            className={`text-[11px] px-2 py-0.5 rounded font-mono transition-colors cursor-pointer border ${
              days === p.days
                ? 'bg-accent/20 text-accent border-accent/40'
                : 'bg-surface-3 text-text-dim border-transparent hover:text-text'}`}>
            {p.label}
          </button>
        ))}
        {loading && <span className="text-[10.5px] text-text-muted ml-1">加载中…</span>}
        <span className="text-[10.5px] text-text-muted ml-auto font-mono">{series.length} 个交易日</span>
      </div>

      {renderStats && renderStats(series, periodPct, loading)}

      <div className="bg-surface-3 rounded-md p-2 relative">
        {points.length < 2 ? (
          <div className="h-[320px] flex items-center justify-center text-text-dim text-[12px]">
            {loading ? '加载中…' : '暂无 K 线数据 (数据源不可达, 稍后重试)'}
          </div>
        ) : (
          <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} className="w-full h-auto select-none cursor-crosshair"
            onMouseMove={handleMouseMove} onMouseLeave={() => setHover(null)}>
            {yTicks.map((t, i) => (
              <g key={'y' + i}>
                <line x1={P.l} y1={t.y} x2={W - P.r} y2={t.y}
                  stroke="var(--color-border-subtle)" strokeWidth="1"
                  strokeDasharray={i === 0 || i === yTicks.length - 1 ? '0' : '2 3'} />
                <text x={P.l - 6} y={t.y + 3} fontSize="10" fill="var(--color-text-dim)" textAnchor="end" fontFamily="monospace">
                  {fmtVal(t.v)}
                </text>
              </g>
            ))}
            {xTicks.map((t, i) => (
              <text key={'x' + i} x={t.x} y={H - 8} fontSize="10" fill="var(--color-text-dim)" textAnchor="middle" fontFamily="monospace">
                {(t.date || '').slice(5)}
              </text>
            ))}
            {hasCandle ? (
              points.map((p, i) => {
                if (p.yHigh == null || p.yLow == null || p.yOpen == null) return null
                const up = p.close >= p.open
                const col = up ? '#cf5c5c' : '#5fa86c'   // A股口径: 涨红 跌绿
                const bodyTop = Math.min(p.yOpen, p.y)
                const bodyH = Math.max(1, Math.abs(p.yOpen - p.y))
                return (
                  <g key={'cdl' + i}>
                    <line x1={p.x} y1={p.yHigh} x2={p.x} y2={p.yLow} stroke={col} strokeWidth="1" />
                    <rect x={p.x - candleW / 2} y={bodyTop} width={candleW} height={bodyH} fill={col} stroke={col} strokeWidth="0.5" />
                  </g>
                )
              })
            ) : (
              <>
                <path d={areaPath} fill={fillColor} />
                <path d={linePath} fill="none" stroke={lineColor} strokeWidth="1.5" />
              </>
            )}
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
            {hover.open != null && hover.high != null ? (
              <div className="grid grid-cols-2 gap-x-2 gap-y-0.5 mt-0.5">
                <span className="text-text-dim">开 <span className="text-text">{fmtVal(hover.open)}</span></span>
                <span className="text-text-dim">高 <span className="text-bear-bright">{fmtVal(hover.high)}</span></span>
                <span className="text-text-dim">收 <span className="text-text-bright">{fmtVal(hover.close)}</span></span>
                <span className="text-text-dim">低 <span className="text-bull-bright">{fmtVal(hover.low)}</span></span>
              </div>
            ) : (
              <div className="text-text-bright">{fmtVal(hover.close)}</div>
            )}
            {start > 0 && (
              <div className={`mt-0.5 ${colorPct(((hover.close / start) - 1) * 100)}`}>
                累计 {fmtPct(((hover.close / start) - 1) * 100)}
              </div>
            )}
          </div>
        )}
      </div>

      <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-text-dim">
        <span>区间高 <span className="text-text font-mono">{fmtVal(max)}</span></span>
        <span>区间低 <span className="text-text font-mono">{fmtVal(min)}</span></span>
        {footerExtra}
        <span className="text-text-muted ml-auto">仅展示数据，不构成投资建议</span>
      </div>
    </div>
  )
}
