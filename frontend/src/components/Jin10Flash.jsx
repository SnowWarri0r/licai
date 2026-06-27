import { useState, useEffect, useRef } from 'react'
import { fetchJSON } from '../hooks/useApi'

// 金十快讯滚动流: 全球宏观/地缘/央行实时快讯, 重要的高亮。30s 自动刷新。
export default function Jin10Flash() {
  const [items, setItems] = useState([])
  const [updated, setUpdated] = useState('')
  const timer = useRef(null)

  const load = () => {
    fetchJSON('/api/news/jin10?limit=40')
      .then(d => {
        setItems(d.items || [])
        setUpdated(new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' }))
      })
      .catch(() => {})
  }

  useEffect(() => {
    load()
    timer.current = setInterval(load, 30000)
    return () => clearInterval(timer.current)
  }, [])

  // "MM-DD HH:MM:SS" → "HH:MM"
  const hm = (t) => {
    const m = (t || '').match(/(\d{2}):(\d{2})/)
    return m ? `${m[1]}:${m[2]}` : ''
  }

  return (
    <div className="bg-surface-2 border border-border rounded-xl p-4 md:p-5">
      <div className="flex items-baseline gap-2 mb-3">
        <h3 className="text-[14px] font-semibold text-text-bright m-0">金十快讯</h3>
        <span className="text-[10.5px] text-text-muted">全球宏观 / 地缘 / 央行实时 · 30s 刷新</span>
        {updated && <span className="text-[10px] text-text-muted ml-auto">更新 {updated}</span>}
      </div>

      {items.length === 0 ? (
        <div className="text-[12px] text-text-dim py-4 text-center">加载中…</div>
      ) : (
        <div className="max-h-[62vh] overflow-y-auto pr-1">
          <div className="relative pl-3 border-l border-border-subtle space-y-3">
            {items.map((it, i) => (
              <div key={i} className="relative">
                <span className={`absolute -left-[15px] top-1 w-1.5 h-1.5 rounded-full ${it.important ? 'bg-accent' : 'bg-border-strong'}`} />
                <div className="flex items-baseline gap-2">
                  <span className="text-[10.5px] text-text-muted shrink-0 tabular-nums">{hm(it.time)}</span>
                  {it.important && <span className="text-[9.5px] px-1 rounded bg-accent/15 text-accent border border-accent/30 shrink-0">重要</span>}
                </div>
                <div className={`text-[12px] leading-relaxed mt-0.5 ${it.important ? 'text-text-bright' : 'text-text-dim'}`}>
                  {it.title}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
      <div className="text-[10px] text-text-muted pt-2.5 mt-2 border-t border-border-subtle">
        来源 金十数据 · 仅供参考, 不构成任何买卖建议
      </div>
    </div>
  )
}
