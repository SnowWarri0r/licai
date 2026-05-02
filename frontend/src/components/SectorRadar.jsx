import { useState, useEffect, useCallback } from 'react'
import { fetchJSON } from '../hooks/useApi'
import Tooltip from './Tooltip'

function Sparkline({ data, width = 64, height = 22, stroke = '#85a0b4' }) {
  if (!data || data.length < 2) return null
  const closes = data.map(d => d.close).filter(c => c > 0)
  if (closes.length < 2) return null
  const min = Math.min(...closes)
  const max = Math.max(...closes)
  const range = max - min || 1
  const stepX = width / (closes.length - 1)
  const points = closes.map((c, i) => {
    const x = i * stepX
    const y = height - ((c - min) / range) * height
    return `${x.toFixed(1)},${y.toFixed(1)}`
  }).join(' ')
  const last = closes[closes.length - 1]
  const first = closes[0]
  const isUp = last >= first
  return (
    <svg width={width} height={height} className="shrink-0">
      <polyline points={points} fill="none" stroke={isUp ? '#cf5c5c' : '#5fa86c'} strokeWidth="1.2" />
    </svg>
  )
}

const fmtPct = (v) => v == null ? '--' : (v >= 0 ? '+' : '') + v.toFixed(1) + '%'
const colorOf = (v) => v == null ? 'text-text-dim' : v > 0 ? 'text-bear-bright' : v < 0 ? 'text-bull-bright' : 'text-text'
const alphaColor = (v) => v == null ? 'text-text-dim' : v > 1 ? 'text-bear-bright' : v < -1 ? 'text-bull-bright' : 'text-text'

export default function SectorRadar() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)

  const load = useCallback(async (force = false) => {
    setLoading(true)
    try { setData(await fetchJSON(`/api/sector/compare-all${force ? '?force=true' : ''}`)) }
    catch (e) { console.error(e) }
    finally { setLoading(false) }
  }, [])

  useEffect(() => { load() }, [load])

  if (loading && !data) {
    return <div className="text-center py-3 text-text-dim text-[12px]">加载板块对比...</div>
  }
  if (!data || !data.holdings || data.holdings.length === 0) {
    return null
  }

  return (
    <section className="rounded-xl border border-border bg-surface/60 overflow-hidden"
      style={{ animation: 'fade-up 0.4s ease-out' }}>
      <div className="px-5 py-3 border-b border-border flex items-center justify-between"
        style={{ background: 'linear-gradient(180deg, var(--color-surface-2), var(--color-surface))' }}>
        <div className="flex items-baseline gap-2">
          <h3 className="text-[13px] font-semibold text-text-bright m-0">板块雷达</h3>
          <span className="text-[11px] text-text-dim">你 vs 行业 ETF · 30/60 日</span>
        </div>
        <button onClick={() => load(true)} disabled={loading}
          className="px-2.5 py-[3px] rounded-md text-[11px] border border-border-med text-text-dim hover:text-text hover:border-accent transition-colors cursor-pointer disabled:opacity-50">
          {loading ? '...' : '刷新'}
        </button>
      </div>

      <div className="grid px-5 py-1.5 text-[10.5px] text-text-dim tracking-wider font-medium border-b border-border-subtle"
        style={{ gridTemplateColumns: '24% 14% 18% 18% 14% 12%' }}>
        <div>持仓</div>
        <div className="text-right">板块</div>
        <div className="text-right">30 日 (你 / 板块)</div>
        <div className="text-right">60 日 (你 / 板块)</div>
        <div className="text-right">
          <Tooltip content={
            <div className="leading-relaxed">
              <div><span className="text-text-bright font-semibold">α</span> = 你 60 日 − 板块 60 日</div>
              <div className="text-text-dim mt-1 text-[10.5px]">
                正值跑赢板块；负值跑输板块；±1% 以内视为同步
              </div>
            </div>
          }>
            <span className="cursor-help underline decoration-dotted decoration-text-muted underline-offset-2">α</span>
          </Tooltip>
        </div>
        <div className="text-right">ETF 60d</div>
      </div>

      <div className="divide-y divide-border-subtle">
        {data.holdings.map(r => (
          <div key={r.stock_code} className="grid px-5 py-2 items-center text-[11.5px]"
            style={{ gridTemplateColumns: '24% 14% 18% 18% 14% 12%' }}>
            <div className="flex flex-col min-w-0">
              <span className="text-text-bright font-semibold truncate">{r.stock_name}</span>
              <span className="font-mono text-[10px] text-text-muted">{r.stock_code}</span>
            </div>
            <div className="text-right">
              {r.etf_code ? (
                <div className="flex flex-col items-end">
                  <span className="text-text">{r.sector_label}</span>
                  <span className="font-mono text-[10px] text-text-muted">{r.etf_code}</span>
                </div>
              ) : (
                <span className="text-text-dim">--</span>
              )}
            </div>
            <div className="text-right font-mono">
              <span className={colorOf(r.stock_30d)}>{fmtPct(r.stock_30d)}</span>
              <span className="text-text-muted mx-0.5">/</span>
              <span className={colorOf(r.etf_30d)}>{fmtPct(r.etf_30d)}</span>
            </div>
            <div className="text-right font-mono">
              <span className={colorOf(r.stock_60d)}>{fmtPct(r.stock_60d)}</span>
              <span className="text-text-muted mx-0.5">/</span>
              <span className={colorOf(r.etf_60d)}>{fmtPct(r.etf_60d)}</span>
            </div>
            <div className="text-right font-mono">
              <span className={alphaColor(r.alpha_60d)}>
                {fmtPct(r.alpha_60d)}
              </span>
            </div>
            <div className="flex justify-end">
              <Sparkline data={r.etf_kline} />
            </div>
          </div>
        ))}
      </div>

      <div className="px-5 py-2 text-[10.5px] text-text-muted bg-surface-2/40 border-t border-border-subtle">
        α &lt; -1% 表示你跑输板块（跌得比 ETF 更狠）；α &gt; +1% 表示跑赢；接近 0 = 跟板块同步。
      </div>
    </section>
  )
}
