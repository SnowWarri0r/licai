import { useEffect, useState } from 'react'
import { fetchJSON } from '../../hooks/useApi'
import { memoryChanged } from './memoryEvents'

/* 画像: AI 记住的关于用户的事实, 每次问答都带上。
   分三组: 需要用户处理的(待确认的提案、到期待复核的阶段性条目)、阶段性、长期。
   提案来源两种: 对话中读过第三方文本后记下的, 和夜间整理提出的; 都要确认才生效。 */

const btnBase = 'text-[10.5px] px-1.5 py-[1px] rounded border disabled:opacity-50 cursor-pointer shrink-0'
const btnPlain = `${btnBase} border-border text-text-muted hover:text-text`
const btnAccent = `${btnBase} bg-accent/15 text-accent border-accent/40 hover:bg-accent/25`

function Group({ title, n, hint, children }) {
  return (
    <div className="mb-4">
      <div className="flex items-baseline gap-2 mb-1.5">
        <span className="text-[12px] font-semibold text-text-bright">{title}</span>
        <span className="text-[11px] text-text-muted">{n}</span>
        {hint && <span className="text-[11px] text-text-muted">· {hint}</span>}
      </div>
      {children}
    </div>
  )
}

export function ProfileTab({ data, reload }) {
  const [busy, setBusy] = useState(0)
  const [msg, setMsg] = useState('')
  useEffect(() => { setMsg('') }, [data])

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
      await reload()
      memoryChanged()
    } catch (e) {
      setMsg(String(e.message || e))
    } finally { setBusy(0) }
  }

  if (!data) return <p className="text-[12px] text-text-muted">加载中…</p>
  const items = data.items || []
  const byId = Object.fromEntries(items.map(m => [m.id, m]))
  const pending = items.filter(m => m.status === 'pending')
  const active = items.filter(m => m.status === 'active')
  const stale = active.filter(m => m.stale)
  const phase = active.filter(m => m.review_days > 0 && !m.stale)
  const longterm = active.filter(m => !(m.review_days > 0))
  const days = data.review_days || 30
  const targets = (m) => [m.replaces, ...String(m.also_replaces || '').split(',')]
    .map(x => Number(x)).filter(Boolean).map(id => byId[id]).filter(Boolean)

  const Row = ({ m, children, strong }) => (
    <div className="flex items-start gap-1.5 py-1.5 border-b border-border-subtle last:border-b-0">
      <div className="flex-1 min-w-0 text-[12px]">
        <span className={strong ? 'text-text-bright' : 'text-text'}>{m.fact}</span>
        {m.review_days > 0 && (
          <span className={`ml-1.5 text-[10.5px] ${m.stale ? 'text-accent' : 'text-text-muted'}`}>
            截至 {m.as_of || '?'}{m.stale && ` · ${m.age_days} 天没再提起`}
          </span>
        )}
      </div>
      {children}
    </div>
  )

  return (
    <div>
      {pending.length + stale.length > 0 && (
        <Group title="需要你看一眼" n={pending.length + stale.length}>
          <div className="rounded-lg border border-accent/30 bg-accent/5 px-3 py-1">
            {stale.map(m => (
              <Row key={m.id} m={m} strong>
                <span className="text-[10.5px] text-accent shrink-0 mr-1 mt-[2px]">还成立吗</span>
                <button onClick={() => act(m.id, 'confirm')} disabled={busy === m.id} className={btnAccent}>仍成立</button>
                <button onClick={() => act(m.id, 'delete')} disabled={busy === m.id} className={`${btnPlain} hover:text-bear`}>删除</button>
              </Row>
            ))}
            {pending.map(m => (
              <div key={m.id} className="py-2 border-b border-border-subtle last:border-b-0">
                <div className="text-[10.5px] text-text-muted mb-0.5">
                  {m.origin === 'dream' ? '夜间整理提出' : '对话中记下(读过外部文本)'}
                  {targets(m).length > 0 && (!m.fact ? ' · 建议删除' : targets(m).length > 1 ? ' · 合并以下几条' : ' · 改写')}
                </div>
                {targets(m).map(t => (
                  <div key={t.id} className="text-[11.5px] text-text-muted line-through decoration-text-muted/60">{t.fact}</div>
                ))}
                {m.fact && (
                  <div className="text-[12px] text-text-bright">
                    {targets(m).length > 0 && '→ '}{m.fact}
                    {m.review_days > 0 && <span className="ml-1.5 text-[10.5px] text-text-muted">阶段性 · 截至 {m.as_of}</span>}
                  </div>
                )}
                {m.note && <div className="text-[10.5px] text-text-muted mt-0.5">{m.note}</div>}
                <div className="flex gap-1.5 mt-1">
                  <button onClick={() => act(m.id, 'approve')} disabled={busy === m.id} className={btnAccent}>
                    {m.fact ? '确认' : '确认删除'}
                  </button>
                  <button onClick={() => act(m.id, 'delete')} disabled={busy === m.id} className={btnPlain}>不要</button>
                </div>
              </div>
            ))}
          </div>
        </Group>
      )}

      <Group title="阶段性" n={phase.length} hint={`资金、近期仓位这类会变的情况, ${days} 天没再提起就请你复核`}>
        {phase.length === 0
          ? <p className="text-[11.5px] text-text-muted">没有。</p>
          : phase.map(m => (
            <Row key={m.id} m={m}>
              <button onClick={() => act(m.id, 'longterm')} disabled={busy === m.id} className={btnPlain}
                title="改为长期成立, 不再提醒复核">改长期</button>
              <button onClick={() => act(m.id, 'delete')} disabled={busy === m.id} className={`${btnPlain} hover:text-bear`}>删除</button>
            </Row>
          ))}
      </Group>

      <Group title="长期" n={longterm.length} hint="偏好、习惯、账户结构">
        {longterm.length === 0
          ? <p className="text-[11.5px] text-text-muted">还没有记下任何事实。</p>
          : longterm.map(m => (
            <Row key={m.id} m={m}>
              <button onClick={() => act(m.id, 'phase')} disabled={busy === m.id} className={btnPlain}
                title="标为会变化的阶段性状态, 到期提醒复核">标阶段性</button>
              <button onClick={() => act(m.id, 'delete')} disabled={busy === m.id} className={`${btnPlain} hover:text-bear`}>删除</button>
            </Row>
          ))}
      </Group>
      {msg && <p className="text-[11px] text-bear mt-1">{msg}</p>}
    </div>
  )
}
