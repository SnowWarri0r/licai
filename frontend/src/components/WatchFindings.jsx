import { useState, useEffect } from 'react'
import { fetchJSON } from '../hooks/useApi'

// 盯盘员当天的发现(services/watcher.py): 固定在内容区顶部, 逐条可收起。没有发现时不占位。
export default function WatchFindings() {
  const [items, setItems] = useState([])

  const load = () => fetchJSON('/api/watch/findings').then(d => setItems(d.items || [])).catch(() => {})
  useEffect(() => {
    load()
    const t = setInterval(load, 5 * 60 * 1000)
    return () => clearInterval(t)
  }, [])

  const dismiss = (id) => {
    setItems(xs => xs.filter(x => x.id !== id))
    fetchJSON(`/api/watch/findings/${id}/dismiss`, { method: 'POST' }).catch(load)
  }

  if (!items.length) return null
  return (
    <div className="mx-4 mt-3 flex flex-col gap-1.5" style={{ animation: 'fade-up 0.3s ease-out' }}>
      {items.map(f => (
        <div key={f.id}
          className={`px-3 py-2 rounded-lg border flex items-start justify-between gap-3 ${
            f.level === 'warn' ? 'bg-accent/10 border-accent/30' : 'bg-surface border-border'}`}>
          <div className="min-w-0">
            <div className="text-[12.5px] font-medium text-text">
              <span className="text-[10.5px] text-text-muted mr-1.5">盯盘员</span>{f.title}
            </div>
            {f.body && <div className="text-[11.5px] text-text-dim whitespace-pre-line mt-0.5">{f.body}</div>}
          </div>
          <button onClick={() => dismiss(f.id)}
            className="text-[11px] px-2 py-0.5 rounded border border-border text-text-muted hover:text-text cursor-pointer shrink-0">
            收起
          </button>
        </div>
      ))}
    </div>
  )
}
