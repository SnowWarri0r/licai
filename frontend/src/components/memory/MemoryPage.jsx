import { useEffect, useState } from 'react'
import { fetchJSON } from '../../hooks/useApi'
import { PromptRulesSection } from '../settings/PromptRulesSection'
import { ProfileTab } from './ProfileTab'
import { NotesTab } from './NotesTab'
import { memoryChanged } from './memoryEvents'

/* AI 记忆: AI 从对话里积累下来、之后每次作答都会用到的东西, 集中在一页看和改。
   画像(关于用户的事实)、个股档案(关于每只票的认识)、回答规则(从纠正里沉淀的作答规则)。
   夜间整理每天 20:40 后自动跑; 页头可以手动触发一次。 */
export default function MemoryPage() {
  const [tab, setTab] = useState('profile')
  const [mem, setMem] = useState(null)
  const [notes, setNotes] = useState(null)
  const [nRules, setNRules] = useState(0)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')

  const loadMem = () => fetchJSON('/api/settings/memory').then(setMem).catch(() => setMem({ items: [] }))
  const loadNotes = () => fetchJSON('/api/settings/notes').then(setNotes).catch(() => setNotes({ stocks: [], n_entries: 0 }))
  const loadRules = () => fetchJSON('/api/settings/rules').then(d => setNRules(d.n_pending || 0)).catch(() => {})
  useEffect(() => { loadMem(); loadNotes(); loadRules() }, [])
  // 规则页签里批了规则, 角标与页签计数一起刷新
  useEffect(() => {
    const on = () => loadRules()
    window.addEventListener('licai:memory-changed', on)
    return () => window.removeEventListener('licai:memory-changed', on)
  }, [])

  const consolidate = async () => {
    setBusy(true); setMsg('整理中: 先画像, 再个股档案, 约一两分钟…')
    try {
      const a = await fetchJSON('/api/settings/memory/dream', { method: 'POST' })
      const b = await fetchJSON('/api/settings/notes/build', { method: 'POST' })
      await Promise.all([loadMem(), loadNotes()])
      memoryChanged()
      const pa = a.note || `画像读了 ${a.messages} 条消息, 新提案 ${a.proposed} 条` +
        (a.confirmed ? `, ${a.confirmed} 条阶段性状态确认仍成立` : '')
      const pb = b.note || `档案新增 ${b.added} 条` + (b.remaining ? `, 还有 ${b.remaining} 块没读完, 可再点一次` : '')
      setMsg(`${pa}; ${pb}` + (a.failed || b.failed ? '。有模型调用失败, 下次接着读' : ''))
    } catch (e) {
      setMsg(String(e.message || e))
    } finally { setBusy(false) }
  }

  const items = mem?.items || []
  const nAttention = items.filter(m => m.status === 'pending' || m.stale).length
  const nProfile = items.filter(m => m.status === 'active').length
  const ld = mem?.last_dream, ln = notes?.last_run

  const TABS = [
    ['profile', '画像', nProfile, nAttention],
    ['notes', '个股档案', (notes?.stocks || []).length, 0],
    ['rules', '回答规则', null, nRules],
  ]

  return (
    <section className="rounded-xl border border-border bg-surface/60 overflow-hidden"
      style={{ animation: 'fade-up 0.4s ease-out' }}>
      <div className="px-3 md:px-5 py-3 border-b border-border"
        style={{ background: 'linear-gradient(180deg, var(--color-surface-2), var(--color-surface))' }}>
        <div className="flex items-baseline gap-2 flex-wrap">
          <h3 className="text-[13px] font-semibold text-text-bright m-0">AI 记忆</h3>
          <span className="text-[11px] text-text-dim">AI 从你们的对话里记下、之后每次作答都会用到的内容</span>
        </div>
        <div className="flex items-center gap-2 mt-2 text-[11px] text-text-muted flex-wrap">
          <span>每天 20:40 后自动整理当天的对话</span>
          {ld && <span>· 画像 {ld.at}</span>}
          {ln && <span>· 档案 {ln.at}</span>}
          <button onClick={consolidate} disabled={busy}
            className="ml-auto px-2 py-0.5 rounded border border-border text-text-dim hover:text-text disabled:opacity-50 cursor-pointer">
            现在整理一次
          </button>
        </div>
        {msg && <div className="text-[11px] text-text-dim mt-1.5">{msg}</div>}
      </div>

      <div className="px-3 md:px-5 pt-2">
        <div className="flex gap-1 border-b border-border-subtle">
          {TABS.map(([k, label, count, attention]) => (
            <button key={k} onClick={() => setTab(k)}
              className={`text-[12px] px-3 py-1.5 -mb-px border-b-2 flex items-center gap-1.5 cursor-pointer
                ${tab === k ? 'border-accent text-text-bright font-medium' : 'border-transparent text-text-dim hover:text-text'}`}>
              {label}
              {count != null && <span className="text-[10.5px] text-text-muted font-normal">{count}</span>}
              {attention > 0 && (
                <span className="text-[10px] leading-none px-1.5 py-[2px] rounded-full bg-accent/20 text-accent font-normal">
                  {attention}
                </span>
              )}
            </button>
          ))}
        </div>
      </div>

      <div className="px-3 md:px-5 py-4">
        {tab === 'profile' && (
          <>
            <p className="text-[11.5px] text-text-muted mb-3">
              你在对话里交代过的事实和偏好, 每次问答都会带上。夜间整理提出的新增、改写、合并, 以及 AI 读过网页或新闻后记下的条目,
              都要你确认才生效。
            </p>
            <ProfileTab data={mem} reload={loadMem} />
          </>
        )}
        {tab === 'notes' && (
          <>
            <p className="text-[11.5px] text-text-muted mb-3">
              从过往问答里整理出的主营、产业链、关联、事件、当时的结论和你的看法, 每条带出处日期。问到这只票时 AI 先读档案,
              再用当下数据补行情。在个股分析抽屉里也能看到这只票的档案。
            </p>
            <NotesTab data={notes} reload={loadNotes} />
          </>
        )}
        {tab === 'rules' && <PromptRulesSection />}
      </div>
    </section>
  )
}
