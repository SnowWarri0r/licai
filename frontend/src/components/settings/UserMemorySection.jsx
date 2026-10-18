import { useEffect, useState } from 'react'
import { fetchJSON } from '../../hooks/useApi'

/* AI 记住的关于你的事实(用户画像)。agent 在你说出长期成立的事实时用 remember 记下,
   之后每次问答都带上。直接生效的条目来自你的原话; 它在同一轮里读过网页/新闻/行情等
   第三方文本后记下的, 可能是夹带的指令, 只存为待确认, 在这里批准后才生效。 */
export function UserMemorySection() {
  const [data, setData] = useState(null)
  const [busy, setBusy] = useState(0)
  const [msg, setMsg] = useState('')

  const load = () => fetchJSON('/api/settings/memory')
    .then(setData).catch(() => setData({ items: [], n_active: 0, n_pending: 0 }))
  useEffect(() => { load() }, [])

  const act = async (id, kind) => {
    setBusy(id)
    try {
      if (kind === 'approve') await fetchJSON(`/api/settings/memory/${id}/approve`, { method: 'POST' })
      else await fetchJSON(`/api/settings/memory/${id}`, { method: 'DELETE' })
      await load()
      setMsg(kind === 'approve' ? '已生效, 下一次问答带上' : '已删除')
    } catch (e) {
      setMsg(String(e.message || e))
    } finally { setBusy(0) }
  }

  const items = data?.items || []
  const pending = items.filter(m => m.status === 'pending')
  const active = items.filter(m => m.status === 'active')

  return (
    <div>
      <label className="text-[12px] text-text-dim font-semibold">
        AI 记住的关于你
        {data && (
          <span className="ml-2 text-[11px] font-normal">
            <span className={pending.length ? 'text-accent' : 'text-text-muted'}>待确认 {pending.length}</span>
            <span className="text-text-muted"> · 已生效 {active.length}</span>
          </span>
        )}
      </label>
      <p className="text-[11px] text-text-muted mt-1 mb-2">
        你在对话里交代的长期事实(比如哪些扣款是定投), 换会话也记得。
        它读过网页/新闻/行情后记下的条目可能不是你的原话, <b>确认了才生效</b>。
      </p>
      {pending.map(m => (
        <div key={m.id} className="mb-2 p-2 rounded-lg border border-accent/25 bg-bg/40">
          <div className="text-[12px] text-text-bright">{m.fact}</div>
          <div className="flex items-center gap-2 mt-1.5">
            <button onClick={() => act(m.id, 'approve')} disabled={busy === m.id}
              className="text-[11px] px-2 py-0.5 rounded bg-accent/20 text-accent border border-accent/40 hover:bg-accent/30 disabled:opacity-50 cursor-pointer">
              确认
            </button>
            <button onClick={() => act(m.id, 'delete')} disabled={busy === m.id}
              className="text-[11px] px-2 py-0.5 rounded border border-border text-text-dim hover:text-text disabled:opacity-50 cursor-pointer">
              删除
            </button>
          </div>
        </div>
      ))}
      {active.length === 0 && pending.length === 0 && (
        <p className="text-[11px] text-text-muted">还没有记下任何事实。</p>
      )}
      {active.map(m => (
        <div key={m.id} className="mt-1.5 text-[11px] flex items-start gap-1">
          <span className="text-text-dim flex-1">{m.fact}</span>
          <button onClick={() => act(m.id, 'delete')} disabled={busy === m.id}
            className="text-[10px] px-1 rounded border border-border text-text-muted hover:text-bear disabled:opacity-50 cursor-pointer shrink-0">
            删除
          </button>
        </div>
      ))}
      {msg && <p className="text-[11px] text-text-dim mt-1.5">{msg}</p>}
    </div>
  )
}
