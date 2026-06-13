import { useState, useEffect } from 'react'
import { fetchJSON } from '../hooks/useApi'

export default function AITradeReview() {
  const [d, setD] = useState(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState(false)

  const load = (force = false) => {
    setLoading(true); setErr(false)
    fetchJSON(`/api/portfolio/trade-review-ai${force ? '?force=1' : ''}`)
      .then(setD).catch(() => setErr(true)).finally(() => setLoading(false))
  }
  useEffect(() => { load(false) }, [])

  if (loading) return (
    <div className="bg-surface-2 border border-border rounded-xl p-5 text-center text-text-dim text-[12px]">
      AI 正在复盘你的交易纪律…<span className="text-text-muted">（首次约 30–60 秒）</span>
    </div>
  )
  if (err || !d || (!d.summary && !d.narrative && !(d.discipline || []).length && !(d.good || []).length)) {
    return (
      <div className="bg-surface-2 border border-border rounded-xl p-4 flex items-center justify-between">
        <span className="text-text-dim text-[12px]">AI 复盘暂不可用</span>
        <button onClick={() => load(true)} className="text-[11px] px-2.5 py-1 rounded border border-border text-text-dim hover:text-text">重试</button>
      </div>
    )
  }

  return (
    <div className="bg-surface-2 border border-border rounded-xl p-4 md:p-5">
      <div className="flex items-baseline justify-between gap-2 mb-3 flex-wrap">
        <div className="flex items-baseline gap-2">
          <h3 className="text-[14px] font-semibold text-text-bright m-0">AI 交易复盘</h3>
          <span className="text-[10.5px] text-text-muted">纪律 · 照镜子</span>
        </div>
        <button onClick={() => load(true)}
          className="text-[11px] px-2.5 py-1 rounded border border-border text-text-dim hover:text-text hover:border-accent/40">
          重新复盘
        </button>
      </div>

      {/* 一句话定性 */}
      {d.summary && (
        <div className="mb-3 px-3 py-2.5 rounded-lg bg-accent/10 border border-accent/30">
          <div className="text-[10px] text-accent/80 mb-0.5 tracking-wider">一句话定性</div>
          <div className="text-[13px] text-text-bright leading-relaxed">{d.summary}</div>
        </div>
      )}

      {/* 做对的 */}
      {(d.good || []).length > 0 && (
        <div className="mb-3 px-3 py-2.5 rounded-lg bg-bull/8 border border-bull/25">
          <div className="text-[10px] text-bull-bright/90 mb-1.5 tracking-wider">做对的</div>
          <ul className="space-y-1">
            {d.good.map((g, i) => (
              <li key={i} className="text-[11.5px] text-text-dim flex gap-1.5 leading-relaxed">
                <span className="text-bull-bright shrink-0">✓</span><span>{g}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* 纪律问题 */}
      {(d.discipline || []).length > 0 && (
        <div className="space-y-2.5 mb-3">
          <div className="text-[10px] text-bear-bright/80 tracking-wider mb-0.5">要改的</div>
          {d.discipline.map((x, i) => (
            <div key={i} className="border-l-2 border-bear-bright/50 pl-3">
              <div className="text-[13px] font-semibold text-text-bright flex items-center gap-1.5">
                <span className="text-bear-bright text-[12px]">⚠</span>{x.problem}
              </div>
              {x.evidence && <div className="text-[11.5px] text-text-dim mt-0.5 leading-relaxed">{x.evidence}</div>}
              {x.why && <div className="text-[11.5px] text-text-muted mt-1 leading-relaxed">↳ {x.why}</div>}
            </div>
          ))}
        </div>
      )}

      {/* 正文复盘 */}
      {d.narrative && (
        <div className="text-[12.5px] text-text leading-relaxed whitespace-pre-line border-t border-border-subtle pt-3">
          {d.narrative}
        </div>
      )}

      <div className="text-[10px] text-text-muted pt-2.5 mt-2 border-t border-border-subtle">
        基于你的真实流水复盘历史交易习惯 · 仅客观回顾，不构成任何买卖建议
      </div>
    </div>
  )
}
