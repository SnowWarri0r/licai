import { useEffect, useRef, useState } from 'react'
import { createChart, ColorType, CandlestickSeries } from 'lightweight-charts'
import { fetchJSON } from '../hooks/useApi'

export default function PriceChart({ stockCode, buyZoneLow, buyZoneHigh, sellZoneLow, sellZoneHigh, recommendedBuy, recommendedSell }) {
  const containerRef = useRef(null)
  const chartRef = useRef(null)
  const tooltipRef = useRef(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    if (!containerRef.current || !stockCode) return

    const container = containerRef.current
    const chart = createChart(container, {
      layout: {
        background: { type: ColorType.Solid, color: 'transparent' },
        textColor: '#958f82',
        fontSize: 11,
        fontFamily: "'JetBrains Mono', monospace",
      },
      grid: {
        vertLines: { color: '#2d2c3820' },
        horzLines: { color: '#2d2c3820' },
      },
      width: container.clientWidth,
      height: 260,
      rightPriceScale: {
        borderColor: '#2d2c38',
        scaleMargins: { top: 0.08, bottom: 0.08 },
      },
      timeScale: {
        borderColor: '#2d2c38',
        timeVisible: false,
      },
      crosshair: {
        mode: 0,
        vertLine: { color: '#c8a87640', width: 1, labelVisible: true },
        horzLine: { color: '#c8a87640', width: 1, labelVisible: true },
      },
      localization: {
        priceFormatter: (p) => p.toFixed(2),
      },
    })
    chartRef.current = chart

    // A股 convention: 红涨绿跌
    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: '#cf5c5c',
      downColor: '#5fa86c',
      borderUpColor: '#cf5c5c',
      borderDownColor: '#5fa86c',
      wickUpColor: '#cf5c5c80',
      wickDownColor: '#5fa86c80',
    })

    // Tooltip element
    const tooltip = tooltipRef.current
    let allData = []

    // Crosshair move → show OHLC tooltip
    chart.subscribeCrosshairMove((param) => {
      if (!tooltip) return
      if (!param.time || !param.seriesData || param.seriesData.size === 0) {
        tooltip.style.display = 'none'
        return
      }

      const data = param.seriesData.get(candleSeries)
      if (!data) { tooltip.style.display = 'none'; return }

      const { open, high, low, close } = data
      const change = close - open
      const changePct = open > 0 ? (change / open * 100) : 0
      const color = change >= 0 ? '#22c55e' : '#ef4444'

      tooltip.style.display = 'flex'
      tooltip.innerHTML = `
        <span style="color:#5a6f8a">${param.time}</span>
        <span>开 <b>${open.toFixed(2)}</b></span>
        <span style="color:${color}">高 <b>${high.toFixed(2)}</b></span>
        <span style="color:${color}">低 <b>${low.toFixed(2)}</b></span>
        <span style="color:${color}">收 <b>${close.toFixed(2)}</b></span>
        <span style="color:${color}">${change >= 0 ? '+' : ''}${change.toFixed(2)} (${changePct >= 0 ? '+' : ''}${changePct.toFixed(2)}%)</span>
      `
    })

    // Load data
    setLoading(true)
    fetchJSON(`/api/market/history/${stockCode}?days=30`).then(data => {
      if (!data || data.length === 0) { setLoading(false); return }

      allData = data
      candleSeries.setData(data)

      // Buy zone lines (A股绿=跌=买点)
      if (buyZoneLow && buyZoneHigh) {
        candleSeries.createPriceLine({
          price: recommendedBuy || ((buyZoneLow + buyZoneHigh) / 2),
          color: '#5fa86c', lineWidth: 2, lineStyle: 0, axisLabelVisible: true,
          title: `买 ${(recommendedBuy || ((buyZoneLow + buyZoneHigh) / 2)).toFixed(2)}`,
        })
        candleSeries.createPriceLine({ price: buyZoneLow, color: '#5fa86c40', lineWidth: 1, lineStyle: 2, axisLabelVisible: false, title: '' })
        candleSeries.createPriceLine({ price: buyZoneHigh, color: '#5fa86c40', lineWidth: 1, lineStyle: 2, axisLabelVisible: false, title: '' })
      }

      // Sell zone lines (A股红=涨=卖点)
      if (sellZoneLow && sellZoneHigh) {
        candleSeries.createPriceLine({
          price: recommendedSell || ((sellZoneLow + sellZoneHigh) / 2),
          color: '#cf5c5c', lineWidth: 2, lineStyle: 0, axisLabelVisible: true,
          title: `卖 ${(recommendedSell || ((sellZoneLow + sellZoneHigh) / 2)).toFixed(2)}`,
        })
        candleSeries.createPriceLine({ price: sellZoneLow, color: '#cf5c5c40', lineWidth: 1, lineStyle: 2, axisLabelVisible: false, title: '' })
        candleSeries.createPriceLine({ price: sellZoneHigh, color: '#cf5c5c40', lineWidth: 1, lineStyle: 2, axisLabelVisible: false, title: '' })
      }

      chart.timeScale().fitContent()
      setLoading(false)
    }).catch(() => setLoading(false))

    const ro = new ResizeObserver(entries => {
      for (const entry of entries) chart.applyOptions({ width: entry.contentRect.width })
    })
    ro.observe(container)

    return () => {
      ro.disconnect()
      chart.remove()
      chartRef.current = null
    }
  }, [stockCode, buyZoneLow, buyZoneHigh, sellZoneLow, sellZoneHigh, recommendedBuy, recommendedSell])

  return (
    <div className="relative mt-2 rounded-lg overflow-hidden border border-border-subtle bg-surface/50">
      {loading && (
        <div className="absolute inset-0 flex items-center justify-center text-[12px] text-text-dim z-10">
          加载K线...
        </div>
      )}
      {/* OHLC Tooltip */}
      <div ref={tooltipRef}
        className="absolute top-1 left-2 z-20 gap-2 text-[11px] font-mono pointer-events-none"
        style={{ display: 'none' }}
      />
      <div ref={containerRef} className="w-full" style={{ minHeight: 260 }} />
    </div>
  )
}
