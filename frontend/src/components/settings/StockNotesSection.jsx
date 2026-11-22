import { useEffect, useState } from 'react'
import { fetchJSON } from '../../hooks/useApi'

/* 个股档案: 夜间整理从过往问答里提取的结构性认识(主营、产业链、关联、事件、当时结论、用户看法),
   一只票一页。问到这只票时 agent 先读档案再用当下工具补行情。条目带出处与日期, 不对的可以删。 */
const KIND_ORDER = ['主营', '产业链', '关联', '事件', '结论', '用户']

export function StockNotesSection() {
  const [data, setData] = useState(null)
  const [open, setOpen] = useState('')
  const [busy, setBusy] = useState(0)
  const [msg, setMsg] = useState('')

  const load = () => fetchJSON('/api/settings/notes')
    .then(setData).catch(() => setData({ stocks: [], n_entries: 0 }))
  useEffect(() => { load() }, [])

  const build = async () => {
    setBusy(-1); setMsg('整理中, 每块约半分钟到一分钟…')
    try {
      const r = await fetchJSON('/api/settings/notes/build', { method: 'POST' })
      await load()
      setMsg(r.note ? r.note : `读了 ${r.messages} 条消息, 新增 ${r.added} 条` +
        (r.superseded ? `, 作废 ${r.superseded} 条` : '') + (r.dropped ? `, 核对不过丢弃 ${r.dropped} 条` : '') +
        (r.remaining ? `, 还有 ${r.remaining} 块没读, 可再点一次` : '') + (r.failed ? ', 模型调用失败, 下次接着读' : ''))
    } catch (e) {
      setMsg(String(e.message || e))
    } finally { setBusy(0) }
  }

  const del = async (id) => {
    setBusy(id)
    try {
      await fetchJSON(`/api/settings/notes/${id}`, { method: 'DELETE' })
      await load()
    } catch (e) {
      setMsg(String(e.message || e))
    } finally { setBusy(0) }
  }

  const stocks = data?.stocks || []
  const lr = data?.last_run

  return (
    <div>
      <label className="text-[12px] text-text-dim font-semibold">
        个股档案
        {data && <span className="ml-2 text-[11px] font-normal text-text-muted">{stocks.length} 只 · {data.n_entries} 条</span>}
      </label>
      <p className="text-[11px] text-text-muted mt-1 mb-2">
        从过往问答里整理出的主营、产业链、关联关系、事件和当时的结论, 一只票一页, 每条带出处日期。
        问到这只票时 AI 先读档案, 再用当下数据补行情。每天 20:40 后随画像一起整理; 不对的条目直接删。
      </p>
      <div className="flex items-center gap-2 mb-2 text-[11px] text-text-muted">
        <button onClick={build} disabled={busy !== 0}
          className="px-2 py-0.5 rounded border border-border text-text-dim hover:text-text disabled:opacity-50 cursor-pointer">
          现在整理一次
        </button>
        {lr && <span>上次整理 {lr.at}{lr.note ? ` · ${lr.note}` : ` · 新增 ${lr.added}`}{lr.remaining ? ` · 还有 ${lr.remaining} 块未读` : ''}</span>}
      </div>
      {stocks.length === 0 && <p className="text-[11px] text-text-muted">还没有档案。</p>}
      <div className="flex flex-wrap gap-1.5">
        {stocks.map(s => (
          <button key={s.code} onClick={() => setOpen(open === s.code ? '' : s.code)}
            className={`text-[11px] px-2 py-0.5 rounded border cursor-pointer ${open === s.code
              ? 'border-accent/50 text-accent bg-accent/10' : 'border-border text-text-dim hover:text-text'}`}>
            {s.name || s.code}<span className="text-text-muted ml-1">{s.entries.length}</span>
          </button>
        ))}
      </div>
      {stocks.filter(s => s.code === open).map(s => (
        <div key={s.code} className="mt-2 p-2 rounded-lg border border-border bg-bg/40">
          <div className="text-[12px] text-text-bright mb-1">{s.name}({s.code})</div>
          {KIND_ORDER.map(k => {
            const items = s.entries.filter(e => e.kind === k)
            if (!items.length) return null
            return (
              <div key={k} className="mb-1.5">
                <div className="text-[10.5px] text-text-muted">{k}</div>
                {items.map(e => (
                  <div key={e.id} className="text-[11px] flex items-start gap-1 mt-0.5" title={`依据: ${e.quote}`}>
                    <span className="text-text-dim flex-1">{e.text}
                      <span className="text-text-muted ml-1">{e.said_on} · 会话 {e.session_id}</span></span>
                    <button onClick={() => del(e.id)} disabled={busy === e.id}
                      className="text-[10px] px-1 rounded border border-border text-text-muted hover:text-bear disabled:opacity-50 cursor-pointer shrink-0">
                      删除
                    </button>
                  </div>
                ))}
              </div>
            )
          })}
        </div>
      ))}
      {msg && <p className="text-[11px] text-text-dim mt-1.5">{msg}</p>}
    </div>
  )
}
