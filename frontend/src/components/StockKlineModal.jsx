import { useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { fetchJSON } from '../hooks/useApi'

const ACQUIRE = new Set(['BUY', 'ADD', 'BONUS'])
export const MA_WARMUP = 60           // 日K 多取的均线预热根数(够 MA60 从首根可见蜡烛起连续)
// A 股口径: 红涨绿跌
const UP = '#cf5c5c', DOWN = '#5fa86c'
const BUY_COLOR = '#3fae6a', SELL_COLOR = '#d04a4a'

const fmtVal = (v) => v == null ? '--' : v < 10 ? v.toFixed(3) : v < 100 ? v.toFixed(2) : v.toFixed(1)
const fmtPct = (v) => v == null ? '--' : (v >= 0 ? '+' : '') + v.toFixed(2) + '%'
const colorPct = (v) => v == null ? 'text-text-dim' : v >= 0 ? 'text-bear-bright' : 'text-bull-bright'
const fmtHand = (h) => h == null ? '--' : h >= 1e4 ? (h / 1e4).toFixed(1) + '万手' : h + '手'

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
    const out = []
    for (const a of actions) {
      const td = (a.trade_date || '').slice(0, 10)
      const idx = dateIdx[td]
      if (idx == null) continue
      const p = points[idx]
      const isBuy = ACQUIRE.has(a.action_type)
      const yPrice = (a.price != null && range > 0) ? P.t + priceH - ((a.price - rangeMin) / range) * priceH : (isBuy ? p.yLow : p.yHigh)
      out.push({ id: a.id, x: p.x, yPrice, yHigh: p.yHigh, yLow: p.yLow, date: td, price: a.price, shares: a.shares, type: a.action_type, isBuy })
    }
    return out
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
              <text x={m.x} y={labelY} fontSize="9" fill={color} textAnchor="middle" fontFamily="monospace" fontWeight="600">{m.isBuy ? 'B' : 'S'}</text>
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
          {bsMarkers.filter(m => m.date === hover.date).map((m, i) => (
            <div key={i} style={{ color: m.isBuy ? BUY_COLOR : SELL_COLOR }}>{m.isBuy ? 'B' : 'S'} {fmtVal(m.price)} × {m.shares}</div>
          ))}
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// 分时图 (TDX) — 价格线 + 均价线 + 昨收基准, 上方红下方绿
// ---------------------------------------------------------------------------
// 分时时刻 → 固定 240 分钟交易网格的槽位 [0,240]。9:30-11:30=0~120, 13:00-15:00=120~240。
// 让点按真实时刻落位(没出满则右侧留白), 而非按索引铺满整宽导致时间轴错位。
function _minuteSlot(t) {
  const parts = String(t || '').split(':')
  const mins = (Number(parts[0]) || 0) * 60 + (Number(parts[1]) || 0)
  const amS = 570, amE = 690, pmS = 780, pmE = 900   // 9:30 / 11:30 / 13:00 / 15:00
  if (mins <= amS) return 0
  if (mins <= amE) return mins - amS
  if (mins < pmS) return 120
  if (mins <= pmE) return 120 + (mins - pmS)
  return 240
}

export function MinuteChart({ points, prevClose, actions = [], day, height = 410 }) {
  const [hover, setHover] = useState(null)
  const svgRef = useRef(null)
  const W = 720, H = height, P = { l: 64, r: 16, t: 16, b: 28 }
  const innerW = W - P.l - P.r, innerH = H - P.t - P.b
  const volH = 48, volGap = 10
  const priceH = innerH - volH - volGap
  const volTop = P.t + priceH + volGap

  const { rows, rangeMin, range, volMax } = useMemo(() => {
    const prices = points.map(p => p.price).filter(v => v > 0)
    if (!prices.length) return { rows: [], rangeMin: 0, range: 1, volMax: 1 }
    // 以昨收为中心对称, 让涨跌幅直观
    const maxDev = Math.max(...prices.map(p => Math.abs(p - prevClose)), prevClose * 0.001)
    const rMin = prevClose - maxDev, rng = maxDev * 2 || 1
    const vMax = Math.max(1, ...points.map(p => Number(p['手']) || 0))
    let cumPV = 0, cumV = 0, prevPx = prevClose, lastUp = true
    const rs = points.map((p, i) => {
      const v = Number(p['手']) || 0
      cumPV += p.price * v; cumV += v
      const avg = cumV > 0 ? cumPV / cumV : p.price
      const x = P.l + (_minuteSlot(p.time) / 240) * innerW   // 按真实时刻落位, 非按索引铺满
      const yOf = (val) => P.t + priceH - ((val - rMin) / rng) * priceH
      // 量柱买卖方向: tick 规则 — 比上一分钟涨=主动买(红), 跌=主动卖(绿), 平=延续
      const up = p.price > prevPx ? true : p.price < prevPx ? false : lastUp
      lastUp = up; prevPx = p.price
      return { ...p, avg, vol: v, x, y: yOf(p.price), yAvg: yOf(avg), i, up }
    })
    return { rows: rs, rangeMin: rMin, range: rng, volMax: vMax }
  }, [points, prevClose, priceH, innerW])

  const yTicks = useMemo(() => {
    const N = 4, step = range / N
    return Array.from({ length: N + 1 }, (_, i) => {
      const v = rangeMin + step * i
      return { v, pct: prevClose > 0 ? ((v / prevClose) - 1) * 100 : 0, y: P.t + priceH - ((v - rangeMin) / range) * priceH }
    })
  }, [rangeMin, range, prevClose, priceH])

  // 当日买卖点: 只取与分时同一天的成交, 按 at_time(成交时刻)落到分时网格
  const bsMarks = useMemo(() => {
    if (!rows.length || !actions?.length) return []
    const norm = s => String(s || '').replace(/\D/g, '').slice(0, 8)   // → YYYYMMDD, 容忍带/不带横杠
    const now = new Date()
    const today = `${now.getFullYear()}${String(now.getMonth() + 1).padStart(2, '0')}${String(now.getDate()).padStart(2, '0')}`
    const matchDay = norm(day) || today
    const yOf = (val) => P.t + priceH - ((val - rangeMin) / range) * priceH
    const out = []
    for (const a of actions) {
      if (norm(a.trade_date) !== matchDay) continue
      if (!a.at_time) continue
      const slot = _minuteSlot(a.at_time)
      const x = P.l + (slot / 240) * innerW
      const isBuy = ACQUIRE.has(a.action_type)
      const price = Number(a.price)
      const y = price > 0 ? yOf(price) : (isBuy ? P.t + priceH : P.t)
      out.push({ id: a.id, x, y, isBuy, price, at: a.at_time, shares: a.shares })
    }
    return out
  }, [rows, actions, day, rangeMin, range, priceH, innerW])

  const priceLine = rows.map(r => `${r.x},${r.y}`).join(' ')
  const avgLine = rows.map(r => `${r.x},${r.yAvg}`).join(' ')
  const last = rows.length ? rows[rows.length - 1].price : prevClose
  const lineColor = last >= prevClose ? UP : DOWN
  const baseY = P.t + priceH - ((prevClose - rangeMin) / range) * priceH

  const onMove = (e) => {
    if (!svgRef.current || !rows.length) return
    const rect = svgRef.current.getBoundingClientRect()
    const cx = ((e.clientX - rect.left) / rect.width) * W
    // x 已按真实时刻分布(非均匀), 取 x 最近的点
    let best = rows[0], bd = Infinity
    for (const r of rows) { const d = Math.abs(r.x - cx); if (d < bd) { bd = d; best = r } }
    setHover(best)
  }

  if (rows.length < 2) return <div className="h-[360px] flex items-center justify-center text-text-dim text-[12px]">暂无分时(非交易时段或 TDX 无数据)</div>

  return (
    <div className="relative">
      <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} className="w-full h-auto select-none cursor-crosshair"
        onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
        {yTicks.map((t, i) => (
          <g key={i}>
            <line x1={P.l} y1={t.y} x2={W - P.r} y2={t.y} stroke="var(--color-border-subtle)" strokeWidth="1" strokeDasharray={Math.abs(t.v - prevClose) < range * 0.02 ? '0' : '2 3'} />
            <text x={P.l - 6} y={t.y + 3} fontSize="10" fill="var(--color-text-dim)" textAnchor="end" fontFamily="monospace">{fmtVal(t.v)}</text>
            <text x={W - P.r} y={t.y + 3} fontSize="9" fill={colorPctHex(t.pct)} textAnchor="end" fontFamily="monospace">{fmtPct(t.pct)}</text>
          </g>
        ))}
        <line x1={P.l} y1={baseY} x2={W - P.r} y2={baseY} stroke="var(--color-text-muted)" strokeWidth="1" strokeDasharray="3 3" opacity="0.6" />
        {['09:30', '11:30/13:00', '15:00'].map((lbl, i) => (
          <text key={i} x={P.l + (i / 2) * innerW} y={H - 8} fontSize="10" fill="var(--color-text-dim)" textAnchor={i === 0 ? 'start' : i === 2 ? 'end' : 'middle'} fontFamily="monospace">{lbl}</text>
        ))}
        {/* 分时成交量 */}
        <text x={P.l} y={volTop - 3} fontSize="9" fill="var(--color-text-muted)" fontFamily="monospace">
          量 <tspan fill={UP}>红买</tspan>/<tspan fill={DOWN}>绿卖</tspan>
        </text>
        {rows.map(r => {
          const h = (r.vol / volMax) * volH
          return <rect key={'mv' + r.i} x={r.x - 1} y={volTop + volH - h} width="1.6" height={Math.max(0.4, h)} fill={r.up ? UP : DOWN} opacity="0.8" />
        })}
        <line x1={P.l} y1={volTop + volH} x2={W - P.r} y2={volTop + volH} stroke="var(--color-border-subtle)" strokeWidth="1" />
        <polyline points={avgLine} fill="none" stroke="#c8a876" strokeWidth="1" opacity="0.85" />
        <polyline points={priceLine} fill="none" stroke={lineColor} strokeWidth="1.4" />
        {/* 当日买卖点: B 在下方, S 在上方, 虚线连到成交价圆点 */}
        {bsMarks.map(m => {
          const col = m.isBuy ? '#8df0b4' : '#ff9a9a'
          const labelY = m.isBuy ? Math.min(m.y + 16, P.t + priceH - 2) : Math.max(m.y - 10, P.t + 8)
          return (
            <g key={'bs' + m.id}>
              <line x1={m.x} y1={labelY} x2={m.x} y2={m.y} stroke={col} strokeWidth="1" strokeDasharray="2 2" opacity="0.8" />
              <circle cx={m.x} cy={m.y} r="2.5" fill={col} />
              <text x={m.x} y={labelY} fontSize="10" fill={col} textAnchor="middle" fontWeight="bold">{m.isBuy ? 'B' : 'S'}</text>
            </g>
          )
        })}
        {hover && <line x1={hover.x} y1={P.t} x2={hover.x} y2={volTop + volH} stroke="var(--color-text-muted)" strokeWidth="1" strokeDasharray="2 3" />}
        {/* 图例画进 SVG(随视图缩放, 不会压到轴标); 悬浮时让位给右上角 tooltip */}
        {!hover && (
          <text x={W - P.r - 6} y={P.t + 11} fontSize="10" textAnchor="end" fontFamily="ui-monospace, monospace">
            <tspan fill={lineColor}>— 价格</tspan>
            <tspan dx="10" fill="#c8a876">— 均价</tspan>
          </text>
        )}
      </svg>
      {hover && (
        <div className="absolute top-2 right-2 bg-surface-2 border border-border-med rounded-md px-2.5 py-1.5 text-[11px] font-mono pointer-events-none">
          <div className="text-text-dim">{hover.time}</div>
          <div>价 <span className="text-text-bright">{fmtVal(hover.price)}</span> <span className={colorPct(((hover.price / prevClose) - 1) * 100)}>{fmtPct(((hover.price / prevClose) - 1) * 100)}</span></div>
          <div className="text-text-dim">均 {fmtVal(hover.avg)} · {fmtHand(hover['手'])}</div>
        </div>
      )}
    </div>
  )
}
const colorPctHex = (v) => v >= 0 ? UP : DOWN

