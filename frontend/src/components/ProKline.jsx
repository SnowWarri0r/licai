import { useEffect, useRef, useState } from 'react'
import { createChart, CandlestickSeries, HistogramSeries, LineSeries, CrosshairMode, LineStyle } from 'lightweight-charts'
import { fetchJSON } from '../hooks/useApi'
import { MinuteChart } from './StockKlineModal'
import SeatHistoryModal from './SeatHistoryModal'

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

const fmt = (v) => v == null ? '--' : Math.abs(v) >= 100 ? v.toFixed(1) : Math.abs(v) >= 10 ? v.toFixed(2) : v.toFixed(3)

const GAP_UP = 'rgba(207,92,92,0.18)', GAP_DOWN = 'rgba(95,168,108,0.18)'   // 跳空缺口阴影: 红跳空/绿跳空
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

// 券商式可拖动/缩放 K线(TradingView lightweight-charts): 蜡烛 + 量能 + MA5/10/20, 滚轮缩放/拖动平移/十字光标。
export default function ProKline({ code, days = 250, height = 460, fill = false, lhbDate = '' }) {
  const wrapRef = useRef(null)
  const chartRef = useRef(null)
  const seriesRef = useRef({})
  const prevCloseLineRef = useRef(null)
  const barsRef = useRef([])
  const [legend, setLegend] = useState(null)
  const [err, setErr] = useState('')
  const [loading, setLoading] = useState(true)
  const [hint, setHint] = useState(null)           // 点蜡烛 → {x, y, date, prevClose} 「分时›」tooltip
  const [intraday, setIntraday] = useState(null)   // 点 tooltip → {date, prevClose} 浮层
  const [ovTab, setOvTab] = useState('分时')        // 浮层页签: 分时 | 龙虎榜
  const [minData, setMinData] = useState(null)
  const [minErr, setMinErr] = useState('')
  const [lhb, setLhb] = useState(null)             // 该日席位明细(懒加载)
  const [seatQ, setSeatQ] = useState('')           // 点席位名 → 该席位历史弹窗
  const ovBodyRef = useRef(null)
  const [minH, setMinH] = useState(200)            // 分时 viewBox 高: 按浮层实际宽高比算, 铺满不留白
  const intradayRef = useRef(null)
  intradayRef.current = intraday

  // 建图(一次)
  useEffect(() => {
    if (!wrapRef.current) return
    const chart = createChart(wrapRef.current, {
      autoSize: true,
      layout: { background: { color: 'transparent' }, textColor: '#9aa0a6', fontSize: 11,
        fontFamily: 'ui-sans-serif, system-ui, -apple-system, sans-serif' },
      grid: { vertLines: { color: 'rgba(255,255,255,0.04)' }, horzLines: { color: 'rgba(255,255,255,0.04)' } },
      crosshair: { mode: CrosshairMode.Normal,
        vertLine: { color: 'rgba(200,168,118,0.5)', width: 1, style: 2 },
        horzLine: { color: 'rgba(200,168,118,0.5)', width: 1, style: 2 } },
      rightPriceScale: { borderColor: 'rgba(255,255,255,0.08)', scaleMargins: { top: 0.08, bottom: 0.28 } },
      timeScale: { borderColor: 'rgba(255,255,255,0.08)', rightOffset: 4, minBarSpacing: 1.5 },
    })
    chartRef.current = chart
    const candle = chart.addSeries(CandlestickSeries, {
      upColor: UP, downColor: DOWN, borderUpColor: UP, borderDownColor: DOWN, wickUpColor: UP, wickDownColor: DOWN,
    })
    const vol = chart.addSeries(HistogramSeries, { priceFormat: { type: 'volume' }, priceScaleId: 'vol' })
    chart.priceScale('vol').applyOptions({ scaleMargins: { top: 0.78, bottom: 0 } })
    const mas = MA_DEFS.map(m => chart.addSeries(LineSeries, { color: m.c, lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false }))
    const gapPrim = new GapPrimitive()
    candle.attachPrimitive(gapPrim)
    seriesRef.current = { candle, vol, mas, gapPrim }

    // 十字光标 → 顶部图例(日期/OHLC/较昨收涨跌%)
    chart.subscribeCrosshairMove(param => {
      const d = param.seriesData?.get(candle)
      if (!d || !param.time) { setLegend(null); return }
      const t = param.time
      const key = typeof t === 'string' ? t
        : `${t.year}-${String(t.month).padStart(2, '0')}-${String(t.day).padStart(2, '0')}`
      const arr = barsRef.current
      const i = arr.findIndex(b => b.time === key)
      const prev = i > 0 ? arr[i - 1].close : null
      setLegend({ time: param.time, o: d.open, h: d.high, l: d.low, c: d.close,
                  pct: prev ? (d.close / prev - 1) * 100 : null })
    })

    // 点蜡烛 → 出「分时›」tooltip; 浮层开着时点K线 → 收起浮层(同花顺式浮层交互)
    chart.subscribeClick(param => {
      if (intradayRef.current) { setIntraday(null); setHint(null); return }
      const t = param.time
      if (!t || !param.point) { setHint(null); return }
      const key = typeof t === 'string' ? t
        : `${t.year}-${String(t.month).padStart(2, '0')}-${String(t.day).padStart(2, '0')}`
      const arr = barsRef.current
      const i = arr.findIndex(b => b.time === key)
      if (i < 0) { setHint(null); return }
      setHint({ x: param.point.x, y: param.point.y, date: key,
                prevClose: arr[i - 1]?.close ?? arr[i].open })
    })

    return () => { chart.remove(); chartRef.current = null }
  }, [])

  // 浮层: 拉该日分钟数据; ESC 关闭; 龙虎榜页签懒加载
  useEffect(() => {
    if (!intraday) return
    let alive = true
    setMinData(null); setMinErr(''); setLhb(null); setOvTab(intraday.tab || '分时')
    fetchJSON(`/api/market/tdx/minute/${encodeURIComponent(code)}?date=${intraday.date}`)
      .then(d => {
        if (!alive) return
        if (!d?.enabled) setMinErr('分时需启用 TDX 数据源(设置→TDX)')
        else if (!d?.data?.points?.length) setMinErr('该日分时不可得(太久远或非交易日)')
        else setMinData(d.data)
      })
      .catch(e => alive && setMinErr(e?.message || '加载失败'))
    const onEsc = (e) => { if (e.key === 'Escape') { setIntraday(null); setHint(null) } }
    window.addEventListener('keydown', onEsc)
    return () => { alive = false; window.removeEventListener('keydown', onEsc) }
  }, [intraday, code])

  // 分时区实际宽高比 → viewBox 高度(svg 按 720:minH 缩放正好占满容器, 大屏不再上浮留白)
  useEffect(() => {
    if (!intraday || ovTab !== '分时') return
    const el = ovBodyRef.current
    if (!el) return
    const compute = () => {
      const w = el.clientWidth || 720, h = el.clientHeight || 200
      setMinH(Math.max(140, Math.round(720 * h / w)))
    }
    compute()
    const ro = new ResizeObserver(compute)
    ro.observe(el)
    return () => ro.disconnect()
  }, [intraday, ovTab, minData])

  useEffect(() => {
    if (!intraday || ovTab !== '龙虎榜' || lhb) return
    let alive = true
    fetchJSON(`/api/market/lhb-detail/${encodeURIComponent(code)}?date=${intraday.date}`)
      .then(d => alive && setLhb(d || { note: '暂不可达' }))
      .catch(() => alive && setLhb({ note: '龙虎榜数据暂不可达(东财抖动)' }))
    return () => { alive = false }
  }, [ovTab, intraday, code, lhb])

  // 换股票 / 周期 → 拉数据填充
  useEffect(() => {
    if (!code) return
    let alive = true
    setLoading(true); setErr('')
    fetchJSON(`/api/market/history/${encodeURIComponent(code)}?days=${days}`)
      .then(k => {
        if (!alive) return
        if (!Array.isArray(k) || !k.length) { setErr('暂无 K 线数据'); return }
        const bars = k.map(x => ({ time: x.time, open: x.open, high: x.high, low: x.low, close: x.close, volume: x.volume }))
        barsRef.current = bars
        const { candle, vol, mas, gapPrim } = seriesRef.current
        candle.setData(bars.map(b => ({ time: b.time, open: b.open, high: b.high, low: b.low, close: b.close })))
        vol.setData(bars.map(b => ({ time: b.time, value: b.volume, color: b.close >= b.open ? 'rgba(207,92,92,0.5)' : 'rgba(95,168,108,0.5)' })))
        mas.forEach((s, i) => s.setData(maLine(bars, MA_DEFS[i].n)))
        gapPrim?.setGaps(detectGaps(bars))
        // 昨收线: 最新一根的前一日收盘 → 一眼看出今天这根(哪怕收红阳线)是否还在昨收下方
        if (prevCloseLineRef.current) { candle.removePriceLine(prevCloseLineRef.current); prevCloseLineRef.current = null }
        const prevClose = bars.length >= 2 ? bars[bars.length - 2].close : null
        if (prevClose != null) {
          prevCloseLineRef.current = candle.createPriceLine({
            price: prevClose, color: '#c8a876', lineWidth: 1, lineStyle: LineStyle.Dashed,
            axisLabelVisible: true, title: '昨收',
          })
        }
        // 初始视窗只看最近约3个月(70根), 更长的历史往左拖/滚轮缩放就有——
        // fitContent 会把250根全塞进屏幕, 蜡烛细得看不清近期形态
        const ts = chartRef.current?.timeScale()
        if (bars.length > 80) {
          ts?.setVisibleLogicalRange({ from: bars.length - 70, to: bars.length + 3 })
        } else {
          ts?.fitContent()
        }
        // 从龙虎榜榜单点进来: 直接弹开该上榜日的席位浮层
        if (lhbDate) {
          const i = bars.findIndex(b => b.time === lhbDate)
          if (i >= 0) setIntraday({ date: lhbDate, prevClose: bars[i - 1]?.close ?? bars[i].open, tab: '龙虎榜' })
        }
      })
      .catch(e => alive && setErr(e?.message || '加载失败'))
      .finally(() => alive && setLoading(false))
    return () => { alive = false }
  }, [code, days, lhbDate])

  return (
    <div className={fill ? 'relative flex flex-col h-full' : 'relative'}>
      <div className="flex items-center gap-3 mb-1 text-[10.5px] h-4 shrink-0">
        {legend
          ? <span className="font-mono text-text-dim flex gap-2.5 flex-wrap">
              <span className="text-text-muted">{legend.time}</span>
              <span>开<span className={legend.c >= legend.o ? 'text-bear' : 'text-bull'}>{fmt(legend.o)}</span></span>
              <span>高<span className="text-bear">{fmt(legend.h)}</span></span>
              <span>低<span className="text-bull">{fmt(legend.l)}</span></span>
              <span>收<span className={legend.c >= legend.o ? 'text-bear' : 'text-bull'}>{fmt(legend.c)}</span></span>
              {legend.pct != null && (
                <span className={`font-semibold ${legend.pct >= 0 ? 'text-bear' : 'text-bull'}`}>
                  {legend.pct >= 0 ? '+' : ''}{legend.pct.toFixed(2)}%
                </span>
              )}
            </span>
          : <span className="text-text-muted">{MA_DEFS.map(m => `MA${m.n}`).join(' / ')} · <span style={{ color: '#c8a876' }}>┄ 昨收</span> · 滚轮缩放 · 点蜡烛看当日分时</span>}
      </div>
      <div className={`relative ${fill ? 'flex-1 min-h-0' : ''}`} style={fill ? { width: '100%' } : { width: '100%', height }}>
        <div ref={wrapRef} className="absolute inset-0" />

        {/* 点蜡烛 → 「分时›」tooltip(跟随点击位置) */}
        {hint && !intraday && (
          <button
            onClick={() => { setIntraday({ date: hint.date, prevClose: hint.prevClose }); setHint(null) }}
            className="absolute z-20 text-[10.5px] font-semibold px-2 py-1 rounded-lg cursor-pointer whitespace-nowrap"
            style={{ left: Math.min(Math.max(hint.x + 8, 4), (wrapRef.current?.clientWidth || 400) - 120),
                     top: Math.max(hint.y - 34, 4),
                     background: '#c8a876', color: '#1a1b1f',
                     boxShadow: '0 4px 14px rgba(0,0,0,0.6)' }}>
            {hint.date.slice(5)} 分时 ›
          </button>
        )}

        {/* 分时浮层: 盖在K线下半部, K线不缩; 点K线任意处/×/ESC 收起 */}
        {intraday && (
          <div className="absolute inset-x-0 bottom-0 z-20 border-t border-border rounded-t-lg px-2 pt-1 pb-1.5 overflow-hidden flex flex-col"
            style={{ height: '62%',
                     background: 'color-mix(in srgb, var(--color-surface-2) 94%, transparent)', backdropFilter: 'blur(2px)' }}>
            <div className="flex items-baseline gap-2 px-1 mb-0.5">
              <span className="text-[11px] font-mono text-text-bright">{(minData?.date || intraday.date).toString().replace(/^(\d{4})(\d{2})(\d{2})$/, '$1-$2-$3')}</span>
              {['分时', '龙虎榜'].map(t => (
                <button key={t} onClick={() => setOvTab(t)}
                  className={`text-[10.5px] px-1.5 py-0.5 rounded cursor-pointer ${ovTab === t ? 'bg-accent/20 text-accent' : 'text-text-dim hover:text-text'}`}>
                  {t}
                </button>
              ))}
              <span className="text-[9.5px] text-text-dim">{ovTab === '分时' ? `基准=前收 ${fmt(intraday.prevClose)} · ` : ''}点K线空白处收起</span>
              <button onClick={() => setIntraday(null)}
                className="ml-auto text-text-dim hover:text-text text-[15px] leading-none px-1 cursor-pointer">×</button>
            </div>
            {ovTab === '分时' && (
              <div ref={ovBodyRef} className="flex-1 min-h-0">
                {minErr && <div className="text-center py-6 text-[11.5px] text-text-dim">{minErr}</div>}
                {!minErr && !minData && <div className="text-center py-6 text-[11.5px] text-text-dim">分时加载中…</div>}
                {minData && (
                  <MinuteChart points={minData.points} prevClose={intraday.prevClose}
                    day={minData.date || intraday.date} height={minH} />
                )}
              </div>
            )}
            {ovTab === '龙虎榜' && (
              !lhb ? <div className="flex-1 flex items-center justify-center text-[11.5px] text-text-dim">席位明细加载中…</div>
              : (!lhb['买入']?.length && !lhb['卖出']?.length)
              ? (
                <div className="flex-1 flex flex-col items-center justify-center gap-2 text-[11.5px] text-text-dim px-4 text-center">
                  <span>{lhb.note || '该日未上龙虎榜'}</span>
                  {(lhb['最近上榜日'] || []).length > 0 && (
                    <span className="flex items-baseline gap-1.5 flex-wrap justify-center">
                      <span className="text-[10.5px]">它最近的上榜日:</span>
                      {lhb['最近上榜日'].map(d => {
                        const arr = barsRef.current
                        const i = arr.findIndex(b => b.time === d)
                        return i >= 0
                          ? <button key={d}
                              onClick={() => { setLhb(null); setIntraday({ date: d, prevClose: arr[i - 1]?.close ?? arr[i].open, tab: '龙虎榜' }) }}
                              className="text-[10.5px] px-1.5 py-0.5 rounded bg-accent/15 text-accent hover:bg-accent/25 cursor-pointer font-mono">
                              {d.slice(2)}
                            </button>
                          : <span key={d} className="text-[10.5px] font-mono text-text-muted" title="超出当前K线窗口">{d.slice(2)}</span>
                      })}
                    </span>
                  )}
                </div>
              )
              : (
                <div className="flex-1 min-h-0 flex flex-col text-[12.5px] overflow-y-auto">
                  {lhb['上榜原因'] && <div className="px-1 text-[10.5px] text-text-dim mb-1 shrink-0">上榜原因: {lhb['上榜原因']}</div>}
                  <div className="grid grid-cols-2 gap-5 px-1 flex-1 content-start">
                    {[['买入', 'text-bear-bright'], ['卖出', 'text-bull-bright']].map(([side, cls]) => (
                      <div key={side}>
                        <div className={`mb-1 text-[13px] font-semibold ${cls}`}>{side}前五 · 计 {(lhb[`${side}总计万`] / 1e4).toFixed(2)}亿</div>
                        {(lhb[side] || []).map((s, i) => (
                          <div key={i} className="flex items-baseline gap-1.5 py-1.5 border-b border-border-subtle/40">
                            {s.席位 === '机构专用' || s.席位.includes('股通')
                              ? <span className="text-text truncate flex-1" title={s.席位}>{s.席位.replace(/(股份|有限责任)?公司|证券营业部/g, '')}</span>
                              : <button onClick={() => setSeatQ(s.席位)} title={`${s.席位} · 点击看该席位近90天上榜记录`}
                                  className="text-text truncate flex-1 text-left cursor-pointer hover:text-accent underline decoration-dotted decoration-border underline-offset-2">
                                  {s.席位.replace(/(股份|有限责任)?公司|证券营业部/g, '')}
                                </button>}
                            {s.标签 && <span className="text-[10px] px-1 rounded bg-accent/15 text-accent shrink-0">{s.标签}</span>}
                            <span className="text-[10.5px] text-text-dim font-mono shrink-0">{s['占成交%']}%</span>
                            <span className={`font-mono font-semibold shrink-0 ${cls}`}>{(s.金额万 / 1e4).toFixed(2)}亿</span>
                          </div>
                        ))}
                      </div>
                    ))}
                  </div>
                  <div className="px-1 pt-1 text-[9.5px] text-text-dim shrink-0">{lhb.note}</div>
                </div>
              )
            )}
          </div>
        )}
      </div>
      {err && <div className="absolute inset-0 flex items-center justify-center text-[12px] text-text-dim">{err}</div>}
      {loading && !err && <div className="absolute inset-x-0 top-1/2 text-center text-[12px] text-text-dim">加载 K 线…</div>}
      {seatQ && <SeatHistoryModal seat={seatQ} onClose={() => setSeatQ('')} />}
    </div>
  )
}
