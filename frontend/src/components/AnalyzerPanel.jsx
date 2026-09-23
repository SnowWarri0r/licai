import { useState } from 'react'

// 分析插件(licai.analyzers)的通用渲染 —— 只认 services/analyzers.py 约定的展示结构:
// headline / items / timeline / state_text / pending_note / footnote。没装插件时 results 为空, 什么也不画。

const TONE = {
  pos: 'var(--color-bear-bright)',     // A 股口径: 正 = 红
  neg: 'var(--color-bull-bright)',     // 负 = 绿
  accent: 'var(--color-accent)',
  muted: 'var(--color-text-muted)',
}
const toneColor = (t) => TONE[t] || 'var(--color-text)'

// 头部小标签(与恐慌/机构并列)
export function AnalyzerChips({ results }) {
  return (results || []).filter(r => r.available && r.headline).map(r => (
    <span key={r.analyzer} className="text-[10.5px] font-mono px-1.5 py-0.5 rounded"
      style={{ border: '1px solid var(--color-border-med)' }} title={r.headline.title}>
      {r.headline.label} <span style={{ color: toneColor(r.headline.tone) }}>{r.headline.value}</span>
    </span>
  ))
}

// 图下方的说明条: 当前命中条目 + 状态 + 近期出现过的日子
export function AnalyzerPanel({ results }) {
  const [openTl, setOpenTl] = useState(false)
  const list = (results || []).filter(r => r.available)
  if (!list.length) return null
  return list.map(r => (
    <div key={r.analyzer} className="mt-2 bg-surface-3 rounded-md px-3 py-2 text-[11px]">
      <div className="flex items-baseline gap-2 flex-wrap">
        <span className="text-text-bright font-semibold text-[11.5px]">{r.display_name}</span>
        {r.state_text && <span className="text-text-dim font-mono text-[10.5px]">{r.state_text}</span>}
      </div>
      {r.pending_note && <div className="text-text-muted text-[10.5px] mt-0.5">{r.pending_note}</div>}

      {(r.items || []).length === 0
        ? <div className="text-text-dim mt-1">未命中已校准的形态</div>
        : r.items.map((it, i) => (
          <div key={i} className="mt-1.5">
            <div className="flex items-baseline gap-2 flex-wrap">
              <span className="text-text-bright">{it.title}</span>
              {it.badge && (
                <span className="text-[10px] px-1 rounded"
                  style={{ border: `1px solid ${toneColor(it.badge_tone)}`, color: toneColor(it.badge_tone) }}>{it.badge}</span>
              )}
              {it.desc && <span className="text-text-muted text-[10.5px]">{it.desc}</span>}
            </div>
            <div className="flex gap-x-3 gap-y-0.5 flex-wrap font-mono text-[10.5px] mt-0.5">
              {(it.stats || []).map(([k, v, t]) => (
                <span key={k} className="text-text-dim">{k} <span style={{ color: toneColor(t) }}>{v}</span></span>
              ))}
            </div>
          </div>
        ))}

      {(r.timeline || []).length > 0 && (
        <div className="mt-1.5">
          <button onClick={() => setOpenTl(o => !o)} className="text-text-dim hover:text-text text-[10.5px] cursor-pointer">
            {openTl ? '▾' : '▸'} 近期出现过 {r.timeline.length} 次
          </button>
          {openTl && (
            <div className="flex gap-1.5 flex-wrap mt-1 font-mono text-[10.5px]">
              {[...r.timeline].reverse().map(e => (
                <span key={e.date} className="px-1.5 rounded bg-surface-2">
                  <span className="text-text-muted">{e.date.slice(5)}</span>{' '}
                  {e.labels.map((l, j) => <span key={j} style={{ color: toneColor(e.tones?.[j]) }}>{l}{j < e.labels.length - 1 ? '·' : ''}</span>)}
                </span>
              ))}
            </div>
          )}
        </div>
      )}
      {r.footnote && <div className="text-text-muted text-[10px] mt-1.5 leading-snug">{r.footnote}</div>}
    </div>
  ))
}
