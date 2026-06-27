import { useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { fetchJSON } from '../hooks/useApi'

const ACQUIRE = new Set(['BUY', 'ADD', 'BONUS'])
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
function CandleChart({ series, cost, actions }) {
  const [hover, setHover] = useState(null)
  const svgRef = useRef(null)
  const W = 720, H = 360, P = { l: 64, r: 16, t: 16, b: 28 }
  const innerW = W - P.l - P.r, innerH = H - P.t - P.b

  const allLows = series.map(d => d.low).filter(v => v > 0)
  const allHighs = series.map(d => d.high).filter(v => v > 0)
  const rangeMin = (allLows.length || cost != null) ? Math.min(...allLows, cost ?? Infinity) : 0
  const rangeMax = (allHighs.length || cost != null) ? Math.max(...allHighs, cost ?? -Infinity) : 1
  const range = rangeMax - rangeMin || 1

  const points = useMemo(() => {
    if (series.length < 2) return []
    return series.map((d, i) => {
      const x = P.l + (i / (series.length - 1)) * innerW
      const yOf = (v) => P.t + innerH - ((v - rangeMin) / range) * innerH
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
      return { v, y: P.t + innerH - ((v - rangeMin) / range) * innerH }
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
      const yPrice = (a.price != null && range > 0) ? P.t + innerH - ((a.price - rangeMin) / range) * innerH : (isBuy ? p.yLow : p.yHigh)
      out.push({ id: a.id, x: p.x, yPrice, yHigh: p.yHigh, yLow: p.yLow, date: td, price: a.price, shares: a.shares, type: a.action_type, isBuy })
    }
    return out
  }, [points, actions, rangeMin, range, innerH])

  const costY = cost != null && range > 0 ? P.t + innerH - ((cost - rangeMin) / range) * innerH : null
  const closes = series.map(d => d.close).filter(c => c > 0)

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
        {costY != null && (
          <g>
            <line x1={P.l} y1={costY} x2={W - P.r} y2={costY} stroke="var(--color-accent)" strokeWidth="1" strokeDasharray="4 3" opacity="0.7" />
            <text x={W - P.r - 4} y={costY - 4} fontSize="10" fill="var(--color-accent)" textAnchor="end" fontFamily="monospace">成本 {fmtVal(cost)}</text>
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
function MinuteChart({ points, prevClose }) {
  const [hover, setHover] = useState(null)
  const svgRef = useRef(null)
  const W = 720, H = 360, P = { l: 64, r: 16, t: 16, b: 28 }
  const innerW = W - P.l - P.r, innerH = H - P.t - P.b

  const { rows, rangeMin, range } = useMemo(() => {
    const prices = points.map(p => p.price).filter(v => v > 0)
    if (!prices.length) return { rows: [], rangeMin: 0, range: 1 }
    // 以昨收为中心对称, 让涨跌幅直观
    const maxDev = Math.max(...prices.map(p => Math.abs(p - prevClose)), prevClose * 0.001)
    const rMin = prevClose - maxDev, rng = maxDev * 2 || 1
    let cumPV = 0, cumV = 0
    const rs = points.map((p, i) => {
      const v = Number(p['手']) || 0
      cumPV += p.price * v; cumV += v
      const avg = cumV > 0 ? cumPV / cumV : p.price
      const x = P.l + (i / Math.max(1, points.length - 1)) * innerW
      const yOf = (val) => P.t + innerH - ((val - rMin) / rng) * innerH
      return { ...p, avg, x, y: yOf(p.price), yAvg: yOf(avg), i }
    })
    return { rows: rs, rangeMin: rMin, range: rng }
  }, [points, prevClose, innerH, innerW])

  const yTicks = useMemo(() => {
    const N = 4, step = range / N
    return Array.from({ length: N + 1 }, (_, i) => {
      const v = rangeMin + step * i
      return { v, pct: prevClose > 0 ? ((v / prevClose) - 1) * 100 : 0, y: P.t + innerH - ((v - rangeMin) / range) * innerH }
    })
  }, [rangeMin, range, prevClose, innerH])

  const priceLine = rows.map(r => `${r.x},${r.y}`).join(' ')
  const avgLine = rows.map(r => `${r.x},${r.yAvg}`).join(' ')
  const last = rows.length ? rows[rows.length - 1].price : prevClose
  const lineColor = last >= prevClose ? UP : DOWN
  const baseY = P.t + innerH - ((prevClose - rangeMin) / range) * innerH

  const onMove = (e) => {
    if (!svgRef.current || !rows.length) return
    const rect = svgRef.current.getBoundingClientRect()
    const cx = ((e.clientX - rect.left) / rect.width) * W
    const i = Math.round(((cx - P.l) / innerW) * (rows.length - 1))
    setHover(rows[Math.max(0, Math.min(rows.length - 1, i))])
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
        <polyline points={avgLine} fill="none" stroke="#c8a876" strokeWidth="1" opacity="0.85" />
        <polyline points={priceLine} fill="none" stroke={lineColor} strokeWidth="1.4" />
        {hover && <line x1={hover.x} y1={P.t} x2={hover.x} y2={P.t + innerH} stroke="var(--color-text-muted)" strokeWidth="1" strokeDasharray="2 3" />}
      </svg>
      <div className="absolute top-2 left-[68px] text-[10px] font-mono flex gap-3">
        <span style={{ color: lineColor }}>— 价格</span><span style={{ color: '#c8a876' }}>— 均价</span>
      </div>
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
function OrderBook({ data, prevClose }) {
  if (!data) return null
  const px = (p) => p == null ? '--' : <span className={colorPct(prevClose ? ((p / prevClose) - 1) * 100 : 0)}>{fmtVal(p)}</span>
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
function Ticks({ ticks }) {
  if (!ticks?.length) return null
  return (
    <div>
      <div className="text-[10.5px] text-text-muted mb-1">逐笔成交</div>
      <div className="max-h-[150px] overflow-y-auto pr-1">
        {ticks.map((t, i) => (
          <div key={i} className="flex justify-between items-center text-[10.5px] font-mono py-[2px]">
            <span className="text-text-muted">{t.time}</span>
            <span className="text-text">{fmtVal(t.price)}</span>
            <span style={{ color: t.dir === '买' ? UP : t.dir === '卖' ? DOWN : 'var(--color-text-muted)' }}>{Math.round(Number(t['手']) || 0)}{t.dir === '买' ? '↑' : t.dir === '卖' ? '↓' : ''}</span>
          </div>
        ))}
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
  const [actions, setActions] = useState([])
  const [minute, setMinute] = useState(null)
  const [book, setBook] = useState(null)
  const [ticks, setTicks] = useState([])
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState('')

  const code = holding?.stock_code
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
      fetchJSON(`/api/market/tdx/kline/${encodeURIComponent(code)}?type=${tab === '周' ? 'week' : 'month'}&limit=200`)
        .then(d => {
          const bars = d?.data?.bars || []
          if (!bars.length) { setErr('暂无K线'); setSeries([]) }
          else setSeries(bars.map(b => ({ date: (b.date || '').slice(0, 10), open: b.open, high: b.high, low: b.low, close: b.close })))
        }).catch(e => setErr(e?.message || '加载失败')).finally(done)
    } else {
      // 日K (akshare, 带成本线 + 自己买卖标记)
      Promise.all([
        fetchJSON(`/api/market/history/${encodeURIComponent(code)}?days=${days}`),
        fetchJSON(`/api/portfolio/${encodeURIComponent(code)}/actions`).catch(() => []),
      ]).then(([k, a]) => {
        if (!Array.isArray(k) || !k.length) { setErr('暂无 K 线数据'); setSeries([]) }
        else setSeries(k.map(x => ({ date: x.time, open: x.open, high: x.high, low: x.low, close: x.close })))
        setActions(Array.isArray(a) ? a : [])
      }).catch(e => setErr(e?.message || '加载失败')).finally(done)
    }
  }, [code, tab, days, tdxOn])

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
                : tab === '分时' ? <MinuteChart points={minute?.points || []} prevClose={prevClose} />
                : <CandleChart series={series} cost={tab === '日' ? cost : null} actions={tab === '日' ? actions : []} />}
            </div>
          </div>

          {/* 侧栏: 五档 + 逐笔 (TDX) */}
          {hasSide && (
            <div className="w-[200px] shrink-0 bg-surface-3 rounded-md p-2.5 space-y-3">
              <OrderBook data={book} prevClose={prevClose} />
              <div className="border-t border-border-subtle" />
              <Ticks ticks={ticks} />
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
