import { useState, useEffect, useCallback } from 'react'
import { fetchJSON } from '../hooks/useApi'

const VERDICT_META = {
  lock_all: { label: '锁档', color: '#e58a8a', bg: '#e58a8a18', icon: '🔒' },
  hold:     { label: '观望', color: '#a8a39a', bg: '#a8a39a18', icon: '⏸' },
  raise:    { label: '上调', color: '#5fa86c', bg: '#5fa86c18', icon: '↗' },
  lower:    { label: '下调', color: '#d4a05c', bg: '#d4a05c18', icon: '↘' },
  add_now:  { label: '可加仓', color: '#c8a876', bg: '#c8a87618', icon: '✅' },
}
const CONFIDENCE_META = {
  high: { label: '高', color: '#5fa86c' },
  med:  { label: '中', color: '#d4a05c' },
  low:  { label: '低', color: '#a8a39a' },
}

export default function MorningBriefing() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const [expanded, setExpanded] = useState({})

  const load = useCallback(async () => {
    setLoading(true)
    try { setData(await fetchJSON('/api/briefing')) }
    catch (e) { console.error(e) }
    finally { setLoading(false) }
  }, [])

  useEffect(() => { load() }, [load])

  const refresh = async () => {
    if (refreshing) return
    setRefreshing(true)
    try {
      await fetchJSON('/api/briefing/refresh', { method: 'POST' })
      await load()
    } catch (e) { console.error(e) }
    finally { setRefreshing(false) }
  }

  if (loading && !data) {
    return <div className="text-center py-4 text-text-dim text-[12px]">加载早盘简报...</div>
  }
  if (!data || !data.briefings || data.briefings.length === 0) {
    return (
      <section className="rounded-xl border border-border bg-surface/60 px-5 py-4">
        <div className="flex items-center justify-between">
          <div>
            <h3 className="text-[13px] font-semibold text-text-bright m-0">早盘简报</h3>
            <p className="text-[11px] text-text-dim mt-1 mb-0">每日 9:00 自动生成 · 由 LLM 综合新闻 + 行情产出</p>
          </div>
          <button onClick={refresh} disabled={refreshing}
            className="px-3 py-1 rounded-md text-[11px] border border-accent/40 bg-accent/10 text-accent hover:bg-accent/20 transition-colors cursor-pointer disabled:opacity-50">
            {refreshing ? '生成中...' : '立即生成'}
          </button>
        </div>
      </section>
    )
  }

  return (
    <section className="rounded-xl border border-border bg-surface/60 overflow-hidden"
      style={{ animation: 'fade-up 0.4s ease-out' }}>
      <div className="px-5 py-3 border-b border-border flex items-center justify-between"
        style={{ background: 'linear-gradient(180deg, var(--color-surface-2), var(--color-surface))' }}>
        <div className="flex items-baseline gap-2">
          <h3 className="text-[13px] font-semibold text-text-bright m-0">早盘简报</h3>
          <span className="text-[11px] font-mono text-text-dim">{data.date}</span>
          {!data.is_today && (
            <span className="text-[10px] px-1.5 py-[1px] rounded bg-warn/20 text-warn border border-warn/40">非今日</span>
          )}
        </div>
        <button onClick={refresh} disabled={refreshing}
          className="px-2.5 py-[3px] rounded-md text-[11px] border border-border-med text-text-dim hover:text-text hover:border-accent transition-colors cursor-pointer disabled:opacity-50">
          {refreshing ? '更新中...' : '重新生成'}
        </button>
      </div>

      <div className="divide-y divide-border-subtle">
        {data.briefings.map(b => {
          const meta = VERDICT_META[b.verdict] || VERDICT_META.hold
          const conf = CONFIDENCE_META[b.confidence] || CONFIDENCE_META.med
          const isExp = expanded[b.stock_code]
          return (
            <div key={b.stock_code} className="px-5 py-3"
              style={{ borderLeft: `3px solid ${meta.color}` }}>
              <div className="flex items-start gap-3">
                <span className="inline-flex items-center gap-1 text-[11px] font-semibold px-2 py-[2px] rounded shrink-0"
                  style={{ background: meta.bg, color: meta.color, border: `1px solid ${meta.color}50` }}>
                  <span>{meta.icon}</span>
                  <span>{meta.label}</span>
                </span>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="text-[13px] font-semibold text-text-bright">{b.stock_name}</span>
                    <span className="font-mono text-[10px] text-text-muted">{b.stock_code}</span>
                    <span className="font-mono text-[11px] text-text-dim">
                      ¥{b.current_price?.toFixed(2)} · {b.pnl_pct >= 0 ? '+' : ''}{b.pnl_pct?.toFixed(1)}%
                    </span>
                    <span className="text-[10px] text-text-muted ml-auto">
                      置信度 <span style={{ color: conf.color }}>{conf.label}</span>
                    </span>
                  </div>
                  {b.summary && (
                    <p className="text-[12px] text-text mt-1 mb-0 leading-relaxed">{b.summary}</p>
                  )}
                  {b.error && (
                    <p className="text-[11px] text-bear-bright mt-1 mb-0">{b.error}</p>
                  )}
                  {(b.reasoning || (b.key_news && b.key_news.length > 0)) && (
                    <button onClick={() => setExpanded(e => ({ ...e, [b.stock_code]: !e[b.stock_code] }))}
                      className="text-[11px] text-accent hover:underline mt-1 cursor-pointer">
                      {isExp ? '收起 ▴' : '详情 ▾'}
                    </button>
                  )}
                  {isExp && (
                    <div className="mt-2 pt-2 border-t border-border-subtle space-y-1.5">
                      {b.reasoning && (
                        <p className="text-[11.5px] text-text-dim m-0 leading-relaxed">{b.reasoning}</p>
                      )}
                      {b.key_news && b.key_news.length > 0 && (
                        <ul className="text-[11px] text-text-muted m-0 pl-4 list-disc space-y-0.5">
                          {b.key_news.map((n, i) => <li key={i}>{n}</li>)}
                        </ul>
                      )}
                      {b.tranche_action && (
                        <div className="text-[11px] text-text-dim">
                          建议动作: <span className="text-text" style={{ color: meta.color }}>{b.tranche_action}</span>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              </div>
            </div>
          )
        })}
      </div>
    </section>
  )
}
