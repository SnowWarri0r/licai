import { useEffect, useRef, useState, useCallback } from 'react'
import { createChart, LineSeries, HistogramSeries, LineStyle, CrosshairMode, createSeriesMarkers } from 'lightweight-charts'
import { ACQUIRE, BUY_COLOR, SELL_COLOR, UP, DOWN, colorPct, fmtHand, fmtPct, fmtVal } from './shared'

// ---------------------------------------------------------------------------
// 分时图 (lightweight-charts) — 价格线 + 均价线 + 昨收基准 + 量柱(红买绿卖) + 买卖点
// ---------------------------------------------------------------------------
// 用图表库画, 所以自带**滚轮缩放 / 拖动平移 / 十字光标**, 与 K线(ProKline)同一套手感。
//
// 时间轴用**序号(ordinal)**而非真实时间戳: lightweight-charts 本就是按数据点等距排布
// (跟 K线按交易日等距、自动并拢非交易日一样), 序号 → 真实 HH:MM 由 tickMarkFormatter
// 查表还原。这样两件事顺带解决: ①逐笔精绘里同一分钟有多笔, 真实时间戳会重复(库不允许),
// 序号天然唯一; ②午休(11:30-13:00)无数据段自动并拢不留白。
// Y 轴范围库自适应可见区间(缩放到某段自动贴合该段高低) —— 正是"闪电图"要的铺满效果。

// 量柱红买绿卖(带透明度, 别盖过价格线)
const volColor = (up) => (up ? 'rgba(207,92,92,0.55)' : 'rgba(95,168,108,0.55)')