// ---------------------------------------------------------------------------
// 五档盘口
// ---------------------------------------------------------------------------
function OrderBook({ data, prevClose, decimals = 2 }) {
  if (!data) return null
  // 五档按标的精度显示(A股 2 位 / ETF 3 位), 否则 178.28/178.29 会被压成同一个 178.3, 看着像没聚类
  const px = (p) => p == null ? '--' : <span className={colorPct(prevClose ? ((p / prevClose) - 1) * 100 : 0)}>{p.toFixed(decimals)}</span>
  const maxVol = Math.max(1, ...[...(data.bids || []), ...(data.asks || [])].map(l => Number(l['手']) || 0))
  const Row = ({ lvl, side, idx }) => (
    <div className="relative flex justify-between items-center px-1.5 py-[3px] text-[11px] font-mono">
      <div className="absolute inset-y-0 right-0 rounded-sm" style={{ width: `${(Number(lvl['手']) || 0) / maxVol * 100}%`, background: side === 'ask' ? 'rgba(95,168,108,.13)' : 'rgba(207,92,92,.13)' }} />
      <span className="relative text-text-muted">{side === 'ask' ? '卖' : '买'}{idx}</span>
      <span className="relative">{px(lvl.price)}</span>
      <span className="relative text-text-dim">{Math.round(Number(lvl['手']) || 0)}</span>
    </div>
  )
  return (
    <div>
      <div className="text-[10.5px] text-text-muted mb-1 flex justify-between"><span>五档盘口</span><span>手</span></div>
      {[...(data.asks || [])].slice(0, 5).reverse().map((l, i, arr) => <Row key={'a' + i} lvl={l} side="ask" idx={arr.length - i} />)}
      <div className="border-t border-border-subtle my-0.5" />
      {(data.bids || []).slice(0, 5).map((l, i) => <Row key={'b' + i} lvl={l} side="bid" idx={i + 1} />)}
      <div className="flex justify-between text-[10.5px] mt-1.5 px-1.5">
        <span className="text-bull">内盘 {fmtHand(data['内盘手'])}</span>
        <span className="text-bear">外盘 {fmtHand(data['外盘手'])}</span>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// 逐笔成交
// ---------------------------------------------------------------------------
function Ticks({ ticks, decimals = 2 }) {
  if (!ticks?.length) return null
  const vols = ticks.map(t => Number(t['手']) || 0)
  const avg = vols.reduce((a, b) => a + b, 0) / (vols.length || 1)
  const bigThresh = Math.max(avg * 3, 100)   // 大单: ≥均量3倍且≥100手
  return (
    <div>
      <div className="text-[10.5px] text-text-muted mb-1 flex justify-between"><span>逐笔成交</span><span className="text-text-muted/70">大单加亮</span></div>
      <div className="max-h-[150px] overflow-y-auto pr-1">
        {ticks.map((t, i) => {
          const v = Math.round(Number(t['手']) || 0)
          const big = v >= bigThresh
          const dc = t.dir === '买' ? UP : t.dir === '卖' ? DOWN : 'var(--color-text-muted)'
          return (
            <div key={i} className="flex justify-between items-center text-[10.5px] font-mono py-[2px]"
              style={big ? { background: t.dir === '买' ? 'rgba(207,92,92,.12)' : t.dir === '卖' ? 'rgba(95,168,108,.12)' : 'transparent', borderRadius: 3 } : undefined}>
              <span className="text-text-muted px-1">{t.time}</span>
              <span className={big ? 'text-text-bright' : 'text-text'}>{t.price.toFixed(decimals)}</span>
              <span className="px-1" style={{ color: dc, fontWeight: big ? 700 : 400 }}>{v}{t.dir === '买' ? '↑' : t.dir === '卖' ? '↓' : ''}</span>
            </div>
          )
        })}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// 主弹窗
// ---------------------------------------------------------------------------
export default function StockKlineModal({ holding, onClose }) {
  const [tdxOn, setTdxOn] = useState(false)
  const [tab, setTab] = useState('日')            // 分时 | 日 | 周 | 月
  const [days, setDays] = useState(60)
  const [series, setSeries] = useState([])
  const [warmup, setWarmup] = useState([])        // MA 预热: 可见窗口前的 close 序列(不显示)
  const [actions, setActions] = useState([])
  const [minute, setMinute] = useState(null)
  const [book, setBook] = useState(null)
  const [ticks, setTicks] = useState([])
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState('')

  const code = holding?.stock_code
  const assetId = holding?.asset_id               // 场外 ETF: 据此走 assets 流水端点
  const isA = code && /^\d{6}$/.test(String(code).replace(/^(sh|sz|SH|SZ)/, ''))

  // TDX 是否启用(决定显示哪些 tab)
  useEffect(() => {
    fetchJSON('/api/market/tdx/status').then(d => setTdxOn(!!d.enabled)).catch(() => setTdxOn(false))
  }, [])

  // 主图数据: 日→akshare(带成本/BS); 周月→TDX蜡烛; 分时→TDX
  useEffect(() => {
    if (!code) return
    setLoading(true); setErr('')
    const done = () => setLoading(false)
    if (tab === '分时' && tdxOn) {
      fetchJSON(`/api/market/tdx/minute/${encodeURIComponent(code)}`)
        .then(d => setMinute(d?.data || null)).catch(e => setErr(e?.message || '加载失败')).finally(done)
    } else if ((tab === '周' || tab === '月') && tdxOn) {
      setWarmup([])
      fetchJSON(`/api/market/tdx/kline/${encodeURIComponent(code)}?type=${tab === '周' ? 'week' : 'month'}&limit=200`)
        .then(d => {
          const bars = d?.data?.bars || []
          if (!bars.length) { setErr('暂无K线'); setSeries([]) }
          else setSeries(bars.map(b => ({ date: (b.date || '').slice(0, 10), open: b.open, high: b.high, low: b.low, close: b.close, volume: b.volume })))
        }).catch(e => setErr(e?.message || '加载失败')).finally(done)
    } else {
      // 日K (akshare, 带成本线 + 自己买卖标记). 多取 MA_WARMUP 根做均线预热, 让 MA 从首根可见
      // 蜡烛就连续, 不在左侧错位断头. 场内 ETF 走 /api/assets/{id}/actions 取 BS 流水.
      const actUrl = assetId
        ? `/api/assets/${assetId}/actions`
        : `/api/portfolio/${encodeURIComponent(code)}/actions`
      Promise.all([
        fetchJSON(`/api/market/history/${encodeURIComponent(code)}?days=${days + MA_WARMUP}`),
        fetchJSON(actUrl).catch(() => []),
      ]).then(([k, a]) => {
        if (!Array.isArray(k) || !k.length) { setErr('暂无 K 线数据'); setSeries([]); setWarmup([]) }
        else {
          const all = k.map(x => ({ date: x.time, open: x.open, high: x.high, low: x.low, close: x.close, volume: x.volume }))
          const cut = Math.max(0, all.length - days)        // 前 cut 根仅作 MA 预热, 不显示
          setWarmup(all.slice(0, cut).map(b => b.close))
          setSeries(all.slice(cut))
        }
        // 场外 asset 流水: {actions:[{unit_price,...}]} → 归一成 BS 标记要的 {price,...}
        // 份额拆分后 K 线是前复权标度: 标记优先用后端算的 adj_price/adj_shares(拆分调整),
        // 原始成交价留在流水列表里; SPLIT 记录本身不是买卖, 不打点
        const raw = Array.isArray(a) ? a : (a?.actions || [])
        setActions(raw.filter(x => x.action_type !== 'SPLIT').map(x => ({
          ...x,
          price: x.adj_price ?? x.price ?? x.unit_price,
          shares: x.adj_shares ?? x.shares,
        })))
      }).catch(e => setErr(e?.message || '加载失败')).finally(done)
    }
  }, [code, tab, days, tdxOn, assetId])

  // 五档 + 逐笔 (TDX, 仅 A 股; 5s 刷新)
  useEffect(() => {
    if (!tdxOn || !isA || !code) { setBook(null); setTicks([]); return }
    let alive = true
    const pull = () => {
      fetchJSON(`/api/market/tdx/orderbook/${encodeURIComponent(code)}`).then(d => alive && setBook(d?.data || null)).catch(() => {})
      fetchJSON(`/api/market/tdx/trade/${encodeURIComponent(code)}?limit=40`).then(d => alive && setTicks(d?.data?.ticks || [])).catch(() => {})
    }
    pull()
    const t = setInterval(pull, 5000)
    return () => { alive = false; clearInterval(t) }
  }, [code, tdxOn, isA])

  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  if (!holding) return null
  const cost = holding.cost_price > 0 ? holding.cost_price : null
  const prevClose = book?.prev_close || (series.length ? series[series.length - 1].close : holding.current_price) || holding.current_price
  const closes = series.map(d => d.close).filter(c => c > 0)
  const vsCostPct = cost && (book?.price || closes[closes.length - 1]) ? (((book?.price || closes[closes.length - 1]) / cost) - 1) * 100 : null
  const showTabs = tdxOn ? ['分时', '日', '周', '月'] : ['日']
  const hasSide = tdxOn && isA

  return createPortal(
    <div className="fixed inset-0 z-[200] flex items-center justify-center bg-black/60 backdrop-blur-sm" onClick={onClose}>
      <div className={`bg-surface-2 border border-border rounded-xl p-4 md:p-5 ${hasSide ? 'w-[1040px]' : 'w-[820px]'} max-w-[96vw]`} onClick={e => e.stopPropagation()}>
        {/* header */}
        <div className="flex items-baseline justify-between gap-3 mb-3 flex-wrap">
          <div className="flex items-baseline gap-2 flex-wrap">
            <h3 className="text-[15px] font-semibold text-text-bright m-0">{holding.stock_name}</h3>
            <span className="text-[11px] font-mono text-text-dim">{holding.stock_code}</span>
            <span className="text-[14px] font-mono text-text-bright">{fmtVal(book?.price || holding.current_price)}</span>
            <span className={`text-[12px] font-mono ${colorPct(holding.price_change_pct)}`}>{fmtPct(holding.price_change_pct)}</span>
            {cost != null && <span className={`text-[11px] font-mono ${colorPct(vsCostPct)}`} title="相对持仓成本">vs 成本 {fmtPct(vsCostPct)}</span>}
            {book?.['盘口'] && <span className="text-[10.5px] text-accent">· {book['盘口']}</span>}
          </div>
          <div className="flex gap-1 items-center">
            {showTabs.map(t => (
              <button key={t} onClick={() => setTab(t)} className="px-2.5 py-[3px] rounded text-[11px] cursor-pointer transition-colors"
                style={{ border: '1px solid', borderColor: tab === t ? 'var(--color-accent)' : 'var(--color-border-med)', color: tab === t ? 'var(--color-accent)' : 'var(--color-text-dim)', background: tab === t ? 'rgba(200,168,118,.1)' : 'transparent' }}>{t}{t !== '分时' ? 'K' : ''}</button>
            ))}
            <button onClick={onClose} className="text-text-dim hover:text-text text-[18px] leading-none px-2 ml-1 cursor-pointer">×</button>
          </div>
        </div>

        <div className={hasSide ? 'flex gap-3' : ''}>
          {/* 主图 */}
          <div className="flex-1 min-w-0">
            {/* 日K 才显示天数切换 */}
            {tab === '日' && (
              <div className="flex gap-1 mb-2">
                {[30, 60, 120, 250].map(d => (
                  <button key={d} onClick={() => setDays(d)} className="px-2 py-[2px] rounded text-[10px] cursor-pointer"
                    style={{ border: '1px solid', borderColor: days === d ? 'var(--color-accent)' : 'var(--color-border-med)', color: days === d ? 'var(--color-accent)' : 'var(--color-text-dim)' }}>{d}日</button>
                ))}
              </div>
            )}
            <div className="bg-surface-3 rounded-md p-2">
              {loading ? <div className="h-[360px] flex items-center justify-center text-text-dim text-[12px]">加载中…</div>
                : err ? <div className="h-[360px] flex items-center justify-center text-text-dim text-[12px]">{err}</div>
                : tab === '分时' ? <MinuteChart points={minute?.points || []} prevClose={prevClose} actions={actions} day={minute?.date} />
                : <CandleChart series={series} cost={tab === '日' ? cost : null} actions={tab === '日' ? actions : []} warmup={tab === '日' ? warmup : []} />}
            </div>
          </div>

          {/* 侧栏: 五档 + 逐笔 (TDX) */}
          {hasSide && (
            <div className="w-[200px] shrink-0 bg-surface-3 rounded-md p-2.5 space-y-3">
              <OrderBook data={book} prevClose={prevClose} decimals={/^[15]\d{5}$/.test(String(code)) ? 3 : 2} />
              <div className="border-t border-border-subtle" />
              <Ticks ticks={ticks} decimals={/^[15]\d{5}$/.test(String(code)) ? 3 : 2} />
            </div>
          )}
        </div>

        <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-text-dim">
          {cost != null && <span>成本 <span className="text-accent font-mono">{fmtVal(cost)}</span></span>}
          <span><span className="inline-block w-2 h-2 rounded-sm align-middle mr-1" style={{ background: BUY_COLOR }} />B 买入<span className="mx-1.5" /><span className="inline-block w-2 h-2 rounded-sm align-middle mr-1" style={{ background: SELL_COLOR }} />S 卖出</span>
          {tdxOn && <span className="text-accent/70">TDX 盘口/分时已接入</span>}
          <span className="text-text-muted ml-auto">仅展示数据，不构成投资建议</span>
        </div>
      </div>
    </div>,
    document.body
  )
}
