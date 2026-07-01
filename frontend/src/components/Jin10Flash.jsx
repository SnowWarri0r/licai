import { useState, useEffect, useRef } from 'react'
import { fetchJSON } from '../hooks/useApi'
import NewsDetailModal from './NewsDetailModal'
import ImageZoom from './ImageZoom'

// 金十快讯滚动流: 全球宏观/地缘/央行实时快讯。重要高亮 + 关联持仓标记 + 筛选。30s 自动刷新。
export default function Jin10Flash() {
  const [items, setItems] = useState([])
  const [updated, setUpdated] = useState('')
  const [filter, setFilter] = useState('all')   // all | important | related
  const [sel, setSel] = useState(null)          // 点开解读的快讯
  const timer = useRef(null)

  const load = () => {
    fetchJSON('/api/news/jin10?limit=80')
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
  // "MM-DD HH:MM:SS" → "MM-DD" (用于日期分隔)
  const md = (t) => {
    const m = (t || '').match(/(\d{2})-(\d{2})/)
    return m ? `${m[1]}-${m[2]}` : ''
  }

  const impCount = items.filter(i => i.important).length
  const relCount = items.filter(i => i.related).length
  const shown = items.filter(i =>
    filter === 'important' ? i.important : filter === 'related' ? i.related : true)

  const Chip = ({ id, label, n }) => (
    <button onClick={() => setFilter(id)}
      className={`text-[10.5px] px-2 py-[2px] rounded-full border transition-colors cursor-pointer ${
        filter === id ? 'border-accent text-accent bg-accent/10' : 'border-border-med text-text-dim hover:text-text'}`}>
      {label}{n != null && <span className="ml-1 opacity-70">{n}</span>}
    </button>
  )

  let lastDay = ''

  return (
    <div className="bg-surface-2 border border-border rounded-xl p-4 md:p-5">
      <div className="flex items-baseline gap-2 mb-2">
        <h3 className="text-[14px] font-semibold text-text-bright m-0">金十快讯</h3>
        <span className="text-[10.5px] text-text-muted">点快讯看 AI 解读 · 30s 刷新</span>
        {updated && <span className="text-[10px] text-text-muted ml-auto">更新 {updated}</span>}
      </div>
      <div className="flex gap-1.5 mb-3">
        <Chip id="all" label="全部" n={items.length} />
        <Chip id="important" label="重要" n={impCount} />
        <Chip id="related" label="关联我" n={relCount} />
      </div>

      {shown.length === 0 ? (
        <div className="text-[12px] text-text-dim py-4 text-center">{items.length ? '该筛选下暂无' : '加载中…'}</div>
      ) : (
        <div className="max-h-[62vh] overflow-y-auto pr-1">
          <div className="relative pl-3 border-l border-border-subtle space-y-3">
            {shown.map((it, i) => {
              const day = md(it.time)
              const showDay = day && day !== lastDay
              lastDay = day
              const dot = it.important ? 'bg-accent' : it.related ? 'bg-[var(--color-up)]' : 'bg-border-strong'
              return (
                <div key={i}>
                  {showDay && i > 0 && (
                    <div className="text-[9.5px] text-text-muted -ml-3 mb-1 mt-1">{day}</div>
                  )}
                  <div className="relative">
                    <span className={`absolute -left-[15px] top-1.5 w-1.5 h-1.5 rounded-full ${dot}`} />
                    <div className="flex items-center gap-1.5 flex-wrap">
                      <span className="text-[10.5px] text-text-muted shrink-0 tabular-nums">{hm(it.time)}</span>
                      {it.important && <span className="text-[9.5px] px-1 rounded bg-accent/15 text-accent border border-accent/30">重要</span>}
                      {it.tag && <span className="text-[9.5px] px-1 rounded bg-accent/10 text-accent/90 border border-accent/25">{it.tag}</span>}
                      {it.related && <span className="text-[9.5px] px-1 rounded" style={{ background: 'var(--color-up)18', color: 'var(--color-up)', border: '1px solid var(--color-up)50' }}>关联</span>}
                      {it.url && <a href={it.url} target="_blank" rel="noreferrer" className="text-[9.5px] text-text-muted hover:text-accent">原文↗</a>}
                    </div>
                    <div onClick={() => setSel({ title: it.title, content: it.content && it.content !== it.title ? it.content : '', source: '金十', time: it.time, url: it.url })}
                      className={`text-[12px] leading-relaxed mt-0.5 cursor-pointer hover:text-accent transition-colors ${it.important ? 'text-text-bright font-medium' : it.related ? 'text-text' : 'text-text-dim'}`}>
                      {it.title}
                    </div>
                    {it.image && (
                      <ImageZoom src={it.image}
                        className="rounded-lg border border-border-subtle max-h-44 w-auto object-cover hover:border-accent/40 mt-1.5 block"
                        imgProps={{ onError: (e) => { e.currentTarget.style.display = 'none' } }} />
                    )}
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      )}
      <div className="text-[10px] text-text-muted pt-2.5 mt-2 border-t border-border-subtle">
        来源 金十数据 · 仅供参考, 不构成任何买卖建议
      </div>
      {sel && <NewsDetailModal item={sel} onClose={() => setSel(null)} />}
    </div>
  )
}
