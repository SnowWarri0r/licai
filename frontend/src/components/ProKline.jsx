import { useCallback, useEffect, useRef, useState } from 'react'
import { createChart, CandlestickSeries, HistogramSeries, LineSeries, CrosshairMode, LineStyle, createSeriesMarkers } from 'lightweight-charts'
import { fetchJSON, prefetchJSON } from '../hooks/useApi'
import { DayOverlay } from './kline/DayOverlay'
import { fmt, ACQUIRE, BUY_COLOR, SELL_COLOR } from './kline/shared'

import { ChipPrimitive, chipDist, turnoverFor } from './kline/chips'

const UP = '#cf5c5c', DOWN = '#5fa86c'   // A股 红涨绿跌
const MA_DEFS = [
  { n: 5, c: '#e8b04a' }, { n: 10, c: '#4aa6e0' }, { n: 20, c: '#cf6bcf' },
  { n: 30, c: '#6fc0b2' }, { n: 60, c: '#9a8cf0' },
]

function maLine(bars, n) {
  const out = []
  for (let i = n - 1; i < bars.length; i++) {
    let s = 0
    for (let j = i - n + 1; j <= i; j++) s += bars[j].close
    out.push({ time: bars[i].time, value: +(s / n).toFixed(3) })
  }
  return out
}

const _ema = (arr, p) => { const o = [], a = 2 / (p + 1); arr.forEach((v, i) => o.push(i === 0 ? v : o[i - 1] + a * (v - o[i - 1]))); return o }

// MACD(12,26,9): 返回 {dif,dea,hist} 各为 [{time,value}] 与 bars 对齐
function macdSeries(bars) {
  const cl = bars.map(b => b.close)
  const e12 = _ema(cl, 12), e26 = _ema(cl, 26)
  const dif = cl.map((_, i) => e12[i] - e26[i])
  const dea = _ema(dif, 9)
  const t = i => bars[i].time
  return {
    dif: dif.map((v, i) => ({ time: t(i), value: +v.toFixed(4) })),
    dea: dea.map((v, i) => ({ time: t(i), value: +v.toFixed(4) })),
    hist: dif.map((v, i) => ({ time: t(i), value: +((v - dea[i]) * 2).toFixed(4) })),
  }
}

// KDJ(9): 返回 {k,d,j} 各为 [{time,value}]
function kdjSeries(bars) {
  const n = bars.length, k = [], d = [], j = []
  for (let i = 0; i < n; i++) {
    const s = Math.max(0, i - 8)
    let ll = Infinity, hh = -Infinity
    for (let q = s; q <= i; q++) { ll = Math.min(ll, bars[q].low); hh = Math.max(hh, bars[q].high) }
    const rsv = hh > ll ? (bars[i].close - ll) / (hh - ll) * 100 : 50
    k[i] = i === 0 ? 50 : (2 / 3) * k[i - 1] + (1 / 3) * rsv
    d[i] = i === 0 ? 50 : (2 / 3) * d[i - 1] + (1 / 3) * k[i]
    j[i] = 3 * k[i] - 2 * d[i]
  }
  const t = i => bars[i].time
  return {
    k: k.map((v, i) => ({ time: t(i), value: +v.toFixed(2) })),
    d: d.map((v, i) => ({ time: t(i), value: +v.toFixed(2) })),
    j: j.map((v, i) => ({ time: t(i), value: +v.toFixed(2) })),
  }
}


// 成交量(手) / 成交额(元) 的人读格式: 万/亿分档, 小数点后一位够看
const fmtVol = (v) => v == null ? '--'
  : v >= 1e8 ? (v / 1e8).toFixed(2) + '亿手'
  : v >= 1e4 ? (v / 1e4).toFixed(1) + '万手'
  : Math.round(v).toLocaleString() + '手'
const fmtAmt = (v) => !v ? '--'
  : v >= 1e8 ? (v / 1e8).toFixed(2) + '亿'
  : v >= 1e4 ? (v / 1e4).toFixed(1) + '万'
  : Math.round(v).toLocaleString() + '元'

const GAP_UP = 'rgba(207,92,92,0.18)', GAP_DOWN = 'rgba(95,168,108,0.18)'   // 跳空缺口阴影: 红跳空/绿跳空

// 这根是阳线还是阴线。A股惯例按 收 vs 开, 但**收==开时改看昨收** —— 一字跌停那种
// 开=收=最低的十字星, 按"收>=开"会画成红的, 看着像涨(实测哈药股份 -10.02% 画成红柱)。
// 蜡烛体、影线、下面的量柱共用这一个判据, 否则三者会各红各绿。
const isUpBar = (b, prev) => (b.close === b.open
  ? (prev ? b.close >= prev.close : true)
  : b.close > b.open)
const volColor = (b, prev) => (isUpBar(b, prev) ? 'rgba(207,92,92,0.55)' : 'rgba(95,168,108,0.55)')

// 单个价 vs 昨收 的红绿(顶部 OHLC 那行用)。没有昨收(第一根)就不着色, 别瞎猜
const cmpColor = (v, prev) => (prev == null || v == null ? 'text-text'
  : v > prev ? 'text-bear' : v < prev ? 'text-bull' : 'text-text')
const GAP_MIN = 0.015   // 缺口≥1.5%才标, 过滤碎口, 只留"两根离得远"的真跳空

