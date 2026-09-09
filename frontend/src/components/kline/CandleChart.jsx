import { useMemo, useRef, useState } from 'react'
import { ACQUIRE, BUY_COLOR, DOWN, SELL_COLOR, UP, colorPct, fmtPct, fmtVal } from './shared'

// ---------------------------------------------------------------------------
// 蜡烛图 (日/周/月) — 真蜡烛 + 成本线 + 自己历史 BS 标记
// ---------------------------------------------------------------------------
export function CandleChart({ series, cost, actions, warmup = [] }) {
  const [hover, setHover] = useState(null)
  const [sub, setSub] = useState('vol')   // 底部副图: vol | macd | kdj
  const svgRef = useRef(null)
  const W = 720, H = 410, P = { l: 64, r: 16, t: 16, b: 28 }
  const innerW = W - P.l - P.r, innerH = H - P.t - P.b
  const volH = 70, volGap = 30                 // 底部副图加高; volGap 留出空隙放图例/切换钮, 不压副图内容
  const priceH = innerH - volH - volGap        // 价格区高度
  const volTop = P.t + priceH + volGap         // 副图顶部

  const allLows = series.map(d => d.low).filter(v => v > 0)
  const allHighs = series.map(d => d.high).filter(v => v > 0)
  const lo0 = (allLows.length || cost != null) ? Math.min(...allLows, cost ?? Infinity) : 0
  const hi0 = (allHighs.length || cost != null) ? Math.max(...allHighs, cost ?? -Infinity) : 1
  // 上下留白, 避免最高价/成本线贴顶跟 MA图例/切换钮挤在一起
  const pad = (hi0 - lo0) * 0.07 || 1
  const rangeMin = lo0 - pad * 0.5, rangeMax = hi0 + pad
  const range = rangeMax - rangeMin || 1

  const points = useMemo(() => {
    if (series.length < 2) return []
    return series.map((d, i) => {
      const x = P.l + (i / (series.length - 1)) * innerW
      const yOf = (v) => P.t + priceH - ((v - rangeMin) / range) * priceH
      return { ...d, x, yOpen: yOf(d.open), yClose: yOf(d.close), yHigh: yOf(d.high), yLow: yOf(d.low), i }
    })
  }, [series, innerH, innerW, rangeMin, range])

  const candleW = useMemo(() => {
    if (points.length < 2) return 4
    return Math.max(2, (points[1].x - points[0].x) * 0.62)
  }, [points])

  const yTicks = useMemo(() => {
    if (!points.length) return []
    const N = 4, step = range / N
    return Array.from({ length: N + 1 }, (_, i) => {
      const v = rangeMin + step * i
      return { v, y: P.t + priceH - ((v - rangeMin) / range) * priceH }
    })
  }, [points.length, rangeMin, range, innerH])

  const xTicks = useMemo(() => {
    if (points.length < 2) return []
    return [0, .25, .5, .75, 1].map(f => points[Math.floor((points.length - 1) * f)])
  }, [points])

  const bsMarkers = useMemo(() => {
    if (!points.length || !actions?.length) return []
    const dateIdx = {}
    points.forEach((p, i) => { dateIdx[p.date] = i })
    // 同一天同方向的多笔(分价成交/加仓)三角标与 B 字全落在同一根K线的同一位置, 会叠成一个,
    // 数不出几笔 → 合成一个标记(均价 + 笔数), 明细留在 fills 里给悬浮框逐笔列出
    const byDay = new Map()
    for (const a of actions) {
      const td = (a.trade_date || '').slice(0, 10)
      const idx = dateIdx[td]
      if (idx == null) continue
      const p = points[idx]
      const isBuy = ACQUIRE.has(a.action_type)
      const fill = { price: Number(a.price) || 0, shares: Number(a.shares) || 0, type: a.action_type }
      const k = `${isBuy ? 'B' : 'S'}@${td}`
      const g = byDay.get(k)
      if (g) { g.n++; g.shares += fill.shares; g.amt += fill.price * fill.shares; g.fills.push(fill) }
      else byDay.set(k, { id: a.id, x: p.x, yHigh: p.yHigh, yLow: p.yLow, date: td, isBuy,
                          n: 1, shares: fill.shares, amt: fill.price * fill.shares, fills: [fill] })
    }
    return [...byDay.values()].map(g => {
      const price = g.shares > 0 ? g.amt / g.shares : g.fills[0].price
      const yPrice = (price > 0 && range > 0) ? P.t + priceH - ((price - rangeMin) / range) * priceH
                                              : (g.isBuy ? g.yLow : g.yHigh)
      return { ...g, price, yPrice }
    })
  }, [points, actions, rangeMin, range, innerH])

  const lastI = points.length ? points[points.length - 1].i : 0
  // 副图图例(等宽字体按字符宽度均匀排, 从绘图区左边界起)
  const subLegend = (items) => {
    let x = P.l + 2
    return items.map((it, i) => {
      const el = <text key={i} x={x} y={volTop - 9} fontSize="9.5" fill={it.c} fontFamily="monospace">{it.t}</text>
      x += it.t.length * 5.9 + 10
      return el
    })
  }
  const costY = cost != null && range > 0 ? P.t + priceH - ((cost - rangeMin) / range) * priceH : null
  const closes = series.map(d => d.close).filter(c => c > 0)
  const volMax = Math.max(1, ...series.map(d => Number(d.volume) || 0))

  // 技术指标 MACD / KDJ (用于底部可切换副图)
  const indic = useMemo(() => {
    const cl = series.map(d => d.close), hi = series.map(d => d.high), lo = series.map(d => d.low)
    const n = cl.length
    if (n < 2) return { dif: [], dea: [], hist: [], k: [], d: [], j: [] }
    const ema = (arr, p) => {
      const out = [], a = 2 / (p + 1)
      arr.forEach((v, i) => out.push(i === 0 ? v : out[i - 1] + a * (v - out[i - 1])))
      return out
    }
    const e12 = ema(cl, 12), e26 = ema(cl, 26)
    const dif = cl.map((_, i) => e12[i] - e26[i])
    const dea = ema(dif, 9)
    const hist = dif.map((v, i) => (v - dea[i]) * 2)
    // KDJ(9)
    const k = [], d = [], j = []
    for (let i = 0; i < n; i++) {
      const s = Math.max(0, i - 8)
      const ll = Math.min(...lo.slice(s, i + 1)), hh = Math.max(...hi.slice(s, i + 1))
      const rsv = hh > ll ? (cl[i] - ll) / (hh - ll) * 100 : 50
      k[i] = i === 0 ? 50 : (2 / 3) * k[i - 1] + (1 / 3) * rsv
      d[i] = i === 0 ? 50 : (2 / 3) * d[i - 1] + (1 / 3) * k[i]
      j[i] = 3 * k[i] - 2 * d[i]
    }
    return { dif, dea, hist, k, d, j }
  }, [series])

  // 均线 MA5/10/20
  const MA_DEFS = [{ n: 5, c: '#e8e0cf' }, { n: 10, c: '#c8a876' }, { n: 20, c: '#7aa2d6' }, { n: 30, c: '#6fc0b2' }, { n: 60, c: '#9a8cf0' }]
  const maLines = useMemo(() => {
    if (points.length < 2) return []
    const w = warmup.length
    const ext = [...warmup, ...points.map(p => p.close)]   // 预热 close 前置, 让 MA 从首根可见蜡烛起连续
    return MA_DEFS.map(({ n, c }) => {
      const pts = []
      let lastVal = null
      for (let vi = 0; vi < points.length; vi++) {
        const ei = w + vi                                   // 在 ext 中的下标
        if (ei < n - 1) continue                            // 连预热都不够(极新标的)才留空
        let s = 0
        for (let j = ei - n + 1; j <= ei; j++) s += ext[j]
        lastVal = s / n
        pts.push(`${points[vi].x},${P.t + priceH - ((lastVal - rangeMin) / range) * priceH}`)
      }
      return { n, c, d: pts.join(' '), enough: pts.length > 1, last: lastVal }
    })
  }, [points, warmup, rangeMin, range, innerH])

  const onMove = (e) => {
    if (!svgRef.current || !points.length) return
    const rect = svgRef.current.getBoundingClientRect()
    const cx = ((e.clientX - rect.left) / rect.width) * W
    if (cx < P.l || cx > P.l + innerW) { setHover(null); return }
    const i = Math.round(((cx - P.l) / innerW) * (points.length - 1))
    setHover(points[Math.max(0, Math.min(points.length - 1, i))])
  }

  if (points.length < 2) return <div className="h-[360px] flex items-center justify-center text-text-dim text-[12px]">暂无数据</div>

  return (
    <div className="relative">
      {/* 副图切换钮: 放在价格区与副图之间的空隙(右侧), 不压副图内容 */}
      <div className="absolute right-1 z-10 flex gap-1" style={{ top: `${((volTop - 24) / H * 100).toFixed(1)}%` }}>
        {[['vol', '量'], ['macd', 'MACD'], ['kdj', 'KDJ']].map(([k, lbl]) => (
          <button key={k} onClick={() => setSub(k)} className="px-1.5 py-[1px] rounded text-[9.5px] font-mono cursor-pointer"
            style={{ border: '1px solid', borderColor: sub === k ? 'var(--color-accent)' : 'var(--color-border-med)', color: sub === k ? 'var(--color-accent)' : 'var(--color-text-muted)', background: sub === k ? 'rgba(200,168,118,.1)' : 'rgba(26,25,35,.7)' }}>{lbl}</button>
        ))}
      </div>
      <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} className="w-full h-auto select-none cursor-crosshair"
        onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
        {yTicks.map((t, i) => (
          <g key={'y' + i}>
            <line x1={P.l} y1={t.y} x2={W - P.r} y2={t.y} stroke="var(--color-border-subtle)" strokeWidth="1"
              strokeDasharray={i === 0 || i === yTicks.length - 1 ? '0' : '2 3'} />
            <text x={P.l - 6} y={t.y + 3} fontSize="10" fill="var(--color-text-dim)" textAnchor="end" fontFamily="monospace">{fmtVal(t.v)}</text>
          </g>
        ))}
        {xTicks.map((t, i) => (
          <text key={'x' + i} x={t.x} y={H - 8} fontSize="10" fill="var(--color-text-dim)" textAnchor="middle" fontFamily="monospace">{(t.date || '').slice(5, 10)}</text>
        ))}
        {points.map(p => {
          const isUp = p.close >= p.open
          const color = isUp ? UP : DOWN
          const bodyTop = Math.min(p.yOpen, p.yClose)
          const bodyH = Math.max(1, Math.abs(p.yClose - p.yOpen))
          return (
            <g key={p.i}>
              <line x1={p.x} y1={p.yHigh} x2={p.x} y2={p.yLow} stroke={color} strokeWidth="1" />
              <rect x={p.x - candleW / 2} y={bodyTop} width={candleW} height={bodyH} fill={color} stroke={color} strokeWidth="0.5" />
            </g>
          )
        })}
        {/* 底部副图: 量 / MACD / KDJ */}
        <line x1={P.l} y1={volTop + volH} x2={W - P.r} y2={volTop + volH} stroke="var(--color-border-subtle)" strokeWidth="1" />
        {sub === 'vol' && points.map(p => {
          const h = ((Number(p.volume) || 0) / volMax) * volH
          return <rect key={'v' + p.i} x={p.x - candleW / 2} y={volTop + volH - h} width={candleW} height={Math.max(0.5, h)} fill={p.close >= p.open ? UP : DOWN} opacity="0.85" />
        })}
        {sub === 'macd' && (() => {
          const idx = points.map(p => p.i)
          const maxAbs = Math.max(1e-6, ...idx.flatMap(i => [Math.abs(indic.dif[i]), Math.abs(indic.dea[i]), Math.abs(indic.hist[i])]))
          const zeroY = volTop + volH / 2, sc = (volH / 2 - 2) / maxAbs
          const line = (arr) => points.map(p => `${p.x},${zeroY - arr[p.i] * sc}`).join(' ')
          return (
            <g>
              <line x1={P.l} y1={zeroY} x2={W - P.r} y2={zeroY} stroke="var(--color-border-subtle)" strokeWidth="0.5" strokeDasharray="2 3" />
              {points.map(p => { const v = indic.hist[p.i]; return <rect key={'m' + p.i} x={p.x - candleW / 2} y={v >= 0 ? zeroY - v * sc : zeroY} width={candleW} height={Math.max(0.4, Math.abs(v * sc))} fill={v >= 0 ? UP : DOWN} opacity="0.85" /> })}
              <polyline points={line(indic.dif)} fill="none" stroke="#e8e0cf" strokeWidth="1" />
              <polyline points={line(indic.dea)} fill="none" stroke="#c8a876" strokeWidth="1" />
              {subLegend([{ c: '#e8e0cf', t: `DIF ${fmtVal(indic.dif[lastI])}` }, { c: '#c8a876', t: `DEA ${fmtVal(indic.dea[lastI])}` }, { c: indic.hist[lastI] >= 0 ? UP : DOWN, t: `MACD ${fmtVal(indic.hist[lastI])}` }])}
            </g>
          )
        })()}
        {sub === 'kdj' && (() => {
          const yOf = (v) => volTop + volH - Math.max(0, Math.min(100, v)) / 100 * volH
          const line = (arr) => points.map(p => `${p.x},${yOf(arr[p.i])}`).join(' ')
          return (
            <g>
              <polyline points={line(indic.k)} fill="none" stroke="#e8e0cf" strokeWidth="1" />
              <polyline points={line(indic.d)} fill="none" stroke="#c8a876" strokeWidth="1" />
              <polyline points={line(indic.j)} fill="none" stroke="#7aa2d6" strokeWidth="1" />
              {subLegend([{ c: '#e8e0cf', t: `K ${fmtVal(indic.k[lastI])}` }, { c: '#c8a876', t: `D ${fmtVal(indic.d[lastI])}` }, { c: '#7aa2d6', t: `J ${fmtVal(indic.j[lastI])}` }])}
            </g>
          )
        })()}
        {sub === 'vol' && <text x={P.l + 2} y={volTop - 9} fontSize="9.5" fill="var(--color-text-muted)" fontFamily="monospace">成交量</text>}
        {/* 均线 MA + 图例(SVG 内, 从绘图区左边界起, 等宽字体按字符宽度均匀排, 避开左侧Y轴刻度) */}
        {maLines.map(m => m.enough && <polyline key={m.n} points={m.d} fill="none" stroke={m.c} strokeWidth="1" opacity="0.9" />)}
        {(() => {
          let x = P.l + 2
          return maLines.filter(m => m.enough).map(m => {
            const label = `MA${m.n} ${fmtVal(m.last)}`
            const el = (
              <g key={m.n}>
                <line x1={x} y1={P.t + 6} x2={x + 11} y2={P.t + 6} stroke={m.c} strokeWidth="2" />
                <text x={x + 15} y={P.t + 9} fontSize="10" fill={m.c} fontFamily="monospace">{label}</text>
              </g>
            )
            x += 15 + label.length * 6.1 + 12   // 等宽 ~6.1px/字符 + 间距
            return el
          })
        })()}
        {costY != null && (
          <g>
            <line x1={P.l} y1={costY} x2={W - P.r} y2={costY} stroke="var(--color-accent)" strokeWidth="1" strokeDasharray="4 3" opacity="0.7" />
            {/* 标签挪到左端(避开右上 量/MACD/KDJ 钮); 贴顶(MA图例区)时放到线下方 */}
            <text x={P.l + 4} y={costY < P.t + 34 ? costY + 13 : costY - 4} fontSize="10" fill="var(--color-accent)" textAnchor="start" fontFamily="monospace">成本 {fmtVal(cost)}</text>
          </g>
        )}
        {bsMarkers.map((m, idx) => {
          const color = m.isBuy ? BUY_COLOR : SELL_COLOR
          const gap = 7, tri = 9
          // 标记移到影线外侧(B 在最低点下方 / S 在最高点上方), 竖直虚线 + 价位点连回真实成交价
          const tipY = m.isBuy ? m.yLow + gap : m.yHigh - gap
          const baseY = m.isBuy ? tipY + tri : tipY - tri
          const labelY = m.isBuy ? baseY + 9 : baseY - 4
          // 连接线用高亮对比色(亮薄荷/亮珊瑚), 穿过同色蜡烛体也看得清
          const lineColor = m.isBuy ? '#8df0b4' : '#ff9a9a'
          return (
            <g key={m.id || idx}>
              <line x1={m.x} y1={m.yPrice} x2={m.x} y2={tipY} stroke={lineColor} strokeWidth="1.4" strokeDasharray="3 2" opacity="0.95" />
              <circle cx={m.x} cy={m.yPrice} r="2.4" fill={lineColor} stroke="var(--color-bg)" strokeWidth="1" />
              <polygon points={`${m.x},${tipY} ${m.x - 5},${baseY} ${m.x + 5},${baseY}`} fill={color} stroke="var(--color-bg)" strokeWidth="0.5" />
              <text x={m.x} y={labelY} fontSize="9" fill={color} textAnchor="middle" fontFamily="monospace" fontWeight="600">
                {(m.isBuy ? 'B' : 'S') + (m.n > 1 ? `×${m.n}` : '')}</text>
            </g>
          )
        })}
        {hover && <line x1={hover.x} y1={P.t} x2={hover.x} y2={P.t + innerH} stroke="var(--color-text-muted)" strokeWidth="1" strokeDasharray="2 3" />}
      </svg>
      {hover && (
        <div className="absolute top-2 right-2 bg-surface-2 border border-border-med rounded-md px-2.5 py-1.5 text-[11px] font-mono pointer-events-none">
          <div className="text-text-dim">{hover.date}</div>
          <div className="flex gap-x-2 flex-wrap">
            <span>O <span className="text-text">{fmtVal(hover.open)}</span></span>
            <span>H <span className="text-bear">{fmtVal(hover.high)}</span></span>
            <span>L <span className="text-bull">{fmtVal(hover.low)}</span></span>
            <span>C <span className="text-text-bright">{fmtVal(hover.close)}</span></span>
          </div>
          {cost > 0 && <div className={colorPct(((hover.close / cost) - 1) * 100)}>{fmtPct(((hover.close / cost) - 1) * 100)} (成本)</div>}
          {/* 图上合成了一个标记, 悬浮框把当天每一笔单独列出来 */}
          {bsMarkers.filter(m => m.date === hover.date).flatMap((m, i) => m.fills.map((f, j) => (
            <div key={`${i}-${j}`} style={{ color: m.isBuy ? BUY_COLOR : SELL_COLOR }}>{m.isBuy ? 'B' : 'S'} {fmtVal(f.price)} × {f.shares}</div>
          )))}
        </div>
      )}
    </div>
  )
}
