import { useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { fetchJSON } from '../hooks/useApi'

const ACQUIRE = new Set(['BUY', 'ADD', 'BONUS'])

// A 股 K 线大图 (真蜡烛 + 持仓成本线 + 自己历史 BS 标记).
// holding: { stock_code, stock_name, current_price, price_change_pct, cost_price }
export default function StockKlineModal({ holding, onClose }) {
  const [hover, setHover] = useState(null)
  const [series, setSeries] = useState([])    // [{date, open, high, low, close, volume}]
  const [actions, setActions] = useState([])  // 自己的买卖流水
  const [loading, setLoading] = useState(true)
  const [days, setDays] = useState(60)
  const [err, setErr] = useState('')
  const svgRef = useRef(null)

  useEffect(() => {
    if (!holding?.stock_code) return
    setLoading(true); setErr('')
    Promise.all([
      fetchJSON(`/api/market/history/${encodeURIComponent(holding.stock_code)}?days=${days}`),
      fetchJSON(`/api/portfolio/${encodeURIComponent(holding.stock_code)}/actions`).catch(() => []),
    ]).then(([k, a]) => {
      if (!Array.isArray(k) || k.length === 0) {
        setErr('暂无 K 线数据')
        setSeries([])
      } else {
        setSeries(k.map(x => ({
          date: x.time, open: x.open, high: x.high, low: x.low, close: x.close,
        })))
      }
      setActions(Array.isArray(a) ? a : [])
    }).catch(e => setErr(e?.message || '加载失败'))
      .finally(() => setLoading(false))
  }, [holding?.stock_code, days])

  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const cost = holding?.cost_price > 0 ? holding.cost_price : null
  // 纵轴 range 把 high/low 全包进来, 再囊括成本
  const allLows = series.map(d => d.low).filter(v => v > 0)
  const allHighs = series.map(d => d.high).filter(v => v > 0)
  const rangeMin = (allLows.length || cost != null) ? Math.min(...allLows, cost ?? Infinity) : 0
  const rangeMax = (allHighs.length || cost != null) ? Math.max(...allHighs, cost ?? -Infinity) : 1
  const range = rangeMax - rangeMin || 1

  const W = 720, H = 360, P = { l: 64, r: 16, t: 16, b: 28 }
  const innerW = W - P.l - P.r
  const innerH = H - P.t - P.b

  const points = useMemo(() => {
    if (series.length < 2) return []
    return series.map((d, i) => {
      const x = P.l + (i / (series.length - 1)) * innerW
      const yOf = (v) => P.t + innerH - ((v - rangeMin) / range) * innerH
      return {
        ...d, x,
        yOpen: yOf(d.open), yClose: yOf(d.close),
        yHigh: yOf(d.high), yLow: yOf(d.low),
        i,
      }
    })
  }, [series, innerH, innerW, rangeMin, range])

  // 蜡烛宽度
  const candleW = useMemo(() => {
    if (points.length < 2) return 4
    const step = (points[1].x - points[0].x)
    return Math.max(2, step * 0.62)
  }, [points])

  const yTicks = useMemo(() => {
    if (!points.length) return []
    const N = 4, step = range / N
    return Array.from({ length: N + 1 }, (_, i) => {
      const v = rangeMin + step * i
      return { v, y: P.t + innerH - ((v - rangeMin) / range) * innerH }
    })
  }, [points.length, rangeMin, range, innerH])

  const xTicks = useMemo(() => {
    if (points.length < 2) return []
    const idxs = [0, Math.floor((points.length - 1) * 0.25), Math.floor((points.length - 1) * 0.5),
                  Math.floor((points.length - 1) * 0.75), points.length - 1]
    return idxs.map(i => points[i])
  }, [points])

  // BS 标记: 按 trade_date 对齐到 K 线 index
  const bsMarkers = useMemo(() => {
    if (!points.length || !actions.length) return []
    const dateIdx = {}
    points.forEach((p, i) => { dateIdx[p.date] = i })
    const out = []
    for (const a of actions) {
      const td = (a.trade_date || '').slice(0, 10)
      const idx = dateIdx[td]
      if (idx == null) continue
      const p = points[idx]
      const isBuy = ACQUIRE.has(a.action_type)
      out.push({
        id: a.id, x: p.x, yHigh: p.yHigh, yLow: p.yLow,
        date: td, price: a.price, shares: a.shares, type: a.action_type, isBuy,
      })
    }
    return out
  }, [points, actions])

  const handleMouseMove = (e) => {
    if (!svgRef.current || !points.length) return
    const rect = svgRef.current.getBoundingClientRect()
    const cursorX = ((e.clientX - rect.left) / rect.width) * W
    if (cursorX < P.l || cursorX > P.l + innerW) { setHover(null); return }
    const i = Math.round(((cursorX - P.l) / innerW) * (points.length - 1))
    setHover(points[Math.max(0, Math.min(points.length - 1, i))])
  }

  const costY = cost != null && range > 0
    ? P.t + innerH - ((cost - rangeMin) / range) * innerH
    : null

  if (!holding) return null

  const fmtVal = (v) => v == null ? '--' : v < 10 ? v.toFixed(3) : v < 100 ? v.toFixed(2) : v.toFixed(1)
  const fmtPct = (v) => v == null ? '--' : (v >= 0 ? '+' : '') + v.toFixed(2) + '%'
  const colorPct = (v) => v == null ? 'text-text-dim' : v >= 0 ? 'text-bear-bright' : 'text-bull-bright'

  const closes = series.map(d => d.close).filter(c => c > 0)
  const calcPct = (lb) => {
    if (closes.length < lb + 1) return null
    const a = closes[closes.length - 1 - lb]
    return a > 0 ? ((closes[closes.length - 1] / a) - 1) * 100 : null
  }
  const pct1d = holding.price_change_pct
  const pct5d = calcPct(5)
  const pct20d = calcPct(20)
  const periodPct = (closes[0] && closes[closes.length-1]) ? ((closes[closes.length-1]/closes[0]) - 1) * 100 : null
  const vsCostPct = cost && closes.length ? ((closes[closes.length-1] / cost) - 1) * 100 : null

  // A 股口径: 红涨绿跌
  const CANDLE_UP = '#cf5c5c'    // 涨 (close >= open) 红
  const CANDLE_DOWN = '#5fa86c'  // 跌 绿
  // BS 标记: 国际通用绿买红卖 (跟蜡烛颜色互补不易混淆)
  const BUY_COLOR = '#3fae6a'
  const SELL_COLOR = '#d04a4a'

  return createPortal(
    <div className="fixed inset-0 z-[200] flex items-center justify-center bg-black/60 backdrop-blur-sm" onClick={onClose}>
      <div className="bg-surface-2 border border-border rounded-xl p-4 md:p-5 w-[820px] max-w-[95vw]"
        onClick={e => e.stopPropagation()}>
        <div className="flex items-baseline justify-between gap-3 mb-3 flex-wrap">
          <div className="flex items-baseline gap-2 flex-wrap">
            <h3 className="text-[15px] font-semibold text-text-bright m-0">{holding.stock_name}</h3>
            <span className="text-[11px] font-mono text-text-dim">{holding.stock_code}</span>
            <span className="text-[14px] font-mono text-text-bright">{fmtVal(holding.current_price)}</span>
            {cost != null && (
              <span className={`text-[11px] font-mono ${colorPct(vsCostPct)}`} title="相对持仓成本">
                vs 成本 {fmtPct(vsCostPct)}
              </span>
            )}
            {bsMarkers.length > 0 && (
              <span className="text-[10.5px] text-text-muted">· {bsMarkers.length} 笔标记</span>
            )}
          </div>
          <div className="flex gap-1 items-center">
            {[30, 60, 120, 250].map(d => (
              <button key={d} onClick={() => setDays(d)}
                className="px-2 py-[2px] rounded text-[10.5px] cursor-pointer transition-colors"
                style={{
                  border: '1px solid',
                  borderColor: days === d ? 'var(--color-accent)' : 'var(--color-border-med)',
                  color: days === d ? 'var(--color-accent)' : 'var(--color-text-dim)',
                  background: days === d ? 'var(--color-accent)1a' : 'transparent',
                }}>
                {d}日
              </button>
            ))}
            <button onClick={onClose} className="text-text-dim hover:text-text text-[18px] leading-none px-2 ml-1 cursor-pointer">×</button>
          </div>
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
          {loading ? (
            <div className="h-[360px] flex items-center justify-center text-text-dim text-[12px]">加载中…</div>
          ) : err ? (
            <div className="h-[360px] flex items-center justify-center text-text-dim text-[12px]">{err}</div>
          ) : points.length < 2 ? (
            <div className="h-[360px] flex items-center justify-center text-text-dim text-[12px]">暂无数据</div>
          ) : (
            <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} className="w-full h-auto select-none cursor-crosshair"
              onMouseMove={handleMouseMove} onMouseLeave={() => setHover(null)}>
              {/* Y axis grid */}
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
              {/* X axis labels */}
              {xTicks.map((t, i) => (
                <text key={'x'+i} x={t.x} y={H - 8} fontSize="10" fill="var(--color-text-dim)" textAnchor="middle" fontFamily="monospace">
                  {(t.date || '').slice(5)}
                </text>
              ))}

              {/* 蜡烛 */}
              {points.map(p => {
                const isUp = p.close >= p.open
                const color = isUp ? CANDLE_UP : CANDLE_DOWN
                const bodyTop = Math.min(p.yOpen, p.yClose)
                const bodyH = Math.max(1, Math.abs(p.yClose - p.yOpen))
                return (
                  <g key={p.i}>
                    <line x1={p.x} y1={p.yHigh} x2={p.x} y2={p.yLow} stroke={color} strokeWidth="1" />
                    <rect x={p.x - candleW / 2} y={bodyTop} width={candleW} height={bodyH}
                      fill={isUp ? color : color}
                      stroke={color} strokeWidth="0.5" />
                  </g>
                )
              })}

              {/* 持仓成本横线 */}
              {costY != null && (
                <g>
                  <line x1={P.l} y1={costY} x2={W - P.r} y2={costY}
                    stroke="var(--color-accent)" strokeWidth="1" strokeDasharray="4 3" opacity="0.7" />
                  <text x={W - P.r - 4} y={costY - 4} fontSize="10" fill="var(--color-accent)" textAnchor="end" fontFamily="monospace">
                    成本 {fmtVal(cost)}
                  </text>
                </g>
              )}

              {/* BS 标记 */}
              {bsMarkers.map((m, idx) => {
                const color = m.isBuy ? BUY_COLOR : SELL_COLOR
                // 买: 蜡烛下方向上三角 ▲; 卖: 蜡烛上方向下三角 ▼
                const baseY = m.isBuy ? m.yLow + 14 : m.yHigh - 14
                const tipY = m.isBuy ? m.yLow + 4 : m.yHigh - 4
                const labelY = m.isBuy ? baseY + 11 : baseY - 4
                return (
                  <g key={m.id || idx}>
                    <polygon
                      points={`${m.x},${tipY} ${m.x - 5},${baseY} ${m.x + 5},${baseY}`}
                      fill={color} stroke="var(--color-bg)" strokeWidth="0.5" />
                    <text x={m.x} y={labelY} fontSize="9" fill={color}
                      textAnchor="middle" fontFamily="monospace" fontWeight="600">
                      {m.isBuy ? 'B' : 'S'}
                    </text>
                  </g>
                )
              })}

              {/* Hover crosshair */}
              {hover && (
                <g>
                  <line x1={hover.x} y1={P.t} x2={hover.x} y2={P.t + innerH}
                    stroke="var(--color-text-muted)" strokeWidth="1" strokeDasharray="2 3" />
                </g>
              )}
            </svg>
          )}

          {hover && (
            <div className="absolute top-2 right-2 bg-surface-2 border border-border-med rounded-md px-2.5 py-1.5 text-[11px] font-mono pointer-events-none">
              <div className="text-text-dim">{hover.date}</div>
              <div className="flex gap-x-2 gap-y-0 flex-wrap">
                <span>O <span className="text-text">{fmtVal(hover.open)}</span></span>
                <span>H <span className="text-bear">{fmtVal(hover.high)}</span></span>
                <span>L <span className="text-bull">{fmtVal(hover.low)}</span></span>
                <span>C <span className="text-text-bright">{fmtVal(hover.close)}</span></span>
              </div>
              {closes[0] > 0 && (
                <div className={colorPct(((hover.close / closes[0]) - 1) * 100)}>
                  {fmtPct(((hover.close / closes[0]) - 1) * 100)} (起)
                </div>
              )}
              {cost > 0 && (
                <div className={colorPct(((hover.close / cost) - 1) * 100)}>
                  {fmtPct(((hover.close / cost) - 1) * 100)} (成本)
                </div>
              )}
              {/* 当日有标记的话, 列出本日所有动作 */}
              {bsMarkers.filter(m => m.date === hover.date).map((m, i) => (
                <div key={i} className="mt-0.5" style={{ color: m.isBuy ? BUY_COLOR : SELL_COLOR }}>
                  {m.isBuy ? 'B' : 'S'} {fmtVal(m.price)} × {m.shares} ({m.type})
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-text-dim">
          <span>区间高 <span className="text-text font-mono">{fmtVal(rangeMax)}</span></span>
          <span>区间低 <span className="text-text font-mono">{fmtVal(rangeMin)}</span></span>
          {cost != null && (
            <span>成本 <span className="text-accent font-mono">{fmtVal(cost)}</span></span>
          )}
          <span>
            <span className="inline-block w-2 h-2 rounded-sm align-middle mr-1" style={{ background: BUY_COLOR }} />
            B 买入
            <span className="mx-1.5"></span>
            <span className="inline-block w-2 h-2 rounded-sm align-middle mr-1" style={{ background: SELL_COLOR }} />
            S 卖出
          </span>
          <span className="text-text-muted ml-auto">仅展示数据，不构成投资建议</span>
        </div>
      </div>
    </div>,
    document.body
  )
}
