import { useEffect, useRef, useState } from 'react'
import { createChart, LineSeries, CrosshairMode } from 'lightweight-charts'
import { fetchJSON } from '../../hooks/useApi'
import ProKline from '../ProKline'
import { MinuteChart } from '../kline/MinuteChart'
import { openStock } from './stockOpener'

// 问答答案里的组件标记 → 就地渲染的真组件(可缩放/拖动/十字光标, 点开完整详情)。
//   ⟦K线:600519⟧  ⟦K线:600519|60⟧        日K(可选天数)
//   ⟦分时:600519⟧                         当日分时
//   ⟦对比:588080,512010⟧  ⟦对比:a,b|60⟧   多条走势归一成涨跌幅%(日线, 可选天数)
//   ⟦对比:588080,512010|分时⟧             当日分时对比
// 对比项可以是代码/名称、指数符号(sh000001)、板块:名称。
export const WIDGET_RE = /⟦(K线|分时|对比):([^⟧]+)⟧/g

export function parseWidget(kind, body) {
  const [main, opt = ''] = body.split('|').map(s => s.trim())
  const days = /^\d+$/.test(opt) ? Math.min(parseInt(opt, 10), 250) : null
  if (kind === '对比') {
    const items = main.split(/[,，]/).map(s => s.trim()).filter(Boolean).slice(0, 6)
    return { kind, items, days: days || 20, mode: opt === '分时' ? '分时' : '日' }
  }
  return { kind, code: main.replace(/^(sh|sz|bj)/i, ''), days: days || 120 }
}

const LINE_COLORS = ['#c8a876', '#5b9bd5', '#cf5c5c', '#5fa86c', '#a77bd1', '#d08b4a']

function Frame({ title, code, children }) {
  return (
    <div className="my-2 rounded-lg border border-border bg-surface-2/60 overflow-hidden max-w-[720px]">
      <div className="flex items-center justify-between px-2.5 py-1.5 border-b border-border-subtle">
        <span className="text-[11.5px] font-medium text-text-bright">{title}</span>
        {code && (
          <button onClick={() => openStock(code)}
            className="text-[11px] text-accent hover:underline cursor-pointer">打开详情</button>
        )}
      </div>
      {children}
    </div>
  )
}

function useCard(code) {
  const [card, setCard] = useState(null)
  useEffect(() => {
    if (!code) return
    fetchJSON(`/api/market/stock-card/${encodeURIComponent(code)}`).then(setCard).catch(() => setCard(null))
  }, [code])
  return card
}

function KlineWidget({ code, days }) {
  const card = useCard(code)
  const name = card?.stock_name || code
  return (
    <Frame title={`${name}(${code}) 日K`} code={code}>
      <ProKline code={code} days={days} height={300} cost={card?.cost_price || null} />
    </Frame>
  )
}

function MinuteWidget({ code }) {
  const card = useCard(code)
  const [data, setData] = useState(null)
  const [err, setErr] = useState('')
  useEffect(() => {
    fetchJSON(`/api/market/tdx/minute/${encodeURIComponent(code)}`)
      .then(d => (d?.data ? setData(d.data) : setErr(d?.enabled === false ? '分时数据源未启用' : '没有分时数据')))
      .catch(e => setErr(e?.message || '加载失败'))
  }, [code])
  // 分时基准是昨收: 现价 ÷ (1 + 涨跌幅)
  const prevClose = card?.current_price && card?.price_change_pct != null
    ? card.current_price / (1 + card.price_change_pct / 100) : null
  const name = card?.stock_name || code
  return (
    <Frame title={`${name}(${code}) 分时${data?.date ? ' · ' + data.date : ''}`} code={code}>
      {err ? <div className="px-3 py-6 text-[12px] text-text-muted">{err}</div>
        : !data || !prevClose ? <div className="px-3 py-6 text-[12px] text-text-muted">加载中…</div>
          : <MinuteChart points={data.points || []} prevClose={prevClose} day={data.date} height={260} />}
    </Frame>
  )
}

