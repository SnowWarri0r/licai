import { useCallback, useEffect, useState } from 'react'
import { fetchJSON } from '../../hooks/useApi'
import { DRIFT_TONE } from './constants'

function DriftPanel({ drift }) {
  if (!drift?.有记录) return null
  const changes = drift.逐次改写 || []
  const track = drift.改写时浮亏轨迹 || {}
  return (
    <div className="mt-3 pt-3 border-t border-border">
      <div className="flex items-baseline gap-2 mb-1.5">
        <span className="text-[12px] font-semibold text-text-bright">逻辑漂移</span>
        <span className="text-[10px] text-text-muted">
          共 {drift.修订次数} 版{drift.距末版天数 != null ? ` · 末版距今 ${drift.距末版天数} 天` : ''}
        </span>
      </div>
      {changes.length === 0 && (
        <p className="text-[10.5px] text-text-muted m-0">只写过一版, 没有改写记录。</p>
      )}
      {changes.map(c => {
        const v = c.判定 || {}
        const t = c.文本变化 || {}
        return (
          <div key={c.rev} className={`mb-1.5 px-2 py-1.5 rounded-lg border text-[10.5px] ${DRIFT_TONE[v.档] || DRIFT_TONE['判不了']}`}>
            <div className="flex items-baseline gap-1.5">
              <span className="font-semibold">第{c.rev}版</span>
              <span className="text-text-muted">{c.日期}</span>
              <span className="ml-auto font-semibold">{v.档}</span>
            </div>
            <div className="mt-0.5 leading-relaxed">{v.说明}</div>
            {(t.删除 || []).length > 0 && (
              <div className="mt-0.5 text-text-muted">删掉: {t.删除.join(' / ')}</div>
            )}
            {(t.新增 || []).length > 0 && (
              <div className="text-text-muted">新增: {t.新增.join(' / ')}</div>
            )}
            {(t.改写 || []).map((r, i) => (
              <div key={i} className="text-text-muted">改写: {r.旧} → {r.新}</div>
            ))}
          </div>
        )
      })}
      {track.逐次走低 && (
        <p className="text-[10.5px] text-bear-bright m-0 mb-1">{track.说明}</p>
      )}
      {drift.自末版以来 && (
        <p className="text-[10.5px] text-text-muted m-0">{drift.自末版以来}</p>
      )}
      <p className="text-[10px] text-text-muted m-0 mt-1 leading-relaxed">{drift.口径}</p>
    </div>
  )
}

export function ThesisModal({ row, onClose, onSaved }) {
  const code = row.code
  const name = row.name || ''
  const [text, setText] = useState('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [meta, setMeta] = useState(null)
  const [drift, setDrift] = useState(null)
  const loadDrift = useCallback(() => {
    fetchJSON(`/api/portfolio/thesis/${encodeURIComponent(code)}/drift`)
      .then(setDrift).catch(() => {})
  }, [code])
  useEffect(() => {
    let alive = true
    fetchJSON(`/api/portfolio/thesis/${encodeURIComponent(code)}`)
      .then(d => { if (alive) { setText(d?.thesis || ''); setMeta(d || null) } })
      .catch(() => {})
      .finally(() => { if (alive) setLoading(false) })
    loadDrift()
    return () => { alive = false }
  }, [code, loadDrift])
  const created = (meta?.created_at || '').slice(0, 10)
  const updated = (meta?.updated_at || '').slice(0, 10)
  const save = async () => {
    setSaving(true)
    try {
      await fetchJSON(`/api/portfolio/thesis/${encodeURIComponent(code)}`, {
        method: 'PUT', body: JSON.stringify({ thesis: text, name }),
      })
      loadDrift()          // 保存那一刻会存下事实快照, 漂移随之变化, 就地刷新
      onSaved?.()
    } catch (e) { console.error(e) } finally { setSaving(false) }
  }
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" onClick={onClose}>
      <div className="bg-surface-2 border border-border rounded-xl w-full max-w-md max-h-[85vh] overflow-y-auto p-4 md:p-5" onClick={e => e.stopPropagation()}>
        <div className="flex items-baseline gap-2 mb-1">
          <h3 className="text-[14px] font-semibold text-text-bright m-0">买入逻辑</h3>
          <span className="text-[12px] text-text-bright">{name}</span>
          <span className="font-mono text-[10.5px] text-text-muted">{code}</span>
          <button onClick={onClose} className="ml-auto text-text-muted hover:text-text cursor-pointer">✕</button>
        </div>
        <p className="text-[10.5px] text-text-muted mb-2">记下当初为什么买、看中什么、预期。以后问 AI「这只逻辑还成立吗」会照这个客观复盘。每次改动都会连同当时的股价与基本面一起存档, 用来分辨改的是事实还是说法。</p>
        <textarea value={text} onChange={e => setText(e.target.value)} disabled={loading}
          rows={6}
          placeholder={loading ? '加载中…' : '例: 国产算力龙头, 中科院系国资背景; 看好 AI 数据中心需求; 等存储涨价兑现到业绩'}
          className="w-full text-[12px] px-3 py-2 rounded-lg bg-surface-3 border border-border text-text placeholder:text-text-muted focus:border-accent/50 outline-none resize-y" />
        <div className="flex items-center gap-2 mt-3">
          {created && <span className="text-[10px] text-text-muted">记于 {created}{updated && updated !== created ? ` · 改于 ${updated}` : ''}</span>}
          <button onClick={save} disabled={saving || loading}
            className="ml-auto text-[12px] px-3.5 py-1.5 rounded-lg bg-accent/20 text-accent border border-accent/40 hover:bg-accent/30 transition-colors cursor-pointer disabled:opacity-40">
            {saving ? '保存中…' : '保存'}
          </button>
        </div>
        <DriftPanel drift={drift} />
      </div>
    </div>
  )
}