// 跳空缺口: 相邻两根价区不重叠的空白带(上跳=前高<后低 / 下跳=前低>后高),
// 盒子从缺口横向延伸到被回补的那根(价区重回带内)或末根, 让未回补的开口缺口成可见的价区带。
function detectGaps(bars) {
  const out = []
  for (let i = 1; i < bars.length; i++) {
    const p = bars[i - 1], c = bars[i]
    let lo, hi, color, up
    if (c.low > p.high && (c.low - p.high) / p.high >= GAP_MIN) { lo = p.high; hi = c.low; color = GAP_UP; up = true }
    else if (c.high < p.low && (p.low - c.high) / p.low >= GAP_MIN) { lo = c.high; hi = p.low; color = GAP_DOWN; up = false }
    else continue
    let end = bars.length - 1
    for (let j = i + 1; j < bars.length; j++) {
      if (up ? bars[j].low <= hi : bars[j].high >= lo) { end = j; break }   // 价格重回缺口带 = 回补
    }
    out.push({ t1: p.time, t2: bars[end].time, lo, hi, color })
  }
  return out
}

// lightweight-charts 自定义图元: 把缺口画成半透明盒子(衬在蜡烛之下)
class GapPaneRenderer {
  constructor(boxes) { this._boxes = boxes }
  draw(target) {
    target.useBitmapCoordinateSpace(scope => {
      const ctx = scope.context, hr = scope.horizontalPixelRatio, vr = scope.verticalPixelRatio
      for (const b of this._boxes) {
        if (b.x1 == null || b.x2 == null || b.y1 == null || b.y2 == null) continue
        const x = Math.min(b.x1, b.x2) * hr, w = Math.max(2, Math.abs(b.x2 - b.x1) * hr)
        const y = Math.min(b.y1, b.y2) * vr, h = Math.max(2, Math.abs(b.y2 - b.y1) * vr)
        ctx.fillStyle = b.color
        ctx.fillRect(x, y, w, h)
      }
    })
  }
}
class GapPaneView {
  constructor(src) { this._src = src; this._boxes = [] }
  update() {
    const { chart, series, gaps } = this._src
    const ts = chart?.timeScale()
    this._boxes = (ts && series) ? gaps.map(g => ({
      x1: ts.timeToCoordinate(g.t1), x2: ts.timeToCoordinate(g.t2),
      y1: series.priceToCoordinate(g.lo), y2: series.priceToCoordinate(g.hi), color: g.color,
    })) : []
  }
  renderer() { return new GapPaneRenderer(this._boxes) }
  zOrder() { return 'bottom' }
}
class GapPrimitive {
  constructor() { this.gaps = []; this.chart = null; this.series = null; this._view = new GapPaneView(this) }
  attached(p) { this.chart = p.chart; this.series = p.series; this._req = p.requestUpdate }
  detached() { this.chart = null; this.series = null }
  updateAllViews() { this._view.update() }
  paneViews() { return [this._view] }
  setGaps(gaps) { this.gaps = gaps; this._req?.() }
}

// 买卖点"价位圆点 + 虚线连回成交价": lightweight-charts 的 marker 只有 above/below/inBar
// 三种箭头, 画不出"圆点钉在真实成交价上、再一根虚线连到影线外箭头"这层信息(旧手绘K线有,
// 见 CandleChart)。用自定义图元补上: 圆点落在该日成交均价 p, 虚线从圆点连到影线端(买=最低下方/
// 卖=最高上方), 恰好接上 createSeriesMarkers 画的箭头。箭头与 B/S 文字仍交给 marker, 这里只补圆点+连线。
const TRADE_BUY = '#8df0b4', TRADE_SELL = '#ff9a9a'   // 亮薄荷/亮珊瑚, 穿过同色蜡烛体也看得清
class TradePaneRenderer {
  constructor(items) { this._items = items }
  draw(target) {
    target.useBitmapCoordinateSpace(scope => {
      const ctx = scope.context, hr = scope.horizontalPixelRatio, vr = scope.verticalPixelRatio
      for (const it of this._items) {
        if (it.x == null || it.yPrice == null || it.yAnchor == null) continue
        const x = it.x * hr, yp = it.yPrice * vr, ya = it.yAnchor * vr
        ctx.strokeStyle = it.color; ctx.lineWidth = Math.max(1, 1.4 * vr)
        ctx.setLineDash([3 * vr, 2 * vr])
        ctx.beginPath(); ctx.moveTo(x, yp); ctx.lineTo(x, ya); ctx.stroke()
        ctx.setLineDash([])
        ctx.beginPath(); ctx.arc(x, yp, 2.6 * vr, 0, Math.PI * 2)
        ctx.fillStyle = it.color; ctx.fill()
        ctx.lineWidth = Math.max(1, vr); ctx.strokeStyle = 'rgba(20,21,25,0.9)'; ctx.stroke()   // 描边, 别糊进蜡烛
      }
    })
  }
}
class TradePaneView {
  constructor(src) { this._src = src; this._items = [] }
  update() {
    const { chart, series, trades } = this._src
    const ts = chart?.timeScale()
    const GAP = 8   // 圆点连到影线外侧箭头处, 留一点空
    this._items = (ts && series) ? trades.map(t => {
      const yPrice = series.priceToCoordinate(t.price)
      const yEdge = series.priceToCoordinate(t.anchor)
      return {
        x: ts.timeToCoordinate(t.time), yPrice, color: t.color,
        yAnchor: yEdge == null ? null : (t.isBuy ? yEdge + GAP : yEdge - GAP),
      }
    }) : []
  }
  renderer() { return new TradePaneRenderer(this._items) }
  zOrder() { return 'top' }   // 画在蜡烛之上, 圆点/连线不被挡
}
class TradePrimitive {
  constructor() { this.trades = []; this.chart = null; this.series = null; this._view = new TradePaneView(this) }
  attached(p) { this.chart = p.chart; this.series = p.series; this._req = p.requestUpdate }
  detached() { this.chart = null; this.series = null }
  updateAllViews() { this._view.update() }
  paneViews() { return [this._view] }
  setTrades(trades) { this.trades = trades; this._req?.() }
}

