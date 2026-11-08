import { useState, useEffect } from 'react'
import { fetchJSON } from '../../hooks/useApi'
import { WL_DEFAULT } from './shared'

// 观察池批量添加: 粘贴 → 解析预览(可去勾、可从候选里补) → 确认加入。
// 解析只认确定的匹配(代码、精确名称), 认不出的列出候选由人来点, 不替人猜。
export default function WatchImportPanel({ groups, defaultGroup, inPool, onDone, onClose }) {
  const [text, setText] = useState('')
  const [group, setGroup] = useState(defaultGroup || WL_DEFAULT)
  const [newGroup, setNewGroup] = useState('')
  const [parsed, setParsed] = useState(null)      // {resolved, unresolved, truncated}
  const [off, setOff] = useState(() => new Set())  // 预览里去掉勾的代码
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  const parse = async () => {
    setBusy(true); setErr('')
    try {
      const d = await fetchJSON('/api/market/watchlist-parse', { method: 'POST', body: JSON.stringify({ text }) })
      setParsed(d); setOff(new Set())
    } catch (e) { setErr(`解析失败: ${e?.message || '请求失败'}`) }
    setBusy(false)
  }
  // 从候选里补一只: 挪进已识别, 并从未识别里拿掉这一条
  const adopt = (token, c) => setParsed(p => ({
    ...p,
    resolved: p.resolved.some(r => r.code === c.code) ? p.resolved : [...p.resolved, { ...c, token }],
    unresolved: p.unresolved.filter(u => u.token !== token),
  }))
  const toggle = (code) => setOff(s => { const n = new Set(s); n.has(code) ? n.delete(code) : n.add(code); return n })

  const picked = (parsed?.resolved || []).filter(r => !off.has(r.code))
  const grp = group === '__new' ? newGroup.trim().slice(0, 20) : group
  const nNew = picked.filter(r => !inPool(r.code)).length
  const submit = async () => {
    if (!picked.length || !grp) return
    setBusy(true); setErr('')
    try {
      const d = await fetchJSON('/api/market/watchlist-import', {
        method: 'POST', body: JSON.stringify({ codes: picked.map(r => r.code), group: grp === WL_DEFAULT ? '' : grp }),
      })
      onDone(`新加入 ${d.added} 只到「${grp}」` + (d.existed ? ` · ${d.existed} 只原本就在池中` : '')
        + (d.grouped ? `(其中 ${d.grouped} 只补进「${grp}」)` : ''), grp)
    } catch (e) { setErr(`没加成: ${e?.message || '请求失败'}`); setBusy(false) }
  }

  const allGroups = [WL_DEFAULT, ...(groups || []).filter(g => g !== WL_DEFAULT)]
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-4" onPointerDown={onClose}>
      <div className="w-full max-w-lg max-h-[85vh] flex flex-col bg-surface-2 border border-border rounded-lg shadow-xl"
        onPointerDown={(e) => e.stopPropagation()}>
        <div className="px-4 py-2.5 border-b border-border-subtle flex items-center">
          <span className="text-[13px] text-text-bright">批量添加到观察池</span>
          <span className="flex-1" />
          <button onClick={onClose} className="text-text-muted hover:text-text text-[13px]">✕</button>
        </div>
        <div className="p-4 overflow-y-auto flex-1 min-h-0 space-y-3">
          <div>
            <textarea value={text} onChange={(e) => { setText(e.target.value); setParsed(null) }} rows={5} autoFocus
              placeholder={'贴代码或名称, 空格/逗号/换行分隔都行, 可以直接从别处复制一整段\n例: 600519 300750 宁德时代 00700 AAPL'}
              className="w-full bg-surface-3 border border-border-subtle rounded px-2 py-1.5 text-[12px] text-text
                         outline-none focus:border-accent/50 font-mono resize-y" />
            <div className="text-[10px] text-text-muted mt-1">
              认得出: A 股/场内基金 6 位代码(带不带 sh、.SZ 都行)、港股 5 位或 HK 开头、美股大写代码或 US. 开头、完整股票名。价格、涨跌幅这类数字自动忽略。
            </div>
          </div>
          {!parsed && (
            <button onClick={parse} disabled={busy || !text.trim()}
              className="text-[12px] px-3 py-1 rounded bg-accent/15 text-accent disabled:opacity-40">
              {busy ? '解析中…' : '解析'}
            </button>
          )}
          {parsed && (
            <>
              <div>
                <div className="text-[11px] text-text-dim mb-1">
                  识别出 {parsed.resolved.length} 只{parsed.truncated ? ' · 太长了, 只解析了前 200 段' : ''}
                </div>
                {parsed.resolved.length === 0 && <div className="text-[11px] text-text-muted">没有认出来的</div>}
                <div className="grid grid-cols-2 gap-x-3">
                  {parsed.resolved.map(r => {
                    const on = !off.has(r.code)
                    return (
                      <button key={r.code} onClick={() => toggle(r.code)} aria-pressed={on}
                        className={`flex items-center gap-1.5 py-0.5 text-left text-[11.5px] ${on ? 'text-text' : 'text-text-muted line-through'}`}>
                        <span className={`w-3 h-3 shrink-0 rounded-sm border text-[8px] leading-[10px] text-center ${on ? 'bg-accent border-accent text-surface' : 'border-text-muted/60'}`}>{on ? '✓' : ''}</span>
                        <span className="truncate">{r.name}</span>
                        <span className="font-mono text-[10px] text-text-muted">{r.code}</span>
                        {inPool(r.code) && <span className="text-[9px] text-text-muted shrink-0">已在池</span>}
                      </button>
                    )
                  })}
                </div>
              </div>
              {parsed.unresolved.length > 0 && (
                <div>
                  <div className="text-[11px] text-text-dim mb-1">没认出 {parsed.unresolved.length} 段(点候选补进来, 不点就忽略)</div>
                  {parsed.unresolved.map(u => (
                    <div key={u.token} className="flex items-center gap-1.5 flex-wrap py-0.5 text-[11px]">
                      <span className="font-mono text-bear-bright">{u.token}</span>
                      {u.suggest.length === 0 && <span className="text-text-muted">无候选</span>}
                      {u.suggest.map(c => (
                        <button key={c.code} onClick={() => adopt(u.token, c)}
                          className="px-1.5 rounded border border-border-subtle text-text-dim hover:text-accent hover:border-accent/50">
                          {c.name} <span className="font-mono text-[10px]">{c.code}</span>
                        </button>
                      ))}
                    </div>
                  ))}
                </div>
              )}
            </>
          )}
          {err && <div className="text-[11px] text-bear-bright">{err}</div>}
        </div>
        {parsed && (
          <div className="px-4 py-2.5 border-t border-border-subtle flex items-center gap-2 flex-wrap">
            <span className="text-[11px] text-text-dim">放进</span>
            <select value={group} onChange={(e) => setGroup(e.target.value)}
              className="bg-surface-3 border border-border-subtle rounded px-1.5 py-0.5 text-[11px] text-text">
              {allGroups.map(g => <option key={g} value={g}>{g}</option>)}
              <option value="__new">新建分组…</option>
            </select>
            {group === '__new' && (
              <input value={newGroup} onChange={(e) => setNewGroup(e.target.value)} maxLength={20} placeholder="新分组名"
                className="w-24 bg-surface-3 border border-border-subtle rounded px-1.5 py-0.5 text-[11px] text-text outline-none focus:border-accent/50" />
            )}
            <span className="flex-1" />
            <button onClick={() => setParsed(null)} className="text-[11px] text-text-dim hover:text-text">改文本</button>
            <button onClick={submit} disabled={busy || !picked.length || !grp}
              className="text-[12px] px-3 py-1 rounded bg-accent/15 text-accent disabled:opacity-40">
              {busy ? '加入中…' : `加入 ${picked.length} 只`}{nNew !== picked.length && picked.length ? `(新 ${nNew})` : ''}
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
