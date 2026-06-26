import { useState, useEffect, useCallback } from 'react'
import { fetchJSON } from '../hooks/useApi'
import SkeletonCard from './Skeleton'

// 信息倾向(描述, 非操作指令)。A股 红=暖/绿=冷。
const SIGNAL_META = {
  偏暖: { label: '偏暖', color: '#cf5c5c', bg: '#cf5c5c18', icon: '🔥' },
  中性: { label: '中性', color: '#a8a39a', bg: '#a8a39a18', icon: '•' },
  偏冷: { label: '偏冷', color: '#5fa86c', bg: '#5fa86c18', icon: '❄' },
  警惕: { label: '警惕', color: '#d4a05c', bg: '#d4a05c18', icon: '⚠' },
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

  if (loading && !data) return <SkeletonCard rows={3} label="早盘简报生成中" />
  if (!data || !data.briefings || data.briefings.length === 0) {
    return (
      <section className="rounded-xl border border-border bg-surface/60 px-3 md:px-5 py-4">
        <div className="flex items-center justify-between">
          <div>
            <h3 className="text-[13px] font-semibold text-text-bright m-0">早盘简报</h3>
            <p className="text-[11px] text-text-dim mt-1 mb-0">每日 9:00 自动生成 · 客观信息摘要 + 风险提示，不含操作建议</p>
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
      <div className="px-3 md:px-5 py-3 border-b border-border flex items-center justify-between"
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
          const meta = SIGNAL_META[b.signal] || SIGNAL_META.中性
          const conf = CONFIDENCE_META[b.confidence] || CONFIDENCE_META.med
          const hasDetail = (b.points && b.points.length > 0) || b.risk
          const isExp = expanded[b.stock_code]
          return (
            <div key={b.stock_code} className="px-3 md:px-5 py-3"
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
                  {b.risk && (
                    <p className="text-[11px] mt-1 mb-0 flex items-start gap-1" style={{ color: '#d4a05c' }}>
                      <span>⚠</span><span>{b.risk}</span>
                    </p>
                  )}
                  {hasDetail && (
                    <button onClick={() => setExpanded(e => ({ ...e, [b.stock_code]: !e[b.stock_code] }))}
                      className="text-[11px] text-accent hover:underline mt-1 cursor-pointer">
                      {isExp ? '收起 ▴' : '要点 ▾'}
                    </button>
                  )}
                  {isExp && b.points && b.points.length > 0 && (
                    <ul className="mt-2 pt-2 border-t border-border-subtle text-[11.5px] text-text-dim m-0 pl-4 list-disc space-y-0.5 leading-relaxed">
                      {b.points.map((p, i) => <li key={i}>{p}</li>)}
                    </ul>
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