// 券商式可拖动/缩放 K线(TradingView lightweight-charts): 蜡烛 + 量能 + MA5/10/20, 滚轮缩放/拖动平移/十字光标。
// cost / actions: 持有该票时画成本线与买卖点(榜单里的票没持仓就都为空, 什么也不画)。
// ⚠️ 两者走 ref + 独立 effect 重画, **不进主 effect 依赖** —— 否则改一下成本/流水
// 就把整张 K 线重新拉一次网络(与下面 volMode 同一个坑)。
export default function ProKline({ code, days = 250, height = 460, fill = false, lhbDate = '', cost = null, actions = null, period = 'day' }) {
  const isDay = period === 'day'   // 周/月: 走 TDX kline, 关掉"点蜡烛看分时"和续史加载(那是日K概念)
  const wrapRef = useRef(null)
  const volWrapRef = useRef(null)                  // 独立量/额副图容器
  const volChartRef = useRef(null)
  const volSeriesRef = useRef(null)
  const subLinesRef = useRef(null)                 // 副图 3 条线(MACD DIF/DEA 或 KDJ K/D/J)
  const syncingRef = useRef(false)                 // 两图时间轴互相同步时防回环
  const alignScalesRef = useRef(null)              // 对齐两图价格轴宽度(否则柱子错位)
  const [volMode, setVolMode] = useState('量')     // 量 | 额
  const [volLegend, setVolLegend] = useState(null) // 副图 hover 的具体数字
  const chartRef = useRef(null)
  const seriesRef = useRef({})
  const prevCloseLineRef = useRef(null)
  const barsRef = useRef([])
  const costRef = useRef(cost)
  const actionsRef = useRef(actions)
  const costLineRef = useRef(null)
  const markersRef = useRef(null)          // { candle, api } —— 换图后要重建
  const overlayCandleRef = useRef(null)
  const [legend, setLegend] = useState(null)
  const [err, setErr] = useState('')
  const [loading, setLoading] = useState(true)
  const [hint, setHint] = useState(null)           // 点蜡烛 → {x, y, date, prevClose} 「分时›」tooltip
  const [intraday, setIntraday] = useState(null)   // 点 tooltip → {date, prevClose} 浮层
  const intradayRef = useRef(null)
  intradayRef.current = intraday
  const volModeRef = useRef(volMode)               // 建图 effect 里的回调要读当前口径
  volModeRef.current = volMode
  const isDayRef = useRef(isDay)                    // 点蜡烛看分时仅日K; period 会变故用 ref
  isDayRef.current = isDay
  // 筹码分布(仅日K + A股个股): 流通股本变动表 → 每根换手率 → 悬停日/最新一日收盘时的筹码
  const [showChips, setShowChips] = useState(() => { try { return localStorage.getItem('licai.kline.chips') !== '0' } catch { return true } })
  const showChipsRef = useRef(showChips)
  showChipsRef.current = showChips
  const [chipInfo, setChipInfo] = useState(null)
  const [chipAvail, setChipAvail] = useState(false)
  const scheduleRef = useRef(null)
  const turnRef = useRef(null)
  const chipKeyRef = useRef(null)                   // 当前悬停日(null = 最新一根)
  const updateChips = useCallback((key) => {
    chipKeyRef.current = key
    const prim = seriesRef.current?.chipPrim
    const bars = barsRef.current
    if (!prim) return
    if (!showChipsRef.current || !isDayRef.current || !turnRef.current || !bars?.length) { prim.setDist(null); setChipInfo(null); return }
    let t = bars.length - 1
    if (key) { const i = bars.findIndex(b => b.time === key); if (i >= 0) t = i }
    const d = chipDist(bars, turnRef.current, t)
    prim.setDist(d)
    setChipInfo(d && { time: d.time, winner: d.winner, avg: d.avg, p5: d.p5, p95: d.p95, cover: d.cover, hover: !!key })
  }, [])

  // 成本线 + 买卖点: 从 ref 读, 可被主 effect(画完K线后)和下面的独立 effect(成本/流水变了)
  // 各自调用。同日同方向多笔合成一个标记(B2/S3), 与持仓那张图口径一致。
  const drawOverlay = useCallback(() => {
    const candle = seriesRef.current?.candle
    const bars = barsRef.current
    if (!candle || !bars?.length) return
    if (overlayCandleRef.current !== candle) {     // 换了图表实例 → 旧句柄作废
      costLineRef.current = null; markersRef.current = null
      overlayCandleRef.current = candle
    }
    if (costLineRef.current) {
      try { candle.removePriceLine(costLineRef.current) } catch { /* 图已销毁 */ }
      costLineRef.current = null
    }
    const c = costRef.current
    if (c != null && c > 0) {
      costLineRef.current = candle.createPriceLine({
        price: c, color: '#c8a876', lineWidth: 1, lineStyle: LineStyle.Solid,
        axisLabelVisible: true, title: '成本',
      })
    }
    const barByTime = new Map(bars.map(b => [b.time, b]))
    const byDay = new Map()
    for (const a of (actionsRef.current || [])) {
      const td = (a.trade_date || '').slice(0, 10)
      if (!barByTime.has(td)) continue             // 不在可见区间的流水不打点
      const isBuy = ACQUIRE.has(a.action_type)
      const k = `${isBuy ? 'B' : 'S'}@${td}`
      const px = Number(a.price) || 0, sh = Number(a.shares) || 0
      const g = byDay.get(k)
      if (g) { g.n++; g.pv += px * sh; g.sh += sh }
      else byDay.set(k, { time: td, isBuy, n: 1, pv: px * sh, sh })
    }
    const groups = [...byDay.values()].sort((x, y) => (x.time < y.time ? -1 : 1))
    const markers = groups.map(g => ({
      time: g.time,
      position: g.isBuy ? 'belowBar' : 'aboveBar',
      color: g.isBuy ? BUY_COLOR : SELL_COLOR,
      shape: g.isBuy ? 'arrowUp' : 'arrowDown',
      text: (g.isBuy ? 'B' : 'S') + (g.n > 1 ? String(g.n) : ''),
    }))
    if (!markersRef.current) markersRef.current = createSeriesMarkers(candle, markers)
    else markersRef.current.setMarkers(markers)
    // 圆点钉在该日成交均价 + 虚线连到影线外的箭头(补 marker 只有箭头这层缺失)
    const trades = groups.map(g => {
      const bar = barByTime.get(g.time)
      const price = g.sh > 0 ? g.pv / g.sh : bar.close
      return { time: g.time, price, isBuy: g.isBuy,
               anchor: g.isBuy ? bar.low : bar.high,
               color: g.isBuy ? TRADE_BUY : TRADE_SELL }
    })
    seriesRef.current?.tradePrim?.setTrades(trades)
  }, [])

  // 成本/流水变了只重画覆盖层, 不动 K 线数据
  useEffect(() => {
    costRef.current = cost
    actionsRef.current = actions
    drawOverlay()
  }, [cost, actions, drawOverlay])
  const xhairRef = useRef(false)                   // 两图光标互相同步时防回环
  const depthRef = useRef(days)                    // 当前已加载的K线深度(根数), 往左拖到头自动升档
  const moreBusyRef = useRef(false)
  const exhaustedRef = useRef(false)               // 服务端没有更早历史了(新股/次新)
  const loadMoreRef = useRef(null)                 // 数据 effect 里注入, 建图 effect 的订阅回调调用

  // 副图统一绘制: 量/额=直方图, MACD=hist+DIF/DEA, KDJ=K/D/J 三线。用已有 bars, 不重新请求。
  const paintSub = useCallback((mode) => {
    const vol = volSeriesRef.current, lines = subLinesRef.current, bars = barsRef.current
    if (!vol || !lines || !bars?.length) return
    const EMPTY = []
    if (mode === 'MACD') {
      const m = macdSeries(bars)
      vol.setData(m.hist.map(p => ({ ...p, color: p.value >= 0 ? 'rgba(207,92,92,0.55)' : 'rgba(95,168,108,0.55)' })))
      lines[0].setData(m.dif); lines[1].setData(m.dea); lines[2].setData(EMPTY)
    } else if (mode === 'KDJ') {
      const k = kdjSeries(bars)
      vol.setData(EMPTY)
      lines[0].setData(k.k); lines[1].setData(k.d); lines[2].setData(k.j)
    } else {
      vol.setData(bars.map((b, i) => ({ time: b.time, value: mode === '额' ? (b.amount || 0) : (b.volume || 0), color: volColor(b, bars[i - 1]) })))
      lines.forEach(l => l.setData(EMPTY))
    }
  }, [])

  // 切换 量/额/MACD/KDJ: 用已有 bars 重绘副图, 不重新请求
  useEffect(() => {
    if (!volSeriesRef.current || !barsRef.current?.length) return
    paintSub(volMode)
    // 各口径量级差很大(万/亿 vs ±小数): 每次换重开自动量程, 否则柱子被压平或顶出
    try { volChartRef.current?.priceScale('right').applyOptions({ autoScale: true }) } catch { /* 尺寸未就绪 */ }
    requestAnimationFrame(() => alignScalesRef.current?.())   // 刻度宽度变, 重新对齐
  }, [volMode, paintSub])

  // 流通股本变动表: 换股票拉一次(ETF/非A股个股没有, 筹码开关不出现)
  useEffect(() => {
    scheduleRef.current = null; turnRef.current = null; setChipAvail(false); updateChips(null)
    const bare = String(code || '').replace(/\D/g, '').slice(-6)
    if (!isDay || bare.length !== 6 || /^(1[56]|5[0-8])/.test(bare)) return
    let alive = true
    fetchJSON(`/api/market/float-shares/${bare}`).then(d => {
      if (!alive || !d?.schedule?.length) return
      scheduleRef.current = d.schedule
      turnRef.current = turnoverFor(barsRef.current, d.schedule)
      setChipAvail(true)
      updateChips(chipKeyRef.current)
    }).catch(() => {})
    return () => { alive = false }
  }, [code, isDay, updateChips])

  useEffect(() => {
    try { localStorage.setItem('licai.kline.chips', showChips ? '1' : '0') } catch { /* 隐私模式 */ }
    updateChips(chipKeyRef.current)
  }, [showChips, updateChips])

  // 建图(一次)
  useEffect(() => {
    if (!wrapRef.current) return
    const chart = createChart(wrapRef.current, {
      autoSize: true,
      // attributionLogo: 关掉图上那枚 TradingView 角标。许可要求的是「用户可见页面上有
      // 署名 + tradingview.com 链接」, 图上角标只是满足它的一种方式 —— 我们改成放在
      // 设置页的开源许可区(见 Settings.jsx), 所以这里可以关。别直接删了不补。
      layout: { background: { color: 'transparent' }, textColor: '#9aa0a6', fontSize: 11,
        attributionLogo: false,
        fontFamily: 'ui-sans-serif, system-ui, -apple-system, sans-serif' },
      grid: { vertLines: { color: 'rgba(255,255,255,0.04)' }, horzLines: { color: 'rgba(255,255,255,0.04)' } },
      crosshair: { mode: CrosshairMode.Normal,
        vertLine: { color: 'rgba(200,168,118,0.5)', width: 1, style: 2 },
        horzLine: { color: 'rgba(200,168,118,0.5)', width: 1, style: 2 } },
      rightPriceScale: { borderColor: 'rgba(255,255,255,0.08)', scaleMargins: { top: 0.08, bottom: 0.06 } },
      // 日期轴交给下方量/额副图统一显示, 主图隐掉避免上下两条重复
      timeScale: { borderColor: 'rgba(255,255,255,0.08)', rightOffset: 4, minBarSpacing: 1.5, visible: false },
    })
    chartRef.current = chart
    const candle = chart.addSeries(CandlestickSeries, {
      upColor: UP, downColor: DOWN, borderUpColor: UP, borderDownColor: DOWN, wickUpColor: UP, wickDownColor: DOWN,
    })
    const mas = MA_DEFS.map(m => chart.addSeries(LineSeries, { color: m.c, lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false }))
    const gapPrim = new GapPrimitive()
    candle.attachPrimitive(gapPrim)
    const tradePrim = new TradePrimitive()   // 买卖点圆点+虚线(补 marker 箭头之外的"钉在成交价")
    candle.attachPrimitive(tradePrim)
    const chipPrim = new ChipPrimitive()     // 筹码分布(右侧横向柱, 垫在蜡烛下)
    candle.attachPrimitive(chipPrim)
    seriesRef.current = { candle, mas, gapPrim, tradePrim, chipPrim }

    const timeKey = (t) => typeof t === 'string' ? t
      : `${t.year}-${String(t.month).padStart(2, '0')}-${String(t.day).padStart(2, '0')}`

    // 两图各有一套十字光标: 只在主图上比划时, 副图没有竖线, 对不出那根柱子是哪一天。
    // 把光标位置双向同步 —— 悬到哪一根, 上下两图同时亮同一根, 两行图例也一起给出该日
    // 的 OHLC 与 量/额。value 传该图自己那根的值, 让横线落在蜡烛/柱子上而不是乱飘。
    const syncXhair = (dst, dstSeries, value, time) => {
      if (!dst || xhairRef.current) return
      xhairRef.current = true
      try {
        if (time == null || value == null) dst.clearCrosshairPosition()
        else dst.setCrosshairPosition(value, time, dstSeries)
      } catch { /* 尺寸未就绪/系列已销毁 */ }
      xhairRef.current = false
    }

    const fillPriceLegend = (key) => {
      const arr = barsRef.current
      const i = arr.findIndex(b => b.time === key)
      if (i < 0) { setLegend(null); return }
      const d = arr[i]
      const prev = i > 0 ? arr[i - 1].close : null
      // 距今: 从这根收盘到最新一根收盘的累计涨跌 —— 回看"那天到现在赚/亏多少"
      const last = arr.length ? arr[arr.length - 1].close : null
      setLegend({ time: key, o: d.open, h: d.high, l: d.low, c: d.close, prev,
                  pct: prev ? (d.close / prev - 1) * 100 : null,
                  since: (last && d.close && i < arr.length - 1) ? (last / d.close - 1) * 100 : null })
    }


    // 量/额独立副图: 叠在主图里只有一条压扁的色带, 读不出某天到底多少; 拆成自己的图表
    // 后有独立纵轴刻度, 加上 hover 出具体数字。两图时间轴双向同步, 拖动/缩放一起走。
    let volChart = null
    if (volWrapRef.current) {
      volChart = createChart(volWrapRef.current, {
        autoSize: true,
        layout: { background: { color: 'transparent' }, textColor: '#9aa0a6', fontSize: 11,
          attributionLogo: false,
          fontFamily: 'ui-sans-serif, system-ui, -apple-system, sans-serif' },
        grid: { vertLines: { color: 'rgba(255,255,255,0.04)' }, horzLines: { color: 'rgba(255,255,255,0.04)' } },
        crosshair: { mode: CrosshairMode.Normal,
          vertLine: { color: 'rgba(200,168,118,0.5)', width: 1, style: 2 },
          horzLine: { color: 'rgba(200,168,118,0.5)', width: 1, style: 2 } },
        // bottom 留一点: 0 刻度紧贴容器下沿会被裁掉半截(副图还要分一截高度给日期轴)
        rightPriceScale: { borderColor: 'rgba(255,255,255,0.08)', scaleMargins: { top: 0.12, bottom: 0.10 } },
        timeScale: { borderColor: 'rgba(255,255,255,0.08)', rightOffset: 4, minBarSpacing: 1.5,
          visible: true, timeVisible: false },
        // 副图纵轴不给拖: 量柱看的是相对高低, 手工量程没有用处, 却一拖就退出自动量程
        // (再切「量↔额」量级差 5 个数量级, 柱子被压平)。时间轴照旧可拖可缩, 与主图同步。
        handleScale: { mouseWheel: true, pinch: true,
                       axisPressedMouseMove: { time: true, price: false } },
        handleScroll: true,
      })
      volChartRef.current = volChart
      // 纵轴刻度自适应: 量/额是万/亿大数, MACD/KDJ 是 ±小数——同一右轴, 格式化按量级切
      volSeriesRef.current = volChart.addSeries(HistogramSeries, {
        priceFormat: {
          type: 'custom', minMove: 0.01,
          formatter: (v) => {
            const a = Math.abs(v)
            if (!v) return '0'
            if (a >= 1e8) return (v / 1e8).toFixed(a >= 1e9 ? 0 : 1) + '亿'
            if (a >= 1e4) return (v / 1e4).toFixed(0) + '万'
            if (a < 100) return v.toFixed(2)          // MACD/KDJ
            return String(Math.round(v))
          },
        },
      })
      // MACD DIF/DEA 或 KDJ K/D/J 复用这 3 条线(同右轴); 量/额模式下清空隐藏
      const subLine = (c) => volChart.addSeries(LineSeries, { color: c, lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false })
      subLinesRef.current = [subLine('#e8b04a'), subLine('#4aa6e0'), subLine('#cf6bcf')]

      const syncFrom = (src, dst) => (range) => {
        if (!range || syncingRef.current) return
        syncingRef.current = true
        try { dst.timeScale().setVisibleLogicalRange(range) } catch { /* 尺寸未就绪时忽略 */ }
        syncingRef.current = false
      }
      chart.timeScale().subscribeVisibleLogicalRangeChange(syncFrom(chart, volChart))
      volChart.timeScale().subscribeVisibleLogicalRangeChange(syncFrom(volChart, chart))

      // 两图价格轴宽度必须一致, 否则上下柱子对不齐: 绘图区 = 容器宽 − 轴宽, 而主图刻度是
      // 「1500.00」(宽)、副图是「500亿」(窄), 轴宽差十几像素, 同一根就落在不同 x 上。
      // 取两者实测宽度的较大值, 双向设成 minimumWidth(该选项的官方用途正是垂直堆叠对齐)。
      // 两阶段: 先把 minimumWidth 解除, 让两边报告"自然宽度"; 下一帧再按较大者 pin 回去。
      // 不解除就量不到新刻度的自然宽 —— width() 永远 ≥ 已 pin 的值, 于是切「成交额」后
      // (「200亿」比「40万」宽)副图实际变宽而主图没跟上, 又错开。
      const alignScales = () => {
        const ps = [chart.priceScale('right'), volChart.priceScale('right')]
        try {
          ps.forEach(x => x.applyOptions({ minimumWidth: 0 }))
        } catch { return }
        requestAnimationFrame(() => {
          try {
            const w = Math.max(...ps.map(x => x.width()))
            if (w > 0) ps.forEach(x => x.applyOptions({ minimumWidth: w }))
          } catch { /* 尺寸未就绪时忽略, 下次数据/尺寸变化再对齐 */ }
        })
      }
      alignScalesRef.current = alignScales
      requestAnimationFrame(alignScales)

      volChart.subscribeCrosshairMove(param => {
        if (!param.time) { setVolLegend(null); syncXhair(chart, candle, null); updateChips(null); return }
        const key = timeKey(param.time)
        updateChips(key)
        const bar = barsRef.current.find(b => b.time === key)
        setVolLegend({ time: key, volume: bar?.volume, amount: bar?.amount })
        fillPriceLegend(key)                              // 上图那行也给出这天的 OHLC
        syncXhair(chart, candle, bar ? bar.close : null, param.time)
      })
    }

    // 十字光标 → 顶部图例(日期/OHLC/较昨收涨跌%) + 副图那行的量/额
    chart.subscribeCrosshairMove(param => {
      if (!param.time) {
        setLegend(null); setVolLegend(null)
        syncXhair(volChart, volSeriesRef.current, null)
        updateChips(null)
        return
      }
      const key = timeKey(param.time)
      updateChips(key)
      const bar = barsRef.current.find(b => b.time === key)
      fillPriceLegend(key)
      setVolLegend({ time: key, volume: bar?.volume, amount: bar?.amount })
      const v = volModeRef.current === '额' ? bar?.amount : bar?.volume
      syncXhair(volChart, volSeriesRef.current, v ?? null, param.time)
    })

    // 点蜡烛 → 出「分时›」tooltip; 浮层开着时点K线 → 收起浮层(同花顺式浮层交互)
    chart.subscribeClick(param => {
      if (intradayRef.current) { setIntraday(null); setHint(null); return }
      if (!isDayRef.current) { setHint(null); return }   // 周/月不看当日分时
      const t = param.time
      if (!t || !param.point) { setHint(null); return }
      const key = typeof t === 'string' ? t
        : `${t.year}-${String(t.month).padStart(2, '0')}-${String(t.day).padStart(2, '0')}`
      const arr = barsRef.current
      const i = arr.findIndex(b => b.time === key)
      if (i < 0) { setHint(null); return }
      setHint({ x: param.point.x, y: param.point.y, date: key,
                prevClose: arr[i - 1]?.close ?? arr[i].open, prevIsOpen: !arr[i - 1] })
    })

    // 往左拖/缩放看到最早的几根 → 自动续加载更早的历史
    chart.timeScale().subscribeVisibleLogicalRangeChange(r => {
      if (r && r.from < 12) loadMoreRef.current?.()
    })

    return () => {
      volChartRef.current?.remove(); volChartRef.current = null; volSeriesRef.current = null
      chart.remove(); chartRef.current = null
    }
  }, [])





  // 换股票 / 周期 → 拉数据填充
  useEffect(() => {
    if (!code) return
    let alive = true
    setLoading(true); setErr('')
    depthRef.current = days
    exhaustedRef.current = false

    const paint = (bars) => {
      barsRef.current = bars
      const { candle, mas, gapPrim } = seriesRef.current
      // 逐根显式给色: 图表库内部是 close>=open 判红绿, 一字跌停(收==开)会被判成红
      candle.setData(bars.map((b, i) => {
        const col = isUpBar(b, bars[i - 1]) ? UP : DOWN
        return { time: b.time, open: b.open, high: b.high, low: b.low, close: b.close,
                 color: col, borderColor: col, wickColor: col }
      }))
      // 副图(量/额/MACD/KDJ)统一走 paintSub, 读 volModeRef 当前口径(不进本 effect 依赖,
      // 免得切口径重拉整张 K 线; 也避开请求在飞时切口径的竞态)。
      paintSub(volModeRef.current)
      mas.forEach((s, i) => s.setData(maLine(bars, MA_DEFS[i].n)))
      gapPrim?.setGaps(detectGaps(bars))
      // 换股票/周期是全新价位与量级: 之前在轴上拖出来的手工量程留着必然不合身, 一并复位
      try {
        chartRef.current?.priceScale('right').applyOptions({ autoScale: true })
        volChartRef.current?.priceScale('right').applyOptions({ autoScale: true })
      } catch { /* 尺寸未就绪 */ }
      requestAnimationFrame(() => alignScalesRef.current?.())   // 刻度文本变长 → 轴宽变 → 重新对齐
      // 昨收线: 最新一根的前一日收盘 → 一眼看出今天这根(哪怕收红阳线)是否还在昨收下方
      if (prevCloseLineRef.current) { candle.removePriceLine(prevCloseLineRef.current); prevCloseLineRef.current = null }
      const prevClose = bars.length >= 2 ? bars[bars.length - 2].close : null
      if (prevClose != null) {
        prevCloseLineRef.current = candle.createPriceLine({
          price: prevClose, color: '#c8a876', lineWidth: 1, lineStyle: LineStyle.Dashed,
          axisLabelVisible: true, title: '昨收',
        })
      }
      drawOverlay()          // 成本线 + 买卖点(持有该票才有)
      turnRef.current = turnoverFor(bars, scheduleRef.current)
      updateChips(null)
    }

    // 归一成 bars: 日→akshare history(数组, 前复权); 周/月→TDX kline(data.bars)
    const fetchBars = async (want) => {
      if (isDay) {
        const k = await prefetchJSON(`/api/market/history/${encodeURIComponent(code)}?days=${want}`)
        return Array.isArray(k) ? k.map(x => ({ time: x.time, open: x.open, high: x.high, low: x.low, close: x.close, volume: x.volume, amount: x.amount })) : null
      }
      const d = await prefetchJSON(`/api/market/tdx/kline/${encodeURIComponent(code)}?type=${period === 'week' ? 'week' : 'month'}&limit=${Math.min(want, 500)}`)
      const bs = d?.data?.bars
      return Array.isArray(bs) ? bs.map(b => ({ time: String(b.date).slice(0, 10), open: b.open, high: b.high, low: b.low, close: b.close, volume: b.volume, amount: b.amount })) : null
    }

    // 日K: 拖到最早处 → 升档拉更长历史(×3), 保持视窗不跳; 周/月一次拉够, 不续史
    loadMoreRef.current = isDay ? () => {
      if (!alive || moreBusyRef.current || exhaustedRef.current) return
      const cur = depthRef.current
      if (barsRef.current.length + 30 < cur || cur >= 2400) { exhaustedRef.current = true; return }
      moreBusyRef.current = true
      const want = Math.min(cur * 3, 2400)
      fetchBars(want).then(bars => {
        if (!alive || !bars) return
        depthRef.current = want
        if (bars.length <= barsRef.current.length) { exhaustedRef.current = true; return }
        const ts = chartRef.current?.timeScale()
        const vr = ts?.getVisibleRange()
        paint(bars)
        if (vr) ts?.setVisibleRange(vr)
      }).catch(() => {}).finally(() => { moreBusyRef.current = false })
    } : null

    let warmTimer = null
    fetchBars(days)
      .then(bars => {
        if (!alive) return
        if (!bars || !bars.length) { setErr('暂无 K 线数据'); return }
        paint(bars)
        const b = barsRef.current
        // 初始视窗只看最近约70根, 更早往左拖/缩放就有(fitContent 会全塞进屏幕看不清近期)
        const ts = chartRef.current?.timeScale()
        if (b.length > 80) ts?.setVisibleLogicalRange({ from: b.length - 70, to: b.length + 3 })
        else ts?.fitContent()
        // 从龙虎榜榜单点进来直接弹席位浮层(仅日K)
        if (isDay && lhbDate) {
          const i = b.findIndex(x => x.time === lhbDate)
          if (i >= 0) setIntraday({ date: lhbDate, prevClose: b[i - 1]?.close ?? b[i].open, prevIsOpen: !b[i - 1], tab: '龙虎榜' })
        }
        // 续史预热(仅日K)
        if (isDay && b.length >= days && days * 3 <= 2400) {
          warmTimer = setTimeout(() => { if (alive) prefetchJSON(`/api/market/history/${encodeURIComponent(code)}?days=${days * 3}`).catch(() => {}) }, 1500)
        }
      })
      .catch(e => alive && setErr(e?.message || '加载失败'))
      .finally(() => alive && setLoading(false))
    return () => { alive = false; loadMoreRef.current = null; if (warmTimer) clearTimeout(warmTimer) }
  }, [code, days, lhbDate, drawOverlay, isDay, period, paintSub])

  return (
    <div className={fill ? 'relative flex flex-col h-full' : 'relative'}>
      <div className="flex items-center gap-3 mb-1 text-[10.5px] h-4 shrink-0">
        {legend
          ? <span className="font-mono text-text-dim flex gap-2.5 flex-wrap">
              <span className="text-text-muted">{legend.time}</span>
              {/* 四个价都跟**昨收**比着上色(通达信口径), 不是跟开盘价比 —— 跌停那天
                  开=收=最低, 按"收>=开"整行都是红的, 而它们全都低于昨收。
                  高/低原来是写死的红/绿, 同样错: 跌停日的最高价也在昨收下面。 */}
              <span>开<span className={cmpColor(legend.o, legend.prev)}>{fmt(legend.o)}</span></span>
              <span>高<span className={cmpColor(legend.h, legend.prev)}>{fmt(legend.h)}</span></span>
              <span>低<span className={cmpColor(legend.l, legend.prev)}>{fmt(legend.l)}</span></span>
              <span>收<span className={cmpColor(legend.c, legend.prev)}>{fmt(legend.c)}</span></span>
              {legend.pct != null && (
                <span className={`font-semibold ${legend.pct >= 0 ? 'text-bear' : 'text-bull'}`}>
                  {legend.pct >= 0 ? '+' : ''}{legend.pct.toFixed(2)}%
                </span>
              )}
              {legend.since != null && (
                <span className="text-text-muted">
                  距今<span className={`font-semibold ${legend.since >= 0 ? 'text-bear' : 'text-bull'}`}>
                    {legend.since >= 0 ? '+' : ''}{legend.since.toFixed(2)}%
                  </span>
                </span>
              )}
            </span>
          : <span className="text-text-muted flex gap-2 flex-wrap items-baseline">
              {MA_DEFS.map(m => <span key={m.n} style={{ color: m.c }}>— MA{m.n}</span>)}
              <span style={{ color: '#c8a876' }}>┄ 昨收</span>
              <span>滚轮缩放 · 点蜡烛看当日分时</span>
            </span>}
        {chipAvail && (
          <span className="ml-auto flex items-center gap-2 shrink-0 font-mono">
            {showChips && chipInfo && (
              <span className="text-text-dim" title={`按换手率推算的筹码分布(每天的成交被之后的换手逐步换走); 只统计已加载的 K 线${chipInfo.cover < 0.9 ? `, 窗口内筹码只覆盖 ${Math.round(chipInfo.cover * 100)}%, 往左拖加载更早的历史会更准` : ''}。描述持仓成本分布, 不预示涨跌。`}>
                <span className="text-text-muted">{chipInfo.hover ? chipInfo.time.slice(5) : '最新'}</span>
                {' '}获利 <span className="text-bear">{Math.round(chipInfo.winner * 100)}%</span>
                {' '}· 平均成本 <span style={{ color: '#e8c77a' }}>{fmt(chipInfo.avg)}</span>
                {chipInfo.p5 != null && <> · 90%筹码 {fmt(chipInfo.p5)}~{fmt(chipInfo.p95)}</>}
                {chipInfo.cover < 0.9 && <span className="text-text-muted"> · 覆盖{Math.round(chipInfo.cover * 100)}%</span>}
              </span>
            )}
            <button onClick={() => setShowChips(v => !v)}
              className={`px-1.5 rounded leading-4 ${showChips ? 'bg-accent/20 text-accent' : 'text-text-dim hover:text-text'}`}>筹码</button>
          </span>
        )}
      </div>
      <div className={`relative ${fill ? 'flex-1 min-h-0' : ''}`} style={fill ? { width: '100%' } : { width: '100%', height: Math.max(120, height - 156) /* 156 = 副图132 + 切换条与间距 */ }}>
        <div ref={wrapRef} className="absolute inset-0" />

        {/* 点蜡烛 → 「分时›」tooltip(跟随点击位置) */}
        {hint && !intraday && (
          <button
            onClick={() => { setIntraday({ date: hint.date, prevClose: hint.prevClose, prevIsOpen: hint.prevIsOpen }); setHint(null) }}
            className="absolute z-20 text-[10.5px] font-semibold px-2 py-1 rounded-lg cursor-pointer whitespace-nowrap"
            style={{ left: Math.min(Math.max(hint.x + 8, 4), (wrapRef.current?.clientWidth || 400) - 120),
                     top: Math.max(hint.y - 34, 4),
                     background: '#c8a876', color: '#1a1b1f',
                     boxShadow: '0 4px 14px rgba(0,0,0,0.6)' }}>
            {hint.date.slice(5)} 分时 ›
          </button>
        )}

      </div>

      {/* 量/额 独立副图: 自己的纵轴刻度 + hover 出具体数字; 与主图时间轴双向同步 */}
      <div className="shrink-0 mt-0.5">
        <div className="flex items-center gap-2 px-0.5 h-4 text-[10px]">
          {[['量', '成交量'], ['额', '成交额'], ['MACD', 'MACD'], ['KDJ', 'KDJ']].map(([m, lbl]) => (
            <button key={m} onClick={() => setVolMode(m)}
              className={`px-1.5 rounded leading-4 ${volMode === m ? 'bg-accent/20 text-accent' : 'text-text-dim hover:text-text'}`}>
              {lbl}
            </button>
          ))}
          {volMode === 'MACD' && <span className="font-mono text-text-dim"><span style={{ color: '#e8b04a' }}>DIF</span> <span style={{ color: '#4aa6e0' }}>DEA</span> <span className="text-text-muted">柱=MACD</span></span>}
          {volMode === 'KDJ' && <span className="font-mono text-text-dim"><span style={{ color: '#e8b04a' }}>K</span> <span style={{ color: '#4aa6e0' }}>D</span> <span style={{ color: '#cf6bcf' }}>J</span></span>}
          {(volMode === '量' || volMode === '额') && volLegend && (
            <span className="font-mono text-text-dim">
              <span className="text-text-muted mr-1.5">{volLegend.time}</span>
              量 <span className="text-text">{fmtVol(volLegend.volume)}</span>
              <span className="text-text-muted mx-1">·</span>
              额 <span className="text-text">{fmtAmt(volLegend.amount)}</span>
            </span>
          )}
        </div>
        <div ref={volWrapRef} style={{ width: '100%', height: 132 }} />
      </div>

      {/* 点某根蜡烛 → 看那一天(分时 / 该日席位)。挂在根层而非主图容器内, 否则它的
          62% 高度只按被副图挤小后的主图算, 纵轴刻度会挤成一团。
          onJumpDay: 席位页签里"最近的上榜日"按钮换一天看, 仍由这里持有 intraday。 */}
      {intraday && (
        <DayOverlay
          day={intraday} code={code}
          getBars={() => barsRef.current}
          onClose={() => { setIntraday(null); setHint(null) }}
          onJumpDay={setIntraday} />
      )}

      {err && <div className="absolute inset-0 flex items-center justify-center text-[12px] text-text-dim">{err}</div>}
      {loading && !err && <div className="absolute inset-x-0 top-1/2 text-center text-[12px] text-text-dim">加载 K 线…</div>}
    </div>
  )
}
