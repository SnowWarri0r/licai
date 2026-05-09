import { useState, useEffect, useCallback, useMemo } from 'react'
import { fetchJSON } from '../hooks/useApi'
import Tooltip from './Tooltip'
import SectorKlineModal from './SectorKlineModal'

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

const MARKETS = {
  A:  { label: 'A 股',  endpoint: '/api/sector/scan',     hasFlow: true,  hasHeld: true },
  HK: { label: '港股',  endpoint: '/api/sector/scan-hk',  hasFlow: false, hasHeld: true },
  US: { label: '美股',  endpoint: '/api/sector/scan-us',  hasFlow: false, hasHeld: true },
}

export default function SectorOpportunities() {
  const [market, setMarket] = useState(() => localStorage.getItem('sectorScanMarket') || 'A')
  const [dataByMarket, setDataByMarket] = useState({})
  const [loading, setLoading] = useState(false)
  const [filter, setFilter] = useState(() => localStorage.getItem('sectorScanFilter') || 'unheld')
  const [sortKey, setSortKey] = useState(() => localStorage.getItem('sectorScanSort') || '5d')
  const [openSector, setOpenSector] = useState(null)

  const cfg = MARKETS[market] || MARKETS.A
  const data = dataByMarket[market]

  const SORT_KEYS = useMemo(() => {
    const base = {
      '5d':  { label: '5 日',   pick: r => r.change_5d },
      '30d': { label: '30 日',  pick: r => r.change_30d },
      '1d':  { label: '1 日',   pick: r => r.change_1d },
    }
    if (cfg.hasFlow) {
      // 把 flow 插到 5d 后
      return { '5d': base['5d'], 'flow': { label: '净流入', pick: r => r.net_flow },
               '30d': base['30d'], '1d': base['1d'] }
    }
    return base
  }, [cfg.hasFlow])

  const load = useCallback(async (force = false, m = market) => {
    setLoading(true)
    try {
      const ep = (MARKETS[m] || MARKETS.A).endpoint
      const result = await fetchJSON(`${ep}${force ? '?force=true' : ''}`)
      setDataByMarket(prev => ({ ...prev, [m]: result }))
    } catch (e) { console.error(e) }
    finally { setLoading(false) }
  }, [market])

  useEffect(() => {
    if (!dataByMarket[market]) load(false, market)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [market])

  const setMkt = (m) => { setMarket(m); localStorage.setItem('sectorScanMarket', m) }
  const setFilt = (f) => { setFilter(f); localStorage.setItem('sectorScanFilter', f) }
  const setSort = (s) => { setSortKey(s); localStorage.setItem('sectorScanSort', s) }

  // 当前 sort key 在新市场不可用时, 自动回退 5d
  useEffect(() => {
    if (!SORT_KEYS[sortKey]) setSort('5d')
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [SORT_KEYS])

  const visibleRows = useMemo(() => {
    if (!data?.sectors) return []
    let rows = data.sectors.slice()
    if (cfg.hasHeld) {
      if (filter === 'unheld') rows = rows.filter(r => !r.held)
      if (filter === 'held') rows = rows.filter(r => r.held)
    }
    const pick = SORT_KEYS[sortKey]?.pick || SORT_KEYS['5d'].pick
    rows.sort((a, b) => {
      const va = pick(a); const vb = pick(b)
      if (va == null && vb == null) return 0
      if (va == null) return 1
      if (vb == null) return -1
      return vb - va
    })
    return rows
  }, [data, filter, sortKey, SORT_KEYS, cfg.hasHeld])

  if (loading && !data) {
    return <div className="text-center py-3 text-text-dim text-[12px]">扫描{cfg.label}板块动量...</div>
  }
  if (!data || !data.sectors || data.sectors.length === 0) {
    return (
      <section className="rounded-xl border border-border bg-surface/60 px-4 py-3 text-[12px] text-text-dim">
        {cfg.label}板块暂无数据
      </section>
    )
  }

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
            {visibleRows.length}/{totalCount} 板块{cfg.hasHeld ? ` · 持仓 ${heldCount}` : ''}
          </span>
        </div>
        <div className="flex gap-1.5 items-center flex-wrap">
          {Object.entries(MARKETS).map(([k, { label }]) => (
            <button key={k} onClick={() => setMkt(k)}
              className="px-2.5 py-[3px] rounded-md text-[11px] border transition-colors cursor-pointer"
              style={{
                borderColor: market === k ? 'var(--color-accent)' : 'var(--color-border-med)',
                background: market === k ? 'var(--color-accent)1a' : 'transparent',
                color: market === k ? 'var(--color-accent)' : 'var(--color-text-dim)',
              }}>
              {label}
            </button>
          ))}
          <span className="w-px h-3.5 bg-border mx-0.5" />
          {cfg.hasHeld && (
            <>
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
              <span className="w-px h-3.5 bg-border mx-0.5" />
            </>
          )}
          <span className="text-[10px] text-text-muted shrink-0">排序</span>
          {Object.entries(SORT_KEYS).map(([k, { label }]) => (
            <button key={k} onClick={() => setSort(k)}
              className="px-2.5 py-[3px] rounded-md text-[11px] border transition-colors cursor-pointer"
              style={{
                borderColor: sortKey === k ? 'var(--color-info)' : 'var(--color-border-med)',
                background: sortKey === k ? 'var(--color-info)20' : 'transparent',
                color: sortKey === k ? 'var(--color-info)' : 'var(--color-text-dim)',
              }}>
              {label}
            </button>
          ))}
          <button onClick={() => load(true, market)} disabled={loading}
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
          {cfg.hasFlow ? (
            <Tooltip content={
              <div className="leading-relaxed">
                <div className="text-text-bright font-semibold mb-0.5">板块净流入</div>
                <div className="text-text-dim text-[10.5px]">主力资金当日净流入金额（亿元）。+ = 加仓 / - = 撤资</div>
              </div>
            }>
              <span className="cursor-help underline decoration-dotted decoration-text-muted underline-offset-2">主力净流入</span>
            </Tooltip>
          ) : (
            <Tooltip content={
              <div className="leading-relaxed">
                <div className="text-text-bright font-semibold mb-0.5">板块资金流</div>
                <div className="text-text-dim text-[10.5px]">{market === 'US' ? '美股' : '港股'}暂无板块级资金流免费数据源（同花顺指标仅 A 股可用）</div>
              </div>
            }>
              <span className="cursor-help underline decoration-dotted decoration-text-muted underline-offset-2 text-text-muted">资金流</span>
            </Tooltip>
          )}
        </div>
        <div className="text-right licai-md-only">{cfg.hasFlow ? '领涨股' : '代码'}</div>
        <div className="text-right licai-md-only">兜底 ETF</div>
        <div className="text-right">走势 60d</div>
      </div>

      <div className="divide-y divide-border-subtle max-h-[480px] overflow-y-auto">
        {visibleRows.length === 0 ? (
          <div className="px-3 md:px-5 py-6 text-center text-text-dim text-[11.5px]">
            {cfg.hasHeld ? (
              filter === 'unheld' ? '当前筛选下没有数据，试试切到"全部"' :
                filter === 'held' ? '没有匹配到任何持仓板块（可能是新股或映射没覆盖）' :
                '暂无数据'
            ) : '暂无数据'}
          </div>
        ) : visibleRows.map(r => (
          <div key={r.name} className="licai-opp-row px-3 md:px-5 py-2 items-center text-[11.5px]">
            <div className="flex items-center gap-1.5 min-w-0">
              <span className="text-text-bright font-semibold truncate">{r.name}</span>
              {cfg.hasHeld && r.held && (
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
            <div className={`text-right font-mono licai-md-only ${cfg.hasFlow ? (r.net_flow > 0 ? 'text-bear' : r.net_flow < 0 ? 'text-bull' : 'text-text-dim') : 'text-text-dim'}`}>
              {cfg.hasFlow ? fmtFlow(r.net_flow) : '--'}
            </div>
            <div className="text-right truncate licai-md-only">
              {cfg.hasFlow ? (
                r.leader ? (
                  <span className="text-text truncate">
                    {r.leader}
                    {r.leader_change != null && (
                      <span className={`ml-1 text-[10px] ${colorOf(r.leader_change)}`}>
                        {fmtPct(r.leader_change)}
                      </span>
                    )}
                  </span>
                ) : <span className="text-text-dim">--</span>
              ) : (
                r.symbol ? <span className="font-mono text-[10.5px] text-text-dim">{r.symbol}</span>
                  : <span className="text-text-dim">--</span>
              )}
            </div>
            <div className="text-right truncate licai-md-only">
              {r.etf_code ? (
                <span className="font-mono text-[10.5px] text-text">{r.etf_code}</span>
              ) : <span className="text-text-dim">--</span>}
            </div>
            <div className="flex justify-end">
              <button onClick={() => setOpenSector(r)}
                className="cursor-pointer hover:bg-surface-3 rounded p-0.5 -m-0.5 transition-colors"
                title="点击查看大图">
                <Sparkline data={r.kline_tail} />
              </button>
            </div>
          </div>
        ))}
      </div>

      <div className="px-3 md:px-5 py-2 text-[10.5px] text-text-muted bg-surface-2/40 border-t border-border-subtle">
        仅展示数据，不构成投资建议。K 线缓存 10 分钟刷新一次，点击走势图可查看大图。
        {market === 'US' && <span className="ml-1">美股以 SPDR Sector ETF 作为 GICS 板块代理。</span>}
        {market === 'HK' && <span className="ml-1">港股使用恒生综合行业指数。</span>}
      </div>

      {openSector && (
        <SectorKlineModal sector={openSector} market={market} onClose={() => setOpenSector(null)} />
      )}
    </section>
  )
}
