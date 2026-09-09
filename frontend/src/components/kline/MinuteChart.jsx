import { useMemo, useRef, useState } from 'react'
import { ACQUIRE, DOWN, UP, colorPct, colorPctHex, fmtHand, fmtPct, fmtVal } from './shared'

// ---------------------------------------------------------------------------
// 分时图 (TDX) — 价格线 + 均价线 + 昨收基准, 上方红下方绿
// ---------------------------------------------------------------------------
// 分时时刻 → 固定 240 分钟交易网格的槽位 [0,240]。9:30-11:30=0~120, 13:00-15:00=120~240。
// 让点按真实时刻落位(没出满则右侧留白), 而非按索引铺满整宽导致时间轴错位。
// 交易时段(按各市场本地时间, 源给的时刻就是本地时刻, 不做时区换算):
//   cn 9:30-11:30 + 13:00-15:00 = 240 分; hk 9:30-12:00 + 13:00-16:00 = 330 分;
//   us 9:30-16:00 美东连续 = 390 分。x 轴按时段总长铺, 午休不占宽度。
export const SESSIONS = {
  cn: { am: [570, 690], pm: [780, 900], labels: ['09:30', '11:30/13:00', '15:00'] },
  hk: { am: [570, 720], pm: [780, 960], labels: ['09:30', '12:00/13:00', '16:00'] },
  us: { am: [570, 960], pm: null, labels: ['09:30', '12:45', '16:00'] },
}

const _slotMax = (s) => (s.am[1] - s.am[0]) + (s.pm ? s.pm[1] - s.pm[0] : 0)

function _minuteSlot(t, s = SESSIONS.cn) {
  const parts = String(t || '').split(':')
  let mins = (Number(parts[0]) || 0) * 60 + (Number(parts[1]) || 0)
  const [amS, amE] = s.am
  // 伦敦冬令时收盘落到北京时间次日 0:30, 时钟数会绕回去 —— 比开盘早一小时以上就按跨日算
  if (mins < amS - 60) mins += 1440
  const amLen = amE - amS
  if (mins <= amS) return 0
  if (mins <= amE) return mins - amS
  if (!s.pm) return amLen
  const [pmS, pmE] = s.pm
  if (mins < pmS) return amLen                       // 午休并到上午收盘位置
  if (mins <= pmE) return amLen + (mins - pmS)
  return amLen + (pmE - pmS)                         // 收盘竞价/盘后快照贴到右端
}

