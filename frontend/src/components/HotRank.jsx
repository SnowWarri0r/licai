import { useState, useEffect } from 'react'
import { fetchJSON } from '../hooks/useApi'
import SkeletonCard from './Skeleton'

const pctColor = (v) => v == null ? 'text-text-dim' : v > 0 ? 'text-bear-bright' : v < 0 ? 'text-bull-bright' : 'text-text-dim'

export default function HotRank() {
  const [d, setD] = useState(null)
  const [loading, setLoading] = useState(true)
  const [expanded, setExpanded] = useState(false)

  useEffect(() => {
    fetchJSON('/api/market/hot-rank?top=30').then(setD).catch(() => {}).finally(() => setLoading(false))
  }, [])

  if (loading) return <SkeletonCard rows={6} label="热度榜加载中" />
  if (!d || !d.count) return null

  const items = expanded ? d.items : d.items.slice(0, 10)

  return (
    <div className="bg-surface-2 border border-border rounded-xl p-4 md:p-5">
      <div className="flex items-baseline justify-between gap-2 mb-3 flex-wrap">
        <div className="flex items-baseline gap-2">
          <h3 className="text-[14px] font-semibold text-text-bright m-0">资金热度榜</h3>
          <span className="text-[10.5px] text-text-muted">东财人气榜 · 资金关注度</span>
        </div>
      </div>

      {/* 我的持仓在榜 */}
      {(d.mine || []).length > 0 ? (
        <div className="mb-3 px-3 py-2.5 rounded-lg bg-accent/10 border border-accent/30">
          <div className="text-[10px] text-accent/80 mb-1.5 tracking-wider">我的持仓人气排名</div>
          <div className="flex flex-wrap gap-2">
            {d.mine.map((m, i) => (
              <span key={i} className="text-[11.5px] bg-surface-3 rounded px-2 py-1">
                <span className="text-text-bright">{m.name}</span>
                <span className="text-accent font-mono ml-1">#{m.rank}</span>
                {m.pct != null && <span className={`font-mono ml-1.5 ${pctColor(m.pct)}`}>{m.pct > 0 ? '+' : ''}{m.pct}%</span>}
              </span>
            ))}
          </div>
        </div>
      ) : (
        <div className="mb-3 text-[11px] text-text-muted">你的持仓暂未进资金人气榜前 {d.count}</div>
      )}

      {/* 全榜 */}
      <div className="space-y-0.5">
        {items.map((r, i) => (
          <div key={i} className={`flex items-center gap-2 text-[11.5px] py-1 border-b border-border-subtle/50 ${r.mine ? 'bg-accent/5 -mx-1 px-1 rounded' : ''}`}>
            <span className="font-mono text-text-muted w-7 shrink-0 text-right">{r.rank}</span>
            <span className={`truncate ${r.mine ? 'text-accent font-medium' : 'text-text-bright'}`}>{r.name}{r.mine && ' ★'}</span>
            <span className="font-mono text-text-dim text-[10.5px] ml-auto shrink-0">{r.price}</span>
            <span className={`font-mono text-[11px] w-14 text-right shrink-0 ${pctColor(r.pct)}`}>{r.pct > 0 ? '+' : ''}{r.pct}%</span>
          </div>
        ))}
      </div>
      {d.items.length > 10 && (
        <button onClick={() => setExpanded(!expanded)} className="mt-2 text-[11px] text-accent hover:text-accent-bright">
          {expanded ? '收起' : `展开前 ${d.items.length} 名`}
        </button>
      )}
      <div className="text-[10px] text-text-muted pt-2 mt-1.5 border-t border-border-subtle">
        资金/散户人气关注度 · 纯客观, 不构成买卖建议
      </div>
    </div>
  )
}
