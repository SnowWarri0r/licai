import { useState, useEffect } from 'react'
import { fetchJSON } from '../hooks/useApi'

// "2026-06-11 10:49:41" → "今天 10:49" / "昨天 10:49" / "06-10 10:49"
function fmtTime(s) {
  if (!s) return ''
  const ts = s.slice(0, 16)
  const today = new Date().toISOString().slice(0, 10)
  const yest = new Date(Date.now() - 86400000).toISOString().slice(0, 10)
  if (ts.startsWith(today)) return '今天 ' + ts.slice(11)
  if (ts.startsWith(yest)) return '昨天 ' + ts.slice(11)
  return ts.slice(5).replace('T', ' ')
}

export default function SmallMetalNews() {
  const [d, setD] = useState(null)
  const [loading, setLoading] = useState(true)
  const [expanded, setExpanded] = useState(false)

  useEffect(() => {
    fetchJSON('/api/news/small-metal?limit=30').then(setD).catch(() => {}).finally(() => setLoading(false))
  }, [])

  if (loading) return <div className="text-center py-4 text-text-dim text-[12px]">小金属资讯加载中…</div>
  if (!d || !d.count) return null

  const items = expanded ? d.items : d.items.slice(0, 6)

  return (
    <div className="bg-surface-2 border border-border rounded-xl p-4 md:p-5">
      <div className="flex items-baseline gap-2 mb-3">
        <h3 className="text-[14px] font-semibold text-text-bright m-0">小金属资讯</h3>
        <span className="text-[10.5px] text-text-muted">钨钼锑稀土 · 政策/收储/供需/价格</span>
        <span className="ml-auto text-[10.5px] text-text-muted">{d.count} 条</span>
      </div>

      <div className="space-y-2">
        {items.map((it, i) => (
          <a key={i} href={it.url || undefined} target={it.url ? '_blank' : undefined} rel="noopener noreferrer"
            className={`block border-b border-border-subtle/50 pb-2 ${it.url ? 'cursor-pointer hover:opacity-80' : ''}`}>
            <div className="flex items-start gap-2">
              <span className="text-[9.5px] px-1 py-[1px] rounded bg-info/15 text-info shrink-0 mt-[2px]">{it.source}</span>
              <div className="min-w-0">
                <div className="text-[12.5px] text-text leading-snug">{it.title}</div>
                {it.content && <div className="text-[11px] text-text-dim mt-0.5 leading-relaxed line-clamp-2">{it.content}</div>}
                <div className="text-[10px] text-text-muted mt-0.5">{fmtTime(it.time)}</div>
              </div>
            </div>
          </a>
        ))}
      </div>

      {d.items.length > 6 && (
        <button onClick={() => setExpanded(!expanded)}
          className="mt-2.5 text-[11px] text-accent hover:text-accent-bright">
          {expanded ? '收起' : `展开全部 ${d.items.length} 条`}
        </button>
      )}
    </div>
  )
}
