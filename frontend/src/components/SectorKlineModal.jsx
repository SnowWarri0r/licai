import { useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'

// 60d 收盘曲线放大图. 数据已经在父组件加载好 (sector.kline_tail), 不再请求.
export default function SectorKlineModal({ sector, market, onClose }) {
  const [hover, setHover] = useState(null)
  const svgRef = useRef(null)

  // ESC 关闭
  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const series = sector?.kline_tail || []
  const closes = series.map(d => d.close).filter(c => c > 0)
  const min = closes.length ? Math.min(...closes) : 0
  const max = closes.length ? Math.max(...closes) : 1
  const range = max - min || 1
  const start = closes[0]
  const end = closes[closes.length - 1]
  const periodPct = start && end ? ((end / start) - 1) * 100 : null

  // 图表布局
  const W = 720, H = 320, P = { l: 56, r: 16, t: 16, b: 28 }
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

  // Y 轴 5 等分
  const yTicks = useMemo(() => {
    if (!closes.length) return []
    const N = 4
    const step = range / N
    return Array.from({ length: N + 1 }, (_, i) => {
      const v = min + step * i
      return { v, y: P.t + innerH - ((v - min) / range) * innerH }
    })
  }, [closes.length, min, range, innerH])

  // X 轴: 始 / 1/4 / 1/2 / 3/4 / 末
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

  // 区域填充: 用闭合路径
  const areaPath = useMemo(() => {
    if (!points.length) return ''
    const top = points.map((p, i) => (i === 0 ? 'M' : 'L') + p.x.toFixed(1) + ' ' + p.y.toFixed(1)).join(' ')
    return top + ` L ${points[points.length - 1].x.toFixed(1)} ${(P.t + innerH).toFixed(1)} L ${points[0].x.toFixed(1)} ${(P.t + innerH).toFixed(1)} Z`
  }, [points, innerH])

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

  return createPortal(
    <div className="fixed inset-0 z-[200] flex items-center justify-center bg-black/60 backdrop-blur-sm"
      onClick={onClose}>
      <div className="bg-surface-2 border border-border rounded-xl p-4 md:p-5 w-[760px] max-w-[95vw]"
        onClick={e => e.stopPropagation()}>
        <div className="flex items-baseline justify-between gap-3 mb-3 flex-wrap">
          <div className="flex items-baseline gap-2 flex-wrap">
            <h3 className="text-[15px] font-semibold text-text-bright m-0">{sector.name}</h3>
            {marketLabel && (
              <span className="text-[10.5px] px-1.5 py-[1px] rounded border border-info/40 bg-info/10 text-info">
                {marketLabel}
              </span>
            )}
            {sector.symbol && (
              <span className="text-[11px] font-mono text-text-dim">{sector.symbol}</span>
            )}
            {sector.held && (
              <span className="text-[10px] px-1 py-0 rounded bg-accent/20 text-accent border border-accent/40">
                持仓
              </span>
            )}
          </div>
          <button onClick={onClose}
            className="text-text-dim hover:text-text text-[18px] leading-none px-2 cursor-pointer">×</button>
        </div>

        <div className="grid grid-cols-2 md:grid-cols-4 gap-2 mb-3 text-[11px]">
          <div className="bg-surface-3 rounded-md px-2 py-1.5">
            <div className="text-text-dim text-[10px] mb-0.5">1 日</div>
            <div className={`font-mono font-semibold ${colorPct(sector.change_1d)}`}>{fmtPct(sector.change_1d)}</div>
          </div>
          <div className="bg-surface-3 rounded-md px-2 py-1.5">
            <div className="text-text-dim text-[10px] mb-0.5">5 日</div>
            <div className={`font-mono font-semibold ${colorPct(sector.change_5d)}`}>{fmtPct(sector.change_5d)}</div>
          </div>
          <div className="bg-surface-3 rounded-md px-2 py-1.5">
            <div className="text-text-dim text-[10px] mb-0.5">30 日</div>
            <div className={`font-mono font-semibold ${colorPct(sector.change_30d)}`}>{fmtPct(sector.change_30d)}</div>
          </div>
          <div className="bg-surface-3 rounded-md px-2 py-1.5">
            <div className="text-text-dim text-[10px] mb-0.5">{series.length} 日</div>
            <div className={`font-mono font-semibold ${colorPct(periodPct)}`}>{fmtPct(periodPct)}</div>
          </div>
        </div>

        <div className="bg-surface-3 rounded-md p-2 relative">
          {points.length < 2 ? (
            <div className="h-[320px] flex items-center justify-center text-text-dim text-[12px]">
              暂无 K 线数据
            </div>
          ) : (
            <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} className="w-full h-auto select-none cursor-crosshair"
              onMouseMove={handleMouseMove} onMouseLeave={() => setHover(null)}>
              {/* Y axis grid + labels */}
              {yTicks.map((t, i) => (
                <g key={'y'+i}>
                  <line x1={P.l} y1={t.y} x2={W - P.r} y2={t.y}
                    stroke="var(--color-border-subtle)" strokeWidth="1" strokeDasharray={i === 0 || i === yTicks.length - 1 ? '0' : '2 3'} />
                  <text x={P.l - 6} y={t.y + 3} fontSize="10" fill="var(--color-text-dim)" textAnchor="end" fontFamily="monospace">
                    {fmtVal(t.v)}
                  </text>
                </g>
              ))}
              {/* X axis labels */}
              {xTicks.map((t, i) => (
                <text key={'x'+i} x={t.x} y={H - 8} fontSize="10" fill="var(--color-text-dim)" textAnchor="middle" fontFamily="monospace">
                  {(t.date || '').slice(5)}
                </text>
              ))}
              {/* Area + line */}
              <path d={areaPath} fill={fillColor} />
              <path d={linePath} fill="none" stroke={lineColor} strokeWidth="1.5" />
              {/* Hover crosshair */}
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
          {sector.etf_code && (
            <span>兜底 ETF <span className="text-text font-mono">{sector.etf_code}</span>
              {sector.etf_name && <span className="ml-1 text-text-muted">{sector.etf_name}</span>}
            </span>
          )}
          <span className="text-text-muted ml-auto">仅展示数据，不构成投资建议</span>
        </div>
      </div>
    </div>,
    document.body
  )
}