function CompareWidget({ items, days, mode }) {
  const wrapRef = useRef(null)
  const [d, setD] = useState(null)
  const [err, setErr] = useState('')
  const [hover, setHover] = useState(null)

  useEffect(() => {
    const q = new URLSearchParams({ items: items.join(','), days: String(days), mode })
    fetchJSON(`/api/market/compare?${q}`).then(setD).catch(e => setErr(e?.message || '加载失败'))
  }, [items.join(','), days, mode])   // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!wrapRef.current || !d?.series?.length) return
    const intraday = d.mode === '分时'
    // 分时的时间轴用序号(同一天, 各条点数一致), 日线直接用日期字符串
    const labels = intraday ? (d.series[0].points || []).map(p => p.t) : null
    const chart = createChart(wrapRef.current, {
      autoSize: true,
      layout: { background: { color: 'transparent' }, textColor: '#9aa0a6', fontSize: 11, attributionLogo: false },
      grid: { vertLines: { color: 'rgba(255,255,255,0.04)' }, horzLines: { color: 'rgba(255,255,255,0.04)' } },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: 'rgba(255,255,255,0.08)' },
      localization: { priceFormatter: v => `${v >= 0 ? '+' : ''}${v.toFixed(2)}%` },
      timeScale: { borderColor: 'rgba(255,255,255,0.08)', fixLeftEdge: true, fixRightEdge: true,
        ...(intraday ? { tickMarkFormatter: t => labels[t] || '' } : {}) },
    })
    const lines = d.series.map((s, i) => {
      const line = chart.addSeries(LineSeries, { color: LINE_COLORS[i % LINE_COLORS.length], lineWidth: 2,
        priceLineVisible: false, lastValueVisible: true, title: '' })
      line.setData((s.points || []).map((p, k) => ({ time: intraday ? k : p.t, value: p.pct })))
      return line
    })
    lines[0].createPriceLine({ price: 0, color: 'rgba(255,255,255,0.25)', lineWidth: 1, lineStyle: 2, axisLabelVisible: false })
    chart.timeScale().fitContent()
    chart.subscribeCrosshairMove(param => {
      if (!param?.time && param?.time !== 0) { setHover(null); return }
      setHover({
        t: intraday ? labels[param.time] : param.time,
        vals: lines.map(l => param.seriesData.get(l)?.value),
      })
    })
    return () => chart.remove()
  }, [d])

  const title = d?.series?.map(s => s.label).join(' vs ') || items.join(' vs ')
  return (
    <Frame title={`${title} · ${mode === '分时' ? '当日分时' : `近${days}日`}走势对比`}>
      {err && <div className="px-3 py-6 text-[12px] text-text-muted">{err}</div>}
      {d?.series?.length > 0 && (
        <div className="flex flex-wrap gap-x-3 gap-y-1 px-2.5 pt-1.5 text-[11px]">
          {d.series.map((s, i) => {
            const v = hover ? hover.vals[i] : s.points?.[s.points.length - 1]?.pct
            return (
              <span key={i} className="flex items-center gap-1">
                <span className="inline-block w-2.5 h-[3px] rounded" style={{ background: LINE_COLORS[i % LINE_COLORS.length] }} />
                {s.code
                  ? <button onClick={() => openStock(s.code, s.label)} className="text-text-dim hover:text-accent hover:underline cursor-pointer">{s.label}</button>
                  : <span className="text-text-dim">{s.label}</span>}
                {v != null && <span className={v >= 0 ? 'text-bear' : 'text-bull'}>{v >= 0 ? '+' : ''}{v.toFixed(2)}%</span>}
              </span>
            )
          })}
          {hover?.t && <span className="text-text-muted ml-auto">{hover.t}</span>}
        </div>
      )}
      <div ref={wrapRef} style={{ height: 240 }} />
      {(d?.basis || d?.notes?.length > 0) && (
        <div className="px-2.5 pb-1.5 text-[10.5px] text-text-muted">{[d.basis, ...(d.notes || [])].filter(Boolean).join('; ')}</div>
      )}
    </Frame>
  )
}

export function AnswerWidget({ spec }) {
  if (spec.kind === 'K线') return <KlineWidget code={spec.code} days={spec.days} />
  if (spec.kind === '分时') return <MinuteWidget code={spec.code} />
  return <CompareWidget items={spec.items} days={spec.days} mode={spec.mode} />
}
