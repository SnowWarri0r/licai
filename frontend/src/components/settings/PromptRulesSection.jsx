import { useEffect, useState } from 'react'
import { fetchJSON } from '../../hooks/useApi'

/* 从"用户纠正"里沉淀出来的候选规则。
   批准的会写进 agent 的 system prompt(下一次问答就生效), 待审的一条都不生效。
   为什么要有这个面板: 之前只有 CLI, 待审队列在界面上根本不存在 —— 挖出来的候选就一直
   躺着没人批, 这套机制等于白做。否决的不删, 留着才知道"提过、被否了"。 */
export function PromptRulesSection() {
  const [data, setData] = useState(null)
  const [busy, setBusy] = useState(0)          // 正在处理的规则 id
  const [mining, setMining] = useState(false)
  const [msg, setMsg] = useState('')

  const load = () => fetchJSON('/api/settings/rules')
    .then(setData).catch(() => setData({ rules: [], n_pending: 0, n_active: 0 }))
  useEffect(() => { load() }, [])

  const decide = async (id, decision) => {
    setBusy(id)
    try {
      await fetchJSON(`/api/settings/rules/${id}/${decision}`, { method: 'POST' })
      await load()
      setMsg(decision === 'approve' ? '已进 prompt, 下一次问答生效' : '已否决, 不进 prompt')
    } catch (e) {
      setMsg(String(e.message || e))
    } finally { setBusy(0) }
  }

  const mine = async () => {
    setMining(true); setMsg('挖掘中(要调模型, 十几秒)…')
    try {
      const r = await fetchJSON('/api/settings/rules/mine', { method: 'POST' })
      setMsg(`新纠正 ${r.corrections} 条 → 新增候选 ${r.added}, 判为非规则 ${r.skipped}, 重复 ${r.dup}, 失败 ${r.failed}`)
      await load()
    } catch (e) {
      setMsg(String(e.message || e))
    } finally { setMining(false) }
  }

  const rules = data?.rules || []
  const pending = rules.filter(r => r.status === 'pending')
  const active = rules.filter(r => r.status === 'active')

  return (
    <div>
      <div className="flex items-center justify-between mb-2">
        <label className="text-[12px] text-text-dim font-semibold">
          AI 读盘规则
          {data && (
            <span className="ml-2 text-[11px] font-normal">
              <span className={pending.length ? 'text-accent' : 'text-text-muted'}>待审 {pending.length}</span>
              <span className="text-text-muted"> · 已生效 {active.length}</span>
            </span>
          )}
        </label>
        <button onClick={mine} disabled={mining}
          className="text-[11px] px-2 py-0.5 rounded border border-border text-text-dim hover:text-text disabled:opacity-50 cursor-pointer">
          {mining ? '挖掘中…' : '立刻挖一次'}
        </button>
      </div>
      <p className="text-[11px] text-text-muted mb-2">
        从你历次「不对/怎么还是」这类纠正里自动起草。<b>批准了才进 prompt</b>，待审的一条都不生效 ——
        prompt 对单个词都敏感，一条坏规则会静默影响所有回答。每周一自动挖一次。
      </p>
      {pending.length === 0 && (
        <p className="text-[11px] text-text-muted">待审队列是空的。</p>
      )}
      {pending.map(r => (
        <div key={r.id} className="mb-2 p-2 rounded-lg border border-accent/25 bg-bg/40">
          <div className="text-[12px] text-text-bright">【{r.title}】</div>
          <div className="text-[11px] text-text-dim mt-0.5 leading-relaxed">{r.body}</div>
          {r.evidence && (
            <div className="text-[10px] text-text-muted mt-1">← 触发它的纠正: {r.evidence}</div>
          )}
          <div className="flex items-center gap-2 mt-1.5">
            <button onClick={() => decide(r.id, 'approve')} disabled={busy === r.id}
              className="text-[11px] px-2 py-0.5 rounded bg-accent/20 text-accent border border-accent/40 hover:bg-accent/30 disabled:opacity-50 cursor-pointer">
              批准进 prompt
            </button>
            <button onClick={() => decide(r.id, 'reject')} disabled={busy === r.id}
              className="text-[11px] px-2 py-0.5 rounded border border-border text-text-dim hover:text-text disabled:opacity-50 cursor-pointer">
              否决
            </button>
          </div>
        </div>
      ))}
      {active.length > 0 && (
        <details className="mt-1">
          <summary className="text-[11px] text-text-muted cursor-pointer">已生效的 {active.length} 条</summary>
          {active.map(r => (
            <div key={r.id} className="mt-1.5 text-[11px]">
              <span className="text-text-dim">【{r.title}】</span>
              <span className="text-text-muted"> {r.body}</span>
              <button onClick={() => decide(r.id, 'reject')} disabled={busy === r.id}
                title="从 prompt 里撤掉"
                className="ml-1 text-[10px] px-1 rounded border border-border text-text-muted hover:text-bear disabled:opacity-50 cursor-pointer">
                撤掉
              </button>
            </div>
          ))}
        </details>
      )}
      {msg && <p className="text-[11px] text-text-dim mt-1.5">{msg}</p>}
    </div>
  )
}
