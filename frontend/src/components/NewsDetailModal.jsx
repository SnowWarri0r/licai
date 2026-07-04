import { useState, useEffect } from 'react'

const _cache = new Map()  // key=url||title → interpret 结果, 同会话重开秒显

export default function NewsDetailModal({ item, onClose }) {
  const [interp, setInterp] = useState(null)
  const [loading, setLoading] = useState(true)
  const cacheKey = item?.url || item?.title || ''

  useEffect(() => {
    const onKey = e => { if (e.key === 'Escape') onClose?.() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  useEffect(() => {
    if (!item) return
    if (_cache.has(cacheKey)) { setInterp(_cache.get(cacheKey)); setLoading(false); return }
    setLoading(true)
    fetch('/api/news/interpret', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title: item.title, content: item.content || '', code: item.code || null, name: item.name || null, source: item.source || null, time: item.time || null, url: item.url || null }),
    }).then(r => r.json()).then(d => { _cache.set(cacheKey, d); setInterp(d) })
      .catch(() => setInterp({ error: '解读暂不可用' }))
      .finally(() => setLoading(false))
  }, [cacheKey])

  if (!item) return null
  const hasUrl = !!item.url
  return (
    <div className="fixed inset-0 z-[220] flex items-center justify-center bg-black/70 backdrop-blur-sm p-4" onClick={onClose}>
      <div className="bg-surface-2 border border-border rounded-xl w-[560px] max-w-[96vw] max-h-[88vh] overflow-y-auto p-5 space-y-3" onClick={e => e.stopPropagation()}>
        <div className="flex items-start justify-between gap-3">
          <h3 className="text-[14px] font-semibold text-text-bright m-0 leading-snug">{item.title}</h3>
          <button onClick={onClose} className="text-text-dim hover:text-text text-[18px] leading-none px-1 cursor-pointer shrink-0">×</button>
        </div>
        <div className="text-[10.5px] text-text-muted flex flex-wrap gap-x-2 gap-y-0.5">
          {item.source && <span>{item.source}</span>}
          {item.time && <span>· {String(item.time).slice(0, 16)}</span>}
          {item.code && <span>· {item.code}{item.name ? `-${item.name}` : ''}</span>}
        </div>

        <div className="rounded-lg border border-accent/20 bg-accent/5 p-3 space-y-1.5">
          {loading ? (
            <div className="text-[11.5px] text-text-dim animate-pulse">解读生成中…</div>
          ) : interp?.error ? (
            <div className="text-[11.5px] text-text-dim">解读暂不可用</div>
          ) : (
            <>
              {interp?.what && <div className="text-[12px] text-text"><span className="text-accent">讲了啥 · </span>{interp.what}</div>}
              {interp?.why && <div className="text-[12px] text-text"><span className="text-accent">为什么重要 · </span>{interp.why}</div>}
              {interp?.relation && <div className="text-[12px] text-text"><span className="text-accent">跟你的关系 · </span>{interp.relation}</div>}
            </>
          )}
        </div>

        {(() => {
          const fetched = interp?.body || ''           // 抓到的全文优先于 teaser
          const body = fetched || item.content || ''
          if (body) return (
            <div className="text-[12px] text-text-dim leading-relaxed whitespace-pre-wrap">
              {fetched && <span className="text-[10px] text-text-muted block mb-1">原文摘录 ↓</span>}
              {body}
            </div>
          )
          if (loading && hasUrl) return <div className="text-[11px] text-text-muted animate-pulse">抓取原文中…</div>
          return <div className="text-[11px] text-text-muted">仅标题，无正文片段{hasUrl ? '（原文未抓到）' : ''}。</div>
        })()}

        <div className="flex justify-end pt-1">
          <button disabled={!hasUrl}
            onClick={() => hasUrl && window.open(item.url, '_blank', 'noopener')}
            className="px-3 py-1.5 rounded-md border border-accent/50 bg-accent/10 text-accent text-[12px] hover:bg-accent/20 transition-colors cursor-pointer disabled:opacity-40 disabled:cursor-default">
            原文 ↗
          </button>
        </div>
      </div>
    </div>
  )
}
