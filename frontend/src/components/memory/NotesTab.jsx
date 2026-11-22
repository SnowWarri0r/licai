import { useMemo, useState } from 'react'
import { fetchJSON } from '../../hooks/useApi'

/* 个股档案: 夜间整理从过往问答里提取的结构性认识, 一只票一页。
   左边是票的列表(可搜), 右边是选中那只的档案页。条目带出处日期, 不对的可以删。 */
const KIND_ORDER = ['主营', '产业链', '关联', '事件', '结论', '用户']
const KIND_LABEL = { 用户: '你的看法' }

const latest = (s) => s.entries.reduce((a, e) => (e.said_on > a ? e.said_on : a), '')

export function NotePage({ stock, onDelete, busy }) {
  return (
    <div>
      {KIND_ORDER.map(k => {
        const items = stock.entries.filter(e => e.kind === k)
        if (!items.length) return null
        return (
          <div key={k} className="mb-3 last:mb-0">
            <div className="text-[11px] text-text-muted mb-1">{KIND_LABEL[k] || k}</div>
            {items.map(e => (
              <div key={e.id} className="flex items-start gap-1.5 py-1 text-[12px]" title={e.quote ? `依据: ${e.quote}` : ''}>
                <span className="flex-1 text-text">
                  {e.text}
                  <span className="text-[10.5px] text-text-muted ml-1.5">{e.said_on} · 会话 {e.session_id}</span>
                </span>
                {onDelete && (
                  <button onClick={() => onDelete(e.id)} disabled={busy === e.id}
                    className="text-[10.5px] px-1.5 py-[1px] rounded border border-border text-text-muted hover:text-bear disabled:opacity-50 cursor-pointer shrink-0">
                    删除
                  </button>
                )}
              </div>
            ))}
          </div>
        )
      })}
    </div>
  )
}

export function NotesTab({ data, reload }) {
  const [q, setQ] = useState('')
  const [sel, setSel] = useState('')
  const [busy, setBusy] = useState(0)
  const [msg, setMsg] = useState('')

  const stocks = useMemo(() => (data?.stocks || []).map(s => ({ ...s, latest: latest(s) }))
    .sort((a, b) => (b.latest > a.latest ? 1 : b.latest < a.latest ? -1 : 0)), [data])
  const shown = stocks.filter(s => !q || (s.name || '').includes(q) || s.code.includes(q.toUpperCase()))
  const cur = stocks.find(s => s.code === sel) || shown[0]

  const del = async (id) => {
    setBusy(id)
    try {
      await fetchJSON(`/api/settings/notes/${id}`, { method: 'DELETE' })
      await reload()
    } catch (e) {
      setMsg(String(e.message || e))
    } finally { setBusy(0) }
  }

  if (!data) return <p className="text-[12px] text-text-muted">加载中…</p>
  if (!stocks.length) return <p className="text-[12px] text-text-muted">还没有档案。聊过的票会在夜间整理后出现在这里。</p>

  return (
    <div className="flex flex-col md:flex-row gap-3 md:h-[560px]">
      <div className="md:w-56 shrink-0 flex flex-col min-h-0">
        <input value={q} onChange={e => setQ(e.target.value)} placeholder={`搜 ${stocks.length} 只票`}
          className="mb-2 px-2 py-1 text-[12px] rounded border border-border bg-bg/60 text-text outline-none focus:border-accent/50" />
        <div className="flex-1 min-h-0 overflow-y-auto max-h-[220px] md:max-h-none rounded-lg border border-border-subtle">
          {shown.map(s => (
            <button key={s.code} onClick={() => setSel(s.code)}
              className={`w-full flex items-baseline gap-1.5 px-2.5 py-1.5 text-left border-b border-border-subtle last:border-b-0 cursor-pointer
                ${cur?.code === s.code ? 'bg-accent/12 text-accent' : 'text-text-dim hover:bg-surface-3/50 hover:text-text'}`}>
              <span className="text-[12px] truncate">{s.name || s.code}</span>
              <span className="text-[10.5px] text-text-muted ml-auto shrink-0">{s.entries.length} · {s.latest.slice(5)}</span>
            </button>
          ))}
          {shown.length === 0 && <p className="text-[11.5px] text-text-muted p-2">没有匹配的票。</p>}
        </div>
      </div>
      <div className="flex-1 min-w-0 min-h-0 overflow-y-auto rounded-lg border border-border-subtle px-4 py-3">
        {cur && (
          <>
            <div className="flex items-baseline gap-2 mb-3">
              <span className="text-[14px] font-semibold text-text-bright">{cur.name}</span>
              <span className="text-[11px] font-mono text-text-muted">{cur.code}</span>
              <span className="text-[11px] text-text-muted ml-auto">{cur.entries.length} 条 · 最近 {cur.latest}</span>
            </div>
            <NotePage stock={cur} onDelete={del} busy={busy} />
          </>
        )}
        {msg && <p className="text-[11px] text-bear mt-1">{msg}</p>}
      </div>
    </div>
  )
}
