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
      else if (kind === 'confirm') await fetchJSON(`/api/settings/memory/${id}/confirm`, { method: 'POST' })
      else if (kind === 'phase' || kind === 'longterm')
        await fetchJSON(`/api/settings/memory/${id}/phase`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ phase: kind === 'phase' }) })
      else await fetchJSON(`/api/settings/memory/${id}`, { method: 'DELETE' })
      await load()
      setMsg({ approve: '已生效, 下一次问答带上', confirm: '已确认仍成立, 重新计时',
        phase: `已标为阶段性, ${data?.review_days || 30} 天后提醒复核`, longterm: '已改为长期' }[kind] || '已删除')
    } catch (e) {
      setMsg(String(e.message || e))
    } finally { setBusy(0) }
  }

  const dream = async () => {
    setBusy(-1); setMsg('整理中, 约半分钟…')
    try {
      const r = await fetchJSON('/api/settings/memory/dream', { method: 'POST' })
      await load()
      setMsg(r.note ? r.note : `读了 ${r.messages} 条消息, 新提案 ${r.proposed} 条` +
        (r.confirmed ? `, ${r.confirmed} 条阶段性状态已确认仍成立` : '') + (r.dropped ? `, 依据核对不过丢弃 ${r.dropped} 条` : '') + (r.failed ? ', 模型调用失败, 下次接着读' : ''))
    } catch (e) {
      setMsg(String(e.message || e))
    } finally { setBusy(0) }
  }

  const items = data?.items || []
  const pending = items.filter(m => m.status === 'pending')
  const active = items.filter(m => m.status === 'active')
    .sort((a, b) => (b.stale ? 1 : 0) - (a.stale ? 1 : 0))
  const nStale = active.filter(m => m.stale).length
  const base = 'text-[10px] px-1 rounded border disabled:opacity-50 cursor-pointer shrink-0'
  const btn = `${base} border-border text-text-muted`
  const byId = Object.fromEntries(items.map(m => [m.id, m]))
  const ld = data?.last_dream
  // 提案改动了哪几条已有画像
  const targets = (m) => [m.replaces, ...String(m.also_replaces || '').split(',')]
    .map(x => Number(x)).filter(Boolean).map(id => byId[id]).filter(Boolean)

  return (
    <div>
      <label className="text-[12px] text-text-dim font-semibold">
        AI 记住的关于你
        {data && (
          <span className="ml-2 text-[11px] font-normal">
            <span className={pending.length ? 'text-accent' : 'text-text-muted'}>待确认 {pending.length}</span>
            <span className="text-text-muted"> · 已生效 {active.length}</span>
            {nStale > 0 && <span className="text-accent"> · 待复核 {nStale}</span>}
          </span>
        )}
      </label>
      <p className="text-[11px] text-text-muted mt-1 mb-2">
        你在对话里交代的长期事实(比如哪些扣款是定投), 换会话也记得。
        它读过网页/新闻/行情后记下的条目可能不是你的原话, <b>确认了才生效</b>。
        每天 20:40 后会回看当天的对话做一次整理, 提出新增、改写、合并, 同样确认了才生效。
        资金、近期仓位这类会变的情况记为<b>阶段性</b>, 带日期, 超过 {data?.review_days || 30} 天没再提起就请你复核一次。
      </p>
      <div className="flex items-center gap-2 mb-2 text-[11px] text-text-muted">
        <button onClick={dream} disabled={busy !== 0}
          className="px-2 py-0.5 rounded border border-border text-text-dim hover:text-text disabled:opacity-50 cursor-pointer">
          现在整理一次
        </button>
        {ld && <span>上次整理 {ld.at}{ld.note ? ` · ${ld.note}` : ` · 读 ${ld.messages} 条 · 提案 ${ld.proposed}`}</span>}
      </div>
      {pending.map(m => (
        <div key={m.id} className="mb-2 p-2 rounded-lg border border-accent/25 bg-bg/40">
          {targets(m).length > 0 && (
            <div className="text-[11px] text-text-muted mb-1">
              {!m.fact ? '建议删除' : targets(m).length > 1 ? '合并以下几条' : '改写'}:
              {targets(m).map(t => <div key={t.id} className="line-through decoration-text-muted/60">{t.fact}</div>)}
            </div>
          )}
          {m.fact && <div className="text-[12px] text-text-bright">{targets(m).length > 0 && '→ '}{m.fact}
            {m.review_days > 0 && <span className="ml-1 text-[10.5px] text-text-muted">阶段性 · 截至 {m.as_of}</span>}</div>}
          {m.note && <div className="text-[10.5px] text-text-muted mt-1">{m.origin === 'dream' && '夜间整理 · '}{m.note}</div>}
          <div className="flex items-center gap-2 mt-1.5">
            <button onClick={() => act(m.id, 'approve')} disabled={busy === m.id}
              className="text-[11px] px-2 py-0.5 rounded bg-accent/20 text-accent border border-accent/40 hover:bg-accent/30 disabled:opacity-50 cursor-pointer">
              {m.fact ? '确认' : '确认删除'}
            </button>
            <button onClick={() => act(m.id, 'delete')} disabled={busy === m.id}
              className="text-[11px] px-2 py-0.5 rounded border border-border text-text-dim hover:text-text disabled:opacity-50 cursor-pointer">
              不要
            </button>
          </div>
        </div>
      ))}
      {active.length === 0 && pending.length === 0 && (
        <p className="text-[11px] text-text-muted">还没有记下任何事实。</p>
      )}
      {active.map(m => (
        <div key={m.id} className={`mt-1.5 text-[11px] flex items-start gap-1 ${m.stale ? 'p-1.5 rounded border border-accent/30 bg-bg/40' : ''}`}>
          <span className="flex-1">
            <span className={m.stale ? 'text-text-bright' : 'text-text-dim'}>{m.fact}</span>
            {m.review_days > 0 && (
              <span className={`ml-1 text-[10px] ${m.stale ? 'text-accent' : 'text-text-muted'}`}>
                阶段性 · 截至 {m.as_of || '?'}{m.stale && ` · ${m.age_days} 天没再提起, 还成立吗`}
              </span>
            )}
          </span>
          {m.stale && (
            <button onClick={() => act(m.id, 'confirm')} disabled={busy === m.id}
              className={`${base} bg-accent/20 text-accent border-accent/40 hover:bg-accent/30`}>
              仍成立
            </button>
          )}
          <button onClick={() => act(m.id, m.review_days > 0 ? 'longterm' : 'phase')} disabled={busy === m.id}
            title={m.review_days > 0 ? '改为长期成立, 不再提醒复核' : '标为会变化的阶段性状态, 到期提醒复核'}
            className={`${btn} hover:text-text`}>
            {m.review_days > 0 ? '改长期' : '标阶段性'}
          </button>
          <button onClick={() => act(m.id, 'delete')} disabled={busy === m.id}
            className={`${btn} hover:text-bear`}>
            删除
          </button>
        </div>
      ))}
      {msg && <p className="text-[11px] text-text-dim mt-1.5">{msg}</p>}
    </div>
  )
}