export function MinuteChart({ points, prevClose, actions = [], day, height = 410,
                             session = 'cn', volUnit = '手', tickMode = false }) {  // eslint-disable-line no-unused-vars
  const wrapRef = useRef(null)
  const chartRef = useRef(null)
  const priceRef = useRef(null)
  const avgRef = useRef(null)
  const volRef = useRef(null)
  const prevLineRef = useRef(null)
  const markersRef = useRef(null)
  const rowsRef = useRef([])                 // 每个序号对应的 {time, price, avg, vol} —— 十字光标查它
  const actionsRef = useRef(actions)
  const dayRef = useRef(day)
  const [legend, setLegend] = useState(null)

  // 买卖点: 同一分钟同方向合成一个标记(B×3), 落到该分钟第一个序号上。从 ref 读, 可被
  // 数据 effect(画完线后)与流水 effect(actions 变了)各自调用, 不重拉数据。
  const drawMarkers = useCallback(() => {
    const price = priceRef.current
    const rows = rowsRef.current
    if (!price || !rows.length) return
    const norm = s => String(s || '').replace(/\D/g, '').slice(0, 8)
    const now = new Date()
    const today = `${now.getFullYear()}${String(now.getMonth() + 1).padStart(2, '0')}${String(now.getDate()).padStart(2, '0')}`
    const matchDay = norm(dayRef.current) || today
    // 分钟(HH:MM) → 该分钟第一个序号
    const minIdx = new Map()
    rows.forEach((r, i) => { const m = r.time.slice(0, 5); if (!minIdx.has(m)) minIdx.set(m, i) })
    const byKey = new Map()
    for (const a of (actionsRef.current || [])) {
      if (norm(a.trade_date) !== matchDay || !a.at_time) continue
      const m = String(a.at_time).slice(0, 5)
      const idx = minIdx.has(m) ? minIdx.get(m) : null
      if (idx == null) continue
      const isBuy = ACQUIRE.has(a.action_type)
      const k = `${isBuy ? 'B' : 'S'}@${idx}`
      const g = byKey.get(k)
      if (g) g.n++
      else byKey.set(k, { idx, isBuy, n: 1 })
    }
    // 买卖点: 圆点落在成交那一刻的价格线上(inBar+circle), 带 B/S 与笔数(B2/S3);
    // 旧 arrowUp/Down 只是柱子上下的箭头, 丢了"点在成交价上"这层信息。
    const markers = [...byKey.values()].sort((x, y) => x.idx - y.idx).map(g => ({
      time: g.idx,
      position: 'inBar',
      color: g.isBuy ? BUY_COLOR : SELL_COLOR,
      shape: 'circle',
      text: (g.isBuy ? 'B' : 'S') + (g.n > 1 ? String(g.n) : ''),
    }))
    if (!markersRef.current) markersRef.current = createSeriesMarkers(price, markers)
    else markersRef.current.setMarkers(markers)
  }, [])

  // 建图(一次)
  useEffect(() => {
    if (!wrapRef.current) return
    const chart = createChart(wrapRef.current, {
      autoSize: true,
      layout: { background: { color: 'transparent' }, textColor: '#9aa0a6', fontSize: 11,
        attributionLogo: false,
        fontFamily: 'ui-sans-serif, system-ui, -apple-system, sans-serif' },
      grid: { vertLines: { color: 'rgba(255,255,255,0.04)' }, horzLines: { color: 'rgba(255,255,255,0.04)' } },
      crosshair: { mode: CrosshairMode.Normal,
        vertLine: { color: 'rgba(200,168,118,0.5)', width: 1, style: 2 },
        horzLine: { color: 'rgba(200,168,118,0.5)', width: 1, style: 2 } },
      rightPriceScale: { borderColor: 'rgba(255,255,255,0.08)', scaleMargins: { top: 0.06, bottom: 0.28 } },
      timeScale: { borderColor: 'rgba(255,255,255,0.08)', rightOffset: 2, minBarSpacing: 0.05, fixLeftEdge: true, fixRightEdge: true,
        // 序号 → 真实 HH:MM(查 rowsRef); 下标越界给空串
        tickMarkFormatter: (t) => { const r = rowsRef.current[t]; return r ? r.time.slice(0, 5) : '' } },
    })
    chartRef.current = chart
    const price = chart.addSeries(LineSeries, { color: UP, lineWidth: 2, priceLineVisible: false, lastValueVisible: true })
    const avg = chart.addSeries(LineSeries, { color: '#c8a876', lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false })
    const vol = chart.addSeries(HistogramSeries, { priceScaleId: 'vol', priceLineVisible: false, lastValueVisible: false })
    vol.priceScale().applyOptions({ scaleMargins: { top: 0.80, bottom: 0 } })
    priceRef.current = price; avgRef.current = avg; volRef.current = vol

    chart.subscribeCrosshairMove(param => {
      const t = param?.time
      const r = (t != null) ? rowsRef.current[t] : null
      if (!r) { setLegend(null); return }
      setLegend(r)
    })

    return () => { chart.remove(); chartRef.current = null; markersRef.current = null }
  }, [])

  // 流水变了只重画买卖点
  useEffect(() => { actionsRef.current = actions; dayRef.current = day; drawMarkers() }, [actions, day, drawMarkers])

  // 数据: 换点集/昨收/逐笔开关 → 重填
  useEffect(() => {
    const chart = chartRef.current, price = priceRef.current, avg = avgRef.current, vol = volRef.current
    if (!chart || !price) return
    const pts = (points || []).filter(p => p && p.price > 0)
    if (pts.length < 2) { price.setData([]); avg.setData([]); vol.setData([]); rowsRef.current = []; return }

    let cumPV = 0, cumV = 0, prevPx = prevClose, lastUp = true
    const priceData = [], avgData = [], volData = [], rows = []
    pts.forEach((p, i) => {
      const v = Number(p['手']) || 0
      cumPV += p.price * v; cumV += v
      const av = cumV > 0 ? cumPV / cumV : p.price
      const up = p.price > prevPx ? true : p.price < prevPx ? false : lastUp
      lastUp = up; prevPx = p.price
      priceData.push({ time: i, value: p.price })
      avgData.push({ time: i, value: +av.toFixed(4) })
      volData.push({ time: i, value: v, color: volColor(up) })
      rows.push({ time: String(p.time || ''), price: p.price, avg: av, vol: v })
    })
    rowsRef.current = rows

    // 价格线整体颜色: 收在昨收上方红、下方绿(A股口径, 与旧版一致)
    const last = pts[pts.length - 1].price
    price.applyOptions({ color: last >= prevClose ? UP : DOWN })
    price.setData(priceData)
    avg.setData(avgData)
    vol.setData(volData)

    // 昨收基准线
    if (prevLineRef.current) { try { price.removePriceLine(prevLineRef.current) } catch { /* 已销毁 */ } prevLineRef.current = null }
    if (prevClose > 0) {
      prevLineRef.current = price.createPriceLine({
        price: prevClose, color: 'rgba(200,168,118,0.6)', lineWidth: 1, lineStyle: LineStyle.Dashed,
        axisLabelVisible: true, title: '昨收',
      })
    }
    chart.timeScale().fitContent()   // 默认铺满全天; 用户可再滚轮放大
    drawMarkers()
  }, [points, prevClose, tickMode, drawMarkers])

  const pct = legend && prevClose > 0 ? ((legend.price / prevClose) - 1) * 100 : null
  const empty = (points || []).filter(p => p && p.price > 0).length < 2

  return (
    <div className="relative w-full" style={{ height: typeof height === 'number' ? height : '100%' }}>
      <div ref={wrapRef} className="absolute inset-0" />
      {empty && (
        <div className="absolute inset-0 flex items-center justify-center text-text-dim text-[12px]">
          暂无分时(非交易时段, 或该标的的源不提供分时)
        </div>
      )}
      {/* 图例 / 悬浮读数 */}
      <div className="absolute top-1 left-1 text-[10.5px] font-mono pointer-events-none flex gap-2.5 items-baseline">
        {legend ? (
          <>
            <span className="text-text-muted">{legend.time}</span>
            <span>价 <span className="text-text-bright">{fmtVal(legend.price)}</span></span>
            {pct != null && <span className={colorPct(pct)}>{fmtPct(pct)}</span>}
            <span className="text-text-dim">均 {fmtVal(legend.avg)}</span>
            <span className="text-text-dim">{fmtHand(legend.vol, volUnit)}</span>
          </>
        ) : (
          <span className="text-text-muted flex gap-2.5">
            <span style={{ color: UP }}>— 价格</span>
            <span style={{ color: '#c8a876' }}>— 均价</span>
            <span style={{ color: '#c8a876' }}>┄ 昨收</span>
            {tickMode && <span className="text-accent">· 逐笔</span>}
            <span>滚轮缩放 · 拖动平移</span>
          </span>
        )}
      </div>
    </div>
  )
}
