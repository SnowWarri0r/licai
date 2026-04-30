import { useState, useEffect, useCallback, useMemo } from 'react'
import { fetchJSON } from '../hooks/useApi'
import Tooltip from './Tooltip'

function Sparkline({ data, width = 60, height = 20 }) {
  if (!data || data.length < 2) return <span className="text-text-dim text-[10px]">--</span>
  const closes = data.map(d => d.close).filter(c => c > 0)
  if (closes.length < 2) return <span className="text-text-dim text-[10px]">--</span>
  const min = Math.min(...closes)
  const max = Math.max(...closes)
  const range = max - min || 1
  const stepX = width / (closes.length - 1)
  const points = closes.map((c, i) => {
    const x = i * stepX
    const y = height - ((c - min) / range) * height
    return `${x.toFixed(1)},${y.toFixed(1)}`
  }).join(' ')
  const isUp = closes[closes.length - 1] >= closes[0]
  return (
    <svg width={width} height={height} className="shrink-0">
      <polyline points={points} fill="none" stroke={isUp ? '#cf5c5c' : '#5fa86c'} strokeWidth="1.2" />
    </svg>
  )
}

const fmtPct = (v) => v == null ? '--' : (v >= 0 ? '+' : '') + v.toFixed(1) + '%'
const colorOf = (v) => v == null ? 'text-text-dim'
  : v > 3 ? 'text-bear-bright'
  : v > 0 ? 'text-bear'
  : v < -3 ? 'text-bull-bright'
  : v < 0 ? 'text-bull' : 'text-text'

// 净流入 单位是亿元
const fmtFlow = (v) => {
  if (v == null) return '--'
  const sign = v >= 0 ? '+' : ''
  return `${sign}${v.toFixed(1)}亿`
}