// session 可传预设名(cn/hk/us), 也可直接传 {am,pm,labels} —— 日经/伦敦的时段随夏令时漂,
// 由后端按当天首个分时点算好传过来, 前端不硬编码。
export function MinuteChart({ points, prevClose, actions = [], day, height = 410,
                             session = 'cn', volUnit = '手' }) {
  const [hover, setHover] = useState(null)
  const svgRef = useRef(null)
  // t=30: 顶部预留图例专属条带(y≈17), 图从其下开始; volGap=24: 两图间隙容纳量图例行;
  // r=52: 右侧涨跌幅轴标专属条带——线画到 W-P.r 为止, 标签在条带里, 互不相压
  const W = 720, H = height, P = { l: 64, r: 52, t: 30, b: 28 }
  const innerW = W - P.l - P.r, innerH = H - P.t - P.b
  // volH/volGap 按可用高度给, 不能写死。调用方的 viewBox 高是 720*h/w 算出来的,
  // 容器越宽这个值越小 —— 宽屏 + 简介展开时实测 H 掉到 150, 而固定的
  // t30+b28+volH48+volGap24=130 会把价格区压到只剩 20 单位, 5 个刻度全叠成一坨。
  // 日经/KOSPI/FTSE 的源只给价不给量 → 不留量图的位置, 价格区占满(否则底下是一条空白带)
  const hasVol = points.some(p => Number(p['手']) > 0)
  const volH = hasVol ? Math.round(Math.min(48, innerH * 0.30)) : 0
  const volGap = hasVol ? Math.round(Math.min(24, innerH * 0.12)) : 0
  const priceH = innerH - volH - volGap
  const volTop = P.t + priceH + volGap

  const sess = (session && typeof session === 'object' ? session : SESSIONS[session]) || SESSIONS.cn
  const slotMax = _slotMax(sess)

  const { rows, rangeMin, range, volMax } = useMemo(() => {
    const prices = points.map(p => p.price).filter(v => v > 0)
    if (!prices.length) return { rows: [], rangeMin: 0, range: 1, volMax: 1 }
    // 范围=当日实际高低并含昨收(0%基准线始终可见), 上下加小 padding——
    // 高开高走/涨停日不再按最大偏离对称到下半场, 起伏占满可用高度
    const hi = Math.max(...prices, prevClose)
    const lo = Math.min(...prices, prevClose)
    const pad = Math.max((hi - lo) * 0.06, prevClose * 0.001)
    const rMin = lo - pad, rng = (hi - lo) + pad * 2 || 1
    const vMax = Math.max(1, ...points.map(p => Number(p['手']) || 0))
    let cumPV = 0, cumV = 0, prevPx = prevClose, lastUp = true
    const rs = points.map((p, i) => {
      const v = Number(p['手']) || 0
      cumPV += p.price * v; cumV += v
      const avg = cumV > 0 ? cumPV / cumV : p.price
      const x = P.l + (_minuteSlot(p.time, sess) / slotMax) * innerW   // 按真实时刻落位, 非按索引铺满
      const yOf = (val) => P.t + priceH - ((val - rMin) / rng) * priceH
      // 量柱买卖方向: tick 规则 — 比上一分钟涨=主动买(红), 跌=主动卖(绿), 平=延续
      const up = p.price > prevPx ? true : p.price < prevPx ? false : lastUp
      lastUp = up; prevPx = p.price
      return { ...p, avg, vol: v, x, y: yOf(p.price), yAvg: yOf(avg), i, up }
    })
    return { rows: rs, rangeMin: rMin, range: rng, volMax: vMax }
  }, [points, prevClose, priceH, innerW, sess, slotMax])

  const yTicks = useMemo(() => {
    // 刻度条数按价格区高度给: 标签字号约 11 个 viewBox 单位, 至少留 22 单位间距,
    // 否则矮图上 5 个标签会首尾相压(实测 priceH=20 时全叠成一坨)
    const N = Math.max(1, Math.min(4, Math.floor(priceH / 22)))
    const step = range / N
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
    // 同一分钟同方向的几笔是一张委托分价成交(实测 601138 09:45 三笔), 圆点和 B 字会原地
    // 叠印成一团 → 合成一个标记, 标 均价 与笔数(B×3)
    const byOrder = new Map()
    for (const a of actions) {
      if (norm(a.trade_date) !== matchDay) continue
      if (!a.at_time) continue
      const slot = _minuteSlot(a.at_time, sess)
      const isBuy = ACQUIRE.has(a.action_type)
      const price = Number(a.price) || 0
      const sh = Number(a.shares) || 0
      const k = `${isBuy ? 'B' : 'S'}@${slot}`
      const g = byOrder.get(k)
      if (g) { g.n++; g.shares += sh; g.amt += price * sh }
      else byOrder.set(k, { id: a.id, slot, isBuy, at: a.at_time, n: 1, shares: sh, amt: price * sh, price })
    }
    const out = [...byOrder.values()].map(g => {
      const price = g.shares > 0 ? g.amt / g.shares : g.price   // 分价成交取成交额加权均价
      return { ...g, price, x: P.l + (g.slot / slotMax) * innerW,
               y: price > 0 ? yOf(price) : (g.isBuy ? P.t + priceH : P.t) }
    })
    // 合并后相邻分钟的标记仍可能横向压字(标签约 18 单位宽), 同向且挨得近的逐个错开一行
    out.sort((a, b) => a.x - b.x)
    const placed = []
    for (const m of out) {
      let lane = 0
      while (placed.some(p => p.isBuy === m.isBuy && Math.abs(p.x - m.x) < 20 && p.lane === lane)) lane++
      m.lane = lane
      placed.push(m)
    }
    return out
  }, [rows, actions, day, rangeMin, range, priceH, innerW, sess, slotMax])

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

  if (rows.length < 2) return <div className="h-[360px] flex items-center justify-center text-text-dim text-[12px]">暂无分时(非交易时段, 或该标的的源不提供分时)</div>

  return (
    <div className="relative">
      <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} className="w-full h-auto select-none cursor-crosshair"
        onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
        {yTicks.map((t, i) => (
          <g key={i}>
            <line x1={P.l} y1={t.y} x2={W - P.r} y2={t.y} stroke="var(--color-border-subtle)" strokeWidth="1" strokeDasharray={Math.abs(t.v - prevClose) < range * 0.02 ? '0' : '2 3'} />
            <text x={P.l - 6} y={t.y + 3} fontSize="10" fill="var(--color-text-dim)" textAnchor="end" fontFamily="monospace">{fmtVal(t.v)}</text>
            <text x={W - 6} y={t.y + 3} fontSize="9" fill={colorPctHex(t.pct)} textAnchor="end" fontFamily="monospace">{fmtPct(t.pct)}</text>
          </g>
        ))}
        <line x1={P.l} y1={baseY} x2={W - P.r} y2={baseY} stroke="var(--color-text-muted)" strokeWidth="1" strokeDasharray="3 3" opacity="0.6" />
        {sess.labels.map((lbl, i) => (
          <text key={i} x={P.l + (i / 2) * innerW} y={H - 8} fontSize="10" fill="var(--color-text-dim)" textAnchor={i === 0 ? 'start' : i === 2 ? 'end' : 'middle'} fontFamily="monospace">{lbl}</text>
        ))}
        {/* 分时成交量图例: 两图间隙条带(volGap=24 专门留的), 右对齐, 与价格区底部轴标隔开 */}
        {hasVol && <>
          <text x={W - 6} y={volTop - 7} fontSize="9" fill="var(--color-text-muted)" textAnchor="end" fontFamily="monospace">
            量 <tspan fill={UP}>红买</tspan>/<tspan fill={DOWN}>绿卖</tspan>
          </text>
          {rows.map(r => {
            const h = (r.vol / volMax) * volH
            return <rect key={'mv' + r.i} x={r.x - 1} y={volTop + volH - h} width="1.6" height={Math.max(0.4, h)} fill={r.up ? UP : DOWN} opacity="0.8" />
          })}
          <line x1={P.l} y1={volTop + volH} x2={W - P.r} y2={volTop + volH} stroke="var(--color-border-subtle)" strokeWidth="1" />
        </>}
        {hasVol && <polyline points={avgLine} fill="none" stroke="#c8a876" strokeWidth="1" opacity="0.85" />}
        <polyline points={priceLine} fill="none" stroke={lineColor} strokeWidth="1.4" />
        {/* 当日买卖点: B 在下方, S 在上方, 虚线连到成交价圆点 */}
        {bsMarks.map(m => {
          const col = m.isBuy ? '#8df0b4' : '#ff9a9a'
          const off = 16 + m.lane * 11        // lane: 挨得近的同向标记错开的行号
          // B 默认在下 / S 默认在上; 错行后顶到价格区边界就翻到另一侧, 免得贴边又叠回一起
          const down = m.isBuy ? m.y + off <= P.t + priceH - 2 : m.y - off < P.t + 8
          const labelY = down ? Math.min(m.y + off, P.t + priceH - 2) : Math.max(m.y - off + 6, P.t + 8)
          return (
            <g key={'bs' + m.id}>
              <title>{`${m.at} ${m.isBuy ? '买入' : '卖出'} ${m.shares}股 @${fmtVal(m.price)}${m.n > 1 ? ` (${m.n}笔均价)` : ''}`}</title>
              <line x1={m.x} y1={labelY} x2={m.x} y2={m.y} stroke={col} strokeWidth="1" strokeDasharray="2 2" opacity="0.8" />
              <circle cx={m.x} cy={m.y} r="2.5" fill={col} />
              <text x={m.x} y={labelY} fontSize="10" fill={col} textAnchor="middle" fontWeight="bold">
                {(m.isBuy ? 'B' : 'S') + (m.n > 1 ? `×${m.n}` : '')}</text>
            </g>
          )
        })}
        {hover && <line x1={hover.x} y1={P.t} x2={hover.x} y2={volTop + volH} stroke="var(--color-text-muted)" strokeWidth="1" strokeDasharray="2 3" />}
        {/* 图例: 顶部专属条带(图内容从 P.t 起, 条带内没有别的东西); 悬浮时让位给 tooltip */}
        {!hover && (
          <text x={W - 6} y={17} fontSize="10" textAnchor="end" fontFamily="ui-monospace, monospace">
            <tspan fill={lineColor}>— 价格</tspan>
            {hasVol && <tspan dx="10" fill="#c8a876">— 均价</tspan>}
          </text>
        )}
      </svg>
      {hover && (
        <div className="absolute top-2 right-2 bg-surface-2 border border-border-med rounded-md px-2.5 py-1.5 text-[11px] font-mono pointer-events-none">
          <div className="text-text-dim">{hover.time}</div>
          <div>价 <span className="text-text-bright">{fmtVal(hover.price)}</span> <span className={colorPct(((hover.price / prevClose) - 1) * 100)}>{fmtPct(((hover.price / prevClose) - 1) * 100)}</span></div>
          {hasVol && <div className="text-text-dim">均 {fmtVal(hover.avg)} · {fmtHand(hover['手'], volUnit)}</div>}
        </div>
      )}
    </div>
  )
}
