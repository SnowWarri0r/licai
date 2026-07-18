import { useState, useEffect } from 'react'
import { fetchJSON } from '../hooks/useApi'

const pctCls = (v) => v == null ? 'text-text-dim' : v > 0 ? 'text-bear' : v < 0 ? 'text-bull' : 'text-text-dim'

// 收盘小结(站内版): 与 15:10 飞书推送同一份数据——没配飞书也能在这里看
export default function EodSummaryCard() {
  const [d, setD] = useState(null)
  const [err, setErr] = useState('')

  useEffect(() => {
    fetchJSON('/api/portfolio/eod-summary')
      .then(setD)
      .catch(e => setErr(e?.message || '加载失败'))
  }, [])

  if (err) return null
  if (!d) return <div className="text-[11.5px] text-text-dim py-2">收盘小结生成中…</div>

  const rows = (d.rows || []).filter(r => r.pct != null)
  const cls = d.by_class || {}

  return (
    <div className="bg-surface-3/60 border border-border-subtle rounded-lg px-3.5 py-3 mb-3">
      <div className="flex items-baseline gap-2 flex-wrap mb-1.5">
        <span className="text-[12.5px] font-semibold text-text-bright">收盘小结</span>
        <span className="text-[10px] text-text-muted">{d.date} · 交易日15:10自动推飞书, 此处随时可看(盘中为实时切片)</span>
      </div>

      <div className="flex items-baseline gap-3 flex-wrap mb-1.5">
        <span className="text-[13px] font-mono font-semibold">
          组合今日浮动 <span className={pctCls(d.total_change)}>{d.total_change >= 0 ? '+' : ''}{(d.total_change || 0).toLocaleString()}</span> 元
        </span>
        {Object.keys(cls).length > 1 && Object.entries(cls).map(([k, v]) => (
          <span key={k} className="text-[10.5px] text-text-dim">{k} <span className={`font-mono ${pctCls(v)}`}>{v >= 0 ? '+' : ''}{v.toLocaleString()}</span></span>
        ))}
      </div>

      {rows.length > 0 && (
        <div className="flex flex-wrap gap-x-4 gap-y-0.5 mb-1.5">
          {rows.slice(0, 8).map(r => (
            <span key={`${r.类别}-${r.code}`} className="text-[11px] text-text-dim">
              {r.name}<span className="text-[9px] text-text-muted ml-0.5">{r.类别}</span>
              <span className={`font-mono ml-1 ${pctCls(r.pct)}`}>{r.pct >= 0 ? '+' : ''}{r.pct}%</span>
            </span>
          ))}
          {rows.length > 8 && <span className="text-[10.5px] text-text-muted">等 {rows.length} 项</span>}
        </div>
      )}

      {(d.events || []).length > 0 && (
        <div className="space-y-0.5 mb-1">
          {d.events.slice(0, 6).map((e, i) => (
            <div key={i} className="text-[11px] text-accent/90">- {e}</div>
          ))}
        </div>
      )}

      <div className="text-[9.5px] text-text-muted">纯客观数据, 不构成任何买卖建议</div>
    </div>
  )
}