export default function SectorOpportunities() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [filter, setFilter] = useState(() => localStorage.getItem('sectorScanFilter') || 'unheld')

  const load = useCallback(async (force = false) => {
    setLoading(true)
    try { setData(await fetchJSON(`/api/sector/scan${force ? '?force=true' : ''}`)) }
    catch (e) { console.error(e) }
    finally { setLoading(false) }
  }, [])

  useEffect(() => { load() }, [load])

  const setFilt = (f) => { setFilter(f); localStorage.setItem('sectorScanFilter', f) }

  const visibleRows = useMemo(() => {
    if (!data?.sectors) return []
    let rows = data.sectors
    if (filter === 'unheld') rows = rows.filter(r => !r.held)
    if (filter === 'held') rows = rows.filter(r => r.held)
    return rows
  }, [data, filter])

  if (loading && !data) {
    return <div className="text-center py-3 text-text-dim text-[12px]">扫描板块动量...</div>
  }
  if (!data || !data.sectors || data.sectors.length === 0) return null

  const heldCount = data.held_boards?.length || 0
  const totalCount = data.total || 0

  return (
    <section className="rounded-xl border border-border bg-surface/60 overflow-hidden"
      style={{ animation: 'fade-up 0.4s ease-out' }}>
      <div className="px-3 md:px-5 py-3 border-b border-border flex items-center justify-between flex-wrap gap-2"
        style={{ background: 'linear-gradient(180deg, var(--color-surface-2), var(--color-surface))' }}>
        <div className="flex items-baseline gap-2">
          <h3 className="text-[13px] font-semibold text-text-bright m-0">板块动量</h3>
          <span className="text-[11px] text-text-dim">
            按 5 日涨幅 · {visibleRows.length}/{totalCount} 板块 · 持仓 {heldCount}
          </span>
        </div>
        <div className="flex gap-1.5 items-center">
          {[
            ['unheld', '未持仓'],
            ['all', '全部'],
            ['held', '已持仓'],
          ].map(([k, l]) => (
            <button key={k} onClick={() => setFilt(k)}
              className="px-2.5 py-[3px] rounded-md text-[11px] border transition-colors cursor-pointer"
              style={{
                borderColor: filter === k ? 'var(--color-accent)' : 'var(--color-border-med)',
                background: filter === k ? 'var(--color-accent)1a' : 'transparent',
                color: filter === k ? 'var(--color-accent)' : 'var(--color-text-dim)',
              }}>
              {l}
            </button>
          ))}
          <button onClick={() => load(true)} disabled={loading}
            className="ml-1 px-2.5 py-[3px] rounded-md text-[11px] border border-border-med text-text-dim hover:text-text hover:border-accent transition-colors cursor-pointer disabled:opacity-50">
            {loading ? '...' : '刷新'}
          </button>
        </div>
      </div>

      <div className="licai-opp-row px-3 md:px-5 py-1.5 text-[10.5px] text-text-dim tracking-wider font-medium border-b border-border-subtle">
        <div>板块</div>
        <div className="text-right licai-md-only">1 日</div>
        <div className="text-right">5 日</div>
        <div className="text-right">30 日</div>
        <div className="text-right licai-md-only">
          <Tooltip content={
            <div className="leading-relaxed">
              <div className="text-text-bright font-semibold mb-0.5">板块净流入</div>
              <div className="text-text-dim text-[10.5px]">主力资金当日净流入金额（亿元）。+ = 加仓 / - = 撤资</div>
            </div>
          }>
            <span className="cursor-help underline decoration-dotted decoration-text-muted underline-offset-2">主力净流入</span>
          </Tooltip>
        </div>
        <div className="text-right licai-md-only">领涨股</div>
        <div className="text-right licai-md-only">兜底 ETF</div>
        <div className="text-right">走势 60d</div>
      </div>

      <div className="divide-y divide-border-subtle max-h-[480px] overflow-y-auto">
        {visibleRows.length === 0 ? (
          <div className="px-3 md:px-5 py-6 text-center text-text-dim text-[11.5px]">
            {filter === 'unheld' ? '当前筛选下没有数据，试试切到"全部"' :
              filter === 'held' ? '没有匹配到任何持仓板块（可能是新股或映射没覆盖）' :
              '暂无数据'}
          </div>
        ) : visibleRows.map(r => (
          <div key={r.name} className="licai-opp-row px-3 md:px-5 py-2 items-center text-[11.5px]">
            <div className="flex items-center gap-1.5 min-w-0">
              <span className="text-text-bright font-semibold truncate">{r.name}</span>
              {r.held && (
                <span className="shrink-0 px-1 py-0 rounded text-[9px] bg-accent/20 text-accent border border-accent/40">
                  持仓
                </span>
              )}
            </div>
            <div className={`text-right font-mono licai-md-only ${colorOf(r.change_1d)}`}>
              {fmtPct(r.change_1d)}
            </div>
            <div className={`text-right font-mono font-semibold ${colorOf(r.change_5d)}`}>
              {fmtPct(r.change_5d)}
            </div>
            <div className={`text-right font-mono ${colorOf(r.change_30d)}`}>
              {fmtPct(r.change_30d)}
            </div>
            <div className={`text-right font-mono licai-md-only ${r.net_flow > 0 ? 'text-bear' : r.net_flow < 0 ? 'text-bull' : 'text-text-dim'}`}>
              {fmtFlow(r.net_flow)}
            </div>
            <div className="text-right truncate licai-md-only">
              {r.leader ? (
                <span className="text-text truncate">
                  {r.leader}
                  {r.leader_change != null && (
                    <span className={`ml-1 text-[10px] ${colorOf(r.leader_change)}`}>
                      {fmtPct(r.leader_change)}
                    </span>
                  )}
                </span>
              ) : <span className="text-text-dim">--</span>}
            </div>
            <div className="text-right licai-md-only">
              {r.etf_code ? (
                <span className="font-mono text-[10.5px] text-text">{r.etf_code}</span>
              ) : <span className="text-text-dim">--</span>}
            </div>
            <div className="flex justify-end">
              <Sparkline data={r.kline_tail} />
            </div>
          </div>
        ))}
      </div>

      <div className="px-3 md:px-5 py-2 text-[10.5px] text-text-muted bg-surface-2/40 border-t border-border-subtle">
        仅展示数据，不构成投资建议。前 30 个板块（按 1 日涨幅）有 5d/30d 数据，其余仅 1d。
      </div>
    </section>
  )
}
