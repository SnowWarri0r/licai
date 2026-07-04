import { useEffect, useRef, useState } from 'react'
import { createChart, CandlestickSeries, HistogramSeries, LineSeries, CrosshairMode } from 'lightweight-charts'
import { fetchJSON } from '../hooks/useApi'

const UP = '#cf5c5c', DOWN = '#5fa86c'   // A股 红涨绿跌
const MA_DEFS = [{ n: 5, c: '#e8b04a' }, { n: 10, c: '#4aa6e0' }, { n: 20, c: '#cf6bcf' }]

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

// 券商式可拖动/缩放 K线(TradingView lightweight-charts): 蜡烛 + 量能 + MA5/10/20, 滚轮缩放/拖动平移/十字光标。
export default function ProKline({ code, days = 250, height = 460 }) {
  const wrapRef = useRef(null)
  const chartRef = useRef(null)
  const seriesRef = useRef({})
  const [legend, setLegend] = useState(null)
  const [err, setErr] = useState('')
  const [loading, setLoading] = useState(true)

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
    seriesRef.current = { candle, vol, mas }

    // 十字光标 → 顶部图例(日期/OHLC/涨跌)
    chart.subscribeCrosshairMove(param => {
      const d = param.seriesData?.get(candle)
      if (!d || !param.time) { setLegend(null); return }
      setLegend({ time: param.time, o: d.open, h: d.high, l: d.low, c: d.close })
    })

    return () => { chart.remove(); chartRef.current = null }
  }, [])

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
        const { candle, vol, mas } = seriesRef.current
        candle.setData(bars.map(b => ({ time: b.time, open: b.open, high: b.high, low: b.low, close: b.close })))
        vol.setData(bars.map(b => ({ time: b.time, value: b.volume, color: b.close >= b.open ? 'rgba(207,92,92,0.5)' : 'rgba(95,168,108,0.5)' })))
        mas.forEach((s, i) => s.setData(maLine(bars, MA_DEFS[i].n)))
        chartRef.current?.timeScale().fitContent()
      })
      .catch(e => alive && setErr(e?.message || '加载失败'))
      .finally(() => alive && setLoading(false))
    return () => { alive = false }
  }, [code, days])

  return (
    <div className="relative">
      <div className="flex items-center gap-3 mb-1 text-[10.5px] h-4">
        {legend
          ? <span className="font-mono text-text-dim flex gap-2.5 flex-wrap">
              <span className="text-text-muted">{legend.time}</span>
              <span>开<span className={legend.c >= legend.o ? 'text-bear' : 'text-bull'}>{fmt(legend.o)}</span></span>
              <span>高<span className="text-bear">{fmt(legend.h)}</span></span>
              <span>低<span className="text-bull">{fmt(legend.l)}</span></span>
              <span>收<span className={legend.c >= legend.o ? 'text-bear' : 'text-bull'}>{fmt(legend.c)}</span></span>
            </span>
          : <span className="text-text-muted">{MA_DEFS.map(m => `MA${m.n}`).join(' / ')} · 滚轮缩放 · 拖动平移</span>}
      </div>
      <div ref={wrapRef} style={{ width: '100%', height }} />
      {err && <div className="absolute inset-0 flex items-center justify-center text-[12px] text-text-dim">{err}</div>}
      {loading && !err && <div className="absolute inset-x-0 top-1/2 text-center text-[12px] text-text-dim">加载 K 线…</div>}
    </div>
  )
}
